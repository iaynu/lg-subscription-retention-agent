"""설문 코드표(feedback/questionnaire.py)를 시안 HTML에서 직접 뽑은 값과 대조한다. 시안이 바뀌면 이 테스트가 먼저 깨진다."""
import re
from pathlib import Path
import pytest
from feedback import questionnaire as Q

HTML = (Path(__file__).resolve().parents[1] / "data" / "survey_form_v2.html").read_text(encoding="utf-8")
DKS = {"DK": "?", "DK2": "?"}


def _balanced(s, i):
    """s[i]가 '[' 또는 '{'일 때 짝이 맞는 닫는 괄호까지의 문자열(따옴표 안은 무시)."""
    op = s[i]; cl = {"[": "]", "{": "}", "(": ")"}[op]; d = 0; q = None
    for j in range(i, len(s)):
        c = s[j]
        if q:
            if c == q and s[j - 1] != "\\": q = None
            continue
        if c in "'\"": q = c
        elif c == op: d += 1
        elif c == cl:
            d -= 1
            if d == 0: return s[i:j + 1]
    raise AssertionError("괄호가 닫히지 않음")


def _codes(lit):
    """[['a','라벨'],['b','라벨'],DK] 같은 보기 목록에서 코드만 뽑는다."""
    body = lit[1:-1]; out = []; i = 0
    while i < len(body):
        c = body[i]
        if c == "[":
            blk = _balanced(body, i); out.append(re.match(r"\[\s*'([^']*)'", blk).group(1)); i += len(blk)
        elif body.startswith("DK2", i): out.append("?"); i += 3
        elif body.startswith("DK", i): out.append("?"); i += 2
        else: i += 1
    return out


def _var(name):
    m = re.search(r"var %s = (\[)" % name, HTML)
    return _codes(_balanced(HTML, m.start(1)))


def _opts_after(pos):
    m = re.compile(r"opts:\s*").search(HTML, pos)
    j = m.end(); rest = HTML[j:j + 40]
    if rest.startswith("["): return _codes(_balanced(HTML, j)), m.end()
    if rest.startswith("ISSUES.concat("):
        inner = _balanced(HTML, j + len("ISSUES.concat")); return _var("ISSUES") + _codes(inner[1:-1]), m.end()
    if rest.startswith("solOpts("):
        inner = _balanced(HTML, j + len("solOpts")); return _var("SOL")[:-1] + _codes(inner[1:-1]) + ["other"], m.end()
    if rest.startswith("NEWSOL"): return _var("NEWSOL"), m.end()
    return None, m.end()                                             # realIssues().map / d.opts 처럼 응답에 따라 정해지는 보기


def test_option_lists_match_html():
    assert list(Q.ISSUES) == _var("ISSUES")
    assert list(Q.SOL) == _var("SOL")[:-1] and _var("SOL")[-1] == "other"
    assert list(Q.NEWSOL) == _var("NEWSOL")


def test_deep_questions_match_html():
    m = re.search(r"var DEEP = (\{)", HTML); deep = _balanced(HTML, m.start(1))
    for key in Q.DEEP:
        km = re.search(r"\b%s:\[" % key, deep); blk = _balanced(deep, km.end() - 1)
        a, pa = _opts_after(0) if False else (None, 0)
        found = []; pos = 0
        for _ in range(2):
            mm = re.compile(r"opts:\s*").search(blk, pos); lit = _balanced(blk, mm.end()); found.append(_codes(lit)); pos = mm.end() + len(lit)
        assert [list(x) for x in Q.DEEP[key]] == found, key


def test_static_question_options_match_html():
    seen = {}
    for m in re.finditer(r"\bid:'(\w+)',\s*type:'(single|multi|text)'", HTML):
        qid, kind = m.group(1), m.group(2)
        codes, _ = _opts_after(m.end()) if kind != "text" else (None, 0)
        seen[qid] = (kind, codes)
    for qid, (kind, opts) in Q.QUESTIONS.items():
        assert qid in seen, f"시안에 {qid} 문항이 없음"
        assert seen[qid][0] == kind, qid
        if seen[qid][1] is not None and opts is not None:
            assert sorted(seen[qid][1]) == sorted(opts), f"{qid}: {seen[qid][1]} vs {list(opts)}"
    extra = set(seen) - set(Q.QUESTIONS)
    assert not extra, f"코드표에 없는 시안 문항: {extra}"


def test_question_order_and_skip_tables_match_html():
    ids = re.findall(r"^\s*\{ id:'(\w+)', (?:skip|def)", HTML, flags=re.M)
    ids = [x for q in ids for x in (["hh", "daytime", "exp", "who"] if q == "ctx" else [q])]
    assert ids == list(Q.ORDER)
    skipb = dict(re.findall(r"(\w+):'(\w+)'", re.search(r"var SKIPB = (\{[^}]*\})", HTML).group(1)))
    assert skipb == Q.SKIPB
    sat = re.findall(r"'(\w+)'", re.search(r"var SAT_SKIP = (\[[^\]]*\])", HTML).group(1))
    assert set(sat) | {"who"} == set(Q.SAT_SKIP)                     # who는 시안에서 ctx 그룹 필터로 따로 뺀다


def test_visible_ids_follow_skip_rules():
    assert Q.visible_ids({"status": "sat"}) == ["status", "channel", "hh", "daytime", "exp", "interview", "open"]   # 만족: 불편·해결·이용 방식·who 건너뜀
    a = {"status": "think", "issues": ["none"]}
    v = Q.visible_ids(a)
    assert not {"top", "form", "deepA", "deepB", "action", "helped", "why", "wanted", "newsol", "firstact"} & set(v) and "motive" in v
    one = {"status": "think", "issues": ["visit"]}
    v = Q.visible_ids(one); assert "top" not in v and "form" in v and "deepA" in v and "deepB" in v   # 1개면 top을 묻지 않고 그 코드가 top
    two = {"status": "think", "issues": ["visit", "fail"], "top": "visit", "deepA": "0"}
    assert "deepB" not in Q.visible_ids(two)                                                          # visit=0이면 deepB 생략
    assert "deepB" not in Q.visible_ids({"status": "think", "issues": ["idle"], "deepA": "daily"})
    other = {"status": "think", "issues": ["other"]}
    assert "deepA" not in Q.visible_ids(other) and "form" in Q.visible_ids(other)                     # other는 심화 없음
    full = dict(two, deepA="2", action="free", helped="part")
    assert "helped" in Q.visible_ids(full) and "why" in Q.visible_ids(full)
    assert "why" not in Q.visible_ids(dict(full, helped="solved")) and "helped" not in Q.visible_ids(dict(full, action="no"))
    assert "gap" not in Q.visible_ids({"status": "think"}) and "gap" in Q.visible_ids({"status": "think", "motive": "cost"})


def test_options_depend_on_previous_answers():
    assert Q.options("top", {"issues": ["cost", "fail"]}) == ("cost", "fail")
    assert Q.options("deepA", {"issues": ["care"]}) == Q.DEEP["care"][0]
    assert Q.options("deepB", {"issues": ["cost", "exit"], "top": "exit"}) == Q.DEEP["exit"][1]


def test_check_answers_catches_violations():
    assert Q.check_answers({"status": "think", "issues": ["cost"], "form": "fade"}) == []
    assert any("보기에 없는" in e for e in Q.check_answers({"status": "bye"}))
    assert any("함께 고를 수 없음" in e for e in Q.check_answers({"status": "think", "issues": ["none", "cost"]}))
    assert any("건너뛰어야" in e for e in Q.check_answers({"status": "sat", "motive": "cost"}))
    assert any("알 수 없는" in e for e in Q.check_answers({"zzz": "x"}))
