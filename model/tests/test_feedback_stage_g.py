"""설문 합성기 4-2페이지(단계 G + 주관식) 검증."""
import collections
import pytest
from feedback import questionnaire as Q, pool
from feedback.mapping import load_config, load_mapping
from feedback.simulator import Subject, synthesize, STAGES

CFG, MP = load_config(), load_mapping()
N = 4000


def S(i, st="post_action", types=None, hidden=(), sol=None, facts=None, risk="HIGH"):
    return Subject(f"G{i}", f"V{i}", st, risk, types or {}, hidden, {}, frozenset(), sol, facts=facts or {})


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


def test_all_answers_valid_and_complete_run_reaches_the_end():
    done = 0
    for kw in [dict(), dict(types={"T06": .8, "T02": .5}, hidden=("H-A", "H-B")), dict(st="post_churn", sol="S-T06-A", types={"T06": .9})]:
        for r in runs(1500, issues=("visit", "cost"), **kw):
            assert Q.check_answers(r.answers) == [], r.answers
            assert r.pending is None
            done += bool(r.completed)
    assert done > 100


def test_motive_base_and_hidden_overrides():
    d = dist(vals(runs(N * 3), "motive"))
    for k, p in CFG["stage_g"]["motive"].items(): assert near(d[k], p, .04), (k, d)
    assert dist(vals(runs(N * 3, hidden=("H-A",)), "motive"))["default"] > .62
    assert dist(vals(runs(N * 3, hidden=("H-B",)), "motive"))["care"] > .52
    d = dist(vals(runs(N * 3, hidden=("H-A", "H-B")), "motive")); assert near(d["default"], .40, .06) and near(d["care"], .40, .06)
    base, t4 = dist(vals(runs(N * 3), "motive")), dist(vals(runs(N * 3, types={"T04": .8}), "motive"))
    assert t4["care"] > base["care"] and t4["new"] > base["new"]


def test_gap_depends_on_motive_and_hidden():
    rs = runs(N * 4, hidden=("H-A",))
    d = dist([r.answers["gap"] for r in rs if r.answers.get("motive") == "default" and "gap" in r.answers])
    assert near(d["below"], .80, .08)
    d = dist(vals(runs(N * 4, hidden=("H-B",)), "gap")); assert d["below"] > .55
    d = dist(vals(runs(N * 4, types={"T06": .8}), "gap")); assert near(d["below"], .55, .08) or d["below"] > .4
    d = dist(vals(runs(N * 4), "gap")); assert d["same"] > d["below"]


def test_gap_only_after_motive():
    for r in runs(1500):
        if "gap" in r.answers: assert "motive" in r.answers


def test_firstact_follows_voc_channel():
    for ch, exp in [("phone", "call"), ("app", "app"), ("chatbot", "app")]:
        assert set(vals(runs(500, facts={"voc_cnt": 2, "voc_channel": ch}), "firstact")) == {exp}
    d = dist(vals(runs(N * 2), "firstact")); assert near(d["wait"], .45, .06) and near(d["search"], .25, .06)


def test_research_conditions_and_none_exclusive():
    for r in runs(1500, types={"T02": .8, "T01": .7}, facts={"voc_cnt": 2}):
        v = r.answers.get("research")
        if v and "none" in v: assert v == ["none"]
    def rate(k, **kw):
        rs = [r for r in runs(N * 2, **kw) if "research" in r.answers]
        return sum(k in r.answers["research"] for r in rs) / len(rs)
    assert rate("penalty", types={"T02": .8}) > rate("penalty") + .25
    assert rate("compare", types={"T01": .8}) > rate("compare") + .25
    assert rate("ask", facts={"voc_cnt": 3}) > rate("ask") + .25
    assert near(rate("review"), .30 * .75, .05)
    assert rate("agency", types={"T10": .9}, facts={"unresolved_voc_cnt": 2}) > .08 and rate("agency", types={"T10": .9}) == 0


def test_after_by_status_and_modifiers():
    st = dict(STAGES); st["issues"] = lambda c: ["visit"]
    def go(status, **kw):
        st["status"] = lambda c: status
        return dist([a for a in (synthesize(S(i, **kw), stages=st).answers.get("after") for i in range(N * 2)) if a])
    think = go("think"); assert near(think["unsure"], .40, .05)
    done = go("done"); assert near(done["buy"], .25, .05) and done["unsure"] < think["unsure"]
    assert go("done", types={"T04": .8})["none"] > done["none"] + .05
    assert go("done", types={"T01": .8})["sub"] > done["sub"] + .03
    assert go("done", hidden=("H-B",))["keep"] > done["keep"] + .02


def test_channel_rules():
    vs = vals(runs(N * 2), "channel")
    assert set(len(v) for v in vs) <= {1, 2, 3}
    for v in vs:
        if "none" in v: assert v == ["none"]
    d = dist([x for v in vs for x in v]); assert d["talk"] > d["sms"] > d["push"]
    sr = dist([x for v in vals(runs(N * 2, facts={"senior_yn": "Y"}), "channel") for x in v])
    assert sr["sms"] > d["sms"] and sr["call"] > d["call"]


def test_context_questions():
    assert set(vals(runs(500, facts={"household": "baby"}), "hh")) == {"baby"}
    d = dist(vals(runs(N * 2), "hh")); assert near(d["solo"], .35, .05)
    assert dist(vals(runs(N * 2, types={"T06": .8}), "daytime"))["rare"] > .42 and near(dist(vals(runs(N * 2), "daytime"))["rare"], .20, .05)
    d = dist(vals(runs(N * 2, facts={"tenure_months": 48, "other_subs_cnt": 1}), "exp")); assert near(d["more"], .70, .06)
    assert near(dist(vals(runs(N * 2), "exp"))["first"], .55, .05)
    assert dist(vals(runs(N * 2, facts={"agent_inquiry_yn": "Y"}), "who"))["agent"] > .40
    assert near(dist(vals(runs(N * 2), "who"))["self"], .55, .05)


def test_who_not_asked_for_sat():
    for r in [synthesize(S(i), stages=forced(status="sat")) for i in range(1500)]: assert "who" not in r.answers


def test_interview_probability_and_dummy_contact():
    lo = dist(vals(runs(N * 3, types={"T06": .1}), "interview"))["yes"]
    hi = dist(vals(runs(N * 3, types={"T06": 1.0}), "interview"))["yes"]
    assert hi > lo + .04 and near(lo, .15 * .6, .04)
    for r in runs(1500, types={"T06": .9}):
        if r.answers.get("interview") == "yes": assert r.other_text["interview"].endswith("@example.test") and r.other_text["interview"].startswith("dummy-")
        else: assert "interview" not in r.other_text


def test_open_text_rate_and_source_key():
    rs = [r for r in runs(N * 4, types={"T06": .8}) if r.completed]
    wrote = [r for r in rs if "open" in r.answers]
    assert near(len(wrote) / len(rs), .55 * .95, .05)                                       # 작성률 55% x (건너뜀 5% 제외)
    assert all(r.answers["open"] in pool.load_pool()[r.truth["open_key"]] for r in wrote)
    assert all(r.truth["open_key"] == "visit" for r in wrote)                               # 숨은 주제 없음, top=visit
    assert not [r for r in runs(500) if not r.completed and "open" in r.answers]            # 중도 이탈자는 작성하지 않음


def test_open_text_hidden_topic_and_sat():
    rs = [r for r in runs(N * 4, types={"T06": .8}, hidden=("H-A",)) if "open" in r.answers]
    d = dist([r.truth["open_key"] for r in rs]); assert near(d["H-A"], .50, .06) and near(d["visit"], .50, .06)
    rs = [synthesize(S(i), stages=forced(status="sat")) for i in range(N * 3)]
    keys = {r.truth["open_key"] for r in rs if "open" in r.answers}
    assert keys == {"sat"}
    rs = [r for r in runs(N * 3, issues=("none",)) if "open" in r.answers]
    assert {r.truth["open_key"] for r in rs} == {"sat"}


def test_issues_other_text_comes_from_pool():
    rs = [r for r in runs(N, issues=("other",)) if "issues" in r.answers]
    st = dict(STAGES); st.pop("issues")                                              # 원래 issues 합성기로 other가 나오는 경우
    rs = [synthesize(S(i), stages=dict(STAGES)) for i in range(N * 2)]
    got = [r for r in rs if "other" in (r.answers.get("issues") or [])]
    assert got and all(r.other_text["issues"] in pool.load_pool()["issue_other"] for r in got)
    assert all("issues" not in r.other_text for r in rs if "other" not in (r.answers.get("issues") or []))


def test_full_run_is_reproducible_end_to_end():
    mk = lambda: [synthesize(S(i, types={"T06": .8, "T02": .4}, hidden=("H-A",), sol="S-T06-A")) for i in range(300)]
    a, b = mk(), mk()
    assert [(r.answers, r.other_text, r.completed) for r in a] == [(r.answers, r.other_text, r.completed) for r in b]
