import pytest
from fastapi.testclient import TestClient
from agent.api import create_app
from agent.config import load_config
from agent.llm.mock import RuleBasedMock
from agent.repo.memory import MemoryRepo
from agent.service import AgentService
from agent.sources import DemoSource
from .conftest import base_input

def inp():
    ai = base_input([("T09", 0.6), ("T10", 0.4)], facts={"same_symptom_cnt": 3, "first_response_unresolved_yn": "Y"})
    ai["subscription"].update(product_category="건조기")
    ai["prediction"]["churn_probability"] = 0.91
    ai["customer"] = {"customer_id": "C000412", "customer_name": "박서연"}
    ai["context"] = {"persona_quote": "프로모션이 끝나고 월 요금이 오르니 다시 계산해 봐야겠어요."}
    ai["types"][0].update(type_name="제품 고장·성능 불만형", top_features=[{"feature": "same_symptom_cnt", "label": "동일 증상 반복 횟수", "value": 3, "shap": 0.21}])
    return ai

@pytest.fixture
def svc(catalog): return AgentService(load_config(), catalog, RuleBasedMock(), MemoryRepo())

def test_forward_draft_structure(svc):
    r = svc.run(inp()); fw = r["forward"]
    assert fw and fw["items"][0]["role"] == "요청 조치" and all(i["role"] == "보조 조치" for i in fw["items"][1:])
    t = fw["draft_text"]
    assert t.startswith("[협업 요청] C000412 박서연 고객 (건조기 · 위험도 91%)\n수신: ") and "■ 처리 기한: 영업일 3일 이내, 완료 후 회신 부탁드립니다" in t
    assert fw["departments"], fw   # 카탈로그 부서명이 마스터에 매핑됨

def test_analysis_block_for_basis_screen(svc):
    a = svc.run(inp())["analysis"]
    assert a["risk_percent"] == 91 and a["types"][0]["share_percent"] == 60 and a["types"][0]["features"][0]["shap"] == 0.21
    assert a["persona_quote"].startswith("프로모션")

def test_customer_name_never_reaches_llm(catalog):
    seen = []
    class Spy(RuleBasedMock):
        def generate(self, i, note, timeout):
            seen.append(str(i)); return super().generate(i, note, timeout)
    AgentService(load_config(), catalog, Spy(), MemoryRepo()).run(inp())
    assert seen and not any("박서연" in s or "C000412" in s for s in seen)

def test_unmapped_dept_reported(svc):
    ai = base_input([("T08", 1.0)], facts={"sales_voc_cnt_30d": 2}); r = svc.run(ai)
    assert r["forward"] and "영업관리팀" in r["forward"]["unmapped"]

def test_forward_confirm_logs_each_item_with_edit_flag(svc):
    r = svc.run(inp()); fw = r["forward"]; sids = [i["solution_id"] for i in fw["items"]]
    rows = svc.forward(r["request_id"], "S1", sids, ["CS", "PRICING"], fw["draft_text"] + "\n추가 요청")
    assert len(rows) == len(sids) and all(x["channel"] == "내부 전달" and x["memo_edited_yn"] == "Y" and x["forward_depts"] == "CS,PRICING" for x in rows)
    with pytest.raises(ValueError): svc.forward(r["request_id"], "S1", sids, ["CS"], "x")        # 중복 전달
    r2 = svc.run(dict(inp(), request_id="T2"))
    same = svc.forward("T2", "S1", sids[:1], ["CS"], r2["forward"]["draft_text"])
    assert same[0]["memo_edited_yn"] == "N"

def test_forward_validation(svc):
    r = svc.run(inp()); sids = [i["solution_id"] for i in r["forward"]["items"]]
    for args in ((["NOPE"], ["CS"], "x"), (sids, [], "x"), (sids, ["ZZZ"], "x"), (sids, ["CS"], "  ")):
        with pytest.raises(ValueError): svc.forward(r["request_id"], "S1", *args)
    with pytest.raises(ValueError): svc.forward(r["request_id"], "OTHER", sids, ["CS"], "x")

def test_pure_internal_item_rejected_by_decision_endpoint(svc):
    r = svc.run(base_input([("T08", 1.0)], facts={"sales_voc_cnt_30d": 2})); pure = next(s for s in r["selected"] if svc.by_id[s["solution_id"]]["delivery_kind"] == "내부 전달")
    with pytest.raises(ValueError): svc.decide(r["request_id"], "S1", pure["solution_id"], "조치완료")
    assert pure["title"] == "" and pure["message_text"] == "" and pure["internal_memo"]

def test_api_forward_and_departments(catalog):
    c = TestClient(create_app(AgentService(load_config(), catalog, RuleBasedMock(), MemoryRepo()), DemoSource()))
    assert len(c.get("/departments").json()) == 8
    r = c.post("/agent/run-input", json=inp()).json()
    assert r["forward"]["items"]
    body = dict(request_id=r["request_id"], subscription_id="S1", solution_ids=[i["solution_id"] for i in r["forward"]["items"]],
                departments=["CS"], text=r["forward"]["draft_text"])
    assert c.post("/agent/forward", json=body).status_code == 200
    assert c.post("/agent/forward", json=body).status_code == 409

def test_status_labels_and_cooldown_starts_at_click(svc):
    from agent.service import status_label
    r = svc.run(base_input([("T08", 1.0)], facts={"sales_voc_cnt_30d": 2})); fw = r["forward"]
    rows = svc.forward(r["request_id"], "S1", [fw["items"][0]["solution_id"]], ["CARE"], fw["draft_text"], now="2026-09-30T10:00:00")
    assert rows[0]["status_label"] == "부서 전달 완료"
    assert status_label(dict(action_status="조치완료", channel="알림톡")) == "발송 완료"
    assert status_label(dict(action_status="반려", channel="알림톡")) == "반려"
    # 전달 클릭(조치완료 기록) 직후부터 같은 솔루션은 14일 쿨다운
    again = svc.run(dict(base_input([("T08", 1.0)], facts={"sales_voc_cnt_30d": 2}), request_id="T9", recent_actions=svc.repo.recent_actions("S1", "2026-09-30")))
    assert any(e["solution_id"] == fw["items"][0]["solution_id"] and e["reason"] == "COOLDOWN_SOLUTION" for e in again["excluded"])
