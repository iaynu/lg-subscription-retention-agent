"""설문 합성 일괄 실행: python -m feedback.cli run --data ../data_out [--limit N] [--out DIR] [--sql DATABASE_URL --reset]
DB 접속 문자열은 인자 또는 환경변수 DATABASE_URL에서 읽고 출력하지 않는다."""
from __future__ import annotations
import argparse, datetime as dt, json, os, sys, time
from . import population as P, store, validate
from .mapping import load_config
from .simulator import synthesize


def run(data, limit=None, out=None, sql_url=None, reset=False, as_of=None, verbose=True):
    cfg = load_config(); t0 = time.time()
    t = P.load_tables(data)
    ids = None
    if limit:
        import numpy as np
        allids = list(t["churn_prediction"].subscription_id)
        ids = sorted(np.random.default_rng(0).choice(allids, size=min(limit, len(allids)), replace=False))     # 시드 고정 무작위 표본
    subjects = P.build_subjects(t, cfg, ids, as_of)
    results = [synthesize(s, cfg=cfg) for s in subjects]
    asof = dt.datetime.fromisoformat(str(t["churn_prediction"].as_of_date.iloc[0])[:10]) + dt.timedelta(days=1, hours=10)
    rows = store.to_rows(subjects, results, asof)
    checks = validate.run_all(subjects, results, cfg)
    info = {"subjects": len(subjects), "sec": round(time.time() - t0, 1)}
    if out:
        info["csv"] = store.write_csv(rows, out)
        from pathlib import Path
        Path(out, "validation_report.md").write_text(validate.report_md(checks), encoding="utf-8")
    if sql_url:
        from sqlalchemy import create_engine
        info["sql"] = store.write_sql(create_engine(sql_url), rows, reset=reset)
    if verbose:
        print(f"설문 대상 {len(subjects)}건, 응답 {sum(r.responded for r in results)}건 ({info['sec']}초)")
        print(validate.report_md(checks))
    return rows, checks, info


def run_candidates(inp, provider="mock", model="gpt-5.4-nano", out=None, sql_url=None, reset=False, min_support=8, verbose=True, two_stage=True, runs=3):
    """저장된 설문 3표(CSV 폴더)를 읽어 후보를 만들고 ai_proposal로 저장한다. 폴더에 synth_truth_topic.csv가 있으면 회수 검증을 덧붙인다(정답은 후보 생성에 쓰지 않음)."""
    import pandas as pd
    from pathlib import Path
    from . import candidates as C, recovery as R
    f = lambda n: pd.read_csv(Path(inp) / f"{n}.csv", dtype={"subscription_id": str, "answer_code": str})
    df = C.load_df(f("survey_send"), f("survey_response"), f("survey_answer"))
    llm = C.MockCandidateLlm() if provider == "mock" else C.OpenAICandidateLlm(model)
    res = C.generate_consensus(df, llm, runs=runs, min_support=min_support, two_stage=two_stage)   # runs>=2: 과반 실행에서 반복된 후보만
    rows = C.to_rows(res, res["pack_stats"]["n_responses"])
    truth = Path(inp) / "synth_truth_topic.csv"
    rec = R.recovery(res, df, f("synth_truth_topic")) if truth.exists() else None
    if out:
        Path(out).mkdir(parents=True, exist_ok=True); C.write_csv(rows, Path(out) / "ai_proposal.csv")
        md = R.report_md(rec, res) if rec else ""
        Path(out, "candidates_report.md").write_text(md + "\n\n" + json.dumps({"rejected": res["rejected"], "stats": res["pack_stats"], "attempts": res["attempts"], "stage1": res["stage1"], "runs": res.get("runs")}, ensure_ascii=False, indent=1), encoding="utf-8")
    if sql_url:
        from sqlalchemy import create_engine
        C.write_sql(create_engine(sql_url), rows, reset=reset)
    if verbose:
        print(f"후보 {len(res['proposals'])}개 · 경로 {res['source']} · 거절 {len(res['rejected'])}" + (f" · LLM 오류 {res['llm_error']}" if res["llm_error"] else ""))
        for p in res["proposals"]: print(f" - [{p['kind']}] {p['title']} (근거 {len(p['response_ids'])}건" + (f", {p['votes']}/{p['n_runs']}회 등장" if "votes" in p else "") + ")")
        if rec: print(R.report_md(rec, res))
    return res, rec


def run_proposal(a) -> int:
    from . import approve as A
    url = os.environ.get("DATABASE_URL")
    if not url: print("환경변수 DATABASE_URL이 없습니다."); return 2
    from sqlalchemy import create_engine
    e = create_engine(url)
    try:
        if a.action == "list":
            for r in A.list_pending(e): print(f"{r['proposal_id']:>4} [{r['status']}] {r['proposal_kind']} {r['title']} (근거 {r['support']}건, {r['source']})")
        elif a.action == "approve":
            ov = json.loads(open(a.overrides, encoding="utf-8").read()) if a.overrides else None
            res = A.approve(e, int(a.id), a.reviewer, ov, dry_run=a.dry_run, note=a.reason)
            print(("[미리보기] " if res["dry_run"] else "채택 완료: ") + f"유형 {res['type_id']}" + (f" 신규({res['type_row']['type_name']})" if res["type_row"] else "") + ", 솔루션 " + ", ".join(r["solution_id"] for r in res["solution_rows"]) + " (비활성)")
        elif a.action == "reject":
            A.reject(e, int(a.id), a.reviewer, a.reason); print("기각 처리")
        else:
            A.activate_solution(e, a.id, forward_dept=a.dept); print(f"{a.id} 활성화")
    except A.ApproveError as ex:
        print(f"처리 불가: {ex}"); return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="feedback.cli")
    sp = ap.add_subparsers(dest="cmd", required=True); r = sp.add_parser("run")
    r.add_argument("--data", required=True); r.add_argument("--limit", type=int); r.add_argument("--out"); r.add_argument("--sql", action="store_true", help="환경변수 DATABASE_URL로 DB에 적재")
    r.add_argument("--reset", action="store_true"); r.add_argument("--as-of")
    c = sp.add_parser("candidates"); c.add_argument("--in", dest="inp", required=True); c.add_argument("--provider", choices=["mock", "openai"], default="mock")
    c.add_argument("--model", default="gpt-5.4-nano"); c.add_argument("--out"); c.add_argument("--sql", action="store_true"); c.add_argument("--reset", action="store_true")
    c.add_argument("--min-support", type=int, default=8); c.add_argument("--runs", type=int, default=3, help="후보 생성 반복 횟수. 2 이상이면 과반 실행에서 반복된 후보만 남김(비용 ×횟수). 1이면 한 번만")
    c.add_argument("--single-stage", action="store_true", help="1단계(기존 유형과 정확히 일치하는 문장 제외) 없이 한 번에 후보 생성. 기본은 2단계")
    c.add_argument("--two-stage", action="store_true", help=argparse.SUPPRESS)
    pr = sp.add_parser("proposal", help="ai_proposal 검토: list | approve | reject | activate (DB는 환경변수 DATABASE_URL)")
    pr.add_argument("action", choices=["list", "approve", "reject", "activate"]); pr.add_argument("id", nargs="?", help="proposal_id 또는 solution_id")
    pr.add_argument("--reviewer", default="reviewer"); pr.add_argument("--overrides", help="JSON 파일(type_name, description, solutions)"); pr.add_argument("--reason", default="")
    pr.add_argument("--dry-run", action="store_true"); pr.add_argument("--dept")
    a = ap.parse_args(argv)
    if a.cmd == "proposal": return run_proposal(a)
    if a.cmd == "candidates":
        url = os.environ.get("DATABASE_URL") if a.sql else None
        if a.sql and not url: print("환경변수 DATABASE_URL이 없습니다."); return 2
        run_candidates(a.inp, a.provider, a.model, a.out, url, a.reset, a.min_support, two_stage=not a.single_stage, runs=a.runs); return 0
    url = None
    if a.sql:
        url = os.environ.get("DATABASE_URL")
        if not url: print("환경변수 DATABASE_URL이 없습니다."); return 2
    _, checks, _ = run(a.data, a.limit, a.out, url, a.reset, a.as_of)
    return 0 if all(c[1] for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
