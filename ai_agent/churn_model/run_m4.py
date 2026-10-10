"""페이지 M4(c): 교차 적합 점수 산출 → 평가 → 파일 저장 → (선택) DB 교체.

python -m churn_model.run_m4 --data data_out --out churn_model/out [--url "$DATABASE_URL"]
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np, pandas as pd
from .io import load_parquet
from .features import build_features, load_cfg
from .train import load_model_cfg
from .metrics import summarize
from .shap_types import load_type_cfg, recovery, confusion_table
from .score import crossfit_predict
import yaml


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="data_out"); ap.add_argument("--out", default="churn_model/out"); ap.add_argument("--url"); a = ap.parse_args()
    from .features import HERE
    sc = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))["scoring"]; mc, tc, fc = load_model_cfg(), load_type_cfg(), load_cfg()
    d = Path(a.data); F = build_features(load_parquet(d)); lab = pd.read_parquet(d / "synth_churn_label.parquet").set_index("subscription_id").churn_30d.reindex(F.index)
    truth = pd.read_parquet(d / "synth_truth_type.parquet"); res = crossfit_predict(F, lab, mc, tc, sc, fc["as_of_date"])
    pred, pt = res["prediction"], res["prediction_type"]; pred["as_of_date"] = pd.Timestamp(fc["as_of_date"]).date()
    pred.to_csv(d / "churn_prediction.csv", index=False); pt.to_csv(d / "churn_prediction_type.csv", index=False); pred.to_parquet(d / "churn_prediction.parquet", index=False); pt.to_parquet(d / "churn_prediction_type.parquet", index=False)
    p = res["oof_prob"]; m = summarize(lab, p.to_numpy(), mc["top_percents"]); lv = pred.set_index("subscription_id").risk_level
    hi_ids = lv[lv.isin(["HIGH", "MID"])].index; rec_all = recovery(pt, truth, F.index); rec_hi = recovery(pt, truth, hi_ids)
    grp = pd.DataFrame({"n": lv.value_counts(), "churn": lab.groupby(lv).mean(), "captured": lab.groupby(lv).sum() / lab.sum()}).reindex(["HIGH", "MID", "LOW"])
    L = ["# M4 전 구독 점수 산출 리포트 (교차 적합)", "", "> 가설 기반 synthetic data. 각 구독은 자기를 학습에 쓰지 않은 모델로 점수화했습니다(5-겹). 성능·복원율은 심어 둔 규칙을 다시 찾은 정도이며 실제 성능이 아닙니다.", "",
         f"- 모델 버전 `{sc['model_version']}`, 기준일 {fc['as_of_date']}, 구독 {len(F):,}건, 겹 {sc['folds']}개(조기 종료 반복 {res['folds'].best_iter.tolist()})", "",
         "## 전체 구독 OOF 성능", "", f"PR-AUC {m['pr_auc']:.3f} (무작위 {m['base_rate']:.3f}), ROC-AUC {m['roc_auc']:.3f}, Recall@10% {m['recall@10']:.3f}, Precision@10% {m['precision@10']:.3f}, 평균 예측 확률 {m['mean_p']:.3f} vs 실제 이탈률 {m['base_rate']:.3f}", "",
         "## 위험 등급(순위 기준: 상위 10% HIGH / 다음 10% MID)", "", "| 등급 | 건수 | 실제 이탈률 | 전체 이탈자 중 포착 |", "|---|---|---|---|"]
    L += [f"| {g} | {int(r.n):,} | {r.churn:.1%} | {r.captured:.1%} |" for g, r in grp.iterrows()]
    L += ["", "## 유형 복원 (정답 유형 대비)", "", f"| 구분 | 전체 | HIGH+MID ({len(hi_ids):,}건) |", "|---|---|---|", f"| 정답 유형이 있는 구독 | {rec_all['n_active']:,} | {rec_hi['n_active']:,} |",
          f"| 하나라도 맞힘 | {rec_all['any_hit_rate_active']:.1%} | {rec_hi['any_hit_rate_active']:.1%} |", f"| 주 유형이 채택 목록에 포함 | {rec_all['primary_covered']:.1%} | {rec_hi['primary_covered']:.1%} |",
          f"| 주 유형 1순위 일치 | {rec_all['primary_top1_acc']:.1%} | {rec_hi['primary_top1_acc']:.1%} |", f"| 유형 없음: 정상군 / 활성군 | {rec_all['no_type_rate_normal']:.1%} / {rec_all['no_type_rate_active']:.1%} | - / {rec_hi.get('no_type_rate_active', float('nan')):.1%} |", "",
          "| 유형 | 정답 | 채택 | 정밀도 | 재현율 |", "|---|---|---|---|---|"] + [f"| {t} | {int(r.truth_n):,} | {int(r.pred_n):,} | {r.precision:.2f} | {r.recall:.2f} |" for t, r in rec_all["per"].iterrows()]
    L += ["", f"채택 유형 개수: " + ", ".join(f"{k}개 {v:.1%}" for k, v in rec_all["n_types_dist"].items()), f"예측 행: churn_prediction {len(pred):,}건, churn_prediction_type {len(pt):,}건"]
    if a.url:
        from .store import replace_predictions
        r = replace_predictions(a.url, pred, pt, pred.as_of_date.iloc[0]); L += ["", "## DB 반영", "", f"churn_prediction {r['churn_prediction']:,}건, churn_prediction_type {r['churn_prediction_type']:,}건, 모델 버전별 건수 {r['model_versions']}"]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); (out / "m4_report.md").write_text("\n".join(L) + "\n", encoding="utf-8"); print("\n".join(L))


if __name__ == "__main__":
    main()
