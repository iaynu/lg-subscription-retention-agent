from agent.rules.engine import run_rule_engine
from agent.service import AgentService
from agent.config import load_config
from agent.llm.mock import RuleBasedMock
from agent.repo.memory import MemoryRepo
from .conftest import base_input

def all_ineligible_facts():
    return {"unapplied_discount_yn": "N", "months_since_start": 1, "days_to_promo_end": 999, "voc_fee_or_cancel_cnt_30d": 0}

def test_zero_candidates_falls_back_to_survey(catalog):
    # T08 후보(A,B)가 모두 쿨다운이면 후보 0건 -> 만족도 조사
    rec = [{"solution_id": s, "action_status": "조치완료", "offer_cost": 0, "created_at": "2026-09-25"} for s in ("S-T08-A", "S-T08-B")]
    r = run_rule_engine(base_input([("T08", 1.0)], recent=rec), catalog)
    assert r["mode"] == "NO_TYPE" and [c["solution_id"] for c in r["candidates"]] == ["S-NT-A"]
    assert r["fallback"] == "NO_ELIGIBLE_CANDIDATE" and r["uncovered_types"] == ["T08"]

def test_survey_in_cooldown_means_no_action(catalog):
    rec = [{"solution_id": s, "action_status": "조치완료", "offer_cost": 0, "created_at": "2026-09-25"} for s in ("S-T08-A", "S-T08-B", "S-NT-A")]
    r = run_rule_engine(base_input([("T08", 1.0)], recent=rec), catalog)
    assert r["mode"] == "ACTION" and r["candidates"] == [] and r["fallback"] is None

def test_flag_off_keeps_old_behavior(catalog):
    rec = [{"solution_id": s, "action_status": "조치완료", "offer_cost": 0, "created_at": "2026-09-25"} for s in ("S-T08-A", "S-T08-B")]
    r = run_rule_engine(base_input([("T08", 1.0)], recent=rec), catalog, {"fallback_survey_when_empty": False})
    assert r["candidates"] == [] and r["mode"] == "ACTION"

def test_normal_case_has_no_fallback(catalog):
    assert run_rule_engine(base_input([("T02", 1.0)]), catalog)["fallback"] is None

def test_end_to_end_survey_message_passes_validator(catalog):
    rec = [{"solution_id": s, "action_status": "조치완료", "offer_cost": 0, "created_at": "2026-09-25"} for s in ("S-T08-A", "S-T08-B")]
    ai = base_input([("T08", 1.0)], recent=rec)
    res = AgentService(load_config(), catalog, RuleBasedMock(), MemoryRepo()).run(ai)
    assert res["fallback"] == "NO_ELIGIBLE_CANDIDATE" and res["outcome"] == "pass"
    assert [s["solution_id"] for s in res["selected"]] == ["S-NT-A"] and res["selected"][0]["type_id"] == "유형없음"
