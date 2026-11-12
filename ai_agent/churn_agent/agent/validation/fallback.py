"""LLM 호출 → 검증 → 재시도 1회 → 부분/전체 템플릿 폴백 (agent_validator_design.md).

템플릿(v3, 2026-10-08): 알림톡 보고서 형식. 고객용 제목(customer_title)을 첫 문장으로 쓰고, 그 솔루션의
▶ 항목(message_items) 중 값이 있는 고객 공개 값(message_facts)을 줄로 붙인 뒤, 구분별 마무리 문장을 붙인다.
카탈로그의 솔루션 이름은 내부 용어라 고객 문구에 쓰지 않는다(customer_title이 없는 구 입력만 예외).
"""
from __future__ import annotations
import copy, re, time
from ..facts import fact_line, MESSAGE_FACT_DEFS
from ..llm.base import LlmUnavailable
from .validator import validate, item_failures, HARD

CLOSING = {
    "정보제공": "자세한 내용은 앱 또는 상담으로 확인하실 수 있습니다.",
    "담당자조치": "담당자가 확인 후 연락드릴 예정이며, 구체적인 일정은 담당자가 별도로 안내드립니다.",
    "혜택제안": "총 비용과 위약금 조건은 함께 확인해 드리며, 신청을 원하시면 말씀해 주세요.",
    "제품조치": "접수나 변경을 원하시면 앱 또는 상담으로 신청하실 수 있습니다.",
    "의견수집": "간단한 설문으로 참여하실 수 있습니다.",
}
SURVEY_BODY = "고객님, 서비스를 이용하시며 불편하셨던 점이 있었는지 의견을 들려주세요."
# customer_title이 없는 구 입력(테스트 픽스처 등)용
LEGACY = {
    "정보제공": "고객님, {name} 내용을 안내드립니다.",
    "담당자조치": "고객님, {name_wa} 관련해 담당자가 연락드릴 수 있도록 안내드립니다.",
    "혜택제안": "고객님, 이용 가능한 {name} 내용을 안내드립니다.",
    "제품조치": "고객님, {name} 접수를 안내드립니다.",
}
FEE_TAIL = "구독료가 달라질 수 있으며, 기존 구독 유지 여부와 총 비용은 안내 시 함께 확인해 드립니다."
COST_TAIL = "총 비용과 위약금 조건은 안내 시 함께 확인해 드립니다."
FEE_DOWN_TAIL = "적용하시면 구독료가 낮아질 수 있으며, 총 비용과 위약금 등 적용 조건은 신청 시 함께 안내드립니다."
SUMMARY_TPL = "규칙에 따라 후보 조치를 순서대로 제안합니다."
STRUCTURAL = {"SCHEMA", "R1", "R1_COUNT", "R1_EMPTY", "R1_DUP", "R5", "R1_CAT"}
# 오류 메시지에 섞여 오는 키 조각(예: OpenAI 401 메시지의 "Incorrect API key provided: <일부 가려진 키>")은 가린다.
SECRET_RE = re.compile(r"(?i)\b(?:sk|rk|pk)-[A-Za-z0-9_\-*.]{4,}|bearer\s+[A-Za-z0-9_\-.~+/=]{8,}")


def _redact(msg: str) -> str:
    return SECRET_RE.sub("[가림]", msg or "")


def josa(word: str, with_final: str, without_final: str) -> str:
    """받침 유무에 맞는 조사(과/와, 을/를 등)."""
    last = (word or " ").rstrip()[-1:]
    has = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return word + (with_final if has else without_final)


def _sentence(s: str) -> str:
    s = s.strip()
    return s if s.endswith((".", "?", "!")) else s + "."


def _evidence(c, inp):
    if inp.get("mode") == "NO_TYPE":
        return [{"feature": "risk_level", "value": inp["prediction"]["risk_level"]},
                {"feature": "risk_rank_pct", "value": inp["prediction"]["risk_rank_pct"]}]
    for ty in inp.get("types", []):
        if ty["type_id"] == c["type_id"] and ty.get("top_features"):
            return [{"feature": f["feature"], "value": f["value"]} for f in ty["top_features"][:2]]
    sub = inp["subscription"]
    return [{"feature": k, "value": sub[k]} for k in ("monthly_fee",) if k in sub][:1]


def template_body(c, inp) -> str:
    cat = c["category"]
    if cat == "의견수집":
        return SURVEY_BODY + " " + CLOSING["의견수집"]
    title = (c.get("customer_title") or "").replace("(광고)", "").strip()
    if title:
        intro = _sentence(title)
    else:
        name = c.get("name", "")
        intro = LEGACY[cat].format(name=name, name_wa=josa(name, "과", "와"))
    mf = inp.get("message_facts") or {}
    lines = [fact_line(k, mf[k]) for k in (c.get("message_items") or []) if k in MESSAGE_FACT_DEFS and mf.get(k) is not None][:4]
    closing = CLOSING[cat]
    if cat == "혜택제안" and c.get("ad_yn", "Y") != "Y":
        closing = CLOSING["정보제공"]               # 사과 포인트처럼 신청할 필요가 없는 혜택은 확인 방법만 안내
    tail = []
    if c.get("fee_impact") == "감소":                 # 마무리 문장을 '요금 감소 + 총 비용·위약금 조건' 한 문장으로 합친다
        closing = FEE_DOWN_TAIL + (" 신청을 원하시면 말씀해 주세요." if cat == "혜택제안" else " 앱 또는 상담으로 신청하실 수 있습니다.")
    elif c.get("fee_impact") == "증가": tail.append(FEE_TAIL)
    elif re.search(r"요금|구독료|할인|감면", " ".join([intro] + lines)) and "총 비용" not in closing and "확인" not in closing:
        tail.append(COST_TAIL)
    parts = [intro] + lines + [closing] + tail
    return "\n".join(parts) if lines else " ".join(parts)


def template_item(c, prio, inp):
    kind = c.get("delivery_kind", "고객 메시지")
    internal_only = kind == "내부 전달"
    item = {"priority": prio, "solution_id": c["solution_id"], "type_id": c["type_id"],
            "selection_reason": "규칙 엔진이 선택한 후보를 우선순위 순서대로 제안합니다.", "evidence": _evidence(c, inp),
            "message_text": "" if internal_only else template_body(c, inp), "message_source": "template"}
    if "ad_yn" in c and kind == "고객 메시지":
        t = c.get("customer_title") or "고객님께 안내드립니다"
        if c["ad_yn"] == "Y" and not t.startswith("(광고)"): t = "(광고) " + t
        item["title"] = t[:40]
    if c.get("internal_forward_yn") == "Y":
        item["internal_memo"] = (f"[내부 전달] {c['name']} 건입니다. 전달 부서: {c.get('forward_dept') or '담당 부서'}. "
                                 f"구독자 정보를 확인해 조치해 주세요.")
    return item


def full_template(inp):
    return {"selected": [template_item(c, i + 1, inp) for i, c in enumerate(inp["candidates"][:inp["constraints"]["max_actions"]])],
            "summary": SUMMARY_TPL}


def retry_note(res):
    lines = [f"- {r}" + (f" (항목 {i + 1})" if i is not None else "") + f": {m}" for r, i, m, sev in res["failures"] if sev == HARD]
    return "[재작성 요청] 이전 출력이 아래 규칙을 어겼습니다. 해당 부분만 고쳐 같은 JSON 형식으로 다시 출력하세요.\n" + "\n".join(lines)


def _safe_validate(raw, inp):
    try:
        return validate(raw, inp)
    except Exception as e:                       # 검증기 자체 오류도 '검증 실패'로 처리해 템플릿으로 보낸다
        return dict(ok=False, failures=[("SCHEMA", None, f"검증 중 오류: {type(e).__name__}", HARD)], repaired=[], data=None)


def _retryable(e) -> bool:
    status = getattr(e, "status_code", None)
    return status is None or status == 429 or status >= 500     # 400·401·403·404 같은 요청 오류는 다시 보내도 같음


def run_with_fallback(inp, llm, deadline_s=15.0, first_timeout=10.0, retry_timeout=8.0):
    """llm(inp, note, timeout) -> raw str. 타임아웃·오류는 예외로 알린다."""
    t0 = time.time(); log = {"attempts": 0, "failed_checks": [], "outcome": None, "llm_errors": []}
    note = None; best = None
    for attempt in (1, 2):
        remaining = deadline_s - (time.time() - t0)
        if attempt == 2 and remaining < 4: break
        try:
            log["attempts"] += 1
            raw = llm(inp, note, min(first_timeout if attempt == 1 else retry_timeout, remaining))
        except TimeoutError:
            log["failed_checks"].append("TIMEOUT"); break
        except LlmUnavailable as e:
            log["failed_checks"].append("LLM_UNAVAILABLE"); log["llm_errors"].append(_redact(str(e))[:200]); break
        except Exception as e:
            log["failed_checks"].append("LLM_ERROR"); log["llm_errors"].append(f"{type(e).__name__}: {_redact(str(e))[:200]}")
            if attempt == 1 and _retryable(e): note = None; continue
            break
        res = _safe_validate(raw, inp)
        if res["ok"]:
            for s in res["data"]["selected"]: s["message_source"] = "llm"
            log["outcome"] = "pass" if attempt == 1 else "retry_pass"; return res["data"], log
        log["failed_checks"] += sorted({r for r, i, m, s in res["failures"] if s == HARD})
        best = res; note = retry_note(res)
    if best and isinstance(best.get("data"), dict) and isinstance(best["data"].get("selected"), list):
        fails = item_failures(best)
        if not (set(fails.get(None, [])) & STRUCTURAL) and not any(set(v) & STRUCTURAL for v in fails.values()):
            data = copy.deepcopy(best["data"]); cmap = {c["solution_id"]: c for c in inp["candidates"]}
            bad = {i for i in fails if i is not None}
            for i, s in enumerate(data["selected"]):
                if i in bad: data["selected"][i] = template_item(cmap[s["solution_id"]], s["priority"], inp)
                else: s["message_source"] = "llm"
            if None in fails: data["summary"] = SUMMARY_TPL
            log["outcome"] = "partial_template"; return data, log
    log["outcome"] = "full_template"; return full_template(inp), log
