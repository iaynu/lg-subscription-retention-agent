from agent.rules.engine import run_rule_engine
from agent.rules.eligibility import check_eligibility
from .conftest import base_input

def sel(res): return [c["solution_id"] for c in res["candidates"]]
def why(res): return {e["solution_id"]: e["reason"] for e in res["excluded"]}

def test_eligibility_unknown_fact_passes():
    assert check_eligibility([["a", ">=", 1]], {})[0]
    assert check_eligibility([["a", ">=", 1]], {"a": None})[0]

def test_eligibility_fail_and_pass():
    assert not check_eligibility([["a", ">=", 1], ["b", "==", "Y"]], {"a": 2, "b": "N"})[0]
    assert check_eligibility([["a", ">=", 1], ["b", "==", "Y"]], {"a": 2, "b": "Y"})[0]

def test_priority_prefers_direct_fix(catalog):
    # T05: S-T05-C(직접 해결, 10)이 S-T05-A(내부 보고, 40)보다 앞 — v2의 알파벳 편향 해소
    res = run_rule_engine(base_input([("T05", 1.0)], facts={"days_since_last_care": 3, "care_short_visit_cnt": 2}), catalog)
    assert sel(res)[0] == "S-T05-C"

def test_not_eligible_excluded_with_reason(catalog):
    res = run_rule_engine(base_input([("T01", 1.0)], facts={"unapplied_discount_yn": "N", "months_since_start": 2,
                                                          "days_to_promo_end": 100, "voc_fee_or_cancel_cnt_30d": 0}), catalog)
    w = why(res)
    assert w["S-T01-C"] == "NOT_ELIGIBLE" and w["S-T01-A"] == "NOT_ELIGIBLE" and w["S-T01-D"] == "NOT_ELIGIBLE"
    assert "S-T01-C" not in sel(res)

def test_missing_facts_required_vs_optional(catalog):
    # 값이 없을 때: '사건' 조건(required)은 제외, 그 밖의 조건은 통과
    res = run_rule_engine(base_input([("T01", 1.0)]), catalog)
    w = why(res)
    assert w.get("S-T01-C") == "NOT_ELIGIBLE"          # 미적용 할인 여부(required) 없음
    assert w.get("S-T01-B") == "NOT_ELIGIBLE"          # 프로모션 종료일(required) 없음
    assert "S-T01-A" in sel(res)                       # months_since_start(선택 조건) 없음 → 통과
    # 설정으로 예전 동작(값이 없으면 모두 통과)
    res2 = run_rule_engine(base_input([("T01", 1.0)]), catalog, {"unknown_required_fact": "pass"})
    assert not any(e["reason"] == "NOT_ELIGIBLE" for e in res2["excluded"]) and len(sel(res2)) == 2

def test_numeric_string_fact_is_compared_as_number(catalog):
    res = run_rule_engine(base_input([("T01", 1.0)], facts={"months_since_start": "3"}), catalog)
    assert why(res).get("S-T01-A") == "NOT_ELIGIBLE"    # "3"개월 < 6

def test_facts_derived_from_subscription(catalog):
    ai = base_input([("T04", 1.0)]); ai["subscription"]["start_date"] = "2026-09-27"   # 설치 3일째 → 체험 종료까지 4일
    res = run_rule_engine(ai, catalog, {"facts_cfg": {"trial_days": 7}})
    assert "S-T04-E" not in why(res) or why(res)["S-T04-E"] != "NOT_ELIGIBLE"
    ai["subscription"]["start_date"] = "2026-01-01"
    assert why(run_rule_engine(ai, catalog, {"facts_cfg": {"trial_days": 7}}))["S-T04-E"] == "NOT_ELIGIBLE"

def test_no_ad_consent(catalog):
    res = run_rule_engine(base_input([("T01", 1.0)], ad="N", facts={"unapplied_discount_yn": "Y"}), catalog)
    assert why(res)["S-T01-C"] == "NO_AD_CONSENT"
    ai = base_input([("T01", 1.0)], facts={"unapplied_discount_yn": "Y"}); ai["subscription"].pop("ad_consent_yn")
    assert why(run_rule_engine(ai, catalog))["S-T01-C"] == "NO_AD_CONSENT"          # 값이 없으면 미동의
    ai["customer"] = {"ad_consent_yn": "Y"}
    assert "S-T01-C" in sel(run_rule_engine(ai, catalog))                           # 고객 테이블 값이 우선

def test_survey_only_respects_survey_cooldown(catalog):
    ai = base_input([], level="LOW", rank=0.25)
    r = run_rule_engine(ai, catalog)
    assert r["mode"] == "SURVEY_ONLY" and r["survey_solution"] == "S-NT-A"
    ai["recent_actions"] = [{"solution_id": "S-NT-A", "action_status": "조치완료", "offer_cost": 0, "created_at": "2026-09-29"}]
    r = run_rule_engine(ai, catalog)
    assert r["survey_solution"] is None and why(r)["S-NT-A"] == "COOLDOWN_SOLUTION"

def test_future_dated_action_counts_as_today(catalog):
    ai = base_input([("T02", 1.0)], recent=[{"solution_id": "S-T02-A", "action_status": "조치완료", "offer_cost": 0, "created_at": "2027-03-01"}])
    r = run_rule_engine(ai, catalog)
    ex = next(e for e in r["excluded"] if e["solution_id"] == "S-T02-A")
    assert ex["reason"] == "COOLDOWN_SOLUTION" and "0일 전" in ex["detail"]

def test_enterprise_and_inactive_never_candidates(catalog):
    res = run_rule_engine(base_input([("T04", 1.0)]), catalog)
    ids = set(sel(res)) | set(why(res))
    assert "S-T04-B" not in ids          # 전사 개선: agent_selectable_yn=N

def test_cooldown_same_solution_only(catalog):
    rec = [{"solution_id": "S-T02-A", "action_status": "조치완료", "offer_cost": 0, "created_at": "2026-09-25"}]
    res = run_rule_engine(base_input([("T02", 1.0)], recent=rec), catalog)
    assert why(res)["S-T02-A"] == "COOLDOWN_SOLUTION"

def test_rejected_not_counted(catalog):
    rec = [{"solution_id": "S-T02-A", "action_status": "반려", "offer_cost": 0, "created_at": "2026-09-29"}]
    res = run_rule_engine(base_input([("T02", 1.0)], recent=rec), catalog)
    assert "S-T02-A" not in why(res) or why(res)["S-T02-A"] != "COOLDOWN_SOLUTION"

def test_modes(catalog):
    assert run_rule_engine(base_input([], level="MID"), catalog)["mode"] == "NO_TYPE"
    assert run_rule_engine(base_input([], level="LOW", rank=0.25), catalog)["mode"] == "SURVEY_ONLY"
    assert run_rule_engine(base_input([], level="LOW", rank=0.5), catalog)["mode"] == "NONE"

def test_every_selectable_solution_has_priority_and_valid_eligibility(catalog):
    for s in catalog:
        assert isinstance(s["priority"], int)
        for rule in (s["eligibility"] or []):
            assert rule[1] in (">=", "<=", ">", "<", "==", "!=") and (len(rule) == 3 or rule[3] == "required")
