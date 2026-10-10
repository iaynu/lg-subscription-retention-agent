import sys, datetime as dt
from pathlib import Path
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datagen.config import load_config
from datagen.generate import generate_all
from churn_model.io import from_generated
from churn_model.features import build_features, encode, feature_groups, load_cfg, CAT_COLS


@pytest.fixture(scope="module")
def env():
    cfg = load_config(run={"n_subscriptions": 2000}); g = generate_all(cfg); return g, from_generated(g)


@pytest.fixture(scope="module")
def F(env): return build_features(env[1])


def test_columns_match_dictionary(F):
    assert list(F.columns) == list(feature_groups()) and F.shape == (2000, 72) and "region_changed_flag" not in F
    assert F.index.is_unique




def test_no_label_or_truth_columns(F):
    assert not [c for c in F.columns if "churn" in c or "truth" in c or c.startswith("synth")]


def test_encode_numeric_and_categories(F):
    X = encode(F); assert (X.dtypes == float).all() and X.product_category.between(0, 5).all() and X.care_type.between(0, 2).all()


def test_ranges_and_flags(F):
    for c in [c for c in F if c.endswith("_yn") or c.endswith("_flag")]: assert set(F[c].unique()) <= {0, 1}, c
    assert F.zero_use_weeks_12w.between(0, 12).all() and F.penalty_zone.between(1, 3).all()
    for c in [c for c in F if c.endswith(("cnt_90d", "cnt_180d", "cnt_365d", "_cnt", "sum_365d"))]: assert (F[c].dropna() >= 0).all(), c
    assert F.tenure_months.min() > 0 and (F.visit_gap_ratio.dropna() >= 0).all()


def test_future_events_do_not_change_features(env, F):
    _, t = env; t2 = {k: v.copy() for k, v in t.items()}; today = pd.Timestamp("2026-09-30"); sid = t["subscription"].subscription_id.iloc[0]
    row = t["voc_contact"].iloc[[0]].copy(); row["subscription_id"] = sid; row["contact_date"] = today + pd.Timedelta(days=5); row["category"] = "해지문의"
    t2["voc_contact"] = pd.concat([t2["voc_contact"]] + [row] * 5, ignore_index=True)
    v = t["care_visit"].iloc[[0]].copy(); v["subscription_id"] = sid; v["visit_type"] = "AS수리"; v["as_received_date"] = today + pd.Timedelta(days=3); v["scheduled_date"] = today + pd.Timedelta(days=3)
    t2["care_visit"] = pd.concat([t2["care_visit"], v], ignore_index=True)
    pd.testing.assert_frame_equal(F, build_features(t2))


def test_hand_checked_small_case():
    today = pd.Timestamp("2026-09-30"); d = lambda n: today - pd.Timedelta(days=n)
    sub = pd.DataFrame([dict(subscription_id="S1", customer_id="C1", product_category="냉장고", model_discontinued_yn="Y", new_model_released_yn="N", start_date=d(400), contract_months=60,
                             end_date=today + pd.Timedelta(days=40), monthly_fee=50000, discount_eligible_yn="Y", discount_applied_yn="N", care_type="방문관리", care_manager_id="M1",
                             promo_end_date=today + pd.Timedelta(days=20), mandatory_months=36)])
    cu = pd.DataFrame([dict(customer_id="C1", weekday_daytime_available_yn="N")]); cm = pd.DataFrame([dict(manager_id="M1", avg_rating=4.1)])
    E = lambda cols: pd.DataFrame(columns=cols)
    vcols = ["subscription_id", "manager_id", "visit_type", "scheduled_date", "completed_date", "visit_status", "reschedule_cnt", "duration_min", "booking_lead_days", "sales_pitch_yn", "repitch_after_refusal_yn",
             "split_as_receipt_yn", "symptom_code", "repeat_symptom_yn", "parts_delay_yn", "inspection_anomaly_yn", "as_received_date", "expected_complete_date", "repair_days", "satisfaction_score"]
    care = dict(subscription_id="S1", manager_id="M1", visit_type="정기점검", scheduled_date=d(30), completed_date=d(30), visit_status="완료", reschedule_cnt=0, duration_min=15, booking_lead_days=7,
                sales_pitch_yn="N", repitch_after_refusal_yn="N", split_as_receipt_yn="N", symptom_code=None, repeat_symptom_yn="N", parts_delay_yn="N", inspection_anomaly_yn="N",
                as_received_date=pd.NaT, expected_complete_date=pd.NaT, repair_days=None, satisfaction_score=4)
    asr = {**care, "visit_type": "AS수리", "scheduled_date": d(25), "completed_date": d(22), "as_received_date": d(25), "repair_days": 3, "duration_min": None}
    vis = pd.DataFrame([care, asr], columns=vcols)
    vocs = pd.DataFrame([dict(subscription_id="S1", contact_date=d(10), channel="전화", category="요금문의", repeat_contact_yn="N", resolved_yn="N", first_response_unresolved_yn="N", cancel_delay_days=None, response_hours=2.0),
                         dict(subscription_id="S1", contact_date=d(100), channel="전화", category="요금문의", repeat_contact_yn="N", resolved_yn="Y", first_response_unresolved_yn="N", cancel_delay_days=None, response_hours=1.0)])
    usage = pd.DataFrame([dict(subscription_id="S1", week_start_date=today - pd.Timedelta(weeks=12 - i), run_count=max(10 - i, 0), run_minutes=60, app_login_days=1) for i in range(12)])
    pay = pd.DataFrame([dict(subscription_id="S1", billing_month=today - pd.Timedelta(days=30 * k), billed_amount=a, discount_amount=0, paid_amount=a, due_date=today, paid_date=today, payment_status="정상")
                        for k, a in zip(range(6, 0, -1), [100, 100, 100, 110, 110, 110])])
    t = dict(subscription=sub, customer=cu, care_manager=cm, care_visit=vis, voc_contact=vocs, usage_thinq=usage, payment=pay, notice_log=E(["subscription_id", "notice_type", "sent_at", "opened_at"]))
    F = build_features(t).iloc[0]
    assert F.voc_fee_cnt_90d == 1 and F.unresolved_voc_cnt == 0 and F.repeat_voc_cnt == 0 and F.days_to_end == 40 and F.promo_days_to_end == 20 and F.unapplied_discount_yn == 1
    assert F.care_short_visit_cnt == 1 and F.care_duration_avg_min == 15 and F.post_care_as_cnt_14d == 1 and F.as_cnt_365d == 0 and F.as_cnt_cum == 0 and pd.isna(F.days_since_last_as) and pd.isna(F.repair_days_avg_365d)   # 케어 직후 A/S는 T05 쪽에만 센다
    assert abs(F.billed_change_rate_3m - 0.1) < 1e-9 and F.weekday_daytime_available_yn == 0 and F.manager_avg_rating == 4.1 and F.run_count_trend_12w < 0
    assert F.zero_use_weeks_12w == 2
    assert F.visit_gap_ratio == pytest.approx(30 / (12 * 30.4375), abs=1e-3) and F.days_overdue_visit == 0
