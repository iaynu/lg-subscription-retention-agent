import copy, json
from agent.validation.validator import validate, HARD
from agent.validation.fallback import full_template
from agent.service import split_buttons

def mk(cat, **over):
    base = {"solution_id": "S-X-A", "type_id": "T01", "category": cat, "name": "테스트 솔루션", "detail": "", "message_hint": "",
            "offer_cost": 0, "fee_impact": "없음", "ad_yn": "N", "internal_forward_yn": "N", "delivery_kind": "고객 메시지", "forward_dept": None}
    base.update(over); return base

def inp(c):
    return {"mode": "ACTION", "subscription": {"monthly_fee": 50000, "contract_months": 60, "days_to_end": 100},
            "prediction": {"risk_level": "HIGH", "risk_rank_pct": 0.05, "churn_probability": 0.6},
            "types": [{"type_rank": 1, "type_id": "T01", "type_name": "x", "contribution_share": 1.0,
                       "top_features": [{"feature": "f1", "label": "피처", "value": 3, "shap": 0.1}]}],
            "context": {}, "candidates": [c], "constraints": {"max_actions": 1, "banned_phrases_extra": [], "config_numbers": []}}

def item(c, **o):
    d = {"priority": 1, "solution_id": c["solution_id"], "type_id": "T01", "selection_reason": "반복 문의가 있어 안내가 적절합니다.",
         "evidence": [{"feature": "f1", "value": 3}], "title": "안내드립니다", "message_text": "고객님, 안내드립니다. 자세한 내용은 앱 또는 상담으로 확인하실 수 있습니다.", "internal_memo": ""}
    d.update(o); return d

def run(c, **o):
    i = inp(c); out = {"selected": [item(c, **o)], "summary": "안내를 제안합니다."}
    r = validate(json.dumps(out, ensure_ascii=False), i)
    return r["ok"], sorted({f[0] for f in r["failures"] if f[3] == HARD})

def test_plain_ok(): assert run(mk("정보제공"))[0]
def test_ad_title_required_prefix():
    c = mk("혜택제안", ad_yn="Y", channel="친구톡")
    msg = "고객님, 이용 가능한 혜택을 총 비용과 위약금 조건과 함께 안내드립니다. 신청을 원하시면 말씀해 주세요."
    ok, h = run(c, title="혜택 안내", message_text=msg); assert not ok and "T2" in h
    assert run(c, title="(광고) 혜택 안내", message_text=msg)[0]
    ok, h = run(c, title="(광고) 할인 안내", message_text=msg); assert not ok and "M2" in h     # 제목에도 후보에 없는 혜택 표현 금지
def test_nonad_title_must_not_have_ad():
    ok, h = run(mk("정보제공"), title="(광고) 안내"); assert not ok and "T2" in h
def test_title_missing_and_long():
    assert "T1" in run(mk("정보제공"), title="")[1]
    assert "T1" in run(mk("정보제공"), title="가" * 41)[1]
def test_internal_only_requires_memo_and_empty_message():
    c = mk("담당자조치", delivery_kind="내부 전달", internal_forward_yn="Y", forward_dept="운영팀", channel="내부 전달")
    ok, h = run(c, title="", message_text="", internal_memo="케어 미완료 건 확인 요청"); assert ok, h
    assert "D2" in run(c, title="", message_text="", internal_memo="")[1]
    assert "D1" in run(c, title="", message_text="고객님 안내드립니다.", internal_memo="메모")[1]
def test_composite_needs_both():
    c = mk("담당자조치", internal_forward_yn="Y", forward_dept="지점")
    msg = "고객님, 담당자가 연락드릴 수 있도록 안내드립니다. 자세한 내용은 앱 또는 상담으로 확인하실 수 있습니다."
    assert "D2" in run(c, message_text=msg, internal_memo="")[1]
    assert run(c, message_text=msg, internal_memo="지점 책임자 사과 전화 요청")[0]
def test_memo_invented_number_blocked():
    c = mk("담당자조치", delivery_kind="내부 전달", internal_forward_yn="Y", forward_dept="운영팀")
    assert "E3" in run(c, title="", message_text="", internal_memo="미완료 99건 조치 요청")[1]
def test_fee_decrease_rule():
    c = mk("혜택제안", ad_yn="Y", fee_impact="감소")
    bad = "고객님, 이용 가능한 혜택을 총 비용과 위약금 조건과 함께 안내드립니다. 신청을 원하시면 말씀해 주세요."
    ok, h = run(c, title="(광고) 안내", message_text=bad)
    assert not ok and "M5" in h
    good = "고객님, 변경 시 구독료가 낮아질 수 있습니다. 총 비용과 위약금 등 적용 조건은 신청 시 함께 안내드립니다."
    assert run(c, title="(광고) 안내", message_text=good)[0]

def test_template_fallback_passes_for_all_catalog_solutions(catalog):
    for s in catalog:
        if s["solution_id"] == "S-NT-A" or s.get("agent_selectable_yn") != "Y": continue
        i = inp(dict(s, offer_cost=s["offer_cost"]))
        i["types"][0]["type_id"] = s["type_id"]
        out = full_template(i)
        r = validate(json.dumps(out, ensure_ascii=False), i)
        assert r["ok"], (s["solution_id"], [f[:3] for f in r["failures"] if f[3] == HARD])

def test_split_buttons():
    assert split_buttons("[할인 신청하기] / [조건 보기]") == ["할인 신청하기", "조건 보기"]
