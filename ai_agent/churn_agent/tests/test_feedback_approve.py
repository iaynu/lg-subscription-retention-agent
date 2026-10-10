import json, os, pytest
from feedback import approve as A, candidates as C

TYPES = [{"type_id": f"T{i:02d}", "type_name": f"기존유형{i}"} for i in range(1, 11)]
SOLS = [{"solution_id": "S-T01-A", "seq_no": 1, "type_id": "T01"}, {"solution_id": "S-T01-B", "seq_no": 2, "type_id": "T01"}, {"solution_id": "S-NT-A", "seq_no": 0, "type_id": "유형없음"}]
SOL = {"title": "가입 범위 재안내", "category": "정보제공", "message_hint": "포함·제외 범위를 한 장으로 다시 알려드립니다."}


def prop(**kw):
    p = {"proposal_id": 1, "proposal_kind": "new_type", "related_type_id": None, "title": "가입 설명 불일치형", "description": "가입 때 들은 범위와 실제가 달라 실망",
         "status": "검토중", "evidence": {"solutions": [SOL, {"title": "상담 기록 문자 제공", "category": "담당자조치", "message_hint": "상담 내용을 문자로 남깁니다."}]}}
    p.update(kw); return p


def test_new_type_plan_ids_defaults_and_inactive():
    pl = A.plan(prop(), TYPES, SOLS)
    assert pl["type_row"] == {"type_id": "T11", "type_name": "가입 설명 불일치형", "description": "가입 때 들은 범위와 실제가 달라 실망"}
    a, b = pl["solution_rows"]
    assert (a["solution_id"], b["solution_id"]) == ("S-T11-A", "S-T11-B") and (a["seq_no"], b["seq_no"]) == (3, 4)               # seq는 기존 최대+1부터
    assert a["channel"] == "알림톡" and a["delivery_kind"] == "고객 메시지" and a["customer_title"] == "가입 범위 재안내"
    assert b["channel"] == "내부 전달" and b["internal_forward_yn"] == "Y" and b["customer_title"] is None and b["priority"] == 40
    assert all(r["active_yn"] == "N" and r["agent_selectable_yn"] == "N" and r["new_policy_yn"] == "Y" and r["eligibility"] is None for r in pl["solution_rows"])


def test_new_solution_attaches_next_letter():
    pl = A.plan(prop(proposal_kind="new_solution", related_type_id="T01", evidence={"solutions": [SOL]}), TYPES, SOLS)
    assert pl["type_row"] is None and pl["type_id"] == "T01" and pl["solution_rows"][0]["solution_id"] == "S-T01-C"


def test_type_id_skips_to_max_plus_one():
    t = TYPES + [{"type_id": "T11", "type_name": "x11"}, {"type_id": "유형없음", "type_name": "없음"}]
    assert A.plan(prop(), t, SOLS)["type_id"] == "T12"


@pytest.mark.parametrize("mut,frag", [
    (dict(status="채택"), "이미 처리"), (dict(title="미분류 패턴(자동): 동기=default · 기대=below"), "자동 이름"), (dict(title="기존유형3"), "기존 유형 이름"),
    (dict(title="가"), "2~60자"), (dict(title="T05와 비슷한 유형"), "T##"), (dict(description="가" * 301), "300"), (dict(proposal_kind="x"), "kind"),
    (dict(evidence={}), "솔루션이 하나도"), (dict(proposal_kind="new_solution", related_type_id="T77"), "기존 유형이 없음"),
])
def test_plan_rejects(mut, frag):
    with pytest.raises(A.ApproveError, match=frag): A.plan(prop(**mut), TYPES, SOLS)


def test_plan_rejects_bad_solution_fields():
    for bad in ({**SOL, "category": "쿠폰"}, {**SOL, "title": ""}, {**SOL, "message_hint": "가" * 301}):
        with pytest.raises(A.ApproveError): A.plan(prop(evidence={"solutions": [bad]}), TYPES, SOLS)


def test_overrides_replace_name_description_and_solutions():
    pl = A.plan(prop(title="미분류 패턴(자동): x"), TYPES, SOLS, {"type_name": "가입 안내 불일치형", "description": "수정한 설명", "solutions": [SOL]})
    assert pl["type_row"]["type_name"] == "가입 안내 불일치형" and len(pl["solution_rows"]) == 1


def test_letters_exhausted():
    many = [{"solution_id": f"S-T01-{c}", "seq_no": i, "type_id": "T01"} for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")]
    with pytest.raises(A.ApproveError, match="알파벳"): A.plan(prop(proposal_kind="new_solution", related_type_id="T01", evidence={"solutions": [SOL]}), TYPES, many)


def test_candidate_solutions_fit_approve_categories():
    assert set(A._DEFAULTS) == set(C.ALLOWED_CATEGORIES)


# ---------- PostgreSQL
URL = os.environ.get("TEST_DATABASE_URL")
pg = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL 없음")


def _engine():
    from sqlalchemy import create_engine
    from .conftest import pg_reset
    e = create_engine(URL, future=True); pg_reset(e); return e


def _seed(e, **kw):
    from sqlalchemy import text
    ev = {"source": "llm", "response_ids": list(range(12)), "solutions": [SOL, {"title": "상담 기록 문자 제공", "category": "담당자조치", "message_hint": "상담 내용을 문자로 남깁니다."}]}
    row = dict(k="new_type", rt=None, t="가입 설명 불일치형", d="가입 때 들은 범위와 실제가 달라 실망", ev=json.dumps(ev, ensure_ascii=False)); row.update(kw)
    with e.begin() as c:
        return c.execute(text("INSERT INTO ai_proposal (proposal_kind, related_type_id, title, description, evidence_json) VALUES (:k, :rt, :t, :d, CAST(:ev AS JSONB)) RETURNING proposal_id"), row).scalar()


@pg
def test_pg_approve_new_type_end_to_end():
    from sqlalchemy import text
    e = _engine(); pid = _seed(e)
    dry = A.approve(e, pid, "yj", dry_run=True); assert dry["dry_run"] and dry["type_id"] == "T11"
    with e.connect() as c: assert c.execute(text("SELECT count(*) FROM churn_type WHERE type_id='T11'")).scalar() == 0          # 미리보기는 쓰지 않음
    res = A.approve(e, pid, "yj", note="회의 결정")
    with e.connect() as c:
        assert c.execute(text("SELECT type_name FROM churn_type WHERE type_id='T11'")).scalar() == "가입 설명 불일치형"
        rows = c.execute(text("SELECT solution_id, active_yn, agent_selectable_yn, new_policy_yn FROM solution_catalog WHERE type_id='T11' ORDER BY 1")).all()
        assert rows == [("S-T11-A", "N", "N", "Y"), ("S-T11-B", "N", "N", "Y")]
        st, rb, rt, rv = c.execute(text("SELECT status, reviewed_by, related_type_id, evidence_json->'review' FROM ai_proposal WHERE proposal_id=:i"), {"i": pid}).one()
        assert (st, rb, rt) == ("채택", "yj", "T11") and rv["solution_ids"] == ["S-T11-A", "S-T11-B"] and rv["note"] == "회의 결정"
        assert c.execute(text("SELECT count(*) FROM ai_proposal WHERE evidence_json ? 'response_ids'")).scalar() == 1               # 기존 근거는 유지
    with pytest.raises(A.ApproveError, match="이미 처리"): A.approve(e, pid, "yj")                                                    # 이중 채택 불가
    from pathlib import Path
    raw = e.raw_connection(); cur = raw.cursor(); cur.execute((Path(__file__).resolve().parent.parent / "sql" / "check_integrity.sql").read_text(encoding="utf-8")); raw.close()


@pg
def test_pg_engine_ignores_inactive_new_solutions_then_activate():
    from sqlalchemy import text
    e = _engine(); pid = _seed(e, k="new_solution", rt="T01"); A.approve(e, pid, "yj")
    with e.connect() as c: assert c.execute(text("SELECT count(*) FROM solution_catalog WHERE active_yn='Y' AND agent_selectable_yn='Y' AND new_policy_yn='Y' AND type_id='T01' AND solution_id='S-T01-C'")).scalar() == 0
    with e.connect() as c: assert c.execute(text("SELECT related_type_id FROM ai_proposal WHERE proposal_id=:i"), {"i": pid}).scalar() == "T01"   # 기존 유형은 그대로


@pg
def test_pg_activate_rules():
    from sqlalchemy import text
    e = _engine(); pid = _seed(e); A.approve(e, pid, "yj")
    A.activate_solution(e, "S-T11-A")                                                                                               # 고객 메시지: 부서 불필요
    with pytest.raises(A.ApproveError, match="이미 활성"): A.activate_solution(e, "S-T11-A")
    with pytest.raises(A.ApproveError, match="forward_dept"): A.activate_solution(e, "S-T11-B")                                       # 내부 전달은 부서 필요
    A.activate_solution(e, "S-T11-B", forward_dept="고객경험팀", eligibility=[["voc_cnt", ">=", 2]])
    with e.connect() as c:
        r = c.execute(text("SELECT active_yn, agent_selectable_yn, forward_dept, eligibility FROM solution_catalog WHERE solution_id='S-T11-B'")).one()
        assert r[:3] == ("Y", "Y", "고객경험팀") and json.loads(r[3]) == [["voc_cnt", ">=", 2]]
    with pytest.raises(A.ApproveError, match="이미 활성"): A.activate_solution(e, "S-T01-A")                                            # 기존 활성 솔루션은 건드리지 않음
    with pytest.raises(A.ApproveError, match="없음"): A.activate_solution(e, "S-T99-Z")


@pg
def test_pg_reject_and_list():
    from sqlalchemy import text
    e = _engine(); p1, p2 = _seed(e), _seed(e, t="다른 후보")
    with pytest.raises(A.ApproveError, match="사유"): A.reject(e, p1, "yj", " ")
    A.reject(e, p1, "yj", "기존 유형과 같음")
    with pytest.raises(A.ApproveError, match="이미 처리"): A.reject(e, p1, "yj", "again")
    with pytest.raises(A.ApproveError, match="이미 처리"): A.approve(e, p1, "yj")
    lst = {r["proposal_id"]: r for r in A.list_pending(e)}; assert lst[p1]["status"] == "기각" and lst[p2]["status"] == "검토중" and lst[p2]["support"] == 12
    with e.connect() as c: assert c.execute(text("SELECT count(*) FROM churn_type WHERE type_id='T11'")).scalar() == 0
    with pytest.raises(A.ApproveError, match="없음"): A.approve(e, 9999, "yj")
