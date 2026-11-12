import random
import pytest
from feedback import pool
from feedback.mapping import load_mapping


def test_seed_pool_complete_and_clean():
    p = pool.load_pool()
    assert set(pool.required_keys()) <= set(p)
    assert len(pool.required_keys()) == 15 and "H-A" in p and "sat" in p
    assert pool.check_pool(p) == []
    for k, v in p.items():
        assert len(v) == len(set(v))
        if k != "issue_other": assert len(v) >= 4


@pytest.mark.parametrize("s,bad", [("010-1234-5678로 연락 주세요", True), ("abc@example.com 으로 보내요", True), ("https://x.com 참고", True), ("", True),
                                   ("총 비용이 올라서 놀랐어요", False), ("a" * 200, True), ("voc_fee_cnt_90d 가 3이에요", True)])
def test_check_sentence(s, bad): assert (pool.check_sentence(s) is not None) == bad


def test_sample_is_deterministic_and_from_pool():
    a = [pool.sample(random.Random(i), "cost") for i in range(50)]
    b = [pool.sample(random.Random(i), "cost") for i in range(50)]
    assert a == b and set(a) <= set(pool.load_pool()["cost"]) and len(set(a)) > 1


def test_keys_follow_mapping():
    m = load_mapping()
    assert set(m["type_to_issue"].values()) <= set(pool.load_pool()) and set(m["hidden_topics"]) <= set(pool.load_pool())
