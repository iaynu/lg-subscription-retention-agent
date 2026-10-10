"""얇은 REST API. UI는 이 엔드포인트만 호출한다.

- request_id는 서버가 실행마다 새로 만든다(/agent/run). /agent/run-input에서 직접 주면 이미 있는 값은 409.
- 입력 오류는 422, 없는 요청·구독은 404, 이미 결정된 항목·제약 위반은 409.
"""
from __future__ import annotations
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator
from .factory import build
from .schemas import AgentInput, AgentResult, Decision, ForwardReq, _iso_date
from .sources import DemoSource


class RunReq(BaseModel):
    subscription_id: str = Field(max_length=12)
    as_of_date: Optional[str] = None

    @field_validator("as_of_date", mode="before")
    @classmethod
    def _date(cls, v): return None if v in (None, "") else _iso_date(v)


class DecisionReq(Decision):
    request_id: str = Field(max_length=20)
    subscription_id: str = Field(max_length=12)
    by: Optional[str] = Field(None, max_length=30)


def _err(e: Exception) -> str:
    return e.args[0] if e.args else type(e).__name__


def create_app(svc=None, source=None) -> FastAPI:
    if svc is None: svc, source = build()
    source = source or DemoSource()
    app = FastAPI(title="구독 이탈 방지 Agent", version="0.2")

    def _run(ai: dict):
        sid = ai["subscription"].get("subscription_id")
        known = {(a["solution_id"], a["created_at"], a["action_status"]) for a in ai.get("recent_actions", [])}
        for a in svc.repo.recent_actions(sid, ai["as_of_date"]):
            if (a["solution_id"], a["created_at"], a["action_status"]) not in known: ai.setdefault("recent_actions", []).append(a)
        try:
            return AgentResult(**svc.run(ai))
        except ValueError as e:
            raise HTTPException(409, _err(e))

    @app.get("/health")
    def health():
        return {"ok": True, "llm": svc.cfg["llm"]["provider"], "llm_status": getattr(svc.llm, "status", None),
                "storage": svc.cfg["storage"]["backend"], "source": type(source).__name__}

    @app.get("/catalog")
    def catalog(type_id: Optional[str] = None):
        return [s for s in svc.catalog if not type_id or s["type_id"] == type_id]

    @app.get("/subscriptions")
    def subscriptions(): return source.list_subscriptions()

    @app.post("/agent/run", response_model=AgentResult)
    def run(req: RunReq):
        ai = source.get_input(req.subscription_id, req.as_of_date)
        if ai is None: raise HTTPException(404, "subscription_id를 찾을 수 없음")
        ai.pop("request_id", None)                       # 실행마다 새 ID
        try: ai = AgentInput(**ai).model_dump()
        except ValidationError as e:                     # 예측 소스가 만든 입력이 계약에 맞지 않음(데이터 문제)
            raise HTTPException(422, f"입력 조립 오류: {e.errors()[0].get('msg', '')}")
        return _run(ai)

    @app.post("/agent/run-input", response_model=AgentResult)
    def run_input(ai: AgentInput): return _run(ai.model_dump())

    @app.post("/agent/decision")
    def decision(req: DecisionReq):
        try: return svc.decide(req.request_id, req.subscription_id, req.solution_id, req.status, req.note, req.by)
        except KeyError as e: raise HTTPException(404, _err(e))
        except ValueError as e: raise HTTPException(409, _err(e))

    @app.get("/departments")
    def departments(): return svc.dep["departments"]

    @app.post("/agent/forward")
    def forward(req: ForwardReq):
        try: return svc.forward(req.request_id, req.subscription_id, req.solution_ids, req.departments, req.text, req.by)
        except KeyError as e: raise HTTPException(404, _err(e))
        except ValueError as e: raise HTTPException(409, _err(e))

    @app.get("/subscriptions/{sid}/actions")
    def actions(sid: str): return svc.list_actions(sid)

    return app
