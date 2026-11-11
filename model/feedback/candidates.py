"""신규 이탈 유형·솔루션 후보 생성 에이전트 (설계서 §9).
구조는 메인 에이전트와 같다: 규칙으로 근거 묶음 준비 -> LLM -> 검증기 -> 재시도 1회 -> 폴백.
- 입력은 설문 응답(survey_send / survey_response / survey_answer)뿐이다. 숨은 주제 정답 테이블은 여기서 읽지 않는다(검증은 recovery.py).
- LLM은 '주관식 문장 묶음(고유 문장 + 건수 + 불편 코드 분포)'과 '원인 층 셀(이용 동기 x 기대 격차)'을 보고 후보를 낸다.
  건수·응답 ID는 LLM이 세지 않는다: 인용한 문장 ID로 코드가 세고, LLM이 말한 건수가 다르면 그 후보를 버린다.
- API 키는 환경변수에서만 읽고, 오류 메시지에서 키처럼 보이는 문자열은 지운다.
- 결과는 ai_proposal(상태 '검토중')에 저장한다. 채택은 사람이 한다(approve는 다음 단계).
"""
from __future__ import annotations
import collections, datetime as dt, json, os, re
import pandas as pd

ALLOWED_CATEGORIES = ("정보제공", "담당자조치", "혜택제안", "제품조치", "의견수집")      # solution_catalog.solution_category (스키마 기준 5종)
KINDS = ("new_type", "new_solution")
MIN_SUPPORT = 8                                   # [제안] 후보 최소 근거 수(응답 건수)
MAX_PROPOSALS = 6
MAX_TEXTS = 400
MOTIVE_LABEL = {"cost": "초기 구매 비용 부담을 줄이려고", "care": "정기 방문 관리를 받으려고", "new": "최신 제품을 써보려고", "promo": "프로모션·혜택이 좋아서",
                "move": "이사·결혼 등 새 출발 시기라서", "recom": "가족·지인이 추천해서", "default": "구매하려 했는데 구독 가격이 먼저 보여서(구독인 줄 몰랐거나 나중에 알았음)", "other": "기타"}
GAP_LABEL = {"below": "기대(또는 가입 때 알던 것)에 못 미침", "same": "기대와 비슷", "over": "기대 이상"}
_KEY = re.compile("s" "k-[A-Za-z0-9_\\-]{8,}")      # 소스에 키 접두사 문자열을 그대로 두지 않는다(저장소 비밀 검사 테스트)
_PII = [re.compile(p) for p in (r"\d{2,4}-\d{3,4}-\d{4}", r"[\w.+-]+@[\w-]+\.[\w.]+", r"\d{6}-?[1-4]\d{6}", r"https?://", r"www\.")]
_TID = re.compile(r"(?<![A-Za-z0-9])T\d{2}(?!\d)")


def scrub(msg) -> str: return _KEY.sub("[KEY]", str(msg))


# ------------------------------------------------------------------ 1. 응답 표
def load_df(send: pd.DataFrame, response: pd.DataFrame, answer: pd.DataFrame) -> pd.DataFrame:
    """long 형식 3표 -> 응답 1건 1행. 컬럼: response_id, subscription_id, survey_type, status, issues(list), top, motive, gap, deepA, deepB, wanted, newsol(list), open.
    top은 'top' 문항 답, 없으면 불편을 하나만 골랐을 때 그 코드(불편 코드 'none'은 top 없음)."""
    a = answer.copy(); a["response_id"] = a.response_id.astype(int)
    one = lambda q: a[a.question_id == q].groupby("response_id").answer_code.first()
    many = lambda q: a[a.question_id == q].groupby("response_id").answer_code.apply(list)
    r = response[["response_id", "survey_id"]].copy(); r["response_id"] = r.response_id.astype(int)
    r = r.merge(send[["survey_id", "subscription_id", "survey_type"]], on="survey_id").set_index("response_id")
    df = r[["subscription_id", "survey_type"]].copy()
    df["status"] = one("status")
    iss = many("issues"); top = one("top")
    df["issues"] = [iss.get(i, []) for i in df.index]
    df["top"] = [top.get(i) if i in top.index else (_single(iss.get(i, []))) for i in df.index]
    for q in ("motive", "gap", "deepA", "deepB", "wanted"): df[q] = one(q)
    nw = many("newsol"); df["newsol"] = [nw.get(i, []) for i in df.index]
    op = a[a.question_id == "open"].groupby("response_id").other_text.first()
    df["open"] = op
    df["top"] = df.top.astype(object).where(df.top.notna(), None)          # NaN이 섞이면 'x or "-"' 같은 처리가 깨진다
    df.index.name = "response_id"
    return df


def _single(codes):
    c = [x for x in codes if x != "none"]
    return c[0] if len(c) == 1 else None


# ------------------------------------------------------------------ 2. 근거 묶음
def default_types() -> list[dict]:
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "solution_catalog.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    return [{"type_id": t["type_id"], "type_name": t["name"], "desc": re.sub(r"\s+", " ", t.get("desc", ""))[:110]} for t in d["types"]]


def build_pack(df: pd.DataFrame, existing_types: list[dict] | None = None, min_support: int = MIN_SUPPORT, max_texts: int = MAX_TEXTS) -> dict:
    """LLM 입력. 응답 ID 목록은 내부 표(pack['_ids'])에만 두고 LLM에는 문장 ID(t1..)와 건수만 준다. 만족(sat) 응답은 후보 대상이 아니라 제외한다."""
    existing_types = existing_types or default_types()
    d = df[df.status != "sat"]
    texts, ids = [], {}
    g = d.dropna(subset=["open"]).groupby("open")
    rows = sorted(((t, grp) for t, grp in g), key=lambda x: (-len(x[1]), x[0]))[:max_texts]
    for i, (t, grp) in enumerate(rows, start=1):
        tid = f"t{i}"; ids[tid] = [int(x) for x in grp.index]
        texts.append({"id": tid, "text": t, "n": len(grp), "tops": dict(collections.Counter(x or "-" for x in grp.top).most_common(4)),
                      "cohorts": dict(collections.Counter(grp.survey_type))})
    cells = []
    dm = d.dropna(subset=["motive", "gap"])
    for (m, gp), grp in dm.groupby(["motive", "gap"]):
        if len(grp) < min_support: continue
        cells.append({"motive": m, "gap": gp, "n": len(grp), "share": round(len(grp) / len(dm), 3),
                      "tops": dict(collections.Counter(x or "-" for x in grp.top).most_common(4))})
    cells.sort(key=lambda c: -c["n"])
    return {"n_responses": int(len(d)), "n_open_texts": int(d.open.notna().sum()), "min_support": min_support, "max_proposals": MAX_PROPOSALS,
            "cohort_counts": dict(collections.Counter(d.survey_type)),
            "existing_types": existing_types, "allowed_categories": list(ALLOWED_CATEGORIES),
            "motive_labels": MOTIVE_LABEL, "gap_labels": GAP_LABEL, "cells": cells, "texts": texts, "_ids": ids}


def public_pack(pack: dict) -> dict:
    return {k: v for k, v in pack.items() if not k.startswith("_")}


# ------------------------------------------------------------------ 3. LLM
SYSTEM_PROMPT = (
    "너는 가전 구독 서비스 이탈 방지 팀의 분석가다. 고객 설문의 주관식 답변과 원인 문항 분포를 읽고, 기존 이탈 유형 10개(existing_types의 이름·설명)로 설명되지 않는 "
    "반복 패턴(신규 이탈 유형 후보)과, 기존 유형에 붙일 새 솔루션 아이디어를 제안한다. 사람이 검토해 채택하므로 근거가 확실한 것만 낸다.\n"
    "작업 순서:\n"
    "A) texts의 문장을 '불평의 원인'이 같은 것끼리 묶는다. 표현이 달라도 뜻이 같으면 한 묶음이고, tops(불편 코드)가 달라도 원인이 같으면 한 묶음이다. 문장 하나의 n은 작아도 된다. "
    "한 묶음에는 원인이 하나만 담는다(예: '요금 수준이 비싸다'와 '가입 때 들은 설명과 다르다'는 서로 다른 원인이다). 원인이 섞이면 쪼갠다.\n"
    "B) 묶음마다 문장 n을 모두 더한다. 합이 min_support 이상인 묶음만 후보 대상이다(문장 하나가 아니라 묶음 합계 기준). 만족·무난 같은 불편이 아닌 문장은 묶지 않는다.\n"
    "C) 후보 대상 묶음마다 existing_types 중 원인이 가장 가까운 유형을 찾아 closest_type_id에 쓰고, 그 유형과 무엇이 다른지 difference에 한두 문장으로 쓴다. "
    "그 유형의 이름·설명이 말하는 원인과 같은 묶음은 신규 유형이 아니므로 new_type으로 내지 않는다(기존 유형에 붙일 새 해결 방법이 보이면 new_solution). "
    "같은 불편(요금·해지·케어 등)이라도 원인이 기존 유형이 말하는 것과 다르면 new_type이다.\n"
    "D) 기존 유형으로 설명되는 묶음은 낼 필요가 없다. 신규 근거가 있는 것만 최대 max_proposals개 내고, 없으면 빈 배열이 정상이다.\n"
    "규칙:\n"
    "1) 근거는 입력의 문장 ID(t1…)로만 댄다. text_ids에 묶음의 문장 ID를 모두 넣는다. 건수와 인용문은 시스템이 ID로 채우니 쓰지 않는다.\n"
    "2) 기존 유형 이름·ID(T01 등)를 신규 유형 제목·설명에 쓰지 않는다. closest_type_id에는 쓴다.\n"
    "3) cells(이용 동기 x 기대 격차 분포)는 보조 참고다. 후보의 cells에는 근거가 되는 셀만 넣고, 없으면 빈 배열.\n"
    "4) 입력에 없는 사실·숫자를 지어내지 않는다. 개인정보(전화번호·이메일 등)는 쓰지 않는다.\n"
    "5) solutions의 category는 allowed_categories 중 하나. message_hint는 고객에게 보낼 안내의 방향을 한 문장으로.\n"
    "6) new_type이면 related_type_id는 null, new_solution이면 붙일 기존 유형 ID.")


def response_schema() -> dict:
    sol = {"type": "object", "additionalProperties": False, "required": ["title", "category", "message_hint"],
           "properties": {"title": {"type": "string"}, "category": {"type": "string", "enum": list(ALLOWED_CATEGORIES)}, "message_hint": {"type": "string"}}}
    cell = {"type": "object", "additionalProperties": False, "required": ["motive", "gap"], "properties": {"motive": {"type": "string"}, "gap": {"type": "string"}}}
    prop = {"type": "object", "additionalProperties": False,
            "required": ["kind", "related_type_id", "title", "description", "text_ids", "closest_type_id", "difference", "cells", "solutions"],
            "properties": {"kind": {"type": "string", "enum": list(KINDS)}, "related_type_id": {"type": ["string", "null"]}, "title": {"type": "string"},
                           "description": {"type": "string"}, "text_ids": {"type": "array", "items": {"type": "string"}},
                           "closest_type_id": {"type": ["string", "null"]}, "difference": {"type": "string"},
                           "cells": {"type": "array", "items": cell}, "solutions": {"type": "array", "items": sol}}}
    return {"type": "object", "additionalProperties": False, "required": ["proposals"], "properties": {"proposals": {"type": "array", "items": prop}}}


def default_model(key: str = "candidate_model") -> str:
    """모델명은 agent/config.py + config.yaml 의 llm.<key> 한 곳에서 읽는다(실패하면 mini)."""
    try:
        from agent.config import load_config as _lc
        return _lc()["llm"].get(key) or "gpt-5.4-mini"
    except Exception:
        return "gpt-5.4-mini"


class OpenAICandidateLlm:
    """OpenAI Structured Outputs. 키는 환경변수에서만 읽는다. SDK 자동 재시도는 끈다."""
    def __init__(self, model: str | None = None, api_key_env: str = "OPENAI_API_KEY", client=None, temperature: float | None = None):
        model = model or default_model()
        self.model, self.temperature, self.last_usage = model, temperature, None
        if client is None:
            key = os.environ.get(api_key_env)
            if not key: raise RuntimeError(f"환경변수 {api_key_env}가 없습니다.")
            from openai import OpenAI
            client = OpenAI(api_key=key, max_retries=0)
        self.client = client

    def generate(self, pack: dict, note: str | None, timeout: float = 120.0) -> str:
        user = "입력(JSON):\n" + json.dumps(public_pack(pack), ensure_ascii=False) + (("\n\n[검증 피드백 — 아래 문제를 고쳐 다시 작성]\n" + note) if note else "")
        kw = dict(model=self.model, timeout=timeout, messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
                  response_format={"type": "json_schema", "json_schema": {"name": "candidates", "strict": True, "schema": response_schema()}})
        if self.temperature is not None: kw["temperature"] = self.temperature
        try:
            r = self.client.chat.completions.create(**kw)
        except Exception as e:
            raise RuntimeError(scrub(f"{type(e).__name__}: {e}")) from None
        u = getattr(r, "usage", None)
        self.last_usage = {"prompt": getattr(u, "prompt_tokens", None), "completion": getattr(u, "completion_tokens", None)} if u is not None else None
        ch = r.choices[0]
        if getattr(ch.message, "refusal", None) or ch.finish_reason != "stop": raise RuntimeError(f"응답이 정상 종료되지 않음({ch.finish_reason})")
        return ch.message.content or ""


    def classify(self, pack: dict, timeout: float = 120.0) -> str:
        """1단계: 문장마다 가장 가까운 기존 유형과 일치 정도(exact/partial/none)를 붙인다."""
        slim = {"existing_types": pack["existing_types"], "texts": [{"id": t["id"], "text": t["text"]} for t in pack["texts"]]}
        kw = dict(model=self.model, timeout=timeout, messages=[{"role": "system", "content": CLASSIFY_PROMPT}, {"role": "user", "content": "입력(JSON):\n" + json.dumps(slim, ensure_ascii=False)}],
                  response_format={"type": "json_schema", "json_schema": {"name": "labels", "strict": True, "schema": classify_schema()}})
        if self.temperature is not None: kw["temperature"] = self.temperature
        try:
            r = self.client.chat.completions.create(**kw)
        except Exception as e:
            raise RuntimeError(scrub(f"{type(e).__name__}: {e}")) from None
        u = getattr(r, "usage", None)
        self.last_usage = {"prompt": getattr(u, "prompt_tokens", None), "completion": getattr(u, "completion_tokens", None)} if u is not None else None
        ch = r.choices[0]
        if getattr(ch.message, "refusal", None) or ch.finish_reason != "stop": raise RuntimeError(f"응답이 정상 종료되지 않음({ch.finish_reason})")
        return ch.message.content or ""


CLASSIFY_PROMPT = (
    "너는 가전 구독 서비스 설문의 주관식 답변을 기존 이탈 유형에 맞춰 보는 분류자다. texts의 각 문장마다 existing_types(이름·설명) 중 가장 가까운 유형을 type_id에 쓰고, fit을 정한다.\n"
    "- exact: 그 문장의 불평이 해당 유형의 이름·설명이 말하는 바로 그 내용이다.\n"
    "- partial: 일부만 겹치거나, 불평의 원인이 유형 설명과 다를 수 있다(예: 같은 요금 불만이지만 가입 때 들은 설명과 달랐다는 이야기, 직접 해도 되는데 굳이 맡겨야 하나라는 이야기).\n"
    "- none: 어느 유형과도 맞지 않는다.\n"
    "애매하면 exact가 아니라 partial로 한다. 모든 문장 ID를 한 번씩 빠짐없이 쓴다.")


def classify_schema() -> dict:
    item = {"type": "object", "additionalProperties": False, "required": ["id", "type_id", "fit"],
            "properties": {"id": {"type": "string"}, "type_id": {"type": ["string", "null"]}, "fit": {"type": "string", "enum": ["exact", "partial", "none"]}}}
    return {"type": "object", "additionalProperties": False, "required": ["labels"], "properties": {"labels": {"type": "array", "items": item}}}


# 주제어로 문장을 묶는 규칙 기반 대역(배관 시험용). 숨은 주제 정답도 문장 풀 라벨도 쓰지 않는다. 품질 평가에는 쓰지 않는다.
_MOCK_RULES = (("계약·조건 불일치", ("가입", "계약", "상담", "포함", "범위")), ("직접 관리 대비 케어 가치 의문", ("직접", "굳이", "가치", "집에서", "차이")))


class MockCandidateLlm:
    def __init__(self): self.last_usage = None

    def classify(self, pack: dict, timeout: float = 0) -> str:
        lab = []
        for t in pack["texts"]:
            if any(k in t["text"] for k in ("일정", "방문 시간", "예약")): lab.append({"id": t["id"], "type_id": "T06", "fit": "exact"})
            elif any(k in t["text"] for k in ("가입", "계약")): lab.append({"id": t["id"], "type_id": "T01", "fit": "partial"})
            else: lab.append({"id": t["id"], "type_id": None, "fit": "none"})
        return json.dumps({"labels": lab}, ensure_ascii=False)

    def generate(self, pack: dict, note: str | None, timeout: float = 0) -> str:
        out = []
        for title, kws in _MOCK_RULES:
            hit = [t for t in pack["texts"] if any(k in t["text"] for k in kws)]
            if sum(t["n"] for t in hit) < pack["min_support"]: continue
            hit = hit[:40]
            out.append({"kind": "new_type", "related_type_id": None, "title": title, "description": f"주관식 {len(hit)}개 문장에서 반복되는 표현(모의 규칙)",
                        "text_ids": [t["id"] for t in hit], "closest_type_id": "T01", "difference": "모의 규칙 결과(품질 평가 대상 아님)", "cells": [],
                        "solutions": [{"title": f"{title} 안내 보강", "category": "정보제공", "message_hint": "가입 때 안내한 내용과 실제 범위를 다시 알려드립니다."}]})
        return json.dumps({"proposals": out}, ensure_ascii=False)


# ------------------------------------------------------------------ 4. 검증기
def _norm(s: str) -> str: return re.sub(r"\s+", "", s or "")


def drop_unknown_cells(p: dict, pack: dict) -> tuple[dict, list[dict]]:
    """입력에 없는 (motive, gap) 셀만 제거한다. 근거 건수·인용문은 text_ids로 계산하므로 셀 오류가 후보 전체를 버릴 이유는 아니다."""
    cell_set = {(c["motive"], c["gap"]) for c in pack["cells"]}
    keep, dropped = [], []
    for c in p.get("cells") or []:
        (keep if isinstance(c, dict) and (c.get("motive"), c.get("gap")) in cell_set else dropped).append(c)
    q = dict(p); q["cells"] = keep
    return q, dropped


def validate_proposal(p: dict, pack: dict, accepted: list[dict] | None = None) -> list[str]:
    """후보 하나를 검사해 문제 목록을 돌려준다(빈 목록이면 통과). 근거 건수·응답 ID는 입력 문장 ID에서 코드가 다시 센다."""
    errs = []
    kind = p.get("kind")
    if kind not in KINDS: errs.append(f"kind 오류: {kind}")
    title, desc = (p.get("title") or "").strip(), (p.get("description") or "").strip()
    if not (2 <= len(title) <= 40): errs.append("제목 길이는 2~40자")
    if not (1 <= len(desc) <= 600): errs.append("설명 길이는 1~600자")
    type_ids = {t["type_id"] for t in pack["existing_types"]}
    if kind == "new_solution" and p.get("related_type_id") not in type_ids: errs.append(f"new_solution의 related_type_id가 기존 유형이 아님: {p.get('related_type_id')}")
    if kind == "new_type":
        if p.get("related_type_id") is not None: errs.append("new_type은 related_type_id가 null이어야 함")
        names = {_norm(t["type_name"]) for t in pack["existing_types"]}
        if _norm(title) in names or any(n and n in _norm(title) for n in names): errs.append("기존 유형 이름과 같거나 포함됨")
        if _TID.search(title) or _TID.search(desc): errs.append("신규 유형 제목·설명에 기존 유형 ID(T##)가 들어 있음")
    tid = list(p.get("text_ids") or [])
    unknown = [t for t in tid if t not in pack["_ids"]]
    if unknown: errs.append(f"입력에 없는 문장 ID: {unknown[:3]}")
    if len(set(tid)) != len(tid): errs.append("text_ids 중복")
    rids = sorted({r for t in tid if t in pack["_ids"] for r in pack["_ids"][t]})
    if len(rids) < pack["min_support"]: errs.append(f"근거 응답 {len(rids)}건 < 최소 {pack['min_support']}건")
    if kind == "new_type":
        if p.get("closest_type_id") not in type_ids: errs.append(f"closest_type_id가 기존 유형이 아님: {p.get('closest_type_id')}")
        if len((p.get("difference") or "").strip()) < 10: errs.append("difference(가장 가까운 기존 유형과의 차이)를 10자 이상 써야 함")
    cell_set = {(c["motive"], c["gap"]) for c in pack["cells"]}
    for c in p.get("cells") or []:
        if (c.get("motive"), c.get("gap")) not in cell_set: errs.append(f"입력에 없는 셀: {c}")
    blob = " ".join([title, desc, p.get("difference") or ""] + [s.get("title", "") + " " + s.get("message_hint", "") for s in p.get("solutions") or []])
    if any(rx.search(blob) for rx in _PII): errs.append("개인정보 형태 포함")
    for s in p.get("solutions") or []:
        if s.get("category") not in ALLOWED_CATEGORIES: errs.append(f"솔루션 category 오류: {s.get('category')}")
        if not (1 <= len(s.get("title") or "") <= 100): errs.append("솔루션 제목 길이 오류")
        if not (1 <= len(s.get("message_hint") or "") <= 300): errs.append("message_hint 길이 오류")
    for a in accepted or []:
        sa, sb = set(a["text_ids"]), set(tid)
        if sa and sb and len(sa & sb) / len(sa | sb) > 0.8: errs.append("앞서 통과한 후보와 근거가 거의 같음(중복)")
    return errs


# ------------------------------------------------------------------ 5. 폴백 (LLM 없이)
def fallback_candidates(df: pd.DataFrame, pack: dict, top_n: int = 3, min_lift: float = 1.3) -> list[dict]:
    """원인 층 셀(이용 동기 x 기대 격차) 중 두 문항의 독립 가정보다 많이 몰린 셀을 '관찰 셀'로 올린다. 자동 발견이 아니라 사람이 볼 목록이며,
    이름은 자동(미분류 패턴)이다. 통계적 탐지는 응답 수가 적고 문항이 구조적으로 종속돼 신뢰도가 낮다(설계서 §9)."""
    d = df[(df.status != "sat")].dropna(subset=["motive", "gap"])
    if d.empty: return []
    N = len(d); pm, pg = d.motive.value_counts(normalize=True), d.gap.value_counts(normalize=True); out = []
    for (m, gp), grp in d.groupby(["motive", "gap"]):
        n = len(grp)
        if n < pack["min_support"]: continue
        lift = n / (N * pm[m] * pg[gp])
        if lift < min_lift: continue
        out.append((lift, n, m, gp, [int(x) for x in grp.index], grp))
    out.sort(key=lambda x: (-x[0], -x[1]))
    res = []
    for lift, n, m, gp, rids, grp in out[:top_n]:
        res.append({"kind": "new_type", "related_type_id": None, "title": f"미분류 패턴(자동): 동기={m} · 기대={gp}",
                    "description": f"이용 동기 '{MOTIVE_LABEL[m]}' + 기대 격차 '{GAP_LABEL[gp]}' 응답이 {n}건(전체의 {n / N:.1%}, 기대 대비 {lift:.2f}배)입니다. 자동 관찰 셀이며 원인은 사람이 확인해야 합니다.",
                    "text_ids": [], "quotes": [], "claimed_count": n, "closest_type_id": None, "difference": "", "cells": [{"motive": m, "gap": gp}], "solutions": [],
                    "_response_ids": rids, "_source": "fallback"})
    return res


# ------------------------------------------------------------------ 6. 오케스트레이터
def _parse(raw: str) -> list[dict]:
    d = json.loads(raw)
    props = d["proposals"]
    if not isinstance(props, list): raise ValueError("proposals가 배열이 아님")
    return props


def stage1_filter(pack: dict, llm, timeout: float = 120.0) -> tuple[dict, dict]:
    """1단계: 기존 유형과 '정확히 일치'(exact)로 분류된 문장을 빼고 2단계로 넘긴다. partial은 남기되 가장 가까운 유형(near)을 붙인다.
    분류 실패·형식 오류는 전체 문장으로 2단계를 진행한다(info['error']). 라벨에 없는 문장은 none으로 본다."""
    info = {"removed_texts": 0, "removed_responses": 0, "by_type": {}, "partial": 0, "error": None, "usage": None}
    try:
        labs = json.loads(llm.classify(pack, timeout))["labels"]; info["usage"] = getattr(llm, "last_usage", None)
        if not isinstance(labs, list): raise ValueError("labels가 배열이 아님")
    except Exception as e:
        info["error"] = scrub(f"{type(e).__name__}: {e}"); return pack, info
    type_ids = {t["type_id"] for t in pack["existing_types"]}
    lab = {x.get("id"): x for x in labs if isinstance(x, dict)}
    keep, ids = [], {}
    for t in pack["texts"]:
        x = lab.get(t["id"], {}); fit, tid = x.get("fit"), x.get("type_id")
        if fit == "exact" and tid in type_ids:
            info["removed_texts"] += 1; info["removed_responses"] += t["n"]; info["by_type"][tid] = info["by_type"].get(tid, 0) + t["n"]; continue
        t = dict(t)
        if fit == "partial" and tid in type_ids: t["near"] = tid; info["partial"] += 1
        keep.append(t); ids[t["id"]] = pack["_ids"][t["id"]]
    if not keep: return pack, {**info, "error": "남은 문장 없음 — 1단계를 적용하지 않음"}
    return {**pack, "texts": keep, "_ids": ids}, info


def generate_candidates(df: pd.DataFrame, llm, existing_types=None, min_support: int = MIN_SUPPORT, timeout: float = 120.0, use_fallback: bool = True, two_stage: bool = True) -> dict:
    """결과: {proposals:[{..., response_ids, source}], source:'llm'|'llm_retry'|'fallback'|'none', rejected:[{title, errors}], pack_stats, llm_error}"""
    pack = build_pack(df, existing_types, min_support)
    stage1 = None
    if two_stage and hasattr(llm, "classify"): pack, stage1 = stage1_filter(pack, llm, timeout)
    stats = {"n_responses": pack["n_responses"], "n_texts": len(pack["texts"]), "n_cells": len(pack["cells"])}
    rejected, llm_error, note, accepted, attempts = [], None, None, [], []
    for attempt in (1, 2):
        try:
            props = _parse(llm.generate(pack, note, timeout)); attempts.append({"n_props": len(props), "usage": getattr(llm, "last_usage", None)})
        except Exception as e:                      # 네트워크·형식·타임아웃 모두 같은 처리: 재시도 없이 폴백
            llm_error = scrub(f"{type(e).__name__}: {e}"); break
        accepted, errs_all = [], []
        for p in props[:MAX_PROPOSALS * 2]:
            p["text_ids"] = list(dict.fromkeys(p.get("text_ids") or []))                    # 같은 ID를 되풀이해 써도 무해하므로 걸러서 쓴다
            p, dropped = drop_unknown_cells(p, pack)                                         # cells는 참고용 힌트라, 입력에 없는 셀만 버리고 후보는 살린다
            errs = validate_proposal(p, pack, accepted)
            if errs: errs_all.append((p.get("title", "?"), errs)); continue
            q = dict(p); q["dropped_cells"] = dropped; q["response_ids"] = sorted({r for t in p["text_ids"] for r in pack["_ids"][t]}); q["claimed_count"] = len(q["response_ids"])
            by_n = {t["id"]: t for t in pack["texts"]}; q["quotes"] = [by_n[t]["text"] for t in sorted(p["text_ids"], key=lambda x: -by_n[x]["n"])[:3]]      # 건수·인용문은 ID에서 코드가 채운다
            q["source"] = "llm" if attempt == 1 else "llm_retry"
            accepted.append(q)
            if len(accepted) >= MAX_PROPOSALS: break
        rejected = [{"title": t, "errors": e} for t, e in errs_all]
        if accepted: break
        if not errs_all:                              # 빈 배열: 처음이면 합계 기준을 다시 확인시키는 재시도 1회, 두 번째도 비면 후보 없음으로 끝
            if attempt == 2 or pack["n_open_texts"] < pack["min_support"]: break
            note = "처음 답이 빈 배열이었다. 문장 하나의 n이 작아도 원인이 같은 문장을 묶어 n을 더하면 min_support 이상이 되는 묶음이 있는지 다시 확인하라. 그래도 정말 없을 때만 빈 배열."
            continue
        note = "\n".join(f"- {t}: {'; '.join(e)}" for t, e in errs_all)[:1500]
    if accepted or (llm_error is None and not rejected):
        return {"proposals": accepted, "source": (accepted[0]["source"] if accepted else "llm"), "rejected": rejected, "pack_stats": stats, "llm_error": None, "attempts": attempts, "stage1": stage1}
    if use_fallback:
        fb = fallback_candidates(df, pack)
        for p in fb: p["response_ids"] = p.pop("_response_ids"); p["source"] = p.pop("_source")
        return {"proposals": fb, "source": "fallback" if fb else "none", "rejected": rejected, "pack_stats": stats, "llm_error": llm_error, "attempts": attempts, "stage1": stage1}
    return {"proposals": [], "source": "none", "rejected": rejected, "pack_stats": stats, "llm_error": llm_error, "attempts": attempts, "stage1": stage1}


# ------------------------------------------------------------------ 6b. 합의: 여러 번 돌려 반복해서 나오는 후보만 남긴다
def _jacc(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def consensus_proposals(runs: list[list[dict]], min_votes: int, overlap: float = 0.3) -> list[dict]:
    """runs[i] = i번째 실행의 후보 목록. 같은 kind이고 근거 응답(response_ids)이 overlap 이상 겹치면 같은 후보로 보고,
    서로 다른 실행 min_votes개 이상에서 나온 묶음만 남긴다. 대표는 묶음 안에서 다른 구성원과 가장 많이 겹치는 후보이고
    근거·인용문은 그 후보 자신의 값을 그대로 쓴다(합집합으로 부풀리지 않음). votes·n_runs를 붙인다."""
    clusters: list[list[tuple[int, dict]]] = []
    for i, props in enumerate(runs):
        for p in props:
            for cl in clusters:
                if p["kind"] == cl[0][1]["kind"] and all(j != i for j, _ in cl) and _jacc(p["response_ids"], cl[0][1]["response_ids"]) >= overlap:
                    cl.append((i, p)); break
            else:
                clusters.append([(i, p)])
    out = []
    for cl in clusters:
        votes = len({i for i, _ in cl})
        if votes < min_votes: continue
        rep = max(cl, key=lambda ip: (sum(_jacc(ip[1]["response_ids"], q["response_ids"]) for _, q in cl), len(ip[1]["response_ids"])))[1]
        out.append({**rep, "votes": votes, "n_runs": len(runs), "source": "consensus"})
    out.sort(key=lambda p: (-p["votes"], -len(p["response_ids"])))
    return out[:MAX_PROPOSALS]


def generate_consensus(df: pd.DataFrame, llm, runs: int = 3, existing_types=None, min_support: int = MIN_SUPPORT, timeout: float = 120.0, two_stage: bool = True) -> dict:
    """generate_candidates를 runs번 돌려 과반(runs//2+1) 이상에서 반복된 후보만 남긴다. runs=1이면 단일 실행과 같다(폴백 포함).
    폴백(통계 후보)은 투표에 넣지 않는다. 결과 형식은 generate_candidates와 같고 'runs' 요약이 추가된다."""
    if runs <= 1:
        return generate_candidates(df, llm, existing_types, min_support, timeout, True, two_stage)
    results = [generate_candidates(df, llm, existing_types, min_support, timeout, False, two_stage) for _ in range(runs)]
    ok = [r for r in results if r["source"] in ("llm", "llm_retry")]
    first = results[0]
    summary = [{"source": r["source"], "n_proposals": len(r["proposals"]), "titles": [p["title"] for p in r["proposals"]], "kinds": [p["kind"] for p in r["proposals"]], "rejected": r["rejected"],
                "stage1_removed": (r["stage1"] or {}).get("removed_texts"), "llm_error": r["llm_error"]} for r in results]
    props = consensus_proposals([r["proposals"] for r in ok], runs // 2 + 1) if ok else []
    return {"proposals": props, "source": "consensus" if props else ("none" if not ok else "llm"), "rejected": [x for r in results for x in r["rejected"]],
            "pack_stats": first["pack_stats"], "llm_error": next((r["llm_error"] for r in results if r["llm_error"]), None),
            "attempts": [a for r in results for a in r["attempts"]], "stage1": first["stage1"], "runs": summary, "min_votes": runs // 2 + 1}


# ------------------------------------------------------------------ 7. 저장 (ai_proposal, 상태 '검토중')
def to_rows(result: dict, n_total: int, created_at: dt.datetime | None = None) -> list[dict]:
    created_at = created_at or dt.datetime.now()
    rows = []
    for p in result["proposals"]:
        ev = {"source": p["source"], "support": len(p["response_ids"]), "share_of_responses": round(len(p["response_ids"]) / max(n_total, 1), 4),
              "text_ids": p["text_ids"], "quotes": p["quotes"], "cells": p["cells"], "closest_type_id": p.get("closest_type_id"), "difference": p.get("difference"), "response_ids": p["response_ids"], "solutions": p["solutions"]}
        if "votes" in p: ev["votes"] = p["votes"]; ev["n_runs"] = p["n_runs"]
        rows.append({"proposal_kind": p["kind"], "related_type_id": p.get("related_type_id"), "title": p["title"][:100], "description": p["description"][:1000],
                     "evidence_json": json.dumps(ev, ensure_ascii=False), "status": "검토중", "created_at": created_at})
    return rows


def write_csv(rows: list[dict], path) -> int:
    pd.DataFrame(rows, columns=["proposal_kind", "related_type_id", "title", "description", "evidence_json", "status", "created_at"]).to_csv(path, index=False, encoding="utf-8")
    return len(rows)


def write_sql(engine, rows: list[dict], reset: bool = False) -> int:
    from sqlalchemy import text
    with engine.begin() as c:
        if reset: c.execute(text("DELETE FROM ai_proposal"))
        for r in rows:
            c.execute(text("INSERT INTO ai_proposal (proposal_kind, related_type_id, title, description, evidence_json, status, created_at) "
                           "VALUES (:proposal_kind, :related_type_id, :title, :description, CAST(:evidence_json AS JSONB), :status, :created_at)"), r)
    return len(rows)
