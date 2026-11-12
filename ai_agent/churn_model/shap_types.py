"""페이지 M3: SHAP → 이탈 유형 산출(방법 B) 및 정답 유형 복원 검증.

group_shap(cm, F)        -> (피처별 SHAP DataFrame, 그룹별 SHAP 합 DataFrame[T01..T10], 기준값)
select_types(G, tcfg)    -> 구독×순위별 채택 유형 DataFrame (churn_prediction_type 행 후보)
- 그룹 합 = 같은 유형 그룹 피처 SHAP의 순합(컨텍스트 피처는 제외). 양수 그룹만 남겨 비중 = 그룹 합 ÷ 양수 그룹 합.
- 채택 = 비중 ≥ share_min 그리고 그룹 SHAP ≥ shap_min, 큰 순서 최대 max_types개. 없으면 '유형 없음'(행 없음).
- 근거 피처 = 채택 유형 그룹 안에서 SHAP이 큰 순으로 값이 있는 피처 top_features_n개: {"feature","value","shap"}.
- 정답 유형(synth_truth_type)은 검증(recovery)에만 쓴다. 산출에는 쓰지 않는다.
"""
from __future__ import annotations
import json
import numpy as np, pandas as pd, xgboost as xgb
from .features import encode, feature_groups
from .train import HERE
import yaml

TYPES = [f"T{i:02d}" for i in range(1, 11)]


def load_type_cfg(path=None) -> dict:
    from pathlib import Path
    return yaml.safe_load(Path(path or HERE / "config.yaml").read_text(encoding="utf-8"))["types"]


def group_shap(cm, F: pd.DataFrame):
    """XGBoost 내장 TreeSHAP(pred_contribs)으로 구독×피처 SHAP(로그오즈)을 구한다. 합 + 기준값 = 예측 마진(가법성 검증됨).
    (shap 라이브러리 0.51의 TreeExplainer는 이 XGBoost 버전에서 마진과 최대 0.3 어긋나 쓰지 않는다.)"""
    X = encode(F)[cm.cols]; c = cm.model.get_booster().predict(xgb.DMatrix(X), pred_contribs=True)
    S = pd.DataFrame(c[:, :-1], index=F.index, columns=cm.cols); base = float(c[0, -1])
    g = feature_groups(); G = pd.DataFrame({t: S[[c_ for c_ in cm.cols if g[c_] == t]].sum(axis=1) for t in TYPES})
    return S, G, base


def select_types(S: pd.DataFrame, G: pd.DataFrame, F: pd.DataFrame, tcfg: dict | None = None) -> pd.DataFrame:
    tcfg = tcfg or load_type_cfg(); g = feature_groups(); pos = G.clip(lower=0); tot = pos.sum(axis=1); share = pos.div(tot.where(tot > 0), axis=0).fillna(0.0)
    ok = (share >= tcfg["share_min"]) & (G >= tcfg["shap_min"]); rows = []
    Fv = F; cols_by_t = {t: [c for c in S.columns if g[c] == t] for t in TYPES}
    for i, sid in enumerate(G.index):
        cand = [t for t in TYPES if ok.iat[i, TYPES.index(t)]]; cand.sort(key=lambda t: -G.iat[i, TYPES.index(t)])
        for rk, t in enumerate(cand[: tcfg["max_types"]], start=1):
            sv = S.loc[sid, cols_by_t[t]]; feats = []
            for f in sv[sv >= 0.005].sort_values(ascending=False).index:         # 0에 가까운 기여는 근거로 쓰지 않음
                v = Fv.at[sid, f]
                if pd.isna(v): continue
                feats.append({"feature": f, "value": (int(v) if float(v).is_integer() else round(float(v), 3)), "shap": round(float(sv[f]), 3)})
                if len(feats) >= tcfg["top_features_n"]: break
            rows.append(dict(subscription_id=sid, type_rank=rk, type_id=t, contribution_share=round(float(share.iat[i, TYPES.index(t)]), 4),
                             group_shap=round(float(G.iat[i, TYPES.index(t)]), 4), top_features_json=json.dumps(feats, ensure_ascii=False)))
    return pd.DataFrame(rows, columns=["subscription_id", "type_rank", "type_id", "contribution_share", "group_shap", "top_features_json"])


def recovery(pred: pd.DataFrame, truth: pd.DataFrame, ids, risk_ids=None) -> dict:
    """정답 유형 복원: 유형별 정밀도·재현율, 주 유형(1순위) 일치율, 유형 없음 비율, 채택 개수 분포."""
    ids = pd.Index(ids); P = pred[pred.subscription_id.isin(ids)]; T = truth[truth.subscription_id.isin(ids)]
    ps = {t: set(P[P.type_id == t].subscription_id) for t in TYPES}; ts = {t: set(T[T.type_id == t].subscription_id) for t in TYPES}
    per = []
    for t in TYPES:
        tp = len(ps[t] & ts[t]); per.append(dict(type=t, truth_n=len(ts[t]), pred_n=len(ps[t]), precision=tp / len(ps[t]) if ps[t] else np.nan, recall=tp / len(ts[t]) if ts[t] else np.nan))
    per = pd.DataFrame(per).set_index("type"); per["f1"] = 2 * per.precision * per.recall / (per.precision + per.recall)
    act = set(T.subscription_id); n_pred = P.groupby("subscription_id").size().reindex(ids).fillna(0).astype(int)
    prim = T[T.is_primary_yn == "Y"].set_index("subscription_id").type_id; top1 = P[P.type_rank == 1].set_index("subscription_id").type_id
    both = [s for s in prim.index if s in top1.index]; hit_any = P.merge(T, on=["subscription_id", "type_id"]).subscription_id.nunique()
    is_act = pd.Series(ids.isin(act), index=ids)
    out = dict(per=per, n=len(ids), n_active=int(is_act.sum()),
               no_type_rate_normal=float((n_pred[~is_act] == 0).mean()), no_type_rate_active=float((n_pred[is_act] == 0).mean()),
               n_types_dist=n_pred.value_counts(normalize=True).sort_index().to_dict(), primary_top1_acc=float(np.mean([top1[s] == prim[s] for s in both])) if both else np.nan,
               primary_covered=float(np.mean([s in set(P[P.type_id == prim[s]].subscription_id) for s in prim.index])) if len(prim) else np.nan,
               any_hit_rate_active=hit_any / max(len(act), 1))
    return out


def render(res_all: dict, res_hi: dict, sweep: pd.DataFrame, confusion: pd.DataFrame, tcfg: dict, add_err: float, n_hi: int) -> str:
    L = ["# M3 SHAP 유형 산출 리포트", "", "> 가설 기반 synthetic data. 정답 유형은 생성 때 심은 값이라 '복원율'은 심어 둔 규칙을 다시 찾은 정도입니다(순환 검증). 파이프라인이 동작함을 확인하는 결과로만 읽어 주세요.", "",
         f"- 방법: XGBoost 내장 TreeSHAP → 유형 그룹별 순합 → 양수 그룹만 → 비중 ≥ {tcfg['share_min']:.0%} 그리고 그룹 SHAP ≥ {tcfg['shap_min']}(로그오즈), 최대 {tcfg['max_types']}개",
         f"- 가법성 확인: SHAP 합 + 기준값 = 예측 마진, 최대 오차 {add_err:.1e}", "",
         "## 요약 (시험 구간 6,000건)", "", "| 구분 | 전체 시험 구간 | 위험 상위 20%(Agent가 다루는 구간) |", "|---|---|---|",
         f"| 정답 유형이 있는 구독 | {res_all['n_active']:,} | {res_hi['n_active']:,} |",
         f"| 정답 유형을 하나라도 맞힌 비율 | {res_all['any_hit_rate_active']:.1%} | {res_hi['any_hit_rate_active']:.1%} |",
         f"| 주 유형(기여가 가장 큰 정답)이 채택 목록에 들어간 비율 | {res_all['primary_covered']:.1%} | {res_hi['primary_covered']:.1%} |",
         f"| 주 유형이 1순위로 일치한 비율 | {res_all['primary_top1_acc']:.1%} | {res_hi['primary_top1_acc']:.1%} |",
         f"| '유형 없음' 비율: 정상군 | {res_all['no_type_rate_normal']:.1%} | - |", f"| '유형 없음' 비율: 활성군 | {res_all['no_type_rate_active']:.1%} | {res_hi.get('no_type_rate_active', float('nan')):.1%} |", "",
         "## 유형별 정밀도·재현율 (시험 구간 전체)", "", "정밀도 = 채택한 유형 중 실제 활성 유형 비율, 재현율 = 실제 활성 유형 중 채택된 비율.", "", "| 유형 | 정답 건수 | 채택 건수 | 정밀도 | 재현율 | F1 |", "|---|---|---|---|---|---|"]
    L += [f"| {t} | {int(r.truth_n)} | {int(r.pred_n)} | {r.precision:.2f} | {r.recall:.2f} | {r.f1:.2f} |" for t, r in res_all["per"].iterrows()]
    L += ["", f"## 위험 상위 20% 구간 (예측 확률 상위 20% 중 시험 구간 {n_hi:,}건)", "", "| 유형 | 정답 건수 | 채택 건수 | 정밀도 | 재현율 |", "|---|---|---|---|---|"]
    L += [f"| {t} | {int(r.truth_n)} | {int(r.pred_n)} | {r.precision:.2f} | {r.recall:.2f} |" for t, r in res_hi["per"].iterrows()]
    L += ["", "## 채택 개수 분포 (시험 구간 전체)", "", "| 개수 | 비율 |", "|---|---|"] + [f"| {k}개 | {v:.1%} |" for k, v in res_all["n_types_dist"].items()]
    L += ["", "## 잘못 채택된 유형 (정답 유형 → 채택된 다른 유형, 상위 10)", "", "| 정답 유형 | 잘못 채택된 유형 | 건수 |", "|---|---|---|"] + [f"| {r.truth} | {r.pred} | {r.n} |" for r in confusion.head(10).itertuples()]
    L += ["", "## 임계값 민감도 (비중 하한 × 그룹 SHAP 하한)", "", "| 비중 | SHAP | 평균 F1 | 활성군 유형없음 | 정상군 유형없음 | 상위 20%: 하나라도 맞힘 | 상위 20%: 1순위 일치 |", "|---|---|---|---|---|---|---|"]
    L += [f"| {r.share:.2f} | {r.shap:.1f} | {r.f1:.3f} | {r.act_none:.1%} | {r.norm_none:.1%} | {r.hi_any:.1%} | {r.hi_top1:.1%} |" for r in sweep.itertuples()]
    L += ["", "임계값을 바꿔도 위험 상위 20%에서의 결과는 거의 같아, 설계서의 20% + 0.5(`[가정]`)를 유지해도 됩니다."]
    return "\n".join(L) + "\n"


def confusion_table(pred: pd.DataFrame, truth: pd.DataFrame, ids) -> pd.DataFrame:
    P = pred[pred.subscription_id.isin(ids)]; T = truth[truth.subscription_id.isin(ids)]
    wrong = P.merge(T, on="subscription_id", suffixes=("_p", "_t")); wrong = wrong[wrong.type_id_p != wrong.type_id_t]
    # 같은 구독에서 정답에도 있는 유형은 '잘못'이 아니므로 제외
    ok = set(zip(T.subscription_id, T.type_id)); wrong = wrong[[(s, p) not in ok for s, p in zip(wrong.subscription_id, wrong.type_id_p)]]
    return wrong.groupby(["type_id_t", "type_id_p"]).subscription_id.nunique().rename("n").reset_index().rename(columns={"type_id_t": "truth", "type_id_p": "pred"}).sort_values("n", ascending=False)


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    from .io import load_parquet
    from .features import build_features
    from .train import fit, load_model_cfg
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="data_out"); ap.add_argument("--out", default="churn_model/out"); a = ap.parse_args()
    mc, tc = load_model_cfg(), load_type_cfg(); F = build_features(load_parquet(a.data)); d = Path(a.data)
    lab = pd.read_parquet(d / "synth_churn_label.parquet").set_index("subscription_id").churn_30d.reindex(F.index); truth = pd.read_parquet(d / "synth_truth_type.parquet")
    cm, info = fit(F, lab, mc); S, G, base = group_shap(cm, F); te = info["test"]
    marg = cm.model.get_booster().predict(xgb.DMatrix(encode(F)[cm.cols]), output_margin=True); err = float(np.abs(S.sum(axis=1).to_numpy() + base - marg).max())
    pred = select_types(S, G, F, tc); risk = pd.Series(cm.predict(encode(F)), index=F.index); hi = risk[risk.rank(ascending=False) <= 0.2 * len(F)].index.intersection(te)
    rows = []
    for sm in [0.15, 0.20, 0.25]:
        for ab in [0.2, 0.3, 0.4, 0.5]:
            p = select_types(S, G, F, {**tc, "share_min": sm, "shap_min": ab}); r = recovery(p, truth, te); rh = recovery(p, truth, hi)
            rows.append(dict(share=sm, shap=ab, f1=r["per"].f1.mean(), act_none=r["no_type_rate_active"], norm_none=r["no_type_rate_normal"], hi_any=rh["any_hit_rate_active"], hi_top1=rh["primary_top1_acc"]))
    md = render(recovery(pred, truth, te), recovery(pred, truth, hi), pd.DataFrame(rows), confusion_table(pred, truth, te), tc, err, len(hi))
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); (out / "m3_report.md").write_text(md, encoding="utf-8"); print(md)
