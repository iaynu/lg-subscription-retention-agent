"""피처 점검 리포트(M1): 결측률, 유형별 신호 분리도(검증용 — 정답 유형을 '진단'에만 쓰고 모델 입력엔 쓰지 않음).

python -m churn_model.feature_report --data data_out --out churn_model/out/feature_report.md
분리도 = (해당 유형 활성 구독 평균 − 나머지 평균) / 전체 표준편차. 결측은 제외하고 계산.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np, pandas as pd
from .io import load_parquet
from .features import build_features, feature_groups, load_cfg

TYPES = [f"T{i:02d}" for i in range(1, 11)]


def separation(F: pd.DataFrame, truth: pd.DataFrame, groups: dict) -> pd.DataFrame:
    rows = []
    for tp in TYPES:
        act = F.index.isin(truth[truth.type_id == tp].subscription_id)
        for f in [c for c, g in groups.items() if g == tp]:
            x = pd.to_numeric(F[f], errors="coerce"); sd = x.std()
            rows.append(dict(type=tp, feature=f, d=float((x[act].mean() - x[~act].mean()) / sd) if sd and sd == sd else np.nan))
    return pd.DataFrame(rows)


def render(F, sep, groups) -> str:
    L = ["# 피처 점검 리포트 (M1)", "", "> 가설 기반 synthetic data. 정답 유형은 신호가 보이는지 진단하는 데만 썼고 모델 입력에는 쓰지 않습니다.", "",
         f"- 구독 {len(F):,}건 × 피처 {F.shape[1]}개 (사전 73개 중 `region_changed_flag`는 원천 컬럼이 없어 제외)", f"- 결측이 있는 피처 {int((F.isna().mean() > 0).sum())}개 (아래 표)", "",
         "## 결측률(0 초과)", "", "| 피처 | 결측률 | 결측의 뜻 |", "|---|---|---|"]
    why = {"promo_days_to_end": "프로모션 없음", "billed_change_rate_3m": "결제 이력 4개월 미만", "care_duration_avg_min": "최근 1년 케어 방문 없음(자가관리·없음 포함)", "days_since_last_visit": "완료된 방문 없음",
           "days_overdue_visit": "방문관리 아님/케어 이력 없음", "visit_gap_ratio": "방문관리 아님/케어 이력 없음", "booking_lead_days_avg": "최근 1년 정기점검 없음", "satisfaction_avg_180d": "최근 6개월 평가 없음",
           "satisfaction_drop": "최근/이전 구간 중 한쪽에 평가 없음", "manager_avg_rating": "담당 매니저 없음", "days_since_last_as": "A/S 이력 없음", "repair_days_avg_365d": "최근 1년 A/S 없음", "response_hours_avg": "최근 90일 상담 없음"}
    for f, r in F.isna().mean()[lambda x: x > 0].sort_values(ascending=False).items(): L.append(f"| {f} | {r:.1%} | {why.get(f, '')} |")
    L += ["", "## 유형별 신호 분리도 (활성 vs 비활성, 표준화 평균 차)", "", "각 유형에서 분리도가 가장 큰 피처 3개입니다. 자기 유형 그룹 피처가 위에 올라와야 모델이 유형을 구분할 수 있습니다.", "", "| 유형 | 상위 피처 (분리도) |", "|---|---|"]
    for tp in TYPES:
        s = sep[sep.type == tp].dropna().assign(a=lambda x: x.d.abs()).sort_values("a", ascending=False).head(3)
        L.append(f"| {tp} | " + ", ".join(f"{r.feature} ({r.d:+.2f})" for r in s.itertuples()) + " |")
    weak = sep.dropna().groupby("type").d.apply(lambda s: s.abs().max()); L += ["", f"유형별 최대 분리도: " + ", ".join(f"{k} {v:.2f}" for k, v in weak.items())]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="data_out"); ap.add_argument("--out", default="churn_model/out/feature_report.md"); a = ap.parse_args()
    t = load_parquet(a.data); F = build_features(t); truth = pd.read_parquet(Path(a.data) / "synth_truth_type.parquet"); g = feature_groups(load_cfg())
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); sep = separation(F, truth, g); Path(a.out).write_text(render(F, sep, g), encoding="utf-8"); print(render(F, sep, g))
