"""숨은 신규 주제(H-A, H-B) 배정 — 검증 전용 정답(synth_truth_topic). 모델·Agent 입력으로 쓰지 않는다."""
from __future__ import annotations
from .mapping import load_config
from .rng import make_rng


def assign_topics(subscription_id: str, no_type_mid_plus: bool = False, cfg: dict | None = None) -> dict:
    """{topic_id: strength}. 같은 구독이면 항상 같은 결과. 유형 없음 + MID 이상이면 배정 확률을 배율만큼 높인다."""
    cfg = cfg or load_config(); h = cfg["hidden_topics"]
    rng = make_rng(subscription_id, "topics", cfg["seed_salt"])
    boost = h["concentrate_no_type_mid_plus"] if no_type_mid_plus else 1.0
    lo, hi = h["strength"]; out = {}
    for t, p in h["rate"].items():
        x, s = rng.random(), rng.random()                    # 항상 두 번 뽑아 주제 하나를 추가해도 앞 주제 결과가 안 바뀌게 한다
        if x < min(1.0, p * boost): out[t] = round(lo + s * (hi - lo), 2)
    return out
