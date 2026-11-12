"""설문 합성기 3페이지(단계 E: deepA / deepB) 검증."""
import collections
import pytest
from feedback import questionnaire as Q
from feedback.mapping import load_config, load_mapping
from feedback.simulator import Subject, synthesize, Ctx
from feedback import deep as D

CFG, MP = load_config(), load_mapping()
N = 3000
TOP = {t: c for t, c in MP["type_to_issue"].items()}


def S(i, top, status_risk="HIGH", facts=None, types=None, hidden=()):
    return Subject(f"D{i}", f"V{i}", "post_action", status_risk, types or {}, hidden, {}, frozenset(), facts=facts or {})


def force_top(top):
    """issues를 [top] 하나로 고정해 심화 문항만 검사한다(시드에 따라 달라지는 앞 단계를 건너뛴다)."""
    from feedback.simulator import STAGES
    st = dict(STAGES); st["issues"] = lambda c: [top]; st["status"] = lambda c: "think"
    return st


def deep_answers(top, n=N, **kw):
    out = []
    for i in range(n):
        r = synthesize(S(i, top, **kw), stages=force_top(top))
        out.append(r)
    return [r for r in out if "deepA" in r.answers]


def dist(vals): c = collections.Counter(vals); n = len(vals); return {k: v / n for k, v in c.items()}
def near(x, t, tol=0.05): return abs(x - t) <= tol


@pytest.mark.parametrize("top", [c for c in Q.DEEP])
def test_every_answer_is_valid_option_with_and_without_facts(top):
    facts = {"voc_penalty_cnt_90d": 2, "visit_delay_cnt_365d": 2, "visit_cancel_cnt_365d": 1, "weekly_use_avg": 7, "as_cnt_365d": 4, "symptom_code": "SYM06",
             "response_hours_avg": 60, "repair_days": 20, "unresolved_voc_cnt": 1, "sales_pitch_cnt": 5, "manager_change_cnt": 4, "visit_max_delay_days": 40,
             "monthly_fee": 50000, "remaining_months": 20, "penalty_zone": 1, "new_model_released_yn": "Y", "billed_change_rate_3m": .1}
    for f in (facts, {}):
        for r in deep_answers(top, 600, facts=f):
            assert Q.check_answers(r.answers) == [], (top, r.answers)
            assert r.answers["deepA"] in Q.options("deepA", r.answers)


def test_deep_not_asked_for_other_and_none():
    from feedback.simulator import STAGES
    for iss in (["other"], ["none"]):
        st = dict(STAGES); st["issues"] = lambda c, i=iss: i; st["status"] = lambda c: "think"
        for i in range(300):
            a = synthesize(S(i, "x"), stages=st).answers
            assert "deepA" not in a and "deepB" not in a


def test_skipb_rule_visit_zero_and_idle_daily():
    rs = deep_answers("visit", facts={"visit_delay_cnt_365d": 0, "visit_cancel_cnt_365d": 0})
    assert all(r.answers["deepA"] in ("0", "?") for r in rs)
    assert all("deepB" not in r.answers for r in rs if r.answers["deepA"] == "0")
    assert any("deepB" in r.answers for r in rs if r.answers["deepA"] == "?")             # DK면 deepB를 물음
    rs = deep_answers("idle", facts={"weekly_use_avg": 7})
    assert all("deepB" not in r.answers for r in rs if r.answers["deepA"] == "daily")


# ---- 데이터 일치율 (DK 제외) >= 95% ----
def match_rate(top, facts, qid, expect):
    rs = deep_answers(top, N, facts=facts)
    vals = [r.answers[qid] for r in rs if qid in r.answers and r.answers[qid] != "?"]
    assert len(vals) > 300
    return sum(v == expect for v in vals) / len(vals)


@pytest.mark.parametrize("facts,exp", [({"visit_delay_cnt_365d": 1, "visit_cancel_cnt_365d": 1}, "2"), ({"visit_delay_cnt_365d": 5, "visit_cancel_cnt_365d": 0}, "3"), ({"visit_delay_cnt_365d": 1}, "1")])
def test_visit_a_matches_event_count(facts, exp): assert match_rate("visit", facts, "deepA", exp) >= .95


def test_visit_a_scheduling_difficulty_clamps_to_1_2():
    vals = {r.answers["deepA"] for r in deep_answers("visit", facts={"visit_delay_cnt_365d": 0, "visit_cancel_cnt_365d": 0, "sched_difficulty": True})}
    assert vals <= {"1", "?"}
    vals = {r.answers["deepA"] for r in deep_answers("visit", facts={"visit_delay_cnt_365d": 6, "sched_difficulty": True})}
    assert vals <= {"2", "?"}


@pytest.mark.parametrize("days,exp", [(3, "w1"), (7, "w1"), (10, "w2"), (21, "m1"), (45, "m2")])
def test_visit_b_matches_max_delay(days, exp):
    assert match_rate("visit", {"visit_delay_cnt_365d": 2, "visit_max_delay_days": days}, "deepB", exp) >= .95


@pytest.mark.parametrize("n,exp", [(1, "once"), (2, "2"), (5, "3")])
def test_fail_b_matches_as_count(n, exp): assert match_rate("fail", {"as_cnt_365d": n}, "deepB", exp) >= .95


def test_fail_b_fallback_uses_repeat_count_plus_one():
    assert match_rate("fail", {"repeat_symptom_cnt_90d": 1}, "deepB", "2") >= .95


@pytest.mark.parametrize("code,exp", [("SYM02", "noise"), ("SYM07", "perf"), ("SYM12", "broken")])
def test_fail_a_symptom_group(code, exp): assert match_rate("fail", {"symptom_code": code}, "deepA", exp) >= .95


@pytest.mark.parametrize("sales,exp", [(1, "1"), (3, "2"), (6, "4")])
def test_sales_a_matches_pitch_count(sales, exp): assert match_rate("sales", {"sales_pitch_cnt": sales}, "deepA", exp) >= .95


@pytest.mark.parametrize("n,exp", [(0, "0"), (2, "2"), (7, "3")])
def test_manager_b_matches_change_count(n, exp): assert match_rate("manager", {"manager_change_cnt": n}, "deepB", exp) >= .95


@pytest.mark.parametrize("kw,exp", [({"repair_days": 2}, "d3"), ({"repair_days": 6}, "d7"), ({"repair_days": 12}, "d14"), ({"repair_days": 25}, "d15"), ({"response_hours": 100}, "d7")])
def test_cs_b_matches_days(kw, exp): assert match_rate("cs", kw, "deepB", exp) >= .95


def test_cs_b_open_when_unresolved():
    d = dist([r.answers["deepB"] for r in deep_answers("cs", facts={"repair_days": 2, "unresolved_voc_cnt": 2}) if r.answers.get("deepB")])
    assert near(d.get("open", 0), CFG["deep"]["cs_open_if_unresolved"] * 0.93, 0.04)           # DK 대체분만큼 약간 낮음


def test_exit_b_penalty_bucket_after_churn_and_think_zero_weight():
    from feedback.simulator import STAGES
    st = dict(STAGES); st["issues"] = lambda c: ["exit"]
    fee = {"monthly_fee": 50000, "remaining_months": 20, "penalty_zone": 1}          # 5만 x 20 x 30% = 30만 -> o30
    st["status"] = lambda c: "done"
    vals = [synthesize(S(i, "exit", facts=fee), stages=st).answers.get("deepB") for i in range(N)]
    vals = [v for v in vals if v and v != "?"]
    assert sum(v == "o30" for v in vals) / len(vals) >= .95
    st["status"] = lambda c: "think"
    vals = [synthesize(S(i, "exit", facts=fee), stages=st).answers.get("deepB") for i in range(N)]
    vals = [v for v in vals if v and v != "?"]
    assert near(sum(v == "0" for v in vals) / len(vals), .60, .05)                    # 해지 전: '0' 비중 60%


@pytest.mark.parametrize("fee,months,zone,exp", [(50000, 1, 3, "u10"), (50000, 10, 2, "u30"), (30000, 0, 1, "0")])
def test_expected_penalty_buckets(fee, months, zone, exp):
    c = Ctx(S(1, "exit", facts={"monthly_fee": fee, "remaining_months": months, "penalty_zone": zone}), CFG, MP, {"status": "done"}, "deepB")
    D.b_exit(c); assert c.truth["deepB_data"] == exp


def test_idle_a_buckets_and_daily_cap():
    for u, exp in [(3, "week"), (1, "month"), (0.1, "rare")]:
        assert match_rate("idle", {"weekly_use_avg": u}, "deepA", exp) >= .95
    d = dist([r.answers["deepA"] for r in deep_answers("idle", N * 2, facts={"weekly_use_avg": 8}) if r.answers["deepA"] != "?"])
    assert near(d["daily"], CFG["deep"]["idle_daily_cap"], .04) and near(d["week"], 1 - CFG["deep"]["idle_daily_cap"], .04)


def test_idle_b_by_t04_and_hidden():
    d = dist([r.answers.get("deepB") for r in deep_answers("idle", N * 2, facts={"weekly_use_avg": 1}, types={"T04": .8}) if "deepB" in r.answers and r.answers["deepB"] != "?"])
    assert near(d["noneed"], .4, .05) and near(d["season"], .3, .05)
    d = dist([r.answers.get("deepB") for r in deep_answers("idle", N * 2, facts={"weekly_use_avg": 1}, hidden=("H-A",)) if "deepB" in r.answers and r.answers["deepB"] != "?"])
    assert near(d["gap"], .5, .05)


def test_cost_a_by_t01_strength_and_dk():
    for s, exp in [(.9, "p31"), (.5, "p30"), (.1, "p10")]:
        rs = deep_answers("cost", N, types={"T01": s})
        d = dist([r.answers["deepA"] for r in rs])
        assert near(d.get("?", 0), .10, .03) and near(d[exp], .90, .03), (s, d)


def test_cost_b_promo_missed_conditions():
    base = dist([r.answers["deepB"] for r in deep_answers("cost", N * 2) if r.answers.get("deepB") and r.answers["deepB"] != "?"])
    promo = dist([r.answers["deepB"] for r in deep_answers("cost", N * 2, facts={"billed_change_rate_3m": .2}) if r.answers.get("deepB") and r.answers["deepB"] != "?"])
    both = dist([r.answers["deepB"] for r in deep_answers("cost", N * 2, facts={"promo_days_to_end": -3, "missed_discount_yn": "Y"}) if r.answers.get("deepB") and r.answers["deepB"] != "?"])
    assert base.get("promo", 0) < .02 and near(promo["promo"], .40, .05) and promo["promo"] > 5 * base.get("promo", 0.01)
    assert both["promo"] > .30 and both["missed"] > .25


def test_exit_a_penalty_vs_move():
    pen = dist([r.answers["deepA"] for r in deep_answers("exit", N * 2, facts={"voc_penalty_cnt_90d": 2}) if r.answers["deepA"] != "?"])
    mv = dist([r.answers["deepA"] for r in deep_answers("exit", N * 2, facts={"voc_move_cnt_90d": 1}) if r.answers["deepA"] != "?"])
    assert near(pen["pen"], .55, .05) and near(mv["reinst"], .50, .05) and near(mv["rm"], .25, .05)


def test_renew_a_and_b():
    d = dist([r.answers["deepA"] for r in deep_answers("renew", N * 2, facts={"model_discontinued_yn": "Y"})])
    assert near(d["eol"], .50, .05)
    d = dist([r.answers["deepA"] for r in deep_answers("renew", N * 2, facts={"renewal_notice_contact_cnt": 3})])
    assert near(d["push"], .45, .05)
    rs = deep_answers("renew", N * 2)                                                  # status=think 고정
    d = dist([r.answers["deepB"] for r in rs if r.answers.get("deepB") not in (None, "?")]); assert near(d["think"], .45, .05) and near(d["accept"], .20, .05)
    from feedback.simulator import STAGES
    st = dict(STAGES); st["issues"] = lambda c: ["renew"]; st["status"] = lambda c: "done"
    d = dist([a for a in (synthesize(S(i, "renew"), stages=st).answers.get("deepB") for i in range(N * 2)) if a and a != "?"])
    assert near(d["switch"], .35, .05) and near(d["refuse"], .35, .05)


def test_care_manager_sales_cs_rules():
    d = dist([r.answers["deepA"] for r in deep_answers("care", N * 2, facts={"care_as_14d_cnt": 2})]); assert near(d["dmg"], .5, .05)
    d = dist([r.answers["deepA"] for r in deep_answers("care", N * 2)]); assert near(d["clean"], .4, .05)
    d = dist([r.answers["deepB"] for r in deep_answers("care", N * 2) if r.answers.get("deepB") and r.answers["deepB"] != "?"]); assert near(d["none"], .5, .05)
    d = dist([r.answers["deepA"] for r in deep_answers("manager", N * 2, facts={"tone_complaint_cnt": 1})]); assert near(d["attitude"], .55, .05)
    d = dist([r.answers["deepA"] for r in deep_answers("manager", N * 2, facts={"manager_change_cnt": 3})]); assert near(d["skill"], .55, .05)
    d = dist([r.answers["deepB"] for r in deep_answers("sales", N * 2, facts={"repitch_after_refusal_yn": "Y"}) if r.answers.get("deepB") and r.answers["deepB"] != "?"]); assert near(d["keep"], .7, .05)
    d = dist([r.answers["deepB"] for r in deep_answers("sales", N * 2) if r.answers.get("deepB") and r.answers["deepB"] != "?"]); assert near(d["keep"], .4, .05)
    d = dist([r.answers["deepA"] for r in deep_answers("cs", N * 2, facts={"split_as_receipt_yn": "Y"})]); assert near(d["split"], .5, .05)
    d = dist([r.answers["deepA"] for r in deep_answers("cs", N * 2, facts={"response_hours_avg": 72})]); assert near(d["slow"], .4, .05)


def test_manager_b_missing_record_has_extra_dk():
    d = dist([r.answers["deepB"] for r in deep_answers("manager", N * 2) if r.answers.get("deepB")])
    assert d.get("?", 0) > .09                                                         # 기록 없음 10% + 일반 DK 5~10%


def test_generic_dk_rate_only_where_dk_option_exists():
    rs = deep_answers("visit", N * 2, facts={"visit_delay_cnt_365d": 2, "visit_max_delay_days": 5})
    dk = sum(r.answers["deepA"] == "?" for r in rs) / len(rs)
    assert CFG["dk_rate"][0] - .01 <= dk <= CFG["dk_rate"][1] + .01
    rs = deep_answers("care", N * 2, facts={"care_as_14d_cnt": 2})                      # care A에는 DK 보기가 없다
    assert all(r.answers["deepA"] != "?" for r in rs)


def test_reproducible_deep():
    a = [synthesize(S(i, "visit", facts={"visit_delay_cnt_365d": 2}), stages=force_top("visit")).answers for i in range(200)]
    b = [synthesize(S(i, "visit", facts={"visit_delay_cnt_365d": 2}), stages=force_top("visit")).answers for i in range(200)]
    assert a == b
