import datetime as dt, sys
from pathlib import Path
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datagen.config import load_config, TYPE_IDS
from datagen.population import build_population, add_months


@pytest.fixture(scope="module")
def pop():
    cfg = load_config(); return cfg, build_population(cfg)


def test_sizes_and_ids(pop):
    cfg, p = pop
    assert len(p["subscription"]) == len(p["customer"]) == 30000 and len(p["care_manager"]) == 140
    assert p["subscription"]["subscription_id"].is_unique and p["customer"]["customer_id"].is_unique
    assert set(p["subscription"]["customer_id"]) == set(p["customer"]["customer_id"])           # 1:1
    assert (p["subscription"]["status"] == "활성").all()


def test_reproducible_and_seed_changes_result():
    a = build_population(load_config(run={"n_subscriptions": 2000})); b = build_population(load_config(run={"n_subscriptions": 2000}))
    c = build_population(load_config(run={"n_subscriptions": 2000, "seed": 7}))
    pd.testing.assert_frame_equal(a["subscription"], b["subscription"]); pd.testing.assert_frame_equal(a["truth_type"], b["truth_type"])
    assert not a["subscription"]["monthly_fee"].equals(c["subscription"]["monthly_fee"])


def test_product_mix_and_contract_rules(pop):
    cfg, p = pop; s = p["subscription"]; pr = cfg["population"]["products"]
    cnt = s["product_category"].value_counts()
    for k, v in pr.items(): assert abs(cnt[k] - round(30000 * v["weight"])) <= 1, k
    for k, v in pr.items():
        x = s[s.product_category == k]
        assert (x.contract_months == v["contract_months"]).all() and x.monthly_fee.between(v["fee_min"], v["fee_max"]).all()
        assert (x.monthly_fee % 100 == 0).all()
    assert (s[s.product_category == "정수기"].mandatory_months == 48).all() and (s[s.product_category != "정수기"].mandatory_months == 36).all()


def test_dates_are_consistent(pop):
    cfg, p = pop; s = p["subscription"]; today = dt.date(2026, 9, 30)
    assert (s.start_date < today).all() and (s.end_date > today).all()
    months = [(today.year - d.year) * 12 + today.month - d.month for d in s.start_date]
    assert min(months) >= 1                                                       # 경과 ≥ 1개월
    el = pd.Series(months) - 0; assert (el <= s.contract_months.to_numpy()).all()
    assert (s.end_date == [add_months(a, int(c)) for a, c in zip(s.start_date, s.contract_months)]).all()
    cu = p["customer"]; assert (cu.joined_at.to_numpy() <= s.start_date.to_numpy()).all()
    assert add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)


def test_care_and_manager_rules(pop):
    cfg, p = pop; s = p["subscription"]; m = p["care_manager"].set_index("manager_id")
    assert (s[s.product_category == "정수기"].care_type == "방문관리").all()      # 제안: 정수기 고정
    big = s[s.product_category != "정수기"].care_type.value_counts(normalize=True)
    assert abs(big["방문관리"] - 0.37) < 0.02 and abs(big["자가관리"] - 0.41) < 0.02 and abs(big["없음"] - 0.22) < 0.02
    visit = s.care_type == "방문관리"
    assert s[visit].care_manager_id.notna().all() and s[~visit].care_manager_id.isna().all()
    assert s[~visit & (s.care_type == "없음")].care_renewal_date.isna().all() and s[s.care_type != "없음"].care_renewal_date.notna().all()
    assert (s[s.care_type != "없음"].care_renewal_date > dt.date(2026, 9, 30)).all()
    cust = p["customer"].set_index("customer_id"); same = [m.loc[mid, "region_sido"] == cust.loc[cid, "region_sido"] for cid, mid in zip(s[visit].customer_id, s[visit].care_manager_id)]
    assert all(same)                                                              # 같은 시도 매니저 배정
    assert m.avg_rating.between(1, 5).all() and set(m.region_sido) == set(cfg["customers"]["region_weights"])


def test_customer_distributions(pop):
    cfg, p = pop; c = p["customer"]
    assert abs((c.thinq_app_installed == "Y").mean() - 0.85) < 0.01 and abs((c.ad_consent_yn == "Y").mean() - 0.50) < 0.01
    assert abs((c.notify_consent_yn == "Y").mean() - 0.80) < 0.01 and abs((c.weekday_daytime_available_yn == "Y").mean() - 0.70) < 0.01
    ag = c.age_group.value_counts(normalize=True); assert abs(ag["40대"] - 0.30) < 0.01 and abs(ag["20대"] - 0.12) < 0.01


def test_type_activation_matches_design(pop):
    cfg, p = pop; t = p["truth_type"]; n = 30000
    per = t.groupby("subscription_id").size()
    assert len(per) == 6000 and per.max() <= 3                                    # 활성 6,000, 최대 3개
    assert per.value_counts().sort_index().to_dict() == {1: 2100, 2: 2400, 3: 1500}   # 정확한 개수
    assert not t.duplicated(["subscription_id", "type_id"]).any()
    # 유형별 활성 건수: 비복원 추출이라 type_weights보다 약간 평평(설계서 시뮬레이션과 같은 방향)
    share = t.type_id.value_counts().reindex(TYPE_IDS) / len(t)
    for tid, w in cfg["types"]["type_weights"].items(): assert abs(share[tid] - w) < 0.04, tid
    assert share["T05"] == share.max() and share["T04"] == share.min() or share["T04"] < 0.07
    s = t[t.type_id != "T03"].activation_strength; assert s.between(0.4, 1.0).all()
    assert t[t.type_id == "T03"].activation_strength.isna().all()                  # 페이지 3에서 채움
