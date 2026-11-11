"""시드 고정 난수: 같은 (구독 ID, 설문 ID, 설정 salt)이면 항상 같은 응답. 파이썬 hash()는 실행마다 달라서 hashlib을 쓴다."""
from __future__ import annotations
import hashlib, random


def make_rng(subscription_id: str, survey_id: str, salt: str = "survey-v1") -> random.Random:
    h = hashlib.sha256(f"{salt}|{subscription_id}|{survey_id}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def pick(rng: random.Random, weights: dict):
    """{값: 가중치} 에서 하나를 뽑는다. 합이 1이 아니어도 된다. 키 순서를 고정해 재현성을 지킨다."""
    keys = list(weights)
    tot = sum(weights[k] for k in keys)
    if tot <= 0: raise ValueError("가중치 합이 0 이하")
    x, acc = rng.random() * tot, 0.0
    for k in keys:
        acc += weights[k]
        if x < acc: return k
    return keys[-1]
