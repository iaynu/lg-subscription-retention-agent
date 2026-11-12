"""AI 제안(ai_proposal) 검토 결과를 반영한다: 채택 -> churn_type / solution_catalog에 '비활성' 행 추가, 기각 -> 상태만 변경.

안전 원칙 [제안]
- 채택해도 새 솔루션은 비활성(active_yn='N', agent_selectable_yn='N')으로 들어간다. Rule Engine은 active·selectable이 모두 'Y'인 행만 후보로 쓰므로, 사람이 자격 조건·채널·부서를
  채우고 `activate_solution`을 호출하기 전에는 Agent 출력이 바뀌지 않는다.
- 새 유형(T11~)은 churn_prediction_type에 나오지 않는다(모델이 아직 모름). 모델 재학습·유형 매핑은 이 단계의 범위가 아니다.
- 상태가 '검토중'인 제안만 처리한다(이중 채택 방지). 같은 제안을 다시 채택하려 하면 오류.
- 순수 계산(`plan`)과 DB 쓰기(`approve`)를 나눠, 계획은 DB 없이 테스트하고 `dry_run=True`로 미리 볼 수 있다.
"""
from __future__ import annotations
import datetime as dt, json, re
from .candidates import ALLOWED_CATEGORIES

# 솔루션 유형별 기본값(사람이 채택 후 고친다). priority는 solution_catalog 주석 기준: 10 직접해결 20 정보 30 광고·혜택 40 내부보고 50 유형없음
_DEFAULTS = {
    "정보제공": dict(channel="알림톡", delivery_kind="고객 메시지", internal_forward_yn="N", priority=20),
    "혜택제안": dict(channel="알림톡", delivery_kind="고객 메시지", internal_forward_yn="N", priority=30),
    "담당자조치": dict(channel="내부 전달", delivery_kind="내부 전달", internal_forward_yn="Y", priority=40),
    "제품조치": dict(channel="내부 전달", delivery_kind="내부 전달", internal_forward_yn="Y", priority=40),
    "의견수집": dict(channel="상담·앱", delivery_kind="상담·앱 안내", internal_forward_yn="N", priority=50),
}
_TID = re.compile(r"^T(\d{2})$")
_norm = lambda s: re.sub(r"\s+", "", s or "")


class ApproveError(ValueError):
    pass


def _next_letters(used: set[str], n: int) -> list[str]:
    free = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in used]
    if len(free) < n: raise ApproveError("솔루션 ID 알파벳이 부족함(유형당 26개)")
    return free[:n]


def plan(proposal: dict, types: list[dict], solutions: list[dict], overrides: dict | None = None) -> dict:
    """채택 계획을 계산한다(DB 접근 없음). proposal: {proposal_id, proposal_kind, related_type_id, title, description, status, evidence(dict)}.
    types: [{type_id, type_name}], solutions: [{solution_id, seq_no, type_id}]. overrides: {type_name, description, solutions:[{title, category, message_hint}]}.
    돌려주는 값: {type_row | None, solution_rows, type_id}. 문제가 있으면 ApproveError."""
    ov = overrides or {}
    if proposal["status"] != "검토중": raise ApproveError(f"이미 처리된 제안(상태 {proposal['status']})")
    kind, ev = proposal["proposal_kind"], proposal.get("evidence") or {}
    sols = ov.get("solutions") if ov.get("solutions") is not None else ev.get("solutions") or []
    for s in sols:
        if s.get("category") not in ALLOWED_CATEGORIES: raise ApproveError(f"솔루션 category 오류: {s.get('category')}")
        if not (1 <= len((s.get("title") or "").strip()) <= 80): raise ApproveError("솔루션 이름은 1~80자")
        if not (1 <= len((s.get("message_hint") or "").strip()) <= 300): raise ApproveError("message_hint는 1~300자")
    type_row, type_id = None, proposal.get("related_type_id")
    if kind == "new_type":
        name = (ov.get("type_name") or proposal["title"]).strip(); desc = (ov.get("description") or proposal["description"]).strip()
        if name.startswith("미분류 패턴"): raise ApproveError("자동 이름(미분류 패턴)은 그대로 채택할 수 없음 — type_name을 지정해야 함")
        if not (2 <= len(name) <= 60): raise ApproveError("유형 이름은 2~60자")
        if not (1 <= len(desc) <= 300): raise ApproveError("유형 설명은 1~300자(채택 시 줄여서 지정)")
        if _norm(name) in {_norm(t["type_name"]) for t in types}: raise ApproveError("기존 유형 이름과 같음")
        if re.search(r"(?<![A-Za-z0-9])T\d{2}(?!\d)", name + desc): raise ApproveError("이름·설명에 기존 유형 ID(T##)가 들어 있음")
        if not sols: raise ApproveError("솔루션이 하나도 없는 신규 유형은 채택할 수 없음(overrides.solutions로 지정)")
        nums = [int(m.group(1)) for t in types if (m := _TID.match(t["type_id"]))]
        nxt = (max(nums) if nums else 0) + 1
        if nxt > 99: raise ApproveError("유형 ID 한도(T99) 초과")
        type_id = f"T{nxt:02d}"; type_row = {"type_id": type_id, "type_name": name, "description": desc}
    elif kind == "new_solution":
        if type_id not in {t["type_id"] for t in types}: raise ApproveError(f"붙일 기존 유형이 없음: {type_id}")
        if not sols: raise ApproveError("new_solution에 솔루션 초안이 없음")
    else:
        raise ApproveError(f"알 수 없는 kind: {kind}")
    used = {s["solution_id"].rsplit("-", 1)[-1] for s in solutions if s["type_id"] == type_id}
    letters = _next_letters(used, len(sols)); seq = max([s["seq_no"] for s in solutions] + [0])
    rows = []
    for i, (s, L) in enumerate(zip(sols, letters), start=1):
        d = _DEFAULTS[s["category"]]; title = s["title"].strip(); hint = s["message_hint"].strip()
        rows.append(dict(solution_id=f"S-{type_id}-{L}", seq_no=seq + i, type_id=type_id, solution_category=s["category"], solution_name=title, solution_detail=hint,
                         channel=d["channel"], ad_yn="N", internal_forward_yn=d["internal_forward_yn"], forward_dept=None, delivery_kind=d["delivery_kind"],
                         agent_selectable_yn="N", offer_cost=0, fee_impact="없음", trigger_note=None, buttons=None, message_hint=hint, new_policy_yn="Y",
                         priority=d["priority"], eligibility=None, customer_title=title[:60] if d["delivery_kind"] == "고객 메시지" else None,
                         message_items=None, active_yn="N"))
    return {"type_id": type_id, "type_row": type_row, "solution_rows": rows}


def _load(conn, proposal_id: int, lock: bool = True) -> dict:
    from sqlalchemy import text
    r = conn.execute(text("SELECT proposal_id, proposal_kind, related_type_id, title, description, evidence_json, status FROM ai_proposal WHERE proposal_id=:i" + (" FOR UPDATE" if lock else "")), {"i": proposal_id}).mappings().first()
    if r is None: raise ApproveError(f"제안 {proposal_id}이(가) 없음")
    ev = r["evidence_json"]; ev = json.loads(ev) if isinstance(ev, str) else (ev or {})
    return {**dict(r), "evidence": ev}


def _review_json(conn, proposal_id: int, patch: dict, status: str, reviewer: str, related_type_id: str | None = None):
    from sqlalchemy import text
    conn.execute(text("UPDATE ai_proposal SET status=:st, reviewed_by=:rb, reviewed_at=:ra, related_type_id=COALESCE(:rt, related_type_id), "
                      "evidence_json=jsonb_set(COALESCE(evidence_json,'{}'::jsonb), '{review}', CAST(:rv AS JSONB)) WHERE proposal_id=:i"),
                 {"st": status, "rb": reviewer[:30], "ra": dt.datetime.now(), "rt": related_type_id, "rv": json.dumps(patch, ensure_ascii=False), "i": proposal_id})


def approve(engine, proposal_id: int, reviewer: str, overrides: dict | None = None, dry_run: bool = False, note: str = "") -> dict:
    """채택. dry_run이면 계획만 돌려주고 아무것도 쓰지 않는다. 새 유형은 ai_proposal.related_type_id에 새 유형 ID를 남겨 추적한다."""
    from sqlalchemy import text
    with engine.begin() as c:
        p = _load(c, proposal_id)
        types = [dict(r) for r in c.execute(text("SELECT type_id, type_name FROM churn_type")).mappings()]
        sols = [dict(r) for r in c.execute(text("SELECT solution_id, seq_no, type_id FROM solution_catalog")).mappings()]
        pl = plan(p, types, sols, overrides)
        if dry_run: return {**pl, "dry_run": True}
        if pl["type_row"]: c.execute(text("INSERT INTO churn_type (type_id, type_name, description) VALUES (:type_id, :type_name, :description)"), pl["type_row"])
        cols = list(pl["solution_rows"][0]) if pl["solution_rows"] else []
        for r in pl["solution_rows"]:
            c.execute(text(f"INSERT INTO solution_catalog ({', '.join(cols)}) VALUES ({', '.join(':' + k for k in cols)})"), r)
        _review_json(c, proposal_id, {"decision": "채택", "note": note, "type_id": pl["type_id"], "solution_ids": [r["solution_id"] for r in pl["solution_rows"]]},
                     "채택", reviewer, pl["type_id"] if pl["type_row"] else None)
    return {**pl, "dry_run": False}


def reject(engine, proposal_id: int, reviewer: str, reason: str) -> dict:
    from sqlalchemy import text
    if not (reason or "").strip(): raise ApproveError("기각 사유가 필요함")
    with engine.begin() as c:
        p = _load(c, proposal_id)
        if p["status"] != "검토중": raise ApproveError(f"이미 처리된 제안(상태 {p['status']})")
        _review_json(c, proposal_id, {"decision": "기각", "note": reason.strip()}, "기각", reviewer)
    return {"proposal_id": proposal_id, "status": "기각"}


def activate_solution(engine, solution_id: str, eligibility: list | None = None, forward_dept: str | None = None) -> dict:
    """사람이 확인을 마친 신규 정책 솔루션을 Agent 후보로 켠다. 채택으로 만든 행(new_policy_yn='Y', 비활성)만 켤 수 있다."""
    from sqlalchemy import text
    with engine.begin() as c:
        r = c.execute(text("SELECT active_yn, new_policy_yn, internal_forward_yn, forward_dept FROM solution_catalog WHERE solution_id=:i FOR UPDATE"), {"i": solution_id}).mappings().first()
        if r is None: raise ApproveError(f"솔루션 {solution_id}이(가) 없음")
        if r["active_yn"] == "Y": raise ApproveError("이미 활성")
        if r["new_policy_yn"] != "Y": raise ApproveError("신규 정책으로 채택된 솔루션만 이 함수로 켤 수 있음")
        dept = forward_dept or r["forward_dept"]
        if r["internal_forward_yn"] == "Y" and not dept: raise ApproveError("내부 전달 솔루션은 forward_dept가 필요함")
        c.execute(text("UPDATE solution_catalog SET active_yn='Y', agent_selectable_yn='Y', forward_dept=:d, eligibility=:e WHERE solution_id=:i"),
                  {"d": dept, "e": json.dumps(eligibility, ensure_ascii=False) if eligibility else None, "i": solution_id})
    return {"solution_id": solution_id, "active_yn": "Y"}


def list_pending(engine) -> list[dict]:
    from sqlalchemy import text
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text("SELECT proposal_id, proposal_kind, related_type_id, title, status, "
                                                "jsonb_array_length(COALESCE(evidence_json->'response_ids','[]'::jsonb)) AS support, evidence_json->>'source' AS source "
                                                "FROM ai_proposal ORDER BY proposal_id")).mappings()]
