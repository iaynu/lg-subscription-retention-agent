import datetime as dt, sys
from pathlib import Path
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datagen.config import load_config
from datagen.population import build_population
from datagen.modulation import apply_type_modulation, lerp
TODAY = dt.date(2026, 9, 30)


@pytest.fixture(scope="module")
def both():
    cfg = load_config(); base = build_population(cfg); return cfg, base, apply_type_modulation(cfg, base)


def ids(p, t): return set(p["truth_type"].query("type_id == @t").subscription_id)


def test_normal_subscriptions_untouched(both):
    cfg, b, m = both; act = set(b["truth_type"].subscription_id)
    nb = b["subscription"][~b["subscription"].subscription_id.isin(act)].reset_index(drop=True)
    nm = m["subscription"][~m["subscription"].subscription_id.isin(act)].reset_index(drop=True)
    pd.testing.assert_frame_equal(nb, nm)
    cb = b["customer"].set_index("customer_id"); cm = m["customer"].set_index("customer_id")
    pd.testing.assert_frame_equal(cb.drop(index=[c for c in b["subscription"][b["subscription"].subscription_id.isin(act)].customer_id]),
                                  cm.drop(index=[c for c in m["subscription"][m["subscription"].subscription_id.isin(act)].customer_id]))


def test_reproducible():
    cfg = load_config(run={"n_subscriptions": 3000}); a = apply_type_modulation(cfg, build_population(cfg)); b = apply_type_modulation(cfg, build_population(cfg))
    pd.testing.assert_frame_equal(a["subscription"], b["subscription"]); pd.testing.assert_frame_equal(a["truth_type"], b["truth_type"])


def test_global_invariants_after_modulation(both):
    cfg, b, m = both; s = m["subscription"].set_index("subscription_id"); c = m["customer"].set_index("customer_id")
    assert (s.start_date < TODAY).all() and (s.end_date >= TODAY).all()
    assert (s.end_date == [__import__("datagen.population", fromlist=["x"]).add_months(a, int(k)) for a, k in zip(s.start_date, s.contract_months)]).all()
    j = c.loc[s.customer_id, "joined_at"].to_numpy(); assert (j <= s.start_date.to_numpy()).all()
    p = s[s.promo_end_date.notna()]; assert (p.promo_end_date >= p.start_date).all() and (p.promo_end_date <= p.end_date).all() and (p.promo_discount_amount > 0).all()
    assert (s[s.promo_end_date.isna()].promo_discount_amount == 0).all()
    ren = s[s.care_type != "없음"]; assert ren.care_renewal_date.notna().all() and (ren.care_renewal_date > TODAY).all()
    v = s.care_type == "방문관리"; assert s[v].care_manager_id.notna().all() and s[~v].care_manager_id.isna().all()
    assert not m["truth_type"].activation_strength.isna().any() and m["truth_type"].activation_strength.between(0.3, 1.0).all()


def test_t03_end_window_and_strength_formula(both):
    cfg, b, m = both; s = m["subscription"].set_index("subscription_id"); t = m["truth_type"]
    x = t[t.type_id == "T03"].set_index("subscription_id")
    d = np.array([(s.at[i, "end_date"] - TODAY).days for i in x.index])
    assert d.min() >= 0 and d.max() <= 90
    assert np.allclose(x.activation_strength.to_numpy(), (0.3 + 0.7 * (1 - d / 90)).round(2), atol=0.011)
    assert (s.loc[x.index, "new_model_released_yn"] == "Y").mean() > (s.drop(index=x.index)["new_model_released_yn"] == "Y").mean() + 0.2
    assert (s.loc[x.index, "model_discontinued_yn"] == "Y").mean() > 0.4                       # 단종 모델 우선


def test_t01_promo_and_discount(both):
    cfg, b, m = both; s = m["subscription"].set_index("subscription_id"); t = m["truth_type"]; x = t[t.type_id == "T01"].set_index("subscription_id")
    d = np.array([(s.at[i, "promo_end_date"] - TODAY).days for i in x.index]); assert d.min() >= 0 and d.max() <= 45
    un = lambda df: ((df.discount_eligible_yn == "Y") & (df.discount_applied_yn == "N")).mean()
    assert 0.55 < un(s.loc[x.index]) < 0.8 and un(s.drop(index=x.index)) < 0.10               # 활성 약 40~90%, 그 외 약 5%
    assert x.activation_strength.between(0.4, 1.0).all()


def test_t02_early_start_and_t06_availability(both):
    cfg, b, m = both; s = m["subscription"].set_index("subscription_id"); c = m["customer"].set_index("customer_id")
    t2 = list(ids(m, "T02") - ids(m, "T03")); el = np.array([(TODAY.year - s.at[i, "start_date"].year) * 12 + TODAY.month - s.at[i, "start_date"].month for i in t2])
    assert 0.5 < (el <= 13).mean() < 0.75                                                      # 약 60% 약정 초반
    t6 = ids(m, "T06"); share_n = (c.loc[s.loc[list(t6), "customer_id"], "weekday_daytime_available_yn"] == "N").mean()
    rest = c.drop(index=s.loc[list(t6), "customer_id"]); assert share_n > 0.55 and (rest.weekday_daytime_available_yn == "N").mean() < 0.35


def test_visit_care_and_t07_manager(both):
    cfg, b, m = both; s = m["subscription"].set_index("subscription_id"); mg = m["care_manager"].set_index("manager_id")
    need = set().union(*[ids(m, t) for t in cfg["modulation"]["visit_care_types"]]); assert (s.loc[list(need), "care_type"] == "방문관리").all()
    t7 = list(ids(m, "T07")); r7 = mg.loc[s.loc[t7, "care_manager_id"], "avg_rating"].mean(); rall = mg.loc[s[s.care_manager_id.notna()].care_manager_id, "avg_rating"].mean()
    assert r7 < rall - 0.2                                                                     # 평점 낮은 매니저 편중
    ch = (s.loc[t7, "manager_change_confirmed_yn"] == "Y").mean(); assert 0.1 < ch < 0.2 and (s.drop(index=t7).manager_change_confirmed_yn == "N").all()
    assert m["notes"]["care_upgraded"] > 0


def test_lerp():
    assert lerp(2, 5, 0.4) == 2 and lerp(2, 5, 1.0) == 5 and abs(lerp(2, 5, 0.7) - 3.5) < 1e-9 and lerp(2, 5, 2) == 5
