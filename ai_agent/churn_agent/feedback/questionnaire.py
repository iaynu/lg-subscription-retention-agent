"""설문 시안 v2(`data/survey_form_v2.html`)의 문항·보기 코드표와 건너뛰기 규칙.
시안의 JS와 같은 규칙을 파이썬으로 옮겼고, tests/test_feedback_questionnaire.py가 시안 HTML에서 보기 코드를 직접 뽑아 이 표와 대조한다.
문항 id·보기 코드는 저장 형식(survey_answer.question_id / answer_code)에 그대로 쓴다."""
from __future__ import annotations

# ---- 보기 목록 (시안의 ISSUES / SOL / NEWSOL / DEEP / 각 문항 opts) ----
ISSUES = ("cost", "exit", "renew", "idle", "care", "visit", "manager", "sales", "fail", "cs", "other")   # + 'none'(불편 없음)은 issues 문항에만
SOL = ("resched", "free", "fee", "swap", "penalty", "apology", "manager", "change", "pause")              # action·wanted 공통 + 'other'
NEWSOL = ("alert", "loan", "mgr", "step", "swap", "pause", "other", "none")
DK = "?"                                                                                                  # 잘 모르겠어요 / 기억나지 않아요

# 심화 문항: top 코드 -> (deepA 보기, deepB 보기)
DEEP = {
    "cost": (("p10", "p30", "p31", DK), ("fixed", "total", "promo", "missed", "other", DK)),
    "exit": (("pen", "rm", "wait", "reinst", "other", DK), ("0", "u10", "u30", "o30", DK)),
    "renew": (("push", "price", "newmodel", "eol", "other"), ("accept", "refuse", "think", "switch", DK)),
    "idle": (("daily", "week", "month", "rare", DK), ("noneed", "season", "hassle", "gap", "other")),
    "care": (("clean", "skip", "dmg", "other"), ("full", "part", "none", DK)),
    "visit": (("0", "1", "2", "3", DK), ("w1", "w2", "m1", "m2", DK)),
    "manager": (("attitude", "skill", "privacy", "other"), ("0", "1", "2", "3", DK)),
    "sales": (("1", "2", "4", DK), ("stop", "keep", "unsaid", DK)),
    "fail": (("noise", "perf", "broken", "other"), ("once", "2", "3", DK)),
    "cs": (("split", "slow", "blame", "other"), ("d3", "d7", "d14", "d15", "open", DK)),
}
SKIPB = {"visit": "0", "idle": "daily"}          # deepA가 이 값이면 deepB를 묻지 않는다

# 문항 정의: id -> (종류, 보기). 종류 single/multi/text. 'top'·'deepA'·'deepB'의 보기는 응답에 따라 달라져 따로 둔다.
QUESTIONS = {
    "status": ("single", ("done", "ing", "think", "sat")),
    "issues": ("multi", ISSUES + ("none",)),
    "top": ("single", None),
    "form": ("single", ("once", "repeat", "fade")),
    "deepA": ("single", None),
    "deepB": ("single", None),
    "hesitate": ("multi", ("pen", "pick", "habit", "term", "visit", "none")),
    "action": ("single", SOL + ("other", "no", "never")),
    "helped": ("single", ("solved", "part", "little", "worse")),
    "why": ("multi", ("late", "mismatch", "again", "diff", "unclear", "promise", "other")),
    "wanted": ("single", SOL + ("other", "hard")),
    "newsol": ("multi", NEWSOL),
    "motive": ("single", ("cost", "care", "new", "promo", "move", "recom", "default", "other")),
    "gap": ("single", ("below", "same", "over")),
    "firstact": ("single", ("call", "app", "tech", "search", "wait", "self", "other")),
    "research": ("multi", ("penalty", "review", "compare", "agency", "ask", "none", "other")),
    "after": ("single", ("buy", "sub", "keep", "used", "none", "unsure", "other")),
    "channel": ("multi", ("talk", "sms", "push", "mail", "call", "none")),
    "hh": ("single", ("solo", "couple", "baby", "kid", "other")),
    "daytime": ("single", ("most", "some", "rare")),
    "exp": ("single", ("first", "more", "past")),
    "who": ("single", ("self", "family", "famreq", "review", "agent")),
    "interview": ("single", ("yes", "no")),
    "open": ("text", None),
}
ORDER = tuple(QUESTIONS)          # 시안의 문항 순서(ctx는 hh·daytime·exp·who 네 문항으로 나눔)
EXCL = {"issues": "none", "hesitate": "none", "newsol": "none", "research": "none", "channel": "none"}   # 이 보기를 고르면 다른 보기는 비움
SAT_SKIP = frozenset(["issues", "top", "form", "deepA", "deepB", "hesitate", "action", "helped", "why", "wanted", "newsol",
                      "motive", "gap", "firstact", "research", "after", "who"])   # status=sat이면 건너뛰는 문항(who는 ctx에서 빠짐)
SECTIONS = {"status": "이용 현황", "issues": "불편했던 일", "top": "불편했던 일", "form": "불편했던 일", "deepA": "조금 더 자세히", "deepB": "조금 더 자세히",
            "hesitate": "해지 고민", "action": "해결 경험", "helped": "해결 경험", "why": "해결 경험", "wanted": "원하셨던 해결 방법", "newsol": "새로운 방법",
            "motive": "이용 방식", "gap": "이용 방식", "firstact": "이용 방식", "research": "이용 방식", "after": "이용 방식", "channel": "안내 방식",
            "hh": "생활 환경", "daytime": "생활 환경", "exp": "생활 환경", "who": "생활 환경", "interview": "추가 이야기", "open": "마지막 질문"}


def real_issues(a: dict) -> list[str]:
    """issues 응답에서 'none'을 뺀 보기(시안 realIssues와 같음). 'none'이나 미응답이면 빈 목록."""
    v = a.get("issues")
    return [k for k in ISSUES if isinstance(v, (list, tuple)) and k in v]


def top_key(a: dict) -> str | None:
    r = real_issues(a)
    return r[0] if len(r) == 1 else (a.get("top") or None)


def received(a: dict) -> bool:
    v = a.get("action")
    return bool(v) and v not in ("no", "never")


def options(qid: str, a: dict | None = None) -> tuple:
    """문항의 보기 코드. top·deepA·deepB는 앞선 응답(a)에 따라 정해진다."""
    a = a or {}
    if qid == "top": return tuple(real_issues(a))
    if qid in ("deepA", "deepB"):
        k = top_key(a)
        return DEEP[k][0 if qid == "deepA" else 1] if k in DEEP else ()
    return QUESTIONS[qid][1] or ()


def skipped(qid: str, a: dict) -> bool:
    """시안의 skip 규칙. a는 지금까지의 응답(문항 id -> 값, 복수 선택은 목록)."""
    if a.get("status") == "sat" and qid in SAT_SKIP: return True
    none = not real_issues(a)
    k = top_key(a)
    if qid == "top": return len(real_issues(a)) < 2
    if qid == "form": return not k
    if qid == "deepA": return not k or k == "other"
    if qid == "deepB": return not k or k == "other" or (k in SKIPB and a.get("deepA") == SKIPB[k])
    if qid in ("action", "wanted", "newsol", "firstact"): return none
    if qid == "helped": return none or not received(a)
    if qid == "why": return none or not received(a) or a.get("helped") not in ("part", "little", "worse")
    if qid == "gap": return not a.get("motive")
    return False


def visible_ids(a: dict) -> list[str]:
    return [q for q in ORDER if not skipped(q, a)]


def check_answers(a: dict) -> list[str]:
    """응답이 시안 규칙과 맞는지 점검하고 위반 메시지를 돌려준다(빈 목록이면 정상). 건너뛰는 문항의 응답, 없는 보기, 배타 보기 위반을 잡는다."""
    errs = []
    for q, v in a.items():
        if q not in QUESTIONS: errs.append(f"{q}: 알 수 없는 문항"); continue
        kind = QUESTIONS[q][0]
        if v is None: continue
        if kind == "text": continue
        opts = options(q, a)
        vals = list(v) if kind == "multi" else [v]
        if kind == "multi" and not isinstance(v, (list, tuple)): errs.append(f"{q}: 복수 선택은 목록이어야 함"); continue
        for x in vals:
            if x not in opts: errs.append(f"{q}: 보기에 없는 코드 {x!r}")
        if kind == "multi" and q in EXCL and EXCL[q] in vals and len(vals) > 1: errs.append(f"{q}: '{EXCL[q]}'는 다른 보기와 함께 고를 수 없음")
        if skipped(q, a): errs.append(f"{q}: 건너뛰어야 하는 문항에 응답이 있음")
    return errs
