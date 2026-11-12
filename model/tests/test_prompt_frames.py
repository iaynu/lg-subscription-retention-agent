"""프롬프트 v3: 문구 틀 예시가 검증기를 통과하는지, LLM에 보내는 데이터에서 내부 값이 빠지는지."""
import json
from agent.llm.prompt import FRAMES, SYSTEM_PROMPT, PROMPT_VERSION, build_user_message
from agent.validation.validator import validate, HARD
from .test_validator_v2 import mk, inp, item

EXAMPLE_FACTS = {"remaining_care_cnt": 1, "contract_year_end_date": "2026-12-31", "next_visit_date": "2026-10-15",
                 "monthly_fee": 52000, "last_care_date": "2026-09-28"}


def test_frame_examples_pass_validator():
    for cat, (_, example) in FRAMES.items():
        c = mk(cat, name="테스트 솔루션"); i = inp(c); i["message_facts"] = dict(EXAMPLE_FACTS)
        if cat == "의견수집":
            i["mode"] = "NO_TYPE"; i["types"] = []; c["type_id"] = "유형없음"
            out = {"selected": [item(c, type_id="유형없음", message_text=example,
                                     evidence=[{"feature": "risk_level", "value": "HIGH"}, {"feature": "risk_rank_pct", "value": 0.05}])],
                   "summary": "의견을 확인합니다."}
        else:
            out = {"selected": [item(c, message_text=example)], "summary": "안내를 제안합니다."}
        r = validate(json.dumps(out, ensure_ascii=False), i)
        assert r["ok"], (cat, [f[:3] for f in r["failures"] if f[3] == HARD])


def test_example_numbers_are_blocked_without_message_facts():
    # 같은 예시라도 고객 공개 값이 없으면 숫자를 쓸 수 없다(R2 또는 M3)
    c = mk("정보제공"); i = inp(c)
    out = {"selected": [item(c, message_text=FRAMES["정보제공"][1])], "summary": "안내를 제안합니다."}
    r = validate(json.dumps(out, ensure_ascii=False), i)
    assert not r["ok"] and {f[0] for f in r["failures"] if f[3] == HARD} & {"R2", "M3"}


def test_prompt_rules_and_version():
    assert PROMPT_VERSION == "v3.4" and "__FRAMES__" not in SYSTEM_PROMPT
    for rule in ("[문구 틀]", "N1.", "N2.", "message_facts", "▶", "T1.", "D1.", "D2.", "M9."):
        assert rule in SYSTEM_PROMPT, rule


def test_user_message_excludes_internal_identifiers_and_facts():
    c = mk("정보제공"); i = inp(c)
    i["subscription"].update(subscription_id="S000101", ad_consent_yn="Y", start_date="2022-01-01")
    i["customer"] = {"customer_id": "C000412", "customer_name": "박서연"}
    i["facts"] = {"voc_fee_or_cancel_cnt_30d": 7}
    i["message_facts"] = {"remaining_care_cnt": 1}
    msg = build_user_message(i)
    for s in ("S000101", "C000412", "박서연", "voc_fee_or_cancel_cnt_30d", "ad_consent_yn"):
        assert s not in msg, s
    assert "올해 남은 케어" in msg and "1회" in msg
