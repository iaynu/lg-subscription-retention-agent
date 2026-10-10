import datetime as dt, sys
from pathlib import Path
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datagen.config import load_config
from datagen.population import build_population
from datagen.modulation import apply_type_modulation
from datagen.events_visit import build_visits
from datagen.events_voc import build_voc, CATEGORIES
TODAY = dt.date(2026, 9, 30)


@pytest.fixture(scope="module")
def env():
    cfg = load_config(); pop = apply_type_modulation(cfg, build_population(cfg)); vis = build_visits(cfg, pop); return cfg, pop, vis, build_voc(cfg, pop, vis)


def T(pop, t): return list(pop["truth_type"].query("type_id == @t").subscription_id)
def n90(voc, ids, cats):
    x = voc[voc.subscription_id.isin(ids) & voc.category.isin(cats) & (voc.contact_date >= TODAY - dt.timedelta(days=90))]
    return x.groupby("subscription_id").size().reindex(ids, fill_value=0)


def test_reproducible():
    cfg = load_config(run={"n_subscriptions": 2000}); pop = apply_type_modulation(cfg, build_population(cfg)); vis = build_visits(cfg, pop)
    pd.testing.assert_frame_equal(build_voc(cfg, pop, vis), build_voc(cfg, pop, vis))


def test_row_rules(env):
    cfg, pop, vis, v = env; s = pop["subscription"].set_index("subscription_id")
    assert set(v.category) <= set(CATEGORIES) and set(v.channel) <= {"앱", "전화", "챗봇"} and len(CATEGORIES) == 15
    assert all(TODAY - dt.timedelta(days=180) <= d <= TODAY for d in v.contact_date) and all(d > a for d, a in zip(v.contact_date, s.loc[v.subscription_id, "start_date"]))
    assert v[v.category == "해지문의"].cancel_delay_days.notna().all() and v[v.category != "해지문의"].cancel_delay_days.isna().all()
    assert v.sentiment_score.between(-1, 1).all() and v.summary_text.notna().all() and v.response_hours.dropna().gt(0).all()
    ch = v.channel.value_counts(normalize=True); assert abs(ch["앱"] - 0.45) < 0.03 and abs(ch["전화"] - 0.40) < 0.03
    # 재문의: 같은 구독·카테고리에서 직전 문의와 14일 이내
    x = v.sort_values(["subscription_id", "category", "contact_date"]); g = x.groupby(["subscription_id", "category"]).contact_date.diff().dt.days if hasattr(x.contact_date, "dt") else None
    d = pd.to_datetime(x.contact_date); gap = d.groupby([x.subscription_id, x.category]).diff().dt.days
    assert ((gap <= 14).to_numpy() == (x.repeat_contact_yn == "Y").to_numpy()).all()


def test_normal_noise(env):
    cfg, pop, vis, v = env; act = set(pop["truth_type"].subscription_id); ids = [i for i in pop["subscription"].subscription_id if i not in act]
    n = v[v.subscription_id.isin(ids)]; per = n.groupby("subscription_id").size().reindex(ids, fill_value=0)
    assert 0.25 < per.mean() < 0.40 and per.max() <= 6                                                                # 정상군 0~1건 수준
    assert abs((n.resolved_yn == "Y").mean() - 0.90) < 0.03 and abs((n.first_response_unresolved_yn == "Y").mean() - 0.10) < 0.03
    assert n.category.isin(["기타", "사용법문의", "요금문의"]).mean() > 0.6


def test_type_signals_counts(env):
    cfg, pop, vis, v = env
    for t, cats, lo, hi in [("T01", ["요금문의", "총비용문의"], 2, 5), ("T06", ["일정변경요청"], 2, 5), ("T07", ["태도불만"], 1, 3), ("T08", ["영업불만"], 1, 2)]:
        ids = T(pop, t); c = n90(v, ids, cats); assert c.mean() >= lo + 0.4 and c.mean() <= hi + 0.6, (t, c.mean()); assert (c >= lo).mean() > 0.9, t
    ids = T(pop, "T02"); c = n90(v, ids, ["위약금문의", "해지문의"]); assert c.mean() >= 2.4 and 0.35 < (n90(v, ids, ["이사문의"]) > 0).mean() < 0.65
    ids = T(pop, "T05"); assert n90(v, ids, ["재방문요청"]).mean() >= 1.5 and n90(v, ids, ["케어결과불만"]).mean() >= 1.5
    ids = T(pop, "T03"); assert 1.0 <= n90(v, ids, ["갱신문의"]).mean() <= 2.6
    ids = T(pop, "T07"); assert n90(v, ids, ["담당자교체요청"]).mean() >= 1.0


def test_t02_delay_and_t10_signals(env):
    cfg, pop, vis, v = env; t2 = T(pop, "T02"); d2 = v[v.subscription_id.isin(t2) & (v.category == "해지문의")].cancel_delay_days
    dn = v[~v.subscription_id.isin(t2) & (v.category == "해지문의")].cancel_delay_days; assert d2.mean() > 4 and dn.max() <= 2 and dn.mean() < 1.5
    t10 = T(pop, "T10"); x = v[v.subscription_id.isin(t10)]; w = x[x.contact_date >= TODAY - dt.timedelta(days=90)]
    assert w.groupby("subscription_id").size().reindex(t10, fill_value=0).mean() >= 2.5
    un = w[w.resolved_yn == "N"].groupby("subscription_id").size().reindex(t10, fill_value=0); assert un.mean() >= 1.2 and (un >= 1).mean() > 0.85
    cx = w[w.category == "응대불만"]
    assert 0.35 < (cx.first_response_unresolved_yn == "Y").mean() < 0.85 and 0.2 < (cx.resolved_yn == "Y").mean() < 0.55        # T10 문의: 1차 미해결 높고 해결률 낮음
    cx = w[w.category == "응대불만"]; assert cx.shape[0] > 0 and cx.response_hours.median() > 14          # 응대불만 응답시간 지연
    oth = v[~v.subscription_id.isin(t10)]; assert oth.response_hours.median() < 9


def test_t05_voc_follows_care_visit(env):
    cfg, pop, vis, v = env; ids = set(T(pop, "T05")); care = vis[(vis.visit_type == "정기점검") & vis.visit_status.isin(["완료", "지연완료"])].groupby("subscription_id").completed_date.apply(list).to_dict()
    x = v[v.subscription_id.isin(ids) & v.category.isin(["재방문요청", "케어결과불만"])]
    near = [any(0 < (d - c).days <= 10 for c in care.get(s, [])) for s, d in zip(x.subscription_id, x.contact_date)]
    assert np.mean(near) > 0.6                                                                                         # 케어 직후 문의
