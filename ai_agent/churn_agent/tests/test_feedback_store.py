"""설문 합성 5페이지: 숨은 주제 배정, 설문 대상 선정, long 형식 변환, CSV/PostgreSQL 저장, 검증 함수."""
import collections, datetime as dt, os
import pandas as pd
import pytest
from feedback import questionnaire as Q, store, topics, validate
from feedback.mapping import load_config
from feedback.population import load_catalog_solutions, plan_survey, stand_in_solution
from feedback.simulator import Subject, SurveyResult, synthesize, STAGES

CFG = load_config()
WHEN = dt.datetime(2026, 10, 1, 10)


def test_topics_are_deterministic_and_rates_match():
    a = [topics.assign_topics(f"S{i}") for i in range(20000)]
    assert a == [topics.assign_topics(f"S{i}") for i in range(20000)]
    n = len(a)
    assert abs(sum("H-A" in x for x in a) / n - .05) < .008 and abs(sum("H-B" in x for x in a) / n - .04) < .008
    assert all(.5 <= v <= 1.0 for x in a for v in x.values())


def test_topics_concentrate_on_no_type_mid_plus():
    base = sum(bool(topics.assign_topics(f"S{i}")) for i in range(20000)) / 20000
    boosted = sum(bool(topics.assign_topics(f"S{i}", True)) for i in range(20000)) / 20000
    assert boosted > 2.4 * base


def test_adding_a_topic_would_not_change_existing_topic_draws():
    """주제마다 난수를 두 번씩 항상 뽑으므로, 한 주제의 결과는 다른 주제의 유무와 무관하다."""
    a = topics.assign_topics("S1"); b = topics.assign_topics("S1", cfg=dict(CFG, hidden_topics=dict(CFG["hidden_topics"], rate={"H-A": .05, "H-B": 0.0})))
    assert a.get("H-A") == b.get("H-A")


@pytest.mark.parametrize("risk,rank,typed,churned,exp", [
    ("HIGH", .01, True, False, "post_action"), ("MID", .2, False, False, "no_type"), ("HIGH", .01, True, True, "post_churn"), ("MID", .1, False, True, "post_churn"),
    ("LOW", .25, True, False, "survey_only"), ("LOW", .5, False, False, None), ("LOW", .1, True, True, None), ("LOW", .2, False, False, "survey_only"), ("LOW", .3, False, False, None)])
def test_plan_survey(risk, rank, typed, churned, exp): assert plan_survey(risk, rank, typed, churned, CFG) == exp


def test_stand_in_solution_is_lowest_priority_active_of_type():
    sols = load_catalog_solutions()
    assert all(s["active_yn"] == "Y" and s["agent_selectable_yn"] == "Y" for s in sols)
    for t in ("T01", "T06", "T09"):
        sid = stand_in_solution(t, sols); mine = [s for s in sols if s["type_id"] == t]
        assert sid and min(s["priority"] for s in mine) == next(s["priority"] for s in mine if s["solution_id"] == sid)
    assert stand_in_solution(None, sols) is None and stand_in_solution("T99", sols) is None


CFG6 = dict(CFG, response_rate=dict(CFG["response_rate"], post_action=.6))


def make(n=400):
    subs = [Subject(f"S{i:04d}", f"SV-{i}", "post_action", "HIGH", {"T06": .8}, ("H-A",) if i % 5 == 0 else (), {}, frozenset(), "S-T06-A",
                    {"H-A": .7} if i % 5 == 0 else {}) for i in range(n)]
    return subs, [synthesize(s, cfg=CFG6) for s in subs]


def test_to_rows_long_format_roundtrip():
    subs, res = make(); rows = store.to_rows(subs, res, WHEN)
    assert len(rows["survey_send"]) == len(subs) and [r["survey_id"] for r in rows["survey_send"]] == list(range(1, len(subs) + 1))
    assert sum(r.responded for r in res) == len(rows["survey_response"]) == sum(x["responded_yn"] == "Y" for x in rows["survey_send"])
    assert {t["subscription_id"] for t in rows["synth_truth_topic"]} == {s.subscription_id for s in subs if s.hidden_topics}
    # 응답 복원: 저장된 행에서 다시 읽으면 합성 결과와 같다
    ans = collections.defaultdict(lambda: collections.defaultdict(list))
    for a in rows["survey_answer"]: ans[a["response_id"]][a["question_id"]].append((a["answer_code"], a["other_text"]))
    resp = {r["survey_id"]: r for r in rows["survey_response"]}
    for i, r in enumerate(res, start=1):
        if not r.responded: assert i not in resp; continue
        got = ans[resp[i]["response_id"]]
        assert resp[i]["completed_yn"] == ("Y" if r.completed else "N") and resp[i]["status_code"] == r.answers["status"]
        for q, v in r.answers.items():
            kind = Q.QUESTIONS[q][0]
            if kind == "text": assert got[q] == [(None, v)]
            elif kind == "multi": assert sorted(c for c, _ in got[q]) == sorted(v)
            else: assert [c for c, _ in got[q]] == [v]
        assert set(got) == set(r.answers)


def test_other_text_goes_with_other_code():
    subs, res = make(1500); rows = store.to_rows(subs, res, WHEN)
    oth = [a for a in rows["survey_answer"] if a["question_id"] == "issues" and a["answer_code"] == "other"]
    assert oth and all(a["other_text"] for a in oth)
    assert all(a["other_text"] is None for a in rows["survey_answer"] if a["question_id"] == "issues" and a["answer_code"] != "other")
    yes = [a for a in rows["survey_answer"] if a["question_id"] == "interview" and a["answer_code"] == "yes"]
    assert yes and all(a["other_text"].startswith("dummy-") for a in yes)


def test_to_rows_rejects_unfinished_result():
    s = Subject("S1", "V", "post_action", "HIGH")
    r = SurveyResult("S1", "V", "post_action", True, {"status": "think"}, None, None, "issues")
    with pytest.raises(ValueError): store.to_rows([s], [r], WHEN)


def test_write_csv(tmp_path):
    subs, res = make(100); rows = store.to_rows(subs, res, WHEN); cnt = store.write_csv(rows, tmp_path)
    for t, c in store.COLS.items():
        df = pd.read_csv(tmp_path / f"{t}.csv"); assert list(df.columns) == c and len(df) == cnt[t] == len(rows[t])


def test_validation_functions_pass_on_good_data_and_catch_bad():
    subs, res = make(3000)
    checks = {c[0]: c for c in validate.run_all(subs, res, CFG6)}
    assert all(c[1] for c in checks.values()), {k: v[2] for k, v in checks.items() if not v[1]}
    bad = [SurveyResult(r.subscription_id, r.survey_id, r.survey_type, r.responded, dict(r.answers), r.completed, r.dropped_at, r.pending, r.other_text, r.truth) for r in res]
    victim = next(r for r in bad if r.answers.get("status") == "think" and "issues" in r.answers); victim.answers["status"] = "sat"
    assert not validate.check_skip_consistency(subs, bad)[1]                                         # sat인데 건너뛰는 문항 응답이 있음
    bad2 = [SurveyResult(r.subscription_id, r.survey_id, r.survey_type, r.responded, dict(r.answers), r.completed, r.dropped_at, r.pending, r.other_text, r.truth) for r in res]
    for r in bad2[:1500]: r.responded = False
    assert not validate.check_rates(subs, bad2, CFG6)[1]                                                    # 응답률이 크게 벗어남


def test_data_match_check_catches_mismatch():
    subs = [Subject(f"S{i}", f"V{i}", "post_action", "HIGH", {"T06": .9}, facts={"visit_delay_cnt_365d": 2, "visit_cancel_cnt_365d": 1}) for i in range(600)]
    st = dict(STAGES); st["issues"] = lambda c: ["visit"]; st["status"] = lambda c: "think"
    res = [synthesize(s, cfg=dict(CFG, response_rate=dict(CFG["response_rate"], post_action=.9)), stages=st) for s in subs]
    assert validate.check_data_match(subs, res)[1]
    for r in res:
        if r.answers.get("deepA") not in (None, "?"): r.answers["deepA"] = "0"
    assert not validate.check_data_match(subs, res)[1]


def test_cli_requires_database_url(monkeypatch, capsys):
    from feedback import cli
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert cli.main(["run", "--data", "x", "--sql"]) == 2 and "DATABASE_URL" in capsys.readouterr().out


URL = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL 없음")
def test_write_sql_roundtrip():
    from sqlalchemy import create_engine, text
    from .conftest import pg_reset
    e = create_engine(URL, future=True); pg_reset(e)
    subs, res = make(300); rows = store.to_rows(subs, res, WHEN)
    cnt = store.write_sql(e, rows, reset=True)
    assert cnt == {t: len(rows[t]) for t in rows}
    with e.connect() as c:
        assert c.execute(text("SELECT count(*) FROM survey_answer a JOIN survey_response r ON r.response_id=a.response_id JOIN survey_send s ON s.survey_id=r.survey_id")).scalar() == len(rows["survey_answer"])
        assert c.execute(text("SELECT count(*) FROM survey_response r LEFT JOIN survey_send s ON s.survey_id=r.survey_id WHERE s.survey_id IS NULL")).scalar() == 0
    store.write_sql(e, rows, reset=True)                                                              # 다시 적재해도 같은 건수(reset)
    with e.begin() as c:
        c.execute(text("INSERT INTO survey_send (subscription_id, survey_type, sent_at, sent_by) VALUES ('SNEW', 'no_type', now(), 'auto')"))   # 시퀀스가 이어짐
        assert c.execute(text("SELECT max(survey_id) FROM survey_send")).scalar() == len(subs) + 1
