"""페이지 5-1(a): 이탈 라벨 생성 — synth_churn_label, synth_truth_type(주 유형 표시).

build_labels(cfg, pop) -> dict(synth_churn_label, synth_truth_type, label_detail)
  logit = b0 + w_scale × (1.0·c1 + 0.5·c2 + 0.25·c3) + w_timing × timing_proximity + N(0, σ)       (cₖ: 유형 기여 w×강도를 큰 순서로)
  timing_proximity = max(1 − 만료까지 일수/90, 0)  (정상군에도 적용)
  P(churn) = sigmoid(logit) → 난수 < P 이면 이탈, 해지 접수일 = 기준일 + 1~30일 균등
- label_detail(p_churn, logit)은 검증용이며 DB에 넣지 않는다(모델 학습·추론 입력 금지 — 누수 방지).
- 라벨 난수는 이벤트 생성과 별도 흐름이라, 이벤트 설정을 바꿔도 같은 구독의 이탈 여부 난수는 유지된다.
"""
from __future__ import annotations
import datetime as dt
import numpy as np, pandas as pd
from .config import as_of, TYPE_IDS


def _rng(seed: int):
    return np.random.default_rng(np.random.SeedSequence(seed).spawn(12)[10])


def sigmoid(x): return 1.0 / (1.0 + np.exp(-x))


def build_labels(cfg: dict, pop: dict) -> dict:
    r = _rng(int(cfg["run"]["seed"])); L = cfg["label"]; today = as_of(cfg)
    sub = pop["subscription"]; truth = pop["truth_type"].copy(); n = len(sub)
    idx = pd.Series(np.arange(n), index=sub["subscription_id"]); ti = {t: i for i, t in enumerate(TYPE_IDS)}
    contrib = np.zeros((n, len(TYPE_IDS)))
    for sid, t, s in truth[["subscription_id", "type_id", "activation_strength"]].itertuples(index=False):
        contrib[idx[sid], ti[t]] = L["type_w"][t] * float(s)
    top = -np.sort(-contrib, axis=1)[:, :3]                                        # 큰 순서 상위 3개
    eff = (top * np.array(L["rank_multipliers"])[None, :]).sum(axis=1)
    days_to_end = np.array([(e - today).days for e in sub["end_date"]])
    timing = np.maximum(1 - days_to_end / L["timing_window_days"], 0.0)
    logit = L["b0"] + L["w_scale"] * eff + L["w_timing"] * timing + r.normal(0, L["noise_sigma"], n)
    p = sigmoid(logit); churn = (r.random(n) < p).astype(int)
    lo, hi = L["churn_day_range"]; offs = r.integers(lo, hi + 1, n)
    label = pd.DataFrame({"subscription_id": sub["subscription_id"].to_numpy(), "as_of_date": today, "churn_30d": churn,
                          "cancel_requested_date": [today + dt.timedelta(days=int(o)) if c else None for o, c in zip(offs, churn)]})
    primary = contrib.argmax(axis=1)                                                # 기여가 가장 큰 유형 = 주 유형
    truth["is_primary_yn"] = ["Y" if ti[t] == primary[idx[sid]] else "N" for sid, t in zip(truth["subscription_id"], truth["type_id"])]
    truth["activation_strength"] = truth["activation_strength"].astype(float).round(2)
    detail = pd.DataFrame({"subscription_id": sub["subscription_id"].to_numpy(), "p_churn": p, "logit": logit, "timing_proximity": timing, "effective_contrib": eff})
    return dict(synth_churn_label=label, synth_truth_type=truth, label_detail=detail)


def risk_levels(detail: pd.DataFrame, high=0.10, mid=0.10) -> pd.Series:
    """생성 확률 순위 기준 HIGH(상위 10%) / MID(다음 10%) / LOW. 학습 모델 점수 기준이 아니라 '정답에 가장 가까운 순위' 참고용."""
    rank = detail["p_churn"].rank(ascending=False, method="first") / len(detail)
    return pd.Series(np.where(rank <= high, "HIGH", np.where(rank <= high + mid, "MID", "LOW")), index=detail["subscription_id"])
