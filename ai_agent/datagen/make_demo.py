"""시연 12건 선정 + 파일 저장. 모델 예측(churn_prediction*.parquet)이 있으면 그것으로 고른다.
python -m datagen.make_demo [--data data_out] [--out datagen/out]"""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from .config import load_config
from .generate import generate_all
from .demo_cases import select_demo_cases


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="data_out"); ap.add_argument("--out", default="datagen/out"); a = ap.parse_args()
    cfg = load_config(); t = generate_all(cfg); d = Path(a.data); pp, pt = d / "churn_prediction.parquet", d / "churn_prediction_type.parquet"
    pred = pd.read_parquet(pp) if pp.exists() else None; ptype = pd.read_parquet(pt) if pt.exists() else None
    cases = select_demo_cases(cfg, t, pred, ptype); out = Path(a.out); out.mkdir(parents=True, exist_ok=True); cases.to_csv(out / "demo_cases.csv", index=False, encoding="utf-8-sig")
    src = "모델 예측(xgb-oof-v1)에서 위험 HIGH/MID이고 필요한 유형을 모두 채택한 구독을 골랐습니다. 따라서 시연 통과율은 모델 정확도를 뜻하지 않습니다." if pred is not None else "생성 규칙 기준으로 골랐습니다."
    L = ["# 시연용 대표 사례 12건 (가상 데이터)", "", f"> 가설 기반 synthetic data. '생성 이탈 확률'은 데이터 생성 규칙의 값이며 학습 모델 점수가 아닙니다. {src} Agent 실행 결과는 `demo_agent_results.md`에 있습니다.", "",
         "| 사례 | 시나리오 | 구독 | 고객(가상) | 제품 | 케어 | 정답 유형 | 생성 확률 | 이탈 | 광고 | 만료까지 | 기대 동작 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    L += [f"| {r.case_id} | {r.scenario} | {r.subscription_id} | {r.customer_name} | {r.product} | {r.care_type} | {r.true_types} | {r.gen_churn_prob:.0%} | {'Y' if r.churn_30d else 'N'} | {r.ad_consent} | {r.days_to_end}일 | {r.expected} |" for r in cases.itertuples()]
    (out / "demo_cases.md").write_text("\n".join(L) + "\n", encoding="utf-8"); print(cases[["case_id", "subscription_id", "true_types", "extra_types"]].to_string())


if __name__ == "__main__":
    main()
