import datetime as dt, sys
from pathlib import Path
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datagen.config import load_config, TYPE_IDS
from datagen.generate import generate_all, OUTPUT_TABLES
from datagen.labels import sigmoid, risk_levels
from datagen.validate import validate_all, render_report, orphans
from datagen.demo_cases import select_demo_cases
TODAY = dt.date(2026, 9, 30)


@pytest.fixture(scope="module")
def env():
    cfg = load_config(); t = generate_all(cfg); return cfg, t


def test_generate_all_tables_and_determinism():
    cfg = load_config(run={"n_subscriptions": 2000, "care_manager_count": 40}); a, b = generate_all(cfg), generate_all(cfg)
    assert set(OUTPUT_TABLES) <= set(a) and "label_detail" in a
    for k in a: pd.testing.assert_frame_equal(a[k], b[k])


def test_label_formula_and_primary(env):
    cfg, t = env; d = t["label_detail"].set_index("subscription_id"); L = cfg["label"]
    assert np.allclose(sigmoid(np.array([0.0, 2.0, -4.6])), [0.5, 0.8808, 0.0100], atol=1e-3)
    sub = t["subscription"].set_index("subscription_id"); days = np.array([(e - TODAY).days for e in sub.end_date])
    assert np.allclose(d.loc[sub.index, "timing_proximity"], np.maximum(1 - days / 90, 0))
    # 유형 없음·만료 멀면 logit은 b0 근처(노이즈 σ 0.5)
    far = d.loc[sub.index[(days > 200)], "logit"][~sub.index[(days > 200)].isin(set(t["synth_truth_type"].subscription_id))]
    assert abs(far.mean() - L["b0"]) < 0.03 and abs(far.std() - L["noise_sigma"]) < 0.03
    tr = t["synth_truth_type"]; assert (tr.groupby("subscription_id").is_primary_yn.apply(lambda x: (x == "Y").sum()) == 1).all()   # 구독당 주 유형 정확히 1개
    w = tr.assign(c=[L["type_w"][a] * s for a, s in zip(tr.type_id, tr.activation_strength)]); top = w.sort_values("c", ascending=False).groupby("subscription_id").head(1)
    assert (top.is_primary_yn == "Y").mean() > 0.99                                                                          # 기여 최대 유형이 주 유형(소수점 반올림 오차 허용)


def test_label_table_shape(env):
    cfg, t = env; lab = t["synth_churn_label"]
    assert len(lab) == len(t["subscription"]) and set(lab.churn_30d) == {0, 1} and (lab.as_of_date == TODAY).all()
    ch = lab[lab.churn_30d == 1]; assert ch.cancel_requested_date.notna().all() and lab[lab.churn_30d == 0].cancel_requested_date.isna().all()
    off = [(d - TODAY).days for d in ch.cancel_requested_date]; assert min(off) == 1 and max(off) == 30
    assert abs(np.mean(off) - 15.5) < 0.6                                                                                     # 1~30일 균등
    assert risk_levels(t["label_detail"]).value_counts().to_dict() == {"LOW": 24000, "HIGH": 3000, "MID": 3000}


def test_validation_all_pass_and_report(env):
    cfg, t = env; res = validate_all(cfg, t); bad = [c for c in res["checks"] if not c[1]]
    assert not bad, bad
    md = render_report(cfg, res); assert "점검 결과: " in md and "실패" not in md and "| synth_churn_label |" in md and "이탈률" in md


def test_orphan_check_detects_breakage(env):
    cfg, t = env; t2 = dict(t); v = t["voc_contact"].copy(); v.loc[v.index[0], "subscription_id"] = "S999999"; t2["voc_contact"] = v
    assert sum(orphans(t2).values()) == 1 and sum(orphans(t).values()) == 0


def test_demo_cases(env):
    cfg, t = env; d = select_demo_cases(cfg, t)
    assert list(d.case_id) == [f"D{i:02d}" for i in range(1, 13)] and d.subscription_id.is_unique and (d.extra_types == 0).all()
    pd.testing.assert_frame_equal(d, select_demo_cases(cfg, t))                                                              # 결정적
    tt = t["synth_truth_type"].groupby("subscription_id").type_id.apply(set); r = d.set_index("case_id")
    assert tt[r.at["D01", "subscription_id"]] == {"T01", "T04"} and r.at["D01", "ad_consent"] == "Y"
    assert tt[r.at["D02", "subscription_id"]] == {"T06", "T07"} and r.at["D02", "ad_consent"] == "N"
    sub = t["subscription"].set_index("subscription_id"); assert sub.at[r.at["D04", "subscription_id"], "manager_change_confirmed_yn"] == "Y"
    v = t["care_visit"]; x = v[(v.subscription_id == r.at["D03", "subscription_id"]) & (v.visit_type == "AS수리") & (v.visit_status == "예정")]
    assert ((x.expected_complete_date - x.as_received_date).map(lambda z: z.days) >= 7).any()
    assert r.at["D06", "days_to_end"] <= 30 and r.at["D11", "true_types"] == "(없음)" and r.at["D11", "churn_30d"] == 1 and r.at["D12", "churn_30d"] == 0
