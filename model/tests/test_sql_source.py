"""DB → Agent 종단 시험(PostgreSQL). 샘플 데이터(agent/sample_data.py)를 적재하고 SqlSource + SqlRepo + REST API로 끝까지 돌린다.
TEST_DATABASE_URL이 있을 때만 실행(scripts/pg_test.sh)."""
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from agent.api import create_app
from agent.config import ROOT, load_config
from agent.llm.mock import RuleBasedMock
from agent.repo.sql import SqlRepo
from agent.sample_data import load_sample
from agent.service import AgentService
from agent.sources import SqlSource
from .conftest import pg_reset

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL 없음")


@pytest.fixture(scope="module")
def env(catalog):
    e = create_engine(URL, future=True)
    pg_reset(e); load_sample(e)
    cfg = load_config()
    svc = AgentService(cfg, catalog, RuleBasedMock(), SqlRepo(None, e))
    src = SqlSource(engine=e, facts_cfg=cfg["facts"])
    return e, src, TestClient(create_app(svc, src))


def ids(r): return [s["solution_id"] for s in r["selected"]]
def why(r): return {x["solution_id"]: x["reason"] for x in r["excluded"]}


def test_list_is_sorted_by_risk_rank(env):
    _, _, c = env
    rows = c.get("/subscriptions").json()
    assert [r["subscription_id"] for r in rows] == ["S100001", "S100003", "S100002", "S100007", "S100008", "S100004", "S100005", "S100006"]
    assert rows[0]["type_ids"] == "T04,T01" and rows[0]["risk_level"] == "HIGH"


def test_facts_computed_from_schema(env):
    _, src, _ = env
    ai = src.get_input("S100001")
    f, m = ai["facts"], ai["message_facts"]
    assert ai["as_of_date"] == "2026-09-30" and ai["customer"]["ad_consent_yn"] == "Y"
    assert f["remaining_care_cnt"] == 1 and f["days_to_contract_year_end"] == 46 and f["unapplied_discount_yn"] == "Y"
    assert f["days_since_last_use"] == 59 and f["voc_fee_or_cancel_cnt_30d"] == 1 and f["days_to_promo_end"] is None
    assert m["remaining_care_cnt"] == 1 and m["contract_year_end_date"] == "2026-11-14" and m["paid_total"] == 39900 * 12
    assert ai["types"][0]["top_features"][0]["label"] == "최근 12주 중 무가동 주 수"
    f3 = src.get_input("S100003")["facts"]
    assert f3["open_as_yn"] == "Y" and f3["first_response_unresolved_yn"] == "Y" and f3["same_symptom_cnt"] == 2
    assert f3["expected_as_days"] == 9                            # 접수 9/28 → 예상 완료 10/7
    assert f3["care_inspection_anomaly_yn"] == "N" and f3["manager_change_confirmed_yn"] == "N"
    f8, f4 = src.get_input("S100008")["facts"], src.get_input("S100004")["facts"]
    assert f8["care_inspection_anomaly_yn"] == "Y" and f4["manager_change_confirmed_yn"] == "Y"
    assert src.get_input("S100001")["facts"]["expected_as_days"] is None   # 열린 A/S 없음 → 값 없음
    assert src.get_input("S100002")["facts"]["missed_or_late_visit_cnt_30d"] == 1


def test_end_to_end_paths(env):
    _, _, c = env
    r1 = c.post("/agent/run", json={"subscription_id": "S100001"}).json()
    assert r1["mode"] == "ACTION" and set(ids(r1)) == {"S-T04-C", "S-T01-C"}
    care = next(s for s in r1["selected"] if s["solution_id"] == "S-T04-C")
    assert "▶ 올해 남은 케어: 1회" in care["message_text"] and "2026년 11월 14일" in care["message_text"]
    assert {m["channel"] for m in r1["messages"]} == {"알림톡", "친구톡"}                  # 광고성과 정보성은 따로
    assert why(r1)["S-T01-B"] == "NOT_ELIGIBLE" and "S-T04-D" not in why(r1)      # 유형요약에 없어 비활성(active_yn=N): 판정 대상 아님

    r2 = c.post("/agent/run", json={"subscription_id": "S100002"}).json()
    assert set(ids(r2)) == {"S-T06-C", "S-T07-B"} and len(r2["messages"]) == 1 and r2["messages"][0]["merged"]
    assert r2["forward"] and r2["forward"]["departments"] == ["CARE"]
    assert why(r2)["S-T07-D"] == "NOT_ELIGIBLE"                   # 매니저 교체 확정 값 없음 → 교체 안내 안 함

    r3 = c.post("/agent/run", json={"subscription_id": "S100003"}).json()
    assert "S-T10-D" in ids(r3) and "S-T10-C" in ids(r3)           # 예상 수리 9일 ≥ 7 → 대체품 안내 포함
    assert r3["forward"]["draft_text"].startswith("[협업 요청] C100003 박서준 고객 (세탁기 · 위험도 79%)")
    assert r3["forward"]["departments"] == ["CS", "PARTS"]      # 대체품 지원 요청은 물류·A/S 부서로도 전달

    r4 = c.post("/agent/run", json={"subscription_id": "S100004"}).json()
    assert ids(r4) == ["S-T07-A", "S-T07-B"] and why(r4)["S-T07-D"] == "DUP_CATEGORY"   # 교체 확정 → 후보가 되지만 같은 카테고리 상위안에 밀림
    assert "▶ 최근 방문일: 2026년 9월 27일" in next(x for x in r4["selected"] if x["solution_id"] == "S-T07-A")["message_text"]
    r8 = c.post("/agent/run", json={"subscription_id": "S100008"}).json()
    assert ids(r8)[0] == "S-T09-C" and r8["forward"] and r8["forward"]["departments"] == ["QUALITY"]        # 점검 이상 → 안내 + A/S 부서 전달

    r5 = c.post("/agent/run", json={"subscription_id": "S100005"}).json()
    assert r5["mode"] == "NO_TYPE" and ids(r5) == ["S-NT-A"] and r5["outcome"] == "pass"
    r6 = c.post("/agent/run", json={"subscription_id": "S100006"}).json()
    assert r6["mode"] == "SURVEY_ONLY" and r6["outcome"] == "survey_template"
    r7 = c.post("/agent/run", json={"subscription_id": "S100007"}).json()
    assert r7["fallback"] == "NO_ELIGIBLE_CANDIDATE" and ids(r7) == ["S-NT-A"] and r7["uncovered_types"] == ["T08"]


def test_decisions_persist_cooldown_and_integrity(env):
    e, _, c = env
    r = c.post("/agent/run", json={"subscription_id": "S100001"}).json()
    assert c.post("/agent/decision", json={"request_id": r["request_id"], "subscription_id": "S100001",
                                           "solution_id": "S-T04-C", "status": "조치완료", "by": "상담자A"}).status_code == 200
    r3 = c.post("/agent/run", json={"subscription_id": "S100003"}).json()
    fw = c.post("/agent/forward", json=dict(request_id=r3["request_id"], subscription_id="S100003", solution_ids=["S-T10-C"],
                                            departments=["CS"], text=r3["forward"]["draft_text"]))
    assert fw.status_code == 200 and fw.json()[0]["status_label"] == "부서 전달 완료"
    again = c.post("/agent/run", json={"subscription_id": "S100001"}).json()
    assert "S-T04-C" not in ids(again) and why(again)["S-T04-C"] == "COOLDOWN_SOLUTION"
    sql = (ROOT / "sql" / "check_integrity.sql").read_text(encoding="utf-8")
    with e.connect() as conn:
        orphans = {row.relation: row.orphans for row in conn.execute(text(sql))}
        n_results = conn.execute(text("SELECT count(*) FROM agent_result")).scalar_one()
        meta = conn.execute(text("SELECT llm_provider, prompt_version FROM agent_result LIMIT 1")).one()
    assert all(v == 0 for v in orphans.values()), {k: v for k, v in orphans.items() if v}
    assert n_results >= 8 and meta.llm_provider == "mock" and meta.prompt_version == "v3.4"
