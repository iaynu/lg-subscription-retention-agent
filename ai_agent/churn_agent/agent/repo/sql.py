"""SQLAlchemy 저장소(PostgreSQL, sql/schema_postgres.sql). agent_result·action_log를 읽고 쓴다.

- agent_result는 덮어쓰지 않는다(같은 request_id면 오류 → API 409). 결과 스냅샷에는 상담자에게 보여 준 초안 전체와
  협업 요청서 머리말의 고객 ID·이름이 들어 있으므로 DB 접근 권한을 내부 직원으로 제한한다.
- DB 제약 위반(길이·CHECK·중복)은 ValueError로 바꿔 올려 보낸다(API는 409로 응답).
"""
from __future__ import annotations
import datetime as dt, json
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, DataError


class SqlRepo:
    def __init__(self, url: str, engine=None):
        self.engine = engine or create_engine(url, future=True)

    def recent_actions(self, subscription_id, as_of_date, days=90):
        lim = dt.date.fromisoformat(as_of_date) - dt.timedelta(days=days)
        with self.engine.connect() as c:
            rows = c.execute(text("""SELECT solution_id, action_status, offer_cost, created_at::date AS d FROM action_log
                                     WHERE subscription_id = :s AND created_at::date > :lim ORDER BY created_at"""),
                             dict(s=subscription_id, lim=lim)).all()
        return [dict(solution_id=r.solution_id, action_status=r.action_status, offer_cost=int(r.offer_cost), created_at=r.d.isoformat()) for r in rows]

    def log_action(self, row):
        sql = text("""INSERT INTO action_log (action_batch_id, subscription_id, prediction_id, type_id, solution_id, action_category,
                        title, message_text, internal_memo, message_source, decision_note, forward_depts, memo_edited_yn, offer_cost,
                        risk_prob_at_action, action_status, channel, created_at, created_by, completed_at)
                      VALUES (:action_batch_id, :subscription_id, :prediction_id, :type_id, :solution_id, :action_category,
                        :title, :message_text, :internal_memo, :message_source, :decision_note, :forward_depts, :memo_edited_yn, :offer_cost,
                        :risk_prob_at_action, :action_status, :channel, :created_at, :created_by, :completed_at)
                      RETURNING action_id""")
        p = dict(prediction_id=None, title=None, message_text=None, internal_memo=None, message_source=None, decision_note=None,
                 forward_depts=None, memo_edited_yn="N", risk_prob_at_action=None, created_by=None, completed_at=None); p.update(row)
        p["created_at"] = dt.datetime.fromisoformat(p["created_at"])
        if p["action_status"] == "조치완료" and not p["completed_at"]: p["completed_at"] = p["created_at"]
        try:
            with self.engine.begin() as c: aid = c.execute(sql, p).scalar_one()
        except (IntegrityError, DataError) as e:
            raise ValueError(f"조치 기록 실패(DB 제약): {type(e.orig).__name__}") from e
        return dict(row, action_id=aid)

    def list_actions(self, subscription_id):
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(text("SELECT * FROM action_log WHERE subscription_id=:s ORDER BY action_id"), dict(s=subscription_id))]

    def put_result(self, request_id, result):
        meta = result.get("meta", {})
        sql = text("""INSERT INTO agent_result (request_id, subscription_id, as_of_date, mode, outcome, fallback, attempts, failed_checks,
                        llm_provider, llm_model, prompt_version, result_json)
                      VALUES (:rid, :sid, :asof, :mode, :outcome, :fb, :att, :fc, :prov, :model, :pv, CAST(:js AS JSONB))""")
        try:
            with self.engine.begin() as c:
                c.execute(sql, dict(rid=request_id, sid=result["subscription_id"], asof=result["as_of_date"], mode=result["mode"],
                                    outcome=result["outcome"], fb=result.get("fallback"), att=result.get("attempts", 0),
                                    fc=",".join(result.get("failed_checks", []))[:200] or None, prov=meta.get("llm_provider"),
                                    model=meta.get("llm_model"), pv=meta.get("prompt_version"), js=json.dumps(result, ensure_ascii=False)))
        except (IntegrityError, DataError) as e:
            raise ValueError(f"결과 저장 실패(DB 제약, request_id 중복 등): {type(e.orig).__name__}") from e

    def get_result(self, request_id):
        with self.engine.connect() as c:
            r = c.execute(text("SELECT result_json FROM agent_result WHERE request_id = :r"), dict(r=request_id)).first()
        return None if r is None else (r[0] if isinstance(r[0], dict) else json.loads(r[0]))
