"""페이지 M2: XGBoost 학습·평가·보정·저장.

python -m churn_model.train --data data_out --out churn_model/out
- 입력: 피처(build_features) + 정답(synth_churn_label.churn_30d). 정답 유형·생성 확률은 학습에 쓰지 않는다(참고선 비교에만 사용).
- 분할: 제품군×이탈 여부 층화 60/20/20(학습/검증/시험). 검증은 조기 종료와 확률 보정에, 시험은 최종 평가에만 쓴다.
- 클래스 가중: scale_pos_weight(config.model.class_weight). 가중하면 확률이 부풀므로 검증 구간에서 Platt 보정해 DB에 저장할 확률로 쓴다.
- 합성 데이터 성능은 '심어 둔 규칙을 다시 찾은 정도'이므로 파이프라인 검증 결과로만 해석한다.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np, pandas as pd, yaml
import xgboost as xgb
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from .io import load_parquet
from .features import build_features, encode, load_cfg, HERE
from .metrics import summarize, by_group


def load_model_cfg(path=None) -> dict:
    return yaml.safe_load(Path(path or HERE / "config.yaml").read_text(encoding="utf-8"))["model"]


def split_ids(F: pd.DataFrame, y: pd.Series, mc: dict):
    key = F.product_category.astype(str) + "_" + y.astype(str); s = mc["seed"]; t, v = mc["split"]["test"], mc["split"]["valid"]
    rest, test = train_test_split(np.asarray(F.index, dtype=object), test_size=t, stratify=key, random_state=s)
    train, valid = train_test_split(rest, test_size=v / (1 - t), stratify=key.loc[rest], random_state=s)
    return train, valid, test


def logit(p): p = np.clip(p, 1e-6, 1 - 1e-6); return np.log(p / (1 - p))


class Calibrated:
    """XGBoost + Platt 보정. predict_proba는 보정된 확률, raw는 보정 전."""
    def __init__(self, model, platt, cols): self.model, self.platt, self.cols = model, platt, cols
    def raw(self, X): return self.model.predict_proba(X[self.cols])[:, 1]
    def predict(self, X):
        r = self.raw(X); return r if self.platt is None else self.platt.predict_proba(logit(r).reshape(-1, 1))[:, 1]


def fit_ids(F: pd.DataFrame, y: pd.Series, tr, va, mc: dict) -> Calibrated:
    """주어진 학습/검증 구독으로 XGBoost를 학습하고 검증 구간에서 Platt 보정한다."""
    X = encode(F); xc = mc["xgb"]; pos = float(y.loc[tr].sum()); neg = len(tr) - pos
    spw = {"none": 1.0, "sqrt": (neg / pos) ** 0.5, "full": neg / pos}[mc["class_weight"]]
    m = xgb.XGBClassifier(**xc, scale_pos_weight=spw, tree_method="hist", random_state=mc["seed"], n_jobs=4)
    m.fit(X.loc[tr], y.loc[tr], eval_set=[(X.loc[va], y.loc[va])], verbose=False)
    cm = Calibrated(m, None, list(X.columns)); cm.spw = spw
    if mc["calibrate"] == "platt": cm.platt = LogisticRegression(C=1e6).fit(logit(cm.raw(X.loc[va])).reshape(-1, 1), y.loc[va])
    return cm


def fit(F: pd.DataFrame, y: pd.Series, mc: dict | None = None):
    mc = mc or load_model_cfg(); tr, va, te = split_ids(F, y, mc); cm = fit_ids(F, y, tr, va, mc)
    return cm, dict(train=tr, valid=va, test=te, spw=cm.spw, best_iter=int(cm.model.best_iteration))


def evaluate(cm, F, y, ids, mc, oracle_p: pd.Series | None = None) -> dict:
    X = encode(F).loc[ids]; yt = y.loc[ids]; p = cm.predict(X); raw = cm.raw(X); tops = mc["top_percents"]
    res = dict(test=summarize(yt, p, tops), raw=summarize(yt, raw, tops), product=by_group(yt, p, F.loc[ids, "product_category"], tops=(10,)))
    b = mc["tenure_buckets"]; ten = pd.cut(F.loc[ids, "tenure_months"], [-1, b[0], b[1], 1e9], labels=[f"≤{b[0]}개월", f"{b[0]}~{b[1]}개월", f">{b[1]}개월"]).astype(str)
    res["tenure"] = by_group(yt, p, ten, tops=(10,))
    res["baseline_timing"] = summarize(yt, -F.loc[ids, "days_to_end"].clip(lower=0).to_numpy(float), tops)             # 참고선: 만료까지 일수만 쓴 단순 규칙
    if oracle_p is not None: res["oracle"] = summarize(yt, oracle_p.loc[ids].to_numpy(), tops)                          # 상한 참고선: 데이터 생성에 쓴 확률
    return res


def render(res: dict, info: dict, mc: dict, imp: pd.Series) -> str:
    f = lambda d, k: f"{d[k]:.3f}" if k in d else "-"; tops = mc["top_percents"]
    L = ["# M2 학습·평가 리포트 (XGBoost)", "", "> 가설 기반 synthetic data. 이 성능은 생성 규칙에 심어 둔 신호를 다시 찾은 정도라 실제 이탈 예측 성능이 아닙니다. 파이프라인이 끝까지 동작함을 검증한 결과로만 읽어 주세요.", "",
         f"- 분할: 학습 {len(info['train']):,} / 검증 {len(info['valid']):,} / 시험 {len(info['test']):,} (제품군×이탈 층화), 조기 종료 최적 반복 {info['best_iter']}, 클래스 가중 scale_pos_weight={info['spw']:.2f}",
         f"- 시험 구간 이탈률 {res['test']['base_rate']:.1%}", "", "## 시험 구간 성능", "", "| 구분 | PR-AUC | ROC-AUC | " + " | ".join(f"Recall@{k}%" for k in tops) + " | " + " | ".join(f"Precision@{k}%" for k in tops) + " | Lift@10% |", "|---|---|---|" + "---|" * (2 * len(tops) + 1)]
    for name, key in [("**XGBoost (보정)**", "test"), ("XGBoost (보정 전)", "raw"), ("단순 규칙: 만료 임박 순", "baseline_timing"), ("상한 참고: 생성 확률", "oracle")]:
        if key in res: d = res[key]; L.append(f"| {name} | {f(d,'pr_auc')} | {f(d,'roc_auc')} | " + " | ".join(f(d, f"recall@{k}") for k in tops) + " | " + " | ".join(f(d, f"precision@{k}") for k in tops) + f" | {f(d,'lift@10')} |")
    L += ["", "무작위로 고르면 PR-AUC는 이탈률(약 8%)과 같고, 'Recall@10%'는 10%입니다.", "", "## 확률 보정", "", "| 구분 | 평균 예측 확률 | 실제 이탈률 | Brier |", "|---|---|---|---|",
          f"| 보정 전 | {res['raw']['mean_p']:.3f} | {res['raw']['base_rate']:.3f} | {res['raw']['brier']:.4f} |", f"| 보정 후 | {res['test']['mean_p']:.3f} | {res['test']['base_rate']:.3f} | {res['test']['brier']:.4f} |",
          "", "## 제품군별 (보정 확률, 시험 구간)", "", "| 제품군 | 건수 | 이탈률 | PR-AUC | Recall@10% |", "|---|---|---|---|---|"]
    L += [f"| {g} | {int(r.n):,} | {r.base_rate:.1%} | {f(r,'pr_auc')} | {f(r,'recall@10')} |" for g, r in res["product"].iterrows()]
    L += ["", "## 가입 기간별", "", "| 구간 | 건수 | 이탈률 | PR-AUC | Recall@10% |", "|---|---|---|---|---|"]
    L += [f"| {g} | {int(r.n):,} | {r.base_rate:.1%} | {f(r,'pr_auc')} | {f(r,'recall@10')} |" for g, r in res["tenure"].iterrows()]
    if "robust" in res:
        r = res["robust"]; L += ["", "## 분할 시드를 바꿔 4회 반복(안정성)", "", f"PR-AUC {r.pr_auc.min():.3f}~{r.pr_auc.max():.3f}, ROC-AUC {r.roc_auc.min():.3f}~{r.roc_auc.max():.3f}, Recall@10% {r['recall@10'].min():.3f}~{r['recall@10'].max():.3f} (시험 6,000건 기준, 시드 1~4)"]
    L += ["", "## 중요도 상위 15 (gain)", "", "| 피처 | 중요도 |", "|---|---|"] + [f"| {k} | {v:.3f} |" for k, v in imp.head(15).items()]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="data_out"); ap.add_argument("--out", default="churn_model/out"); a = ap.parse_args()
    mc = load_model_cfg(); t = load_parquet(a.data); F = build_features(t)
    lab = pd.read_parquet(Path(a.data) / "synth_churn_label.parquet").set_index("subscription_id").churn_30d.reindex(F.index)
    cm, info = fit(F, lab, mc)
    stub = Path(a.data) / "churn_prediction_stub.parquet"; oracle = pd.read_parquet(stub).set_index("subscription_id").churn_probability if stub.exists() else None
    res = evaluate(cm, F, lab, info["test"], mc, oracle)
    imp = pd.Series(cm.model.get_booster().get_score(importance_type="gain")).sort_values(ascending=False); imp = imp / imp.sum()
    rob = []                                                                                  # 분할 시드를 바꿨을 때 성능이 얼마나 흔들리는지
    for sd in [1, 2, 3, 4]:
        c2, i2 = fit(F, lab, {**mc, "seed": sd}); rob.append(evaluate(c2, F, lab, i2["test"], mc)["test"])
    res["robust"] = pd.DataFrame(rob)[["pr_auc", "roc_auc", "recall@10"]]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); md = render(res, info, mc, imp); (out / "m2_report.md").write_text(md, encoding="utf-8")
    cm.model.save_model(out / "xgb_model.json")
    (out / "model_meta.json").write_text(json.dumps(dict(features=cm.cols, platt_coef=None if cm.platt is None else [float(cm.platt.coef_[0][0]), float(cm.platt.intercept_[0])], best_iteration=info["best_iter"],
                                                         scale_pos_weight=info["spw"], seed=mc["seed"], note="합성 데이터 위에서 파이프라인을 검증한 모델. 실제 성능 아님"), ensure_ascii=False, indent=1), encoding="utf-8")
    print(md)
