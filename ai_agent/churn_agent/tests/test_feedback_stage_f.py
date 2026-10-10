"""설문 합성기 4-1페이지(단계 F: hesitate, action, helped, why, wanted, newsol) 검증."""
import collections
import pytest
from feedback import questionnaire as Q
from feedback.mapping import load_config, load_mapping, action_codes
from feedback.simulator import Subject, synthesize, STAGES
from feedback.stage_f import fit_level, solution_type

CFG, MP = load_config(), load_mapping()
N = 4000


def S(i, st="post_action", types=None, hidden=(), sol=None, facts=None, risk="HIGH"):
    return Subject(f"F{i}", f"V{i}", st, risk, types or {}, hidden, {}, frozenset(), sol, facts=facts or {})


def forced(issues=("visit",), top=None, status="think"):
    st = dict(STAGES); st["status"] = lambda c: status; st["issues"] = lambda c: list(issues)
    if top: st["top"] = lambda c: top
    return st


def runs(n, issues=("visit",), top=None, status="think", **kw):
    st = forced(issues, top, status)
    return [synthesize(S(i, **kw), stages=st) for i in range(n)]


def vals(rs, q): return [r.answers[q] for r in rs if q in r.answers]
def dist(v): c = collections.Counter(v); return {k: x / len(v) for k, x in c.items()}
def near(x, t, tol=0.05): return abs(x - t) <= tol


def test_fit_level():
    a = {"T06": .9, "T10": .5, "T02": .1}
    f = lambda sol: fit_level(S(1, types=a, sol=sol), CFG)
    assert f("S-T06-A") == "exact" and f("S-T10-C") == "partial" and f("S-T02-A") == "mismatch" and f("S-T05-C") == "mismatch"
    assert f("S-NT-A") == "mismatch" and f(None) is None and solution_type("S-NT-A") is None and solution_type("S-T09-D") == "T09"


def test_all_f_answers_follow_questionnaire_rules():
    for sol in (None, "S-T06-A", "S-T07-A"):
        for st in ("post_action", "post_churn"):
            for r in runs(1500, issues=("visit", "fail"), st=st, types={"T06": .8, "T09": .5}, sol=sol):
                assert Q.check_answers(r.answers) == [], r.answers


def test_hesitate_probabilities_and_exclusivity():
    for r in runs(1500):
        h = r.answers.get("hesitate")
        if h and "none" in h: assert h == ["none"]
    base = dist([x for r in runs(N * 2) for x in r.answers.get("hesitate", [])])
    assert base.get("pen", 0) < base.get("habit", 0)
    # 조건이 맞으면 해당 항목 선택률이 오른다
    def rate(k, **kw):
        rs = [r for r in runs(N * 2, **kw) if "hesitate" in r.answers]
        return sum(k in r.answers["hesitate"] for r in rs) / len(rs)
    assert near(rate("pen", types={"T02": .8}), .50 * .80, .05)             # none(20%)이 아닐 때만 항목이 남음
    assert near(rate("pen", st="post_churn", facts={"penalty_zone": 2}), .40, .05)
    assert rate("pen", st="post_action", facts={"penalty_zone": 2}) < .15       # 해지 접수 전에는 구간만으로 올리지 않음
    assert near(rate("pick", facts={"large_appliance_yn": "Y"}), .30 * .80, .05)
    assert near(rate("term", facts={"days_to_end": 300}), .40 * .80, .05) and rate("term", facts={"days_to_end": 100}) < .15
    assert near(rate("visit", facts={"visit_care_yn": "Y"}), .35 * .80, .05)


def test_action_uses_solution_mapping():
    for sol in ("S-T06-A", "S-T01-D", "S-T03-B", "S-T05-A"):          # S-T05-A는 대응 없음 -> other
        v = set(vals(runs(1500, sol=sol), "action"))
        assert v <= set(action_codes(sol)), (sol, v)
    d = dist(vals(runs(N * 3, sol="S-T06-D"), "action"))                 # apology 주 70% / fee 부 30%
    assert near(d["apology"], .70) and near(d["fee"], .30)


def test_action_without_solution_is_no_or_never():
    d = dist(vals(runs(N * 2), "action"))
    assert set(d) == {"no", "never"} and near(d["no"], .55)


def test_helped_follows_fit_table():
    types = {"T06": .9, "T10": .5}
    for sol, fit in [("S-T06-A", "exact"), ("S-T10-C", "partial"), ("S-T01-D", "mismatch")]:
        d = dist(vals(runs(N * 3, types=types, sol=sol), "helped"))
        for k, p in CFG["stage_f"]["helped_by_fit"][fit].items(): assert near(d[k], p, .05), (sol, k, d)


def test_helped_post_churn_halves_solved_and_keeps_sum():
    d = dist(vals(runs(N * 3, st="post_churn", types={"T06": .9}, sol="S-T06-A"), "helped"))
    assert near(d["solved"], .25) and near(d["part"], .30) and d["little"] > .15 and d["worse"] > .05
    assert abs(sum(d.values()) - 1) < 1e-9


def test_helped_and_why_only_when_received():
    for r in runs(2000, sol=None): assert "helped" not in r.answers and "why" not in r.answers
    for r in runs(2000, sol="S-T06-A", types={"T06": .9}):
        if "why" in r.answers: assert r.answers["helped"] in ("part", "little", "worse")


def test_why_count_and_weights():
    rs = [r for r in runs(N * 4, sol="S-T01-D", types={"T09": .8}) if "why" in r.answers]
    c = collections.Counter(len(r.answers["why"]) for r in rs)
    assert near(c[1] / len(rs), .60, .06) and set(c) <= {1, 2}
    allw = collections.Counter(x for r in rs for x in r.answers["why"])
    assert allw["again"] > allw["late"] and allw["again"] > allw["promise"]                 # T09 활성 -> again
    rs2 = [r for r in runs(N * 4, sol="S-T01-D", types={"T06": .8}) if "why" in r.answers]
    w2 = collections.Counter(x for r in rs2 for x in r.answers["why"])
    assert w2["late"] > w2["again"]
    rs3 = [r for r in runs(N * 4, sol="S-T01-D", hidden=("H-A",)) if "why" in r.answers]
    w3 = collections.Counter(x for r in rs3 for x in r.answers["why"])
    assert w3["unclear"] > w3["promise"]


def test_why_mismatch_higher_when_fit_is_mismatch():
    def share(sol):
        rs = [r for r in runs(N * 4, sol=sol, types={"T06": .9}) if "why" in r.answers]
        return sum("mismatch" in r.answers["why"] for r in rs) / len(rs)
    assert share("S-T01-D") > share("S-T06-A") + .15


def test_wanted_follows_issue_mapping():
    for top, (main, sub) in [("visit", ("resched", "fee")), ("cost", ("fee", "change")), ("idle", ("pause", "change"))]:
        d = dist(vals(runs(N * 2, issues=(top,), sol=None), "wanted"))
        assert near(d[main], .80 * .60 + .02, .06)
        assert d[main] > d[sub] > .15
        assert near(sum(d.get(k, 0) for k in (main, sub)), .80 + .10 * 2 / 10, .05)            # 20% 대체분 중 무작위 절반이 우연히 같은 보기를 고름
        assert near(d["hard"], .10, .04)


def test_wanted_for_other_issue_is_hard_or_random():
    d = dist(vals(runs(N, issues=("other",)), "wanted"))
    assert near(d["hard"], .50, .10) and len(d) > 4


def test_wanted_uses_top_when_multiple_issues():
    d = dist(vals(runs(N * 2, issues=("visit", "cost"), top="cost"), "wanted"))
    assert d["fee"] > d.get("resched", 0)


def test_newsol_follows_mapping_and_none_share():
    rs = runs(N * 3, issues=("visit",))
    nv = vals(rs, "newsol")
    d = dist([x == ["none"] for x in nv]); assert near(d.get(True, 0), .15 + .85 * .40, .05)   # none 15% + 해당 보기를 하나도 안 고른 40%
    only = collections.Counter(x for v in nv for x in v if x != "none")
    assert set(only) == {"alert"}
    rs = runs(N * 3, issues=("exit", "idle"))
    got = {x for r in rs for x in r.answers.get("newsol", []) if x != "none"}
    assert got == {"step", "swap", "pause"}
    for r in rs:
        v = r.answers.get("newsol", [])
        if "none" in v: assert v == ["none"]


def test_newsol_other_issue():
    got = collections.Counter(x for r in runs(N * 2, issues=("other",)) for x in r.answers.get("newsol", []))
    assert got["other"] > 0 and set(got) <= {"other", "none"}


def test_f_skipped_when_sat_or_none():
    for r in runs(500, issues=("none",)):
        assert not {"action", "helped", "why", "wanted", "newsol"} & set(r.answers)
    for r in [synthesize(S(i), stages=forced(status="sat")) for i in range(500)]:
        assert not {"hesitate", "action", "wanted", "newsol"} & set(r.answers)


def test_reproducible_f():
    a = [synthesize(S(i, sol="S-T06-A", types={"T06": .9}), stages=forced()).answers for i in range(200)]
    b = [synthesize(S(i, sol="S-T06-A", types={"T06": .9}), stages=forced()).answers for i in range(200)]
    assert a == b
