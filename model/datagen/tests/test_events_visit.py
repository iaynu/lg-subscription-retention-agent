import datetime as dt, sys
from pathlib import Path
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datagen.config import load_config
from datagen.population import build_population
from datagen.modulation import apply_type_modulation
from datagen.events_visit import build_visits
TODAY = dt.date(2026, 9, 30)


@pytest.fixture(scope="module")
def env():
    cfg = load_config(); pop = apply_type_modulation(cfg, build_population(cfg)); return cfg, pop, build_visits(cfg, pop)


def T(pop, t): return set(pop["truth_type"].query("type_id == @t").subscription_id)
def of(v, ids): return v[v.subscription_id.isin(ids)]


def test_reproducible():
    cfg = load_config(run={"n_subscriptions": 2000}); pop = apply_type_modulation(cfg, build_population(cfg))
    pd.testing.assert_frame_equal(build_visits(cfg, pop), build_visits(cfg, pop))


def test_row_level_rules(env):
    cfg, pop, v = env; s = pop["subscription"].set_index("subscription_id")
    assert set(v.visit_type) <= {"정기점검", "AS수리", "이전설치", "최초설치"} and set(v.visit_status) <= {"완료", "지연완료", "취소", "미이행", "예정"}
    done = v.visit_status.isin(["완료", "지연완료"]); assert v[done].completed_date.notna().all() and v[~done].completed_date.isna().all()
    assert all(c <= TODAY for c in v[done].completed_date) and all(c >= d for c, d in zip(v[done].completed_date, v[done].scheduled_date))
    up = v.visit_status == "예정"; assert all(d > TODAY for d in v[up].scheduled_date) and all(d <= TODAY for d in v[~up].scheduled_date)
    start = s.loc[v.subscription_id, "start_date"].to_numpy(); assert all(d >= a for d, a in zip(v.scheduled_date, start))     # 가입 전 방문 없음
    assert all(d >= TODAY - dt.timedelta(days=365) for d in v[~up].scheduled_date)
    as_ = v[v.visit_type == "AS수리"]; non = v[v.visit_type != "AS수리"]
    assert as_.as_received_date.notna().all() and as_.expected_complete_date.notna().all() and as_.symptom_code.notna().all()
    assert non[["as_received_date", "expected_complete_date", "repair_days", "symptom_code"]].isna().all().all()
    ad = as_[as_.visit_status == "완료"]; assert ((pd.Series(ad.scheduled_date.values) - pd.Series(ad.as_received_date.values)).map(lambda d: d.days).to_numpy() == ad.repair_days.to_numpy()).all()
    assert as_[as_.visit_status == "예정"].repair_days.isna().all() and (as_[as_.visit_status == "예정"].shape[0] > 0)
    care = v[v.visit_type == "정기점검"]; assert set(care.subscription_id) <= set(s[s.care_type == "방문관리"].index) and care.manager_id.notna().all()
    assert care[care.visit_status.isin(["완료", "지연완료"])].duration_min.notna().all() and v[v.visit_type == "AS수리"].duration_min.isna().all()
    assert v.satisfaction_score.dropna().between(1, 5).all()


def test_normal_baselines(env):
    cfg, pop, v = env; act = set(pop["truth_type"].subscription_id); n = v[~v.subscription_id.isin(act)]; c = n[n.visit_type == "정기점검"]
    past = c[c.visit_status != "예정"]; w = past.visit_status.value_counts(normalize=True)
    assert abs(w["완료"] - 0.92) < 0.03 and 0.03 < w["지연완료"] < 0.07
    d = past[past.visit_status.isin(["완료", "지연완료"])]; assert abs(d.duration_min.mean() - 45) < 1.5
    assert abs((d.sales_pitch_yn == "Y").mean() - 0.03) < 0.015 and abs((d.inspection_anomaly_yn == "Y").mean() - 0.02) < 0.012
    ok = n[n.visit_status.isin(["완료", "지연완료"]) & n.visit_type.isin(["정기점검", "AS수리"])]; assert abs(ok.satisfaction_score.notna().mean() - 0.6) < 0.04
    a = n[(n.visit_type == "AS수리") & (n.visit_status == "완료")]; assert 2.5 < a.repair_days.mean() < 3.6
    big = pop["subscription"].query("product_category != '정수기' and care_type == '방문관리'").subscription_id; ids = set(big) - act
    per = of(v, ids).query("visit_type == '정기점검' and visit_status != '예정'").groupby("subscription_id").size(); assert per.mean() < 1.2       # 대형가전 연 1건


def test_t05_t06_signals(env):
    cfg, pop, v = env; c5 = of(v, T(pop, "T05")).query("visit_type == '정기점검' and visit_status in ['완료','지연완료']")
    assert c5.duration_min.mean() < 36                                                                                      # 정상 45분 대비 크게 단축
    care_done = v.query("visit_type == '정기점검' and visit_status in ['완료','지연완료']")
    n_post = 0; as5 = of(v, T(pop, "T05")).query("visit_type == 'AS수리' and visit_status == '완료'")
    cd = care_done.groupby("subscription_id").completed_date.apply(list).to_dict()
    for sid, rec in zip(as5.subscription_id, as5.as_received_date):
        if any(0 <= (rec - c).days <= 14 for c in cd.get(sid, [])): n_post += 1
    assert n_post / max(len(T(pop, "T05")), 1) > 1.0                                                                       # T05 구독당 케어 후 A/S 1건 이상
    t6 = of(v, T(pop, "T06")); w = t6[t6.visit_type == "정기점검"]
    bad = w[w.visit_status.isin(["지연완료", "취소", "미이행"])].groupby("subscription_id").size().reindex(list(T(pop, "T06")), fill_value=0)
    assert bad.mean() >= 2.6 and (bad >= 2).mean() > 0.75
    assert w.booking_lead_days.mean() > 17 and w.reschedule_cnt.mean() > 0.5


def test_t07_t08_signals(env):
    cfg, pop, v = env; t7 = list(T(pop, "T07")); w = v[v.subscription_id.isin(t7) & (v.visit_status != "예정") & v.visit_type.isin(["정기점검", "AS수리"])]
    mgr = w.groupby("subscription_id").manager_id.nunique().reindex(t7, fill_value=0); assert mgr.mean() >= 1.8 and (mgr >= 2).mean() > 0.7           # 담당자 교체
    sat = w.satisfaction_score.dropna(); assert sat.mean() < 3.1
    oth = v[~v.subscription_id.isin(t7) & v.satisfaction_score.notna()]; assert oth.satisfaction_score.mean() > 4.0
    t8 = list(T(pop, "T08")); x = v[v.subscription_id.isin(t8) & (v.sales_pitch_yn == "Y") & (v.scheduled_date >= TODAY - dt.timedelta(days=180))]
    cnt = x.groupby("subscription_id").size().reindex(t8, fill_value=0); assert 1.8 < cnt.mean() < 3.2 and (cnt >= 1).mean() > 0.9
    assert 0.2 < (x.repitch_after_refusal_yn == "Y").mean() < 0.55


def test_t09_t10_t02_signals(env):
    cfg, pop, v = env; s = pop["subscription"].set_index("subscription_id")
    t9 = list(T(pop, "T09")); a9 = v[v.subscription_id.isin(t9) & (v.visit_type == "AS수리") & (v.visit_status == "완료")]
    cnt = a9.groupby("subscription_id").size().reindex(t9, fill_value=0); water = s.loc[t9, "product_category"].to_numpy() == "정수기"
    assert cnt[~water].mean() >= 2.2 and cnt[water].mean() >= 3.2
    rep = a9.groupby("subscription_id").repeat_symptom_yn.apply(lambda x: (x == "Y").sum()).reindex(t9, fill_value=0); assert (rep >= 1).mean() > 0.80   # 동일 증상 반복
    base = v[~v.subscription_id.isin(t9) & (v.visit_type == "AS수리")]; assert (base.repeat_symptom_yn == "Y").mean() < 0.05
    t10 = list(T(pop, "T10")); a10 = v[v.subscription_id.isin(t10) & (v.visit_type == "AS수리")]; done = a10[a10.visit_status == "완료"]
    assert done.repair_days.mean() > 10 and 0.55 < (a10.parts_delay_yn == "Y").mean() < 0.85 and 0.2 < (a10.split_as_receipt_yn == "Y").mean() < 0.7
    open10 = a10[a10.visit_status == "예정"]; assert 0.35 < open10.subscription_id.nunique() / len(t10) < 0.65
    exp = (pd.Series(open10.expected_complete_date.values) - pd.Series(open10.as_received_date.values)).map(lambda d: d.days); assert exp.min() >= 7  # S-T10-D 조건
    t2 = list(T(pop, "T02")); reloc = v[v.subscription_id.isin(t2) & (v.visit_type == "이전설치")]; assert 0.3 < reloc.subscription_id.nunique() / len(t2) < 0.65
    assert reloc.visit_status.isin(["지연완료", "완료"]).all() and (reloc.visit_status == "지연완료").mean() > 0.3
    assert v[~v.subscription_id.isin(t2) & (v.visit_type == "이전설치")].empty
