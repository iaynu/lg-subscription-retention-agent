"""카탈로그 eligibility 평가. 조건은 AND.

조건 형식: [fact, op, value] 또는 [fact, op, value, "required"].
- 값이 없을 때(None·누락·숫자로 읽을 수 없는 값):
  - 'required' 조건 → 후보 제외(사건이 확인되지 않았는데 "~가 바뀌었어요" 같은 안내를 보내지 않기 위함).
    단 unknown_required="pass"로 설정하면 예전처럼 통과.
  - 그 밖의 조건 → '알 수 없음'으로 통과(데이터가 없어 솔루션을 빼는 일을 막기 위함).
- 숫자 비교에 숫자 문자열("3")이 오면 숫자로 바꿔 비교한다.
"""
import operator

OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq, "!=": operator.ne}
_MISSING = object()


def _coerce(actual, expected):
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if isinstance(actual, bool): return _MISSING
        if isinstance(actual, (int, float)): return actual
        if isinstance(actual, str):
            try: return float(actual.strip())
            except ValueError: return _MISSING
        return _MISSING
    return actual


def check_eligibility(rules, facts, unknown_required: str = "fail"):
    """returns (ok, detail). 불충족이면 첫 번째 실패 조건을 detail로."""
    for rule in (rules or []):
        fact, op, val = rule[0], rule[1], rule[2]
        required = len(rule) > 3 and rule[3] == "required"
        raw = facts.get(fact) if isinstance(facts, dict) else None
        actual = _MISSING if raw is None else _coerce(raw, val)
        if actual is _MISSING:
            if required and unknown_required == "fail":
                return False, f"{fact} 값이 없음(필수 조건)"
            continue
        try:
            if not OPS[op](actual, val):
                return False, f"{fact} {op} {val} 불충족(값 {raw})"
        except TypeError:
            if required and unknown_required == "fail":
                return False, f"{fact} 값 형식 오류(필수 조건)"
            continue
    return True, ""
