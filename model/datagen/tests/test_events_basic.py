import datetime as dt, sys
from pathlib import Path
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datagen.config import load_config
from datagen.population import build_population
from datagen.modulation import apply_type_modulation
from datagen.events_basic import build_events_basic, week_starts
TODAY = dt.date(2026, 9, 30)


@pytest.fixture(scope="module")
def env():
    cfg = load_config(); pop = apply_type_modulation(cfg, build_population(cfg)); return cfg, pop, build_events_basic(cfg, pop)


def tids(pop, t): return set(pop["truth_type"].query("type_id == @t").subscription_id)


def test_week_starts_example():
    w = week_starts(TODAY, 26); assert w[-1] == dt.date(2026, 9, 21) and w[0] == dt.date(2026, 3, 30) and all(d.weekday() == 0 for d in w)


def test_reproducible():
    cfg = load_config(run={"n_subscriptions": 2000}); pop = apply_type_modulation(cfg, build_population(cfg))
    a, b = build_events_basic(cfg, pop), build_events_basic(cfg, pop)
    for k in a: pd.testing.assert_frame_equal(a[k], b[k])


def test_usage_shape_and_ranges(env):
    cfg, pop, ev = env; u = ev["usage_thinq"]
    assert not u.duplicated(["subscription_id", "week_start_date"]).any() and u.run_count.min() >= 0 and u.app_login_days.between(0, 7).all()
    full = u.groupby("subscription_id").size(); assert full.max() == 26 and (full == 26).mean() > 0.9           # 대부분 26주 전체
    s = pop["subscription"].set_index("subscription_id"); first = u.groupby("subscription_id").week_start_date.min()
    assert all(w + dt.timedelta(days=6) >= s.at[i, "start_date"] for i, w in first.items())                  # 가입 이전 주 없음
    c = pop["customer"].set_index("customer_id"); inst = c.loc[s.loc[u.subscription_id, "customer_id"], "thinq_app_installed"].to_numpy()
    assert (u.app_login_days[inst == "N"] == 0).all() and u.app_login_days[inst == "Y"].mean() > 1.5
    assert (u.run_minutes[u.run_count == 0] == 0).all()


def test_normal_usage_is_stable_and_matches_base(env):
    cfg, pop, ev = env; u = ev["usage_thinq"]; s = pop["subscription"]; act = set(pop["truth_type"].subscription_id)
    nm = u[~u.subscription_id.isin(act)].merge(s[["subscription_id", "product_category"]], on="subscription_id")
    for prod, base in {k: v["base_usage_per_week"] for k, v in cfg["population"]["products"].items()}.items():
        assert abs(nm[nm.product_category == prod].run_count.mean() - base) / base < 0.06, prod
    z = nm.groupby("subscription_id").run_count.apply(lambda x: (x.iloc[-12:] == 0).sum())
    assert z.mean() < 0.5                                                                                  # 정상군은 무가동 주가 거의 없음


def test_t04_signal(env):
    cfg, pop, ev = env; u = ev["usage_thinq"]; t4 = tids(pop, "T04"); x = u[u.subscription_id.isin(t4)]
    last12 = x.groupby("subscription_id").apply(lambda g: g.sort_values("week_start_date").tail(12), include_groups=False)
    z = last12.groupby(level=0).run_count.apply(lambda v: (v == 0).sum())
    assert z.min() >= 3 and z.max() <= 12 and 4 < z.mean() < 9              # 설계 3~10주 + 감소 구간의 우연한 0(푸아송) 1~2주
    ratio = last12.groupby(level=0).run_count.apply(lambda v: v.iloc[-4:].sum() / max(v.iloc[4:8].sum(), 1))
    assert ratio.mean() < 0.55                                                                             # 최근 4주 / 이전 4주 크게 하락
    allr = u[~u.subscription_id.isin(t4)].groupby("subscription_id").run_count.apply(lambda v: v.iloc[-4:].sum() / max(v.iloc[-8:-4].sum(), 1))
    assert 0.85 < allr.mean() < 1.2


def test_payment_rules(env):
    cfg, pop, ev = env; p = ev["payment"]; s = pop["subscription"].set_index("subscription_id")
    assert not p.duplicated(["subscription_id", "billing_month"]).any() and set(p.payment_status) <= {"정상", "연체", "미납"}
    assert p.billing_month.min() == dt.date(2026, 4, 1) and p.billing_month.max() == dt.date(2026, 9, 1) and (pd.Series(p.due_date).map(lambda d: d.day) == 25).all()
    paid = p[p.payment_status != "미납"]; assert str(p.paid_amount.dtype) == "Int64" and (paid.paid_amount.astype(int) == paid.billed_amount - paid.discount_amount).all()
    assert p[p.payment_status == "미납"].paid_amount.isna().all() and p[p.payment_status == "미납"].paid_date.isna().all()
    assert all(d <= TODAY for d in paid.paid_date)
    assert ((paid.payment_status.to_numpy() == "정상") == (np.array(list(paid.paid_date)) <= np.array(list(paid.due_date)))).all()
    first_ok = p.merge(s[["start_date"]], left_on="subscription_id", right_index=True)
    assert all(b >= a.replace(day=1) for a, b in zip(first_ok.start_date, first_ok.billing_month))      # 가입 전 청구 없음
    assert (p.billed_amount >= 0).all() and (p.discount_amount <= p.billed_amount).all()


def test_t01_payment_signal(env):
    cfg, pop, ev = env; p = ev["payment"]; s = pop["subscription"].set_index("subscription_id"); t1 = tids(pop, "T01")
    late = lambda df: (df.payment_status != "정상").mean()
    assert 0.10 < late(p[p.subscription_id.isin(t1)]) < 0.22 and late(p[~p.subscription_id.isin(t1)]) < 0.06      # 15% vs 3%
    x = p[p.subscription_id.isin(t1)].copy(); x["fee"] = s.loc[x.subscription_id, "monthly_fee"].to_numpy()
    last = x[x.billing_month >= dt.date(2026, 7, 1)]; early = x[x.billing_month < dt.date(2026, 7, 1)]
    assert (last.billed_amount / last.fee).mean() > 1.05 and (early.billed_amount == early.fee).all()         # 최근 3개월만 인상


def test_notices(env):
    cfg, pop, ev = env; n = ev["notice_log"]; s = pop["subscription"].set_index("subscription_id"); t3 = tids(pop, "T03")
    assert set(n.notice_type) == {"갱신안내", "사용팁", "계약조건안내"} and (n.opened_at.dropna() >= n.sent_at[n.opened_at.notna()]).all()
    assert all(o <= dt.datetime(2026, 9, 30, 23, 59) for o in n.opened_at.dropna())
    ren = n[n.notice_type == "갱신안내"]; cnt = ren.groupby("subscription_id").size()
    soon = s[(s.end_date - pd.Timestamp(TODAY).date()).map(lambda d: d.days) <= 90].index
    assert set(ren.subscription_id) == set(soon)                                                                # 만료 90일 이내 전원에게 갱신안내
    assert cnt[list(t3)].mean() > 2.0 and cnt[[i for i in cnt.index if i not in t3]].max() == 1                  # T03는 접촉 증가, 그 외 1회
    op = lambda df: df.opened_at.notna().mean()
    assert op(ren[ren.subscription_id.isin(t3)]) > 0.88 and 0.5 < op(n[n.notice_type == "사용팁"]) < 0.7
    assert (pd.Series(n.sent_at).map(lambda d: d.date()) <= TODAY).all()
