"""PG16 통합 테스트. TEST_DATABASE_URL이 있을 때만 실행(scripts/pg_test.sh가 임시 클러스터를 띄워 준다)."""
import os, pathlib
import pytest
from sqlalchemy import create_engine, text
from agent.config import load_config
from agent.llm.mock import RuleBasedMock
from agent.repo.sql import SqlRepo
from agent.service import AgentService

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL 없음")
from .conftest import pg_reset

@pytest.fixture(scope="module")
def engine():
    e = create_engine(URL, future=True)
    pg_reset(e)
    with e.begin() as c:
        c.execute(text("INSERT INTO product_profile VALUES ('정수기','렌탈형',30000,50000,60,'Y',4,0.01,1,0.12)"))
        c.execute(text("INSERT INTO customer VALUES ('C1','가상','30대','서울','2024-01-01','Y','Y','Y','Y')"))
        c.execute(text("""INSERT INTO subscription (subscription_id,customer_id,product_category,model_name,start_date,contract_months,mandatory_months,end_date,monthly_fee,care_type,care_auto_renew_yn)
                          VALUES ('S000101','C1','정수기','M1','2024-01-01',60,36,'2029-01-01',52000,'방문관리','Y')"""))
    return e

def test_seed_has_priority_and_eligibility(engine):
    with engine.connect() as c:
        r = c.execute(text("SELECT priority, eligibility FROM solution_catalog WHERE solution_id='S-T01-C'")).one()
    assert r.priority == 10 and "unapplied_discount_yn" in r.eligibility

def test_end_to_end_with_sql(engine, catalog, demos):
    repo = SqlRepo(URL, engine); svc = AgentService(load_config(), catalog, RuleBasedMock(), repo)
    ai = next(d for d in demos if d["agent_input"]["subscription"]["subscription_id"] == "S000101")["agent_input"]
    res = svc.run(ai); first = res["selected"][0]
    row = svc.decide(ai["request_id"], "S000101", first["solution_id"], "조치완료", by="tester")
    assert row["action_id"] > 0
    rec = repo.recent_actions("S000101", "2026-09-30")
    assert [a["solution_id"] for a in rec] == [first["solution_id"]] and rec[0]["created_at"] == "2026-09-30"
    with pytest.raises(ValueError): svc.run(ai)                 # 같은 request_id 재실행은 거부(결과 덮어쓰기 방지)
    ai2 = dict(ai, request_id="REQ-DEMO-D1-B", recent_actions=rec); res2 = svc.run(ai2)
    assert first["solution_id"] not in [s["solution_id"] for s in res2["selected"]]
    svc.decide(ai["request_id"], "S000101", res["selected"][1]["solution_id"], "반려")
    rec = repo.recent_actions("S000101", "2026-09-30")
    assert len(rec) == 2 and {a["action_status"] for a in rec} == {"조치완료", "반려"}
    with pytest.raises(Exception):   # CHECK 제약: 허용되지 않은 상태
        repo.log_action(dict(row, action_status="보류"))

def test_forward_rows_in_pg(engine, catalog):
    from .conftest import base_input
    repo = SqlRepo(URL, engine); svc = AgentService(load_config(), catalog, RuleBasedMock(), repo)
    ai = base_input([("T08", 1.0)], facts={"sales_voc_cnt_30d": 2}); ai["subscription"]["subscription_id"] = "S000101"; ai["request_id"] = "REQ-FWD"
    r = svc.run(ai); fw = r["forward"]
    rows = svc.forward("REQ-FWD", "S000101", [i["solution_id"] for i in fw["items"]], ["CARE", "CS"], fw["draft_text"] + "\n추가")
    got = [a for a in repo.list_actions("S000101") if a["action_batch_id"] == "REQ-FWD"]
    assert len(got) == len(rows) and got[0]["forward_depts"] == "CARE,CS" and got[0]["memo_edited_yn"] == "Y" and got[0]["channel"] == "내부 전달"

def test_result_survives_restart(engine, catalog):
    from .conftest import base_input
    ai = base_input([("T02", 1.0)]); ai["subscription"]["subscription_id"] = "S000101"; ai["request_id"] = "REQ-PERSIST"
    ai["types"][0]["top_features"] = [{"feature": "voc_penalty_cnt_90d", "label": "최근 90일 위약금 문의 횟수", "value": 4, "shap": 0.4}]
    AgentService(load_config(), catalog, RuleBasedMock(), SqlRepo(URL, engine)).run(ai)
    svc2 = AgentService(load_config(), catalog, RuleBasedMock(), SqlRepo(URL, create_engine(URL, future=True)))   # 서버 재시작과 같은 상태
    sid = svc2.repo.get_result("REQ-PERSIST")["selected"][0]["solution_id"]
    assert svc2.decide("REQ-PERSIST", "S000101", sid, "조치완료")["action_id"] > 0
    with engine.connect() as c:
        r = c.execute(text("SELECT outcome, prompt_version, llm_provider FROM agent_result WHERE request_id='REQ-PERSIST'")).one()
    assert r.outcome == "pass" and r.prompt_version and r.llm_provider == "mock"

def test_no_fk_constraints_and_orphan_check(engine):
    from pathlib import Path
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM pg_constraint WHERE contype = 'f'")).scalar_one() == 0
    sql = (Path(__file__).resolve().parents[1] / "sql" / "check_integrity.sql").read_text(encoding="utf-8")
    def orphans():
        with engine.connect() as c: return {r.relation: r.orphans for r in c.execute(text(sql))}
    assert len(orphans()) == 29
    repo = SqlRepo(URL, engine)
    repo.log_action(dict(action_batch_id="NO-SUCH", subscription_id="S000101", type_id="T02", solution_id="S-T02-A", action_category="정보제공",
                         offer_cost=0, action_status="조치완료", channel="알림톡", created_at="2026-09-30T09:00:00"))   # FK가 없으니 들어간다
    assert orphans()["action_log.action_batch_id -> agent_result.request_id"] == 1                                   # 대신 검사 쿼리가 잡는다
    with engine.begin() as c: c.execute(text("DELETE FROM action_log WHERE action_batch_id = 'NO-SUCH'"))
    assert orphans()["action_log.action_batch_id -> agent_result.request_id"] == 0

def test_service_blocks_orphans_at_app_level(engine, catalog):
    svc = AgentService(load_config(), catalog, RuleBasedMock(), SqlRepo(URL, engine))
    with pytest.raises(KeyError): svc.decide("NO-SUCH", "S000101", "S-T02-A", "조치완료")
