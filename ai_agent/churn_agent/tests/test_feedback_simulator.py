"""설문 합성기 2페이지(단계 A~D) 검증: 재현성, 시안 규칙 준수, 설정값 근처의 분포."""
import collections, random
import pytest
from feedback import questionnaire as Q
from feedback.mapping import load_config, load_mapping
from feedback.simulator import Subject, synthesize, draw_drop_index, event_bucket, stage_a, _source_weights, Ctx

CFG, MP = load_config(), load_mapping()
N = 4000


def subj(i, st="post_action", risk="MID", types=None, hidden=(), ev=None, rep=frozenset()):
    return Subject(f"S{i}", f"V{i}", st, risk, types or {}, hidden, ev or {}, rep)


def run(n, **kw):
    return [synthesize(subj(i, **kw)) for i in range(n)]


def within(x, target, tol): return abs(x - target) <= tol


def test_reproducible_and_seed_changes_result():
    a = [synthesize(subj(i, types={"T06": .8})).answers for i in range(300)]
    b = [synthesize(subj(i, types={"T06": .8})).answers for i in range(300)]
    assert a == b
    cfg2 = dict(CFG, seed_salt="other-salt")
    c = [synthesize(subj(i, types={"T06": .8}), cfg=cfg2).answers for i in range(300)]
    assert a != c


def test_later_stage_does_not_change_earlier_answers():
    """문항별 독립 난수: 뒤 문항이 추가돼도 앞 문항 결과가 같아야 한다(여기선 마지막 deepB 단계를 빼고 비교)."""
    from feedback.simulator import STAGES
    part = {k: v for k, v in STAGES.items() if k != "deepB"}
    for i in range(300):
        s = subj(i, types={"T06": .8}, ev={"visit": 2})
        full, cut = synthesize(s).answers, synthesize(s, stages=part).answers
        assert cut == {k: full[k] for k in cut} and "deepB" not in cut      # 뒤 문항이 없을 때와 있을 때 앞 문항 응답이 같다


def test_response_rate_by_survey_type():
    for st, rate in CFG["response_rate"].items():
        rs = run(N, st=st, risk="HIGH" if st != "post_churn" else "MID")
        got = sum(r.responded for r in rs) / N
        assert within(got, rate, 0.03), (st, got, rate)


def test_non_responder_has_no_answers():
    for r in run(500):
        if not r.responded: assert r.answers == {} and r.completed is None


def test_dropout_rate_and_position_weighting():
    n = 20000
    drops = sum(stage_a(subj(i), CFG)[1] is not None for i in range(n)) / n
    assert within(drops, CFG["dropout_rate"], 0.01)
    rng = random.Random(1); L = len(Q.ORDER)
    idx = [draw_drop_index(rng, L) for _ in range(40000)]
    assert min(idx) >= 1 and max(idx) <= L - 1                        # status는 항상 응답
    c = collections.Counter(idx)
    first, last = sum(c[i] for i in range(1, 7)), sum(c[i] for i in range(L - 6, L))
    assert last > 3 * first                                           # 뒤쪽 문항에서 훨씬 많이 멈춤


def test_dropout_marks_incomplete_and_stops_before_question():
    got = 0
    for r in run(3000, risk="HIGH", types={"T06": .8}):
        if r.dropped_at:
            got += 1
            assert r.completed is False and r.dropped_at not in r.answers
    assert got > 0


@pytest.mark.parametrize("risk", ["HIGH", "MID", "LOW"])
def test_status_distribution_before_churn(risk):
    rs = [r for r in run(N * 2, risk=risk) if r.responded]
    c = collections.Counter(r.answers["status"] for r in rs)
    for k, p in CFG["status_given_before_churn"][risk].items():
        assert within(c[k] / len(rs), p, 0.04), (risk, k, c)
    assert set(c) <= {"think", "sat"}


def test_status_after_churn():
    rs = [r for r in run(N * 2, st="post_churn") if r.responded]
    c = collections.Counter(r.answers["status"] for r in rs)
    assert set(c) == {"done", "ing"} and within(c["done"] / len(rs), 0.5, 0.05)


def test_sat_skips_discomfort_section_and_goes_to_context():
    """sat이면 불편·해결·이용 방식 문항 응답이 없고 안내 방식·생활 환경으로 바로 간다."""
    seen = full = 0
    for r in run(N, risk="LOW"):
        if r.answers.get("status") == "sat":
            seen += 1
            assert not set(r.answers) & (Q.SAT_SKIP - {"status"})
            if r.completed: full += 1; assert len({"channel", "hh", "daytime", "exp", "interview"} & set(r.answers)) >= 3 and "who" not in r.answers   # 선택 문항은 5% 건너뜀
    assert seen > 100 and full > 50


def test_all_answers_follow_questionnaire_rules():
    for kw in [dict(), dict(types={"T06": .8, "T10": .5}, ev={"visit": 3}), dict(hidden=("H-A",)), dict(st="post_churn", types={"T02": .9})]:
        for r in run(1500, **kw):
            assert Q.check_answers(r.answers) == [], (kw, r.answers)


def test_issues_none_only_without_signal():
    for r in run(1500, types={"T06": .8}):
        assert r.answers.get("issues") != ["none"]
    for r in run(1500, hidden=("H-A",)):
        assert r.answers.get("issues") != ["none"]
    rs = [r for r in run(N * 2) if r.answers.get("issues")]
    assert within(sum(r.answers["issues"] == ["none"] for r in rs) / len(rs), CFG["issues_none_rate_no_signal"], 0.04)


def test_issues_pick_count_matches_config_and_dedup():
    """선택 횟수 k는 설정(50/35/15%)을 따르고, 같은 코드가 겹치면 한 번만 세므로 최종 개수는 k 이하."""
    rs = [r for r in run(N * 2, types={"T06": .8}) if r.truth.get("issue_picks")]
    c = collections.Counter(len(r.truth["issue_picks"]) for r in rs)
    for k, p in CFG["issues_k"].items():
        assert within(c[k] / len(rs), p, 0.03), (k, c)
    assert all(len(r.answers["issues"]) <= len(r.truth["issue_picks"]) for r in rs)
    assert all(len(set(r.answers["issues"])) == len(r.answers["issues"]) for r in rs)


def test_issue_source_share_per_pick():
    """§5 '유형↔불편 코드': 한 번의 선택 중 자기 유형 출처 비율이 설정(60%)±10%p."""
    picks = [p for r in run(N * 2, types={"T06": .8}) for p in r.truth.get("issue_picks", [])]
    share = collections.Counter(s for s, _ in picks)
    assert within(share["own_type"] / len(picks), 0.60, 0.04)
    assert within(share["hidden_topic"] / len(picks), 0.0, 0.0001)      # 숨은 주제가 없으면 나오지 않음
    assert all(c == "visit" for s, c in picks if s == "own_type")


def test_hidden_topic_share_and_codes():
    picks = [p for r in run(N * 2, types={"T06": .8}, hidden=("H-A",)) for p in r.truth.get("issue_picks", [])]
    c = collections.Counter(s for s, _ in picks)
    assert within(c["hidden_topic"] / len(picks), CFG["issue_source_hidden_boost"], 0.04)
    assert {code for s, code in picks if s == "hidden_topic"} == {"cost", "exit"}


def test_no_active_type_redistribution():
    # 활성 유형 없음 + 숨은 주제 있음 -> 숨은 주제 몫이 자기 유형 몫(60%)까지 받아 80%
    picks = [p for r in run(N * 2, types={"T06": .1}, hidden=("H-B",)) for p in r.truth.get("issue_picks", [])]
    c = collections.Counter(s for s, _ in picks)
    assert c["own_type"] == 0 and within(c["hidden_topic"] / len(picks), 0.80, 0.04)
    assert {code for s, code in picks if s == "hidden_topic"} == {"idle", "cost"}
    # 활성 유형 없음 + 숨은 주제 없음 -> 자기 유형 몫이 다른 기존 유형으로
    picks = [p for r in run(N * 2, types={"T06": .1}) if r.answers.get("issues") != ["none"] for p in r.truth.get("issue_picks", [])]
    c = collections.Counter(s for s, _ in picks)
    assert c["own_type"] == 0 and c["hidden_topic"] == 0 and within(c["other_type"] / len(picks), 0.70 / 0.80, 0.04)


def test_source_weights_sum_to_one():
    class S: pass
    for act in (True, False):
        for hid in (True, False):
            c = Ctx(subj(1), CFG, MP, {}, "issues")
            w = _source_weights(c, act, hid)
            assert abs(sum(w.values()) - 1) < 1e-9 and all(v >= 0 for v in w.values()), (act, hid, w)


def test_other_type_prefers_confusable_pairs():
    # T05(케어)가 활성이면 헷갈리는 쌍 T07(manager), T09(fail)이 다른 유형 중 3배 가중
    picks = [c for r in run(N * 4, types={"T05": .9}) for s, c in r.truth.get("issue_picks", []) if s == "other_type"]
    cnt = collections.Counter(picks)
    base = cnt["sales"] / 1                                             # T08은 쌍이 없음
    assert cnt["manager"] > 2 * base and cnt["fail"] > 2 * base and cnt["care"] == 0


def test_top_asked_only_with_two_or_more_and_in_selection():
    for r in run(3000, types={"T06": .8, "T10": .6}):
        iss = r.answers.get("issues") or []
        real = [x for x in iss if x != "none"]
        if "top" in r.answers: assert len(real) >= 2 and r.answers["top"] in real
        if len(real) < 2: assert "top" not in r.answers


def test_top_prefers_strongest_own_type():
    n = hit = 0
    for r in run(N * 3, types={"T06": .9, "T10": .5}):
        a = r.answers
        if "top" in a and "visit" in a["issues"] and r.truth["own_strength"].get("visit", 0) > r.truth["own_strength"].get("cs", 0):
            n += 1; hit += a["top"] == "visit"
    assert n > 200 and within(hit / n, CFG["top_own_strongest"] + (1 - CFG["top_own_strongest"]) * 0, 0.08)


def test_single_issue_makes_top_implicit():
    for r in run(1500, types={"T06": .8}):
        a = r.answers
        if a.get("issues") == ["visit"] and "form" in a: assert "top" not in a and Q.top_key(a) == "visit"


def test_event_bucket():
    s = subj(1, ev={"visit": 2, "fail": 1}, rep=frozenset({"care"}))
    assert [event_bucket(s, c) for c in ("visit", "fail", "care", "cs", "idle", "other")] == ["many", "one", "many", "none", "none", "none"]
    assert event_bucket(subj(1, ev={"idle": 5}), "idle") == "none"


@pytest.mark.parametrize("ev,bucket", [({"visit": 3}, "many"), ({"visit": 1}, "one"), ({}, "none")])
def test_form_distribution_follows_event_count(ev, bucket):
    forms = [r.answers["form"] for r in run(N * 3, types={"T06": .9}, ev=ev) if r.answers.get("issues") == ["visit"] and "form" in r.answers]
    c = collections.Counter(forms)
    assert len(forms) > 300
    for k, p in CFG["form_given_events"][bucket].items():
        assert within(c[k] / len(forms), p, 0.07), (bucket, k, c)


def test_item_skip_only_optional():
    rs = [r for r in run(N * 2, types={"T06": .8, "T10": .6}) if r.answers.get("issues") and len([x for x in r.answers["issues"] if x != "none"]) >= 2 and r.answers.get("status") == "think"]
    reached = [r for r in rs if r.completed]                            # 끝까지 합성된 응답
    miss_top = sum("top" not in r.answers for r in reached) / len(reached)
    assert within(miss_top, CFG["item_skip_rate"], 0.03)
    assert all("status" in r.answers and "issues" in r.answers for r in reached)   # 필수 문항은 건너뛰지 않음


def test_subject_validation():
    with pytest.raises(ValueError): Subject("a", "b", "nope")
    with pytest.raises(ValueError): Subject("a", "b", "post_action", "MAX")
