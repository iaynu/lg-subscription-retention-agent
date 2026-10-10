import json, pandas as pd, pytest
from pathlib import Path
from feedback import candidates as C, recovery as R

TYPES = [{"type_id": "T01", "type_name": "구독료 부담형"}, {"type_id": "T02", "type_name": "해지·이사 장벽형"}]


def make_df(n_a=12, n_b=11, n_sat=3):
    rows = []
    def add(txt, top="cost", motive="default", gap="below", status="ing", cohort="post_action"):
        rows.append(dict(subscription_id=f"S{len(rows)}", survey_type=cohort, status=status, issues=[top], top=top, motive=motive, gap=gap,
                         deepA=None, deepB=None, wanted=None, newsol=[], open=txt))
    for i in range(n_a): add("가입할 때 들은 범위와 달라요", top="cost" if i % 2 else "exit")
    for i in range(n_b): add("직접 관리해도 되는데 굳이 내야 하나요", top="idle", motive="care", gap="same")
    for i in range(5): add("앱이 느려요", top="other", motive="new", gap="over")
    for i in range(n_sat): add("만족해요", status="sat", top=None)
    df = pd.DataFrame(rows); df.index = range(1, len(df) + 1); df.index.name = "response_id"; return df


class Fake:
    def __init__(self, outs): self.outs, self.calls, self.notes = list(outs), 0, []
    def generate(self, pack, note, timeout=0):
        self.calls += 1; self.notes.append(note); o = self.outs.pop(0)
        if isinstance(o, Exception): raise o
        return o if isinstance(o, str) else json.dumps(o, ensure_ascii=False)


def good(pack, **kw):
    t = next(x for x in pack["texts"] if "가입할" in x["text"])
    p = {"kind": "new_type", "related_type_id": None, "title": "가입 설명 불일치", "description": "가입 때 들은 범위와 실제가 달랐다는 반복 응답",
         "text_ids": [t["id"]], "closest_type_id": "T01", "difference": "요금 수준이 아니라 가입 때 들은 설명과 달랐다는 점이 다름", "cells": [{"motive": "default", "gap": "below"}],
         "solutions": [{"title": "가입 범위 재안내", "category": "정보제공", "message_hint": "포함 범위를 다시 알려드립니다."}]}
    p.update(kw); return p


def pack_of(df=None): return C.build_pack(df if df is not None else make_df(), TYPES, 10)


# ---------- load_df / pack
def test_load_df_top_rules():
    send = pd.DataFrame({"survey_id": [1, 2, 3], "subscription_id": ["A", "B", "C"], "survey_type": ["post_action"] * 3})
    resp = pd.DataFrame({"response_id": [1, 2, 3], "survey_id": [1, 2, 3]})
    ans = pd.DataFrame([(1, "issues", "cost", None), (2, "issues", "cost", None), (2, "issues", "exit", None), (2, "top", "exit", None),
                        (3, "issues", "cost", None), (3, "issues", "exit", None), (1, "open", None, "문장")], columns=["response_id", "question_id", "answer_code", "other_text"])
    df = C.load_df(send, resp, ans)
    assert df.top.to_dict() == {1: "cost", 2: "exit", 3: None} and df.top[3] is None          # 하나만 고르면 그 코드, 둘 이상이면 top 답(없으면 None)
    assert df.open[1] == "문장"


def test_pack_excludes_sat_and_ids_internal():
    p = pack_of(); assert all(t["text"] != "만족해요" for t in p["texts"])
    assert p["n_responses"] == 28 and "_ids" in p and "_ids" not in C.public_pack(p)
    t = next(x for x in p["texts"] if "가입할" in x["text"]); assert t["n"] == 12 and len(p["_ids"][t["id"]]) == 12
    assert any(c["motive"] == "default" and c["gap"] == "below" and c["n"] == 12 for c in p["cells"])
    assert not any(c["n"] < 10 for c in p["cells"])                       # 최소 근거 미만 셀은 묶음에서 뺀다


def test_pack_has_no_truth_fields():
    s = json.dumps(C.public_pack(pack_of()), ensure_ascii=False)
    assert "H-A" not in s and "H-B" not in s and "subscription" not in s and "S0" not in s


# ---------- 검증기 (정상 + 변형 하나씩)
def test_validator_accepts_good():
    p = pack_of(); assert C.validate_proposal(good(p), p) == []


@pytest.mark.parametrize("mut,frag", [
    (dict(kind="x"), "kind"), (dict(title="a"), "제목"), (dict(text_ids=["t99"]), "입력에 없는"), (dict(closest_type_id="T77"), "closest_type_id"),
    (dict(difference="같음"), "difference"), (dict(title="구독료 부담형"), "기존 유형 이름"), (dict(title="T01 같은 유형"), "T##"), (dict(title="T01과 같은 유형"), "T##"),
    (dict(cells=[{"motive": "cost", "gap": "over"}]), "입력에 없는 셀"), (dict(description="문의는 010-1234-5678"), "개인정보"),
    (dict(solutions=[{"title": "x", "category": "쿠폰", "message_hint": "y"}]), "category"), (dict(related_type_id="T01"), "null"),
])
def test_validator_rejects(mut, frag):
    p = pack_of(); errs = C.validate_proposal(good(p, **mut), p)
    assert any(frag in e for e in errs), errs


def test_validator_min_support_and_new_solution_and_dup():
    p = pack_of(make_df(n_a=6)); t = next(x for x in p["texts"] if "가입할" in x["text"])
    assert any("최소" in e for e in C.validate_proposal(good(p), p))
    p = pack_of()
    assert C.validate_proposal(good(p, kind="new_solution", related_type_id="T01"), p) == []
    assert any("기존 유형" in e for e in C.validate_proposal(good(p, kind="new_solution", related_type_id="T77"), p))
    a = [dict(good(p))]; assert any("중복" in e for e in C.validate_proposal(good(p), p, a))


# ---------- 오케스트레이터
def run(outs, df=None, **kw):
    df = df if df is not None else make_df(); llm = Fake(outs); return C.generate_candidates(df, llm, TYPES, **kw), llm


def test_llm_path_ok_and_ids_from_code():
    df = make_df(); p = pack_of(df)
    r, llm = run([{"proposals": [good(p)]}], df)
    assert r["source"] == "llm" and len(r["proposals"]) == 1 and r["rejected"] == [] and llm.calls == 1
    assert len(r["proposals"][0]["response_ids"]) == 12                      # 건수는 코드가 센 값


def test_counts_and_quotes_filled_by_code_ignoring_model_values():
    df = make_df(); p = pack_of(df)
    r, _ = run([{"proposals": [good(p, claimed_count=999, quotes=["지어낸 문장"])]}], df)        # 모델이 쓴 건수·인용문은 무시된다(스키마에도 없음)
    q = r["proposals"][0]; assert q["claimed_count"] == 12 == len(q["response_ids"]) and q["quotes"] == ["가입할 때 들은 범위와 달라요"]


def test_schema_has_no_count_or_quotes_fields():
    props = C.response_schema()["properties"]["proposals"]["items"]["properties"]; assert "claimed_count" not in props and "quotes" not in props and "closest_type_id" in props


def test_retry_with_feedback_then_pass():
    df = make_df(); p = pack_of(df)
    r, llm = run([{"proposals": [good(p, text_ids=["t99"])]}, {"proposals": [good(p)]}], df)
    assert r["source"] == "llm_retry" and llm.calls == 2 and "입력에 없는 문장 ID" in llm.notes[1]


def test_all_rejected_twice_falls_back():
    df = make_df(); p = pack_of(df); bad = {"proposals": [good(p, text_ids=["t99"])]}
    r, llm = run([bad, bad], df)
    assert r["source"] in ("fallback", "none") and llm.calls == 2 and r["rejected"]


def test_exception_falls_back_without_retry_and_scrubs_key():
    key = "s" "k-" + "A" * 20
    r, llm = run([RuntimeError(f"auth failed {key}")])
    assert llm.calls == 1 and key not in json.dumps(r, ensure_ascii=False) and "[KEY]" in r["llm_error"]
    assert r["source"] in ("fallback", "none")


def test_bad_json_falls_back():
    r, llm = run(["not json"]); assert r["llm_error"] and llm.calls == 1


def test_empty_array_retries_once_then_no_fallback():
    r, llm = run([{"proposals": []}, {"proposals": []}]); assert r["proposals"] == [] and r["source"] == "llm" and llm.calls == 2
    assert "빈 배열" in llm.notes[1] and [a["n_props"] for a in r["attempts"]] == [0, 0]


def test_empty_then_good_uses_retry():
    df = make_df(); p = pack_of(df)
    r, llm = run([{"proposals": []}, {"proposals": [good(p)]}], df); assert r["source"] == "llm_retry" and len(r["proposals"]) == 1


def test_empty_with_too_few_texts_does_not_retry():
    df = make_df(n_a=1, n_b=1); r, llm = run([{"proposals": []}], df); assert llm.calls == 1


def test_no_fallback_option():
    r, _ = run([RuntimeError("x")], use_fallback=False); assert r["proposals"] == [] and r["source"] == "none"


def test_max_proposals_cap():
    df = make_df(); p = pack_of(df)
    outs = [good(p, title=f"후보 {i}번 패턴", text_ids=[p["texts"][i]["id"]]) for i in range(2)]
    r, _ = run([{"proposals": outs}], df); assert len(r["proposals"]) <= C.MAX_PROPOSALS


# ---------- 폴백
def test_fallback_cells():
    df = make_df(); p = pack_of(df); fb = C.fallback_candidates(df, p, min_lift=1.0)
    assert fb and all(len(x["_response_ids"]) >= 10 and x["_source"] == "fallback" and "미분류" in x["title"] for x in fb)
    assert all(C.validate_proposal({**x, "text_ids": [], "quotes": []}, p) != [] for x in fb)      # 폴백은 LLM용 검증기를 통과시키려는 것이 아님(근거는 셀)
    assert C.fallback_candidates(df[df.status == "sat"], p) == []


# ---------- 저장 / 복원 검증
def test_to_rows_and_csv(tmp_path):
    df = make_df(); p = pack_of(df); r, _ = run([{"proposals": [good(p)]}], df)
    rows = C.to_rows(r, r["pack_stats"]["n_responses"])
    assert rows[0]["status"] == "검토중" and rows[0]["proposal_kind"] == "new_type" and len(rows[0]["title"]) <= 100
    ev = json.loads(rows[0]["evidence_json"]); assert ev["support"] == 12 and ev["source"] == "llm" and len(ev["response_ids"]) == 12
    assert C.write_csv(rows, tmp_path / "x.csv") == 1


def test_recovery_precision_recall():
    df = make_df(); p = pack_of(df); r, _ = run([{"proposals": [good(p)]}], df)
    holders = df[df.open.str.contains("가입할")].subscription_id.tolist()
    truth = pd.DataFrame({"subscription_id": holders[:9] + ["S_x"], "topic_id": "H-A", "strength": 0.8})
    rec = R.recovery(r, df, truth, topics=("H-A",))
    b = rec["H-A"]["best"]; assert b["hits"] == 9 and b["precision"] == round(9 / 12, 3) and b["recall"] == 1.0
    assert R.recovery(r, df, truth.iloc[0:0], topics=("H-A",))["H-A"]["best"] is None
    assert "H-A" in R.report_md(rec, r)


# ---------- 클라이언트 / 보안
class _Msg: refusal = None; content = json.dumps({"proposals": []})
class _Ch: message = _Msg(); finish_reason = "stop"
class _R: choices = [_Ch()]; usage = None
class _Comp:
    def __init__(self, exc=None): self.exc, self.kw = exc, None
    def create(self, **kw):
        self.kw = kw
        if self.exc: raise self.exc
        return _R()
class _Cl:
    def __init__(self, exc=None): self.chat = type("c", (), {"completions": _Comp(exc)})()


def test_openai_client_schema_and_scrub():
    cl = _Cl(); llm = C.OpenAICandidateLlm("m", client=cl); out = llm.generate(pack_of(), None)
    kw = cl.chat.completions.kw; assert json.loads(out) == {"proposals": []} and kw["response_format"]["json_schema"]["strict"] is True
    assert "_ids" not in kw["messages"][1]["content"] and "S0" not in kw["messages"][1]["content"]
    key = "s" "k-" + "B" * 20
    bad = C.OpenAICandidateLlm("m", client=_Cl(RuntimeError(f"bad {key}")))
    with pytest.raises(RuntimeError) as e: bad.generate(pack_of(), None)
    assert key not in str(e.value)


def test_missing_key_message(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"): C.OpenAICandidateLlm()


def test_schema_is_strict_compatible():
    def chk(s):
        if s.get("type") == "object": assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"]); [chk(v) for v in s["properties"].values()]
        if s.get("type") == "array": chk(s["items"])
    chk(C.response_schema())


def test_candidate_module_never_reads_truth():
    src = Path(C.__file__).read_text(encoding="utf-8"); assert "synth_truth" not in src and "hidden_strengths" not in src


def test_mock_runs_end_to_end():
    r = C.generate_candidates(make_df(), C.MockCandidateLlm(), TYPES); assert r["proposals"] and r["source"] == "llm"


import os
URL = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL 없음")
def test_write_sql_ai_proposal():
    from sqlalchemy import create_engine, text
    from .conftest import pg_reset
    e = create_engine(URL, future=True); pg_reset(e)
    df = make_df(); p = pack_of(df); r, _ = run([{"proposals": [good(p)]}], df)
    rows = C.to_rows(r, r["pack_stats"]["n_responses"])
    assert C.write_sql(e, rows, reset=True) == 1
    with e.connect() as c:
        row = c.execute(text("SELECT status, proposal_kind, evidence_json->>'support', jsonb_array_length(evidence_json->'response_ids') FROM ai_proposal")).one()
    assert row == ("검토중", "new_type", "12", 12)
    assert C.write_sql(e, rows, reset=True) == 1                       # 다시 적재해도 1건(reset)


# ---------- 2단계(1단계 분류 + 후보 생성)
class Fake2(Fake):
    def __init__(self, outs, labels): super().__init__(outs); self.labels, self.cl_calls = labels, 0
    def classify(self, pack, timeout=0):
        self.cl_calls += 1
        if isinstance(self.labels, Exception): raise self.labels
        return self.labels if isinstance(self.labels, str) else json.dumps(self.labels(pack), ensure_ascii=False)


def labels_for(pack, fit_by_word):
    out = []
    for t in pack["texts"]:
        fit, tid = "none", None
        for w, (f, ty) in fit_by_word.items():
            if w in t["text"]: fit, tid = f, ty
        out.append({"id": t["id"], "type_id": tid, "fit": fit})
    return {"labels": out}


def test_stage1_removes_exact_keeps_partial_with_near():
    df = make_df(); pk = pack_of(df)
    info_pack, info = C.stage1_filter(pk, Fake2([], lambda p: labels_for(p, {"직접": ("exact", "T01"), "가입할": ("partial", "T02")})))
    texts = {t["text"]: t for t in info_pack["texts"]}
    assert "직접 관리해도 되는데 굳이 내야 하나요" not in texts and texts["가입할 때 들은 범위와 달라요"]["near"] == "T02"
    assert info["removed_texts"] == 1 and info["removed_responses"] == 11 and info["partial"] == 1 and info["by_type"] == {"T01": 11}
    assert set(info_pack["_ids"]) == {t["id"] for t in info_pack["texts"]}                      # 제외된 문장 ID는 인용할 수 없다


def test_stage1_ignores_bad_type_and_missing_labels_and_errors():
    pk = pack_of()
    p2, i2 = C.stage1_filter(pk, Fake2([], lambda p: {"labels": [{"id": p["texts"][0]["id"], "type_id": "T99", "fit": "exact"}]}))
    assert i2["removed_texts"] == 0 and len(p2["texts"]) == len(pk["texts"])                 # 모르는 유형 ID의 exact는 제외하지 않는다
    for bad in (RuntimeError("down"), "not json", {"labels": "x"}):
        p3, i3 = C.stage1_filter(pk, Fake2([], bad)); assert p3 is pk and i3["error"]
    p4, i4 = C.stage1_filter(pk, Fake2([], lambda p: {"labels": [{"id": t["id"], "type_id": "T01", "fit": "exact"} for t in p["texts"]]}))
    assert p4 is pk and "남은 문장 없음" in i4["error"]


def test_two_stage_end_to_end_cannot_cite_removed_text():
    df = make_df(); pk = pack_of(df)
    tid = next(t["id"] for t in pk["texts"] if "가입할" in t["text"])
    lab = lambda p: labels_for(p, {"가입할": ("exact", "T01")})
    llm = Fake2([{"proposals": [good(pk)]}, {"proposals": [good(pk)]}], lab)
    r = C.generate_candidates(df, llm, TYPES, two_stage=True)
    assert llm.cl_calls == 1 and r["stage1"]["removed_texts"] == 1
    assert r["proposals"] == [] or r["source"] == "fallback"                                  # 제외된 문장을 인용한 후보는 거절 -> 폴백
    assert r["rejected"] and all(any("입력에 없는 문장 ID" in e for e in x["errors"]) for x in r["rejected"])


def test_two_stage_error_falls_back_to_full_pack_and_can_be_turned_off():
    df = make_df(); pk = pack_of(df)
    llm = Fake2([{"proposals": [good(pk)]}], RuntimeError("down"))
    r = C.generate_candidates(df, llm, TYPES, two_stage=True)
    assert r["source"] == "llm" and r["stage1"]["error"] and len(r["proposals"]) == 1
    llm2 = Fake2([{"proposals": [good(pk)]}], lambda p: {"labels": []})
    C.generate_candidates(df, llm2, TYPES, two_stage=False); assert llm2.cl_calls == 0 and C.generate_candidates.__defaults__[-1] is True


def test_duplicate_text_ids_are_deduped_not_rejected():
    df = make_df(); pk = pack_of(df); g = good(pk); g["text_ids"] = g["text_ids"] * 2
    r, _ = run([{"proposals": [g]}], df); assert len(r["proposals"]) == 1 and r["rejected"] == []


def test_classify_schema_and_openai_classify_calls():
    def chk(s):
        if s.get("type") == "object": assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"]); [chk(v) for v in s["properties"].values()]
        if s.get("type") == "array": chk(s["items"])
    chk(C.classify_schema())
    cl = _Cl(); cl.chat.completions.create = lambda **kw: (setattr(cl, "kw", kw) or _R())
    llm = C.OpenAICandidateLlm("m", client=cl); out = llm.classify(pack_of())
    assert out and cl.kw["response_format"]["json_schema"]["strict"] is True and "S0" not in cl.kw["messages"][1]["content"]


def test_recovery_per_candidate_and_stage1_in_report():
    df = make_df(); pk = pack_of(df); llm = Fake2([{"proposals": [good(pk)]}], lambda p: labels_for(p, {"직접": ("exact", "T01")}))
    r = C.generate_candidates(df, llm, TYPES, two_stage=True)
    holders = df[df.open.str.contains("가입할")].subscription_id.tolist()
    rec = R.recovery(r, df, pd.DataFrame({"subscription_id": holders[:5], "topic_id": "H-A", "strength": 0.9}), topics=("H-A",))
    assert rec["H-A"]["per_candidate"] == [{"title": "가입 설명 불일치", "evidence": 12, "hits": 5}]
    md = R.report_md(rec, r); assert "후보별 주제 보유자 수" in md and "1단계 분류" in md


def test_bad_cell_is_dropped_not_rejected():
    """입력에 없는 셀이 하나 섞여도 후보는 살리고 그 셀만 버린다(근거는 text_ids로 계산)."""
    p = pack_of()
    bad = good(p, cells=[{"motive": "default", "gap": "below"}, {"motive": "idle", "gap": "below"}])
    res, _ = run([{"proposals": [bad]}], use_fallback=False, two_stage=False)
    assert len(res["proposals"]) == 1 and res["rejected"] == []
    q = res["proposals"][0]
    assert q["cells"] == [{"motive": "default", "gap": "below"}] and q["dropped_cells"] == [{"motive": "idle", "gap": "below"}]


# ---------- 합의(여러 번 실행)
def _pr(kind, ids): return {"kind": kind, "response_ids": list(ids), "title": "t", "source": "llm"}


def test_consensus_keeps_only_repeated_and_counts_votes():
    runs = [[_pr("new_type", range(0, 20)), _pr("new_type", range(100, 120))],
            [_pr("new_type", range(5, 25))],                                   # 첫 후보와 겹침(15/25=0.6)
            [_pr("new_type", range(200, 220))]]                                # 어느 것과도 안 겹침
    out = C.consensus_proposals(runs, min_votes=2)
    assert len(out) == 1 and out[0]["votes"] == 2 and out[0]["n_runs"] == 3 and out[0]["source"] == "consensus"


def test_consensus_same_run_not_double_counted_and_kind_must_match():
    out = C.consensus_proposals([[_pr("new_type", range(20)), _pr("new_type", range(2, 22))], []], min_votes=2)
    assert out == []                                                           # 한 실행 안의 두 후보는 한 표
    out = C.consensus_proposals([[_pr("new_type", range(20))], [_pr("new_solution", range(20))]], min_votes=2)
    assert out == []


def test_generate_consensus_runs_and_excludes_failed_runs():
    df = make_df(); p = pack_of(df)
    llm = Fake([{"proposals": [good(p)]}] * 3)
    r = C.generate_consensus(df, llm, runs=3, existing_types=TYPES, two_stage=False)
    assert llm.calls == 3 and r["source"] == "consensus" and r["proposals"][0]["votes"] == 3 and len(r["runs"]) == 3 and r["runs"][0]["kinds"] == ["new_type"]
    llm = Fake([{"proposals": [good(p)]}, RuntimeError("x"), {"proposals": [good(p)]}])
    r = C.generate_consensus(df, llm, runs=3, existing_types=TYPES, two_stage=False)
    assert r["proposals"][0]["votes"] == 2 and r["llm_error"]                  # 실패한 실행은 표에서 빠지고 오류는 남음
    assert C.generate_consensus(df, Fake([{"proposals": [good(p)]}]), runs=1, existing_types=TYPES, two_stage=False)["source"] == "llm"
