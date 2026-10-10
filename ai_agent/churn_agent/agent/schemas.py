"""입출력 계약(pydantic). API·저장소가 공유한다.

입력 검증(2026-10-08 점검 반영): 날짜 형식, 구독 ID·월 구독료 필수, 조치 이력 날짜(날짜 또는 날짜+시각), 문자열 길이를
여기서 걸러 422로 돌려준다(이전에는 서비스 안에서 예외가 나 500이 됐음).
"""
from __future__ import annotations
import datetime as dt
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator


def _iso_date(v: Any) -> str:
    if isinstance(v, dt.datetime): return v.date().isoformat()
    if isinstance(v, dt.date): return v.isoformat()
    if isinstance(v, str):
        try: return dt.date.fromisoformat(v.strip()[:10]).isoformat()
        except ValueError: pass
    raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다")


class Feature(BaseModel):
    feature: str; label: str = ""; value: Any; shap: float = 0.0


class TypeInfo(BaseModel):
    type_rank: int = Field(ge=1, le=3); type_id: str = Field(max_length=4); type_name: str = ""
    contribution_share: float = Field(ge=0, le=1)
    top_features: list[Feature] = []


class Prediction(BaseModel):
    churn_probability: float = Field(0.0, ge=0, le=1)
    risk_level: Literal["HIGH", "MID", "LOW"]
    risk_rank_pct: float = Field(ge=0, le=1)


class RecentAction(BaseModel):
    solution_id: str; action_status: str; offer_cost: int = Field(0, ge=0); created_at: str

    @field_validator("created_at", mode="before")
    @classmethod
    def _date(cls, v): return _iso_date(v)                 # '2026-09-25T10:00:00'도 받아서 날짜만 남긴다


class AgentInput(BaseModel):
    request_id: str = Field("", max_length=20)       # action_log.action_batch_id(20자)와 같은 길이
    as_of_date: str                                  # 시스템 시계 대신 입력으로 받는다(재현성)
    subscription: dict[str, Any]
    customer: dict[str, Any] = {}                    # 내부 화면·협업 요청서 전용(customer_id, customer_name, ad_consent_yn). LLM에는 보내지 않음
    prediction: Prediction
    types: list[TypeInfo] = []
    context: dict[str, Any] = {}
    facts: dict[str, Any] = {}                       # eligibility 판정용(내부 값). 'required' 조건은 값이 없으면 제외
    message_facts: dict[str, Any] = {}               # 고객에게 보여도 되는 값(▶ 항목). agent/facts.py MESSAGE_FACT_DEFS 키
    recent_actions: list[RecentAction] = []

    @field_validator("as_of_date", mode="before")
    @classmethod
    def _as_of(cls, v): return _iso_date(v)

    @field_validator("subscription")
    @classmethod
    def _sub(cls, v):
        sid = v.get("subscription_id")
        if not isinstance(sid, str) or not sid or len(sid) > 12: raise ValueError("subscription.subscription_id(12자 이내 문자열) 필수")
        fee = v.get("monthly_fee")
        if not isinstance(fee, (int, float)) or isinstance(fee, bool) or fee <= 0: raise ValueError("subscription.monthly_fee(양수) 필수")
        for k in ("end_date", "promo_end_date", "care_renewal_date", "start_date"):
            if v.get(k) is not None: v[k] = _iso_date(v[k])
        return v

    @field_validator("customer")
    @classmethod
    def _cust(cls, v):
        for k in ("customer_id", "customer_name"):
            if k in v and v[k] is not None: v[k] = str(v[k])[:50]
        if v.get("ad_consent_yn") not in (None, "Y", "N"): raise ValueError("customer.ad_consent_yn은 Y 또는 N")
        return v


class SelectedAction(BaseModel):
    priority: int; solution_id: str; type_id: str
    selection_reason: str; evidence: list[dict[str, Any]] = []
    title: str = ""; message_text: str = ""; internal_memo: str = ""
    channel: str = ""; buttons: list[str] = []
    message_source: Literal["llm", "template"] = "llm"


class OutgoingMessage(BaseModel):
    channel: str; ad_yn: str = "N"; title: str = ""; message_text: str
    buttons: list[str] = []; solution_ids: list[str]; merged: bool = False; buttons_truncated: bool = False


class ForwardItem(BaseModel):
    role: str; solution_id: str; name: str; memo: str


class ForwardDraft(BaseModel):
    departments: list[str]                           # 기본 수신 부서 코드(상담자가 변경)
    unmapped: list[str] = []                         # 카탈로그에는 있으나 부서 마스터에 없는 이름
    items: list[ForwardItem]; draft_text: str; deadline_business_days: int


class ForwardReq(BaseModel):
    request_id: str = Field(max_length=20); subscription_id: str = Field(max_length=12)
    solution_ids: list[str] = Field(min_length=1); departments: list[str] = Field(min_length=1)
    text: str = Field(min_length=1, max_length=1000)
    by: Optional[str] = Field(None, max_length=30)

    @field_validator("solution_ids", "departments")
    @classmethod
    def _unique(cls, v):
        if len(set(v)) != len(v): raise ValueError("중복된 값이 있습니다")
        return v


class AgentResult(BaseModel):
    request_id: str = ""
    mode: str
    outcome: str                                     # pass|retry_pass|partial_template|full_template|survey_template|no_action
    attempts: int = 0
    failed_checks: list[str] = []
    selected: list[SelectedAction] = []
    analysis: dict[str, Any] = {}                    # 위험도·유형 비중·페르소나(근거 화면용)
    forward: Optional[ForwardDraft] = None           # 협업 부서 전달 초안(내부 전달 항목이 있을 때)
    messages: list[OutgoingMessage] = []             # 발송 단위(같은 채널·광고 여부면 한 통으로 합침)
    summary: str = ""
    excluded: list[dict[str, Any]] = []
    uncovered_types: list[str] = []
    limits: dict[str, Any] = {}
    fallback: Optional[str] = None                   # NO_ELIGIBLE_CANDIDATE: 후보가 0건이라 만족도 조사로 대체
    meta: dict[str, Any] = {}                        # llm_provider·llm_model·prompt_version·llm_errors (mock이면 화면에 표시 권장)


class Decision(BaseModel):
    status: Literal["조치완료", "반려"]
    solution_id: str
    note: str = Field("", max_length=300)
