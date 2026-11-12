"""폴백 템플릿 v3(보고서 형식): 모든 솔루션의 템플릿이 검증을 통과하고, 내부 솔루션 이름을 고객 문구에 쓰지 않는지."""
import json
import pytest
from agent.validation.fallback import template_item, full_template, josa, run_with_fallback
from agent.validation.validator import validate, HARD
from agent.llm.mock import ScriptedLlm
from .test_validator_v2 import inp

FACTS = {"monthly_fee": 50000, "contract_months": 60, "end_date": "2027-03-01", "days_to_end": 100,
         "promo_end_date": "2026-10-20", "care_renewal_date": "2027-01-01", "months_since_start": 40, "paid_total": 1200000,
         "remaining_care_cnt": 1, "contract_year_end_date": "2026-11-14", "trial_end_date": "2026-10-03",
         "last_care_date": "2026-09-28", "next_visit_date": "2026-10-06", "last_visit_date": "2026-09-27",
         "days_since_last_use": 45, "expected_as_days": 9}
INTERNAL_WORDS = ("저평점", "선제안", "재배정 제한", "표준화", "지침", "자동 지급")


def _inp_for(s, with_facts):
    i = inp(dict(s)); i["types"][0]["type_id"] = s["type_id"]
    if with_facts: i["message_facts"] = dict(FACTS)
    if s["type_id"] == "유형없음":
        i["mode"] = "NO_TYPE"; i["types"] = []
    return i


@pytest.mark.parametrize("with_facts", [False, True])
def test_every_solution_template_passes_and_hides_internal_names(catalog, with_facts):
    for s in catalog:
        if s.get("agent_selectable_yn") != "Y": continue
        i = _inp_for(s, with_facts)
        out = full_template(i)
        r = validate(json.dumps(out, ensure_ascii=False), i)
        assert r["ok"], (s["solution_id"], [f[:3] for f in r["failures"] if f[3] == HARD])
        it = out["selected"][0]
        if s["delivery_kind"] == "내부 전달":
            assert it["message_text"] == "" and it["internal_memo"]; continue
        assert s["name"] not in it["message_text"] and not any(w in it["message_text"] for w in INTERNAL_WORDS), s["solution_id"]
        if with_facts and s.get("message_items"):
            assert "▶" in it["message_text"], s["solution_id"]
        if s["delivery_kind"] == "고객 메시지":
            assert it["title"] and it["title"].startswith("(광고)") == (s["ad_yn"] == "Y")


def test_template_lines_use_exact_fact_values(catalog):
    s = next(c for c in catalog if c["solution_id"] == "S-T04-C")
    it = template_item(s, 1, _inp_for(s, True))
    assert "▶ 올해 남은 케어: 1회" in it["message_text"] and "▶ 케어 사용 기한: 2026년 11월 14일" in it["message_text"]


def test_non_ad_benefit_template_does_not_ask_to_apply(catalog):
    for sid in ("S-T06-D", "S-T07-C"):                     # 사과 포인트: 신청할 필요가 없음
        s = next(c for c in catalog if c["solution_id"] == sid)
        assert "신청을 원하시면" not in template_item(s, 1, _inp_for(s, False))["message_text"]


def test_josa():
    assert josa("케어", "과", "와") == "케어와" and josa("반영", "과", "와") == "반영과" and josa("", "과", "와") == "와"


def test_report_style_llm_output_passes(catalog):
    s = next(c for c in catalog if c["solution_id"] == "S-T04-C")
    i = _inp_for(s, True)
    good = {"selected": [{"priority": 1, "solution_id": "S-T04-C", "type_id": "T04", "selection_reason": "남은 케어가 있어 안내합니다.",
                          "evidence": [{"feature": "f1", "value": 3}], "title": "고객님, 올해 받을 수 있는 케어가 남았어요",
                          "message_text": "고객님, 올해 받으실 수 있는 케어가 남아 안내드립니다.\n▶ 올해 남은 케어: 1회\n▶ 케어 사용 기한: 2026년 11월 14일\n원하시는 날짜는 앱 또는 상담으로 예약하실 수 있습니다.",
                          "internal_memo": ""}], "summary": "남은 케어 안내를 제안합니다."}
    bad = json.loads(json.dumps(good)); bad["selected"][0]["message_text"] = bad["selected"][0]["message_text"].replace("1회", "2회")
    data, log = run_with_fallback(i, ScriptedLlm([{"output": good}]).generate)
    assert log["outcome"] == "pass" and data["selected"][0]["message_source"] == "llm"
    data, log = run_with_fallback(i, ScriptedLlm([{"output": bad}, {"output": bad}]).generate)
    assert log["outcome"] == "partial_template" and data["selected"][0]["message_source"] == "template"
    assert "▶ 올해 남은 케어: 1회" in data["selected"][0]["message_text"]


def test_validator_crash_falls_back_to_template(monkeypatch):
    import agent.validation.fallback as fb
    def boom(raw, inp): raise RuntimeError("검증기 버그")
    monkeypatch.setattr(fb, "validate", boom)
    from .fixtures import INP_A, OUT_A
    data, log = fb.run_with_fallback(INP_A, lambda i, n, t: json.dumps(OUT_A, ensure_ascii=False))
    assert log["outcome"] == "full_template" and "SCHEMA" in log["failed_checks"]


def test_service_blocks_duplicate_request_id_even_if_repo_does_not(catalog):
    from agent.config import load_config
    from agent.llm.mock import RuleBasedMock
    from agent.repo.memory import MemoryRepo
    from agent.service import AgentService
    from .conftest import base_input

    class LooseRepo(MemoryRepo):                  # 중복 검사를 하지 않는 저장소
        def put_result(self, request_id, result): self.results[request_id] = result
    svc = AgentService(load_config(), catalog, RuleBasedMock(), LooseRepo())
    svc.run(base_input([("T02", 1.0)]))
    with pytest.raises(ValueError): svc.run(base_input([("T02", 1.0)]))


def test_config_extra_banned_phrase_reaches_validator(catalog):
    """config.yaml llm.banned_phrases_extra에 넣은 표현이 검증기(M7)까지 전달되는지."""
    from agent.config import load_config
    from agent.llm.mock import RuleBasedMock
    from agent.repo.memory import MemoryRepo
    from agent.service import AgentService
    from .conftest import base_input

    class Spicy(RuleBasedMock):                   # 정상 문구 끝에 팀 금지 표현을 붙이는 LLM
        def generate(self, inp, note, timeout):
            d = json.loads(super().generate(inp, note, timeout))
            for s in d["selected"]:
                if s["message_text"]: s["message_text"] = s["message_text"].rstrip() + " 특가로 안내드립니다."
            return json.dumps(d, ensure_ascii=False)
    r = AgentService(load_config(), catalog, Spicy(), MemoryRepo()).run(base_input([("T02", 1.0)]))
    assert r["outcome"] == "pass"
    cfg = load_config(); cfg["llm"]["banned_phrases_extra"] = ["특가"]
    r = AgentService(cfg, catalog, Spicy(), MemoryRepo()).run(base_input([("T02", 1.0)]))
    assert r["outcome"] in ("partial_template", "full_template") and "M7" in r["failed_checks"]
    assert all("특가" not in s["message_text"] for s in r["selected"])


def test_selected_items_are_returned_in_priority_order(catalog):
    """LLM이 priority 2를 먼저 내도 결과는 우선순위 순으로 정렬된다."""
    from agent.config import load_config
    from agent.llm.mock import RuleBasedMock
    from agent.repo.memory import MemoryRepo
    from agent.service import AgentService
    from .conftest import base_input

    class Reversed(RuleBasedMock):
        def generate(self, inp, note, timeout):
            d = json.loads(super().generate(inp, note, timeout)); d["selected"].reverse()
            return json.dumps(d, ensure_ascii=False)
    r = AgentService(load_config(), catalog, Reversed(), MemoryRepo()).run(base_input([("T01", 0.6), ("T06", 0.4)]))
    assert r["outcome"] == "pass" and len(r["selected"]) == 2
    assert [s["priority"] for s in r["selected"]] == [1, 2]
