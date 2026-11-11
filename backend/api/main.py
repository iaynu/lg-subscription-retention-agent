"""FastAPI 앱.  실행:  uvicorn api.main:app --reload --port 8000   (문서: http://localhost:8000/docs)"""
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api import handlers as h
from api import proposals
from config import ROOT, settings
from db import schema
from db.conn import session

@asynccontextmanager
async def lifespan(_app):
    # 이미 만들어진 DB 에 새 컬럼이 있으면 덧붙인다(데이터는 그대로). DB 가 아직 비어 있으면 아무것도 하지 않는다.
    try:
        with session() as s:
            added = schema.migrate(s)
        if added:
            print('[migrate] 컬럼 추가:', ', '.join(added))
    except Exception as e:  # 서버가 뜨는 것을 막지 않는다(DB 미적재 상태 등)
        print('[migrate] 건너뜀:', type(e).__name__)
    yield


app = FastAPI(title='LG 구독 이탈 방지 Agent API', version='0.2.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.ALLOWED_ORIGINS, allow_methods=['*'], allow_headers=['*'])


def _call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except h.NotFound as e:
        raise HTTPException(404, f'없는 대상: {e}')
    except h.BadRequest as e:
        raise HTTPException(400, str(e))


class PlanReq(BaseModel):
    use_llm: bool = True


class ApproveReq(BaseModel):
    """모든 필드는 선택. 예전처럼 solution_ids 만 보내도 동작한다."""
    solution_ids: Optional[List[str]] = None
    part: Optional[str] = None                 # 'customer'(고객 발송분) | 'internal'(협업 전달분) | 생략(조치의 target 그대로)
    message_text: Optional[str] = None         # 상담자가 확정한 고객 문구(고쳤다면 고친 것)
    internal_memo: Optional[str] = None        # 상담자가 확정한 협업 전달문
    forward_depts: Optional[List[str]] = None  # 협업 전달 부서 이름
    decision_note: Optional[str] = None
    edited: bool = False                       # 상담자가 AI 초안을 고쳤는지


class ForwardReq(BaseModel):
    depts: List[str]
    note: str = ''
    run_id: Optional[int] = None


class RejectReq(BaseModel):
    reason: str = ''
    solution_ids: Optional[List[str]] = None
    part: Optional[str] = None


@app.get('/health')
def health():
    return h.health()


@app.get('/stats')
def stats():
    return h.stats()


@app.get('/overview')
def overview():
    return h.overview()


@app.get('/insights')
def insights():
    return h.insights()


@app.get('/catalog')
def catalog():
    return h.catalog()


@app.get('/subscriptions')
def subscriptions(risk: Optional[str] = Query(None, description='HIGH,MID 처럼 쉼표로'), type_id: Optional[str] = None,
                  q: Optional[str] = None, limit: int = 50, offset: int = 0):
    return _call(h.list_subscriptions, risk, type_id, q, limit, offset)


@app.get('/subscriptions/{sub_id}')
def subscription(sub_id: str):
    return _call(h.get_subscription, sub_id)


@app.post('/subscriptions/{sub_id}/plan')
def plan(sub_id: str, body: PlanReq = PlanReq()):
    return _call(h.make_plan, sub_id, body.use_llm)


@app.get('/runs/{run_id}')
def run(run_id: int):
    return _call(h.get_run, run_id)


@app.post('/runs/{run_id}/approve')
def approve(run_id: int, body: ApproveReq = ApproveReq()):
    return _call(h.approve, run_id, body.solution_ids, body.part, body.message_text, body.internal_memo,
                 body.forward_depts, body.decision_note, body.edited)


@app.post('/subscriptions/{sub_id}/forward')
def forward(sub_id: str, body: ForwardReq):
    return _call(h.manual_forward, sub_id, body.depts, body.note, body.run_id)


@app.post('/runs/{run_id}/reject')
def reject(run_id: int, body: RejectReq):
    return _call(h.reject, run_id, body.reason, body.solution_ids, body.part)


@app.get('/actions')
def actions(subscription_id: Optional[str] = None, limit: int = 50):
    return h.list_actions(subscription_id, limit)


# ---- 설문 분석 · 신규 유형 후보 검토(팀원 feedback 단계) ----
class ReviewReq(BaseModel):
    reviewer: str = '관리자'
    overrides: Optional[dict] = None
    dry_run: bool = False
    note: str = ''
    reason: str = ''


class ActivateReq(BaseModel):
    forward_dept: Optional[str] = None
    offer_cost: Optional[int] = None           # 혜택제안 솔루션은 필수(활성화하는 사람이 입력)
    fee_impact: Optional[str] = None           # 없음 | 증가 | 감소 (생략하면 없음)


def _prop(fn, *a, **k):
    try:
        return fn(*a, **k)
    except proposals.BadInput as e:
        raise HTTPException(400, str(e))


@app.get('/survey/eda')
def survey_eda():
    return proposals.survey_eda()


@app.get('/proposals')
def list_proposals():
    return proposals.list_proposals()


@app.post('/proposals/{pid}/approve')
def approve_proposal(pid: int, body: ReviewReq = ReviewReq()):
    return _prop(proposals.approve, pid, body.reviewer, body.overrides, body.dry_run, body.note)


@app.post('/proposals/{pid}/reject')
def reject_proposal(pid: int, body: ReviewReq = ReviewReq()):
    return _prop(proposals.reject, pid, body.reviewer, body.reason)


@app.post('/proposals/{pid}/restore')
def restore_proposal(pid: int):
    return _prop(proposals.restore, pid)


@app.post('/solutions/{sid}/activate')
def activate_solution(sid: str, body: ActivateReq = ActivateReq()):
    return _prop(proposals.activate, sid, body.forward_dept, body.offer_cost, body.fee_impact)


@app.get('/departments')
def departments():
    import json as _j
    return _j.loads((ROOT.parent / 'model' / 'data' / 'departments.json').read_text(encoding='utf-8'))['departments']


# ---- 화면(UI)을 같은 서버에서 보여 주기: http://localhost:8000/  (터널 주소로 열어도 같은 화면) ----
UI_DIR = ROOT / 'ui'


@app.get('/', include_in_schema=False)
def home():
    # ui/ 폴더(옛 HTML 화면)가 없으면 API 문서로 보낸다. 화면은 frontend/ 의 Streamlit 앱을 쓴다.
    return RedirectResponse('/ui/LG%20Care%20Better.dc.html' if UI_DIR.exists() else '/docs')


if UI_DIR.exists():
    app.mount('/ui', StaticFiles(directory=str(UI_DIR)), name='ui')
