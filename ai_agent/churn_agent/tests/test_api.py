import json
import pytest
from fastapi.testclient import TestClient
from agent.api import create_app
from agent.config import load_config
from agent.llm.mock import RuleBasedMock
from agent.repo.memory import MemoryRepo
from agent.service import AgentService
from agent.sources import DemoSource

@pytest.fixture
def client(catalog):
    svc = AgentService(load_config(), catalog, RuleBasedMock(), MemoryRepo())
    return TestClient(create_app(svc, DemoSource()))

def test_health_and_lists(client):
    assert client.get("/health").json()["llm"] == "mock"
    assert len(client.get("/subscriptions").json()) == 8
    assert all(s["type_id"] == "T03" for s in client.get("/catalog", params={"type_id": "T03"}).json())

def test_run_decide_then_cooldown(client):
    r = client.post("/agent/run", json={"subscription_id": "S000101"}).json()
    assert r["mode"] == "ACTION" and r["selected"]
    first = r["selected"][0]
    assert "title" in first and "buttons" in first
    d = client.post("/agent/decision", json={"request_id": r["request_id"], "subscription_id": "S000101",
                                             "solution_id": first["solution_id"], "status": "조치완료"})
    assert d.status_code == 200
    # 같은 항목 재결정은 409
    assert client.post("/agent/decision", json={"request_id": r["request_id"], "subscription_id": "S000101",
                                                "solution_id": first["solution_id"], "status": "반려"}).status_code == 409
    assert len(client.get("/subscriptions/S000101/actions").json()) == 1
    r2 = client.post("/agent/run", json={"subscription_id": "S000101"}).json()
    assert first["solution_id"] not in [s["solution_id"] for s in r2["selected"]]
    assert any(e["solution_id"] == first["solution_id"] and e["reason"] == "COOLDOWN_SOLUTION" for e in r2["excluded"])

def test_rejected_does_not_cooldown(client):
    r = client.post("/agent/run", json={"subscription_id": "S000101"}).json()
    first = r["selected"][0]["solution_id"]
    client.post("/agent/decision", json={"request_id": r["request_id"], "subscription_id": "S000101", "solution_id": first, "status": "반려"})
    r2 = client.post("/agent/run", json={"subscription_id": "S000101"}).json()
    assert first in [s["solution_id"] for s in r2["selected"]]

def test_errors(client):
    assert client.post("/agent/run", json={"subscription_id": "NOPE"}).status_code == 404
    assert client.post("/agent/decision", json={"request_id": "X", "subscription_id": "S000101", "solution_id": "S-T01-A", "status": "조치완료"}).status_code == 404
    r = client.post("/agent/run", json={"subscription_id": "S000101"}).json()
    assert client.post("/agent/decision", json={"request_id": r["request_id"], "subscription_id": "S000101", "solution_id": "S-T09-D", "status": "조치완료"}).status_code == 409
    assert client.post("/agent/decision", json={"request_id": r["request_id"], "subscription_id": "S000101", "solution_id": "S-T01-A", "status": "보류"}).status_code == 422

def test_run_input_endpoint(client, demos):
    r = client.post("/agent/run-input", json=demos[1]["agent_input"])
    assert r.status_code == 200 and r.json()["mode"] in ("ACTION", "NO_TYPE", "SURVEY_ONLY", "NONE")


# ---------- 2026-10-08 점검 반영 ----------
def test_each_run_gets_new_request_id_and_rerun_decision_works(client):
    r1 = client.post("/agent/run", json={"subscription_id": "S000101"}).json()
    r2 = client.post("/agent/run", json={"subscription_id": "S000101"}).json()
    assert r1["request_id"] != r2["request_id"] and r1["request_id"].startswith("REQ-") and len(r1["request_id"]) <= 20
    sid = r1["selected"][0]["solution_id"]
    assert client.post("/agent/decision", json={"request_id": r1["request_id"], "subscription_id": "S000101", "solution_id": sid, "status": "반려"}).status_code == 200
    r3 = client.post("/agent/run", json={"subscription_id": "S000101"}).json()
    assert sid in [s["solution_id"] for s in r3["selected"]]            # 반려는 쿨다운 대상이 아님
    assert client.post("/agent/decision", json={"request_id": r3["request_id"], "subscription_id": "S000101", "solution_id": sid, "status": "조치완료"}).status_code == 200


def test_run_input_duplicate_request_id_is_409(client, demos):
    body = dict(demos[0]["agent_input"], request_id="REQ-FIXED-1")
    assert client.post("/agent/run-input", json=body).status_code == 200
    assert client.post("/agent/run-input", json=body).status_code == 409


def test_composite_forward_then_customer_message(client, demos):
    # 점검 이상 소견이 확인된 고객 → S-T09-C(알림톡 + A/S 부서 전달, 복합)
    body = json.loads(json.dumps(demos[6]["agent_input"])); body.pop("request_id")
    body["facts"]["care_inspection_anomaly_yn"] = "Y"
    r = client.post("/agent/run-input", json=body).json()
    comp = [i["solution_id"] for i in (r["forward"] or {}).get("items", [])
            if i["solution_id"] in [s["solution_id"] for s in r["selected"] if s["message_text"]]]
    assert comp, r["selected"]
    sid = comp[0]
    assert "QUALITY" in r["forward"]["departments"] and r["forward"]["unmapped"] == []   # "A/S 부서" → 품질팀으로 매핑(미매핑 경로는 test_forward가 검증). S-T09-C가 정보제공이 되어 담당자조치 S-T10-C(CS)도 함께 선택될 수 있다
    assert client.post("/agent/forward", json=dict(request_id=r["request_id"], subscription_id="S000107", solution_ids=[sid],
                                                   departments=["CS"], text=r["forward"]["draft_text"])).status_code == 200
    d = client.post("/agent/decision", json={"request_id": r["request_id"], "subscription_id": "S000107", "solution_id": sid, "status": "조치완료"})
    assert d.status_code == 200 and d.json()["status_label"] == "발송 완료"
    labels = {a["status_label"] for a in client.get("/subscriptions/S000107/actions").json()}
    assert {"부서 전달 완료", "발송 완료"} <= labels


def test_survey_only_respects_cooldown_over_api(client):
    r = client.post("/agent/run", json={"subscription_id": "S000106"}).json()
    assert r["mode"] == "SURVEY_ONLY" and r["outcome"] == "survey_template" and r["selected"][0]["buttons"] == ["설문 참여하기"]
    assert client.post("/agent/decision", json={"request_id": r["request_id"], "subscription_id": "S000106", "solution_id": "S-NT-A", "status": "조치완료"}).status_code == 200
    r2 = client.post("/agent/run", json={"subscription_id": "S000106"}).json()
    assert r2["outcome"] == "no_action" and any(e["solution_id"] == "S-NT-A" and e["reason"] == "COOLDOWN_SOLUTION" for e in r2["excluded"])


def test_input_validation_returns_422(client, demos):
    base = demos[0]["agent_input"]
    bad_date = dict(base, as_of_date="2026/09/30")
    no_fee = dict(base, subscription={k: v for k, v in base["subscription"].items() if k != "monthly_fee"})
    for body in (bad_date, no_fee, dict(base, as_of_date="yesterday")):
        assert client.post("/agent/run-input", json=body).status_code == 422
    assert client.post("/agent/run", json={"subscription_id": "S000101", "as_of_date": "2026/09/30"}).status_code == 422
    assert client.post("/agent/run", json={"subscription_id": "S000101", "as_of_date": "2026-09-30T00:00:00"}).status_code == 200
    with_time = dict(base, request_id="", recent_actions=[{"solution_id": "S-T01-A", "action_status": "조치완료", "offer_cost": 0,
                                                            "created_at": "2026-09-25T10:00:00"}])
    assert client.post("/agent/run-input", json=with_time).status_code == 200
    r = client.post("/agent/run", json={"subscription_id": "S000107"}).json()
    fw = dict(request_id=r["request_id"], subscription_id="S000107", solution_ids=[r["forward"]["items"][0]["solution_id"]],
              departments=["CS", "CS"], text="x")
    assert client.post("/agent/forward", json=fw).status_code == 422
    assert client.post("/agent/forward", json=dict(fw, departments=["CS"], text="가" * 1001)).status_code == 422


def test_404_detail_is_plain_and_note_is_stored(client):
    d = client.post("/agent/decision", json={"request_id": "NOPE", "subscription_id": "S000101", "solution_id": "S-T01-A", "status": "반려"})
    assert d.status_code == 404 and d.json()["detail"] == "request_id를 찾을 수 없음"
    r = client.post("/agent/run", json={"subscription_id": "S000101"}).json()
    sid = r["selected"][0]["solution_id"]
    row = client.post("/agent/decision", json={"request_id": r["request_id"], "subscription_id": "S000101", "solution_id": sid,
                                               "status": "반려", "note": "고객이 통화 중 거절"}).json()
    assert row["decision_note"] == "고객이 통화 중 거절" and row["created_at"].startswith("2026-09-30T")


def test_health_and_meta_show_llm_status(client):
    h = client.get("/health").json()
    assert h["llm_status"] == "mock" and h["source"] == "DemoSource"
    r = client.post("/agent/run", json={"subscription_id": "S000101"}).json()
    assert r["meta"]["llm_provider"] == "mock" and r["meta"]["prompt_version"] == "v3.4"


def test_demo_messages_use_customer_titles_not_internal_names(client, catalog):
    names = {c["solution_id"]: c["name"] for c in catalog}
    for sid in ("S000101", "S000102", "S000104", "S000107"):
        r = client.post("/agent/run", json={"subscription_id": sid}).json()
        for s in r["selected"]:
            if s["message_text"]:
                assert names[s["solution_id"]] not in s["message_text"], (sid, s["solution_id"])
