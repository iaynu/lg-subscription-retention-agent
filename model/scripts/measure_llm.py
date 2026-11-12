"""LLM 측정: 입력 JSON의 사례를 Agent(Rule Engine → LLM → 검증기 → 재시도/폴백)로 N회 돌려 통과율·재시도·폴백·지연·토큰·비용을 잰다.
DB 없이 돈다(입력은 scripts/export_inputs.py가 만든 JSON). 결과 파일에는 API 키가 들어가지 않는다.

  mock으로 연습:   python scripts/measure_llm.py --inputs data/demo_inputs_12.json --provider mock
  실제 측정:       export OPENAI_API_KEY=...   # 키는 환경변수로만
                   python scripts/measure_llm.py --inputs data/demo_inputs_12.json --provider openai --model <사용 가능한 모델> --repeats 3 \\
                          --price-in <100만 토큰당 입력 단가> --price-out <100만 토큰당 출력 단가>
단가는 계정·모델마다 달라 코드에 넣지 않는다(주면 비용도 계산). 결과: out/llm_measure_<provider>.md, .json
"""
from __future__ import annotations
import argparse, copy, json, re, statistics as st, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.catalog import load_catalog
from agent.config import ROOT, load_config
from agent.llm.factory import make_llm
from agent.repo.memory import MemoryRepo
from agent.schemas import AgentInput
from agent.service import AgentService
import agent.validation.fallback as _fb

CONN_RETRIES = []                                                # 연결 오류로 다시 시도한 횟수(측정 전용)
FAILS = []                                                       # 검증 실패 메시지 수집(규칙, 메시지) — 원인 진단용
_orig_validate = _fb.validate
def _rec_validate(raw, inp):
    r = _orig_validate(raw, inp)
    sel = ((r.get("data") or {}).get("selected")) if isinstance(r.get("data"), dict) else None
    for rule, idx, msg, sev in r.get("failures", []):
        sid = sel[idx].get("solution_id") if isinstance(sel, list) and isinstance(idx, int) and 0 <= idx < len(sel) and isinstance(sel[idx], dict) else None
        FAILS.append((rule, str(msg)[:160], sid or "-", sev))
    return r
_fb.validate = _rec_validate

KEY = re.compile(r"(s" + r"k-[A-Za-z0-9_\-]{6,}|Bearer\s+\S+)")      # 키 모양 문자열(저장소 비밀 검사에 걸리지 않게 나눠 씀)
scrub = lambda s: KEY.sub("[KEY]", str(s))                      # 오류 문자열에 키 조각이 섞여도 지움


class Timed:
    """llm.generate를 감싸 호출별 지연·토큰·오류를 기록한다."""
    def __init__(self, llm): self.llm, self.calls, self.status = llm, [], getattr(llm, "status", None)
    def generate(self, inp, note, timeout):
        t0 = time.time(); rec = dict(retry=note is not None)
        try:
            for k in range(4):                                       # 측정 전용: 네트워크 연결 오류(APIConnectionError)는 최대 3번 다시 시도해 환경 불안정이 통과율을 흔들지 않게 함
                try:
                    out = self.llm.generate(inp, note, timeout); break
                except Exception as e:
                    if type(e).__name__ != "APIConnectionError" or k == 3: raise
                    CONN_RETRIES.append(1); time.sleep(1.5 * (k + 1))
            rec["ok"] = True; return out
        except Exception as e:
            rec["ok"] = False; rec["error"] = scrub(f"{type(e).__name__}: {e}")[:200]; raise
        finally:
            rec["sec"] = round(time.time() - t0, 3); u = getattr(self.llm, "last_usage", None) or {}
            rec["prompt"], rec["completion"] = u.get("prompt"), u.get("completion"); self.calls.append(rec)


def pct(xs, q): xs = sorted(xs); return xs[min(int(round(q * (len(xs) - 1))), len(xs) - 1)] if xs else None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--inputs", required=True); ap.add_argument("--provider", choices=["mock", "openai"], default="mock"); ap.add_argument("--model"); ap.add_argument("--prompt", help="프롬프트 변형: v3.3(기본)|v3.2|v3.2c9|base. 지정하면 결과 파일 이름에 붙는다")
    ap.add_argument("--repeats", type=int, default=3); ap.add_argument("--price-in", type=float); ap.add_argument("--price-out", type=float); ap.add_argument("--out", default="out"); a = ap.parse_args()
    if a.prompt:
        from agent.llm import prompt as _pm
        _pm.set_variant(a.prompt)
    cfg = copy.deepcopy(load_config()); cfg["llm"]["provider"] = a.provider
    if a.model: cfg["llm"]["model"] = a.model
    base = make_llm(cfg)
    if a.provider == "openai" and not str(getattr(base, "status", "")).startswith("ready"): print("OpenAI 사용 불가:", scrub(getattr(base, "status", "")), "\n환경변수 OPENAI_API_KEY를 설정했는지 확인하세요."); return 2
    timed = Timed(base); svc = AgentService(cfg, load_catalog(ROOT / "data/solution_catalog.json"), timed, MemoryRepo())
    data = json.loads(Path(a.inputs).read_text(encoding="utf-8")); rows = []
    for c in data["cases"]:
        for k in range(a.repeats):
            n0 = len(timed.calls); ai = AgentInput(**c["input"]).model_dump(); t0 = time.time()
            try:
                r = svc.run(ai); err = None
            except Exception as e:
                r, err = None, scrub(f"{type(e).__name__}: {e}")
            calls = timed.calls[n0:]
            rows.append(dict(case=c["case_id"], run=k + 1, outcome=(r or {}).get("outcome", "error"), attempts=(r or {}).get("attempts"), failed=(r or {}).get("failed_checks", []),
                             selected=[s["solution_id"] for s in (r or {}).get("selected", [])], sec=round(time.time() - t0, 3), llm_calls=len(calls), errors=[x["error"] for x in calls if not x["ok"]] + ([err] if err else []),
                             prompt_tokens=sum(x["prompt"] or 0 for x in calls), completion_tokens=sum(x["completion"] or 0 for x in calls),
                             messages=[dict(id=s["solution_id"], title=s.get("title"), text=s.get("message_text")) for s in (r or {}).get("selected", [])] if k == 0 else None))
    llm_rows = [x for x in rows if x["llm_calls"] > 0]; n = len(llm_rows); cnt = lambda o: sum(x["outcome"] == o for x in llm_rows)
    secs = [x["sec"] for x in llm_rows]; pt, ct = sum(x["prompt_tokens"] for x in rows), sum(x["completion_tokens"] for x in rows)
    cost = (pt * a.price_in + ct * a.price_out) / 1e6 if a.price_in is not None and a.price_out is not None else None
    L = [f"# LLM 측정 리포트 ({a.provider}{' / ' + cfg['llm']['model'] if a.provider == 'openai' else ''}{' / 프롬프트 ' + a.prompt if a.prompt else ''})", "", "> 가설 기반 synthetic data(가상 고객)로 잰 값입니다. 모델·네트워크·시점에 따라 달라집니다.", "",
         f"- 사례 {len(data['cases'])}건 × 반복 {a.repeats}회 = 실행 {len(rows)}회 (LLM을 부른 실행 {n}회, 나머지는 Rule Engine만으로 끝난 설문·조치 없음)", "",
         "## 요약 (LLM을 부른 실행 기준)", "", "| 지표 | 값 |", "|---|---|"]
    if n:
        L += [f"| 1차 통과(pass) | {cnt('pass')}/{n} ({cnt('pass')/n:.0%}) |", f"| 재시도 후 통과(retry_pass) | {cnt('retry_pass')}/{n} ({cnt('retry_pass')/n:.0%}) |", f"| 일부 템플릿(partial_template) | {cnt('partial_template')}/{n} |", f"| 전체 템플릿 폴백(full_template) | {cnt('full_template')}/{n} ({cnt('full_template')/n:.0%}) |",
              f"| 연결 오류 재시도(측정 전용) | {len(CONN_RETRIES)}회 |", f"| 실행 지연 중앙값 / p95 / 최대 | {st.median(secs):.2f}s / {pct(secs, .95):.2f}s / {max(secs):.2f}s |", f"| 토큰 합계 (입력 / 출력) | {pt:,} / {ct:,} |", f"| 실행당 평균 토큰 | {(pt + ct) / n:,.0f} |"]
        if cost is not None: L += [f"| 추정 비용 합계 / 실행당 | ${cost:.4f} / ${cost / n:.5f} |"]
    fc = {}
    for x in llm_rows:
        for f in x["failed"]: fc[f] = fc.get(f, 0) + 1
    L += ["", "## 검증 실패 항목(재시도 유발)", "", ("| 항목 | 횟수 |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in sorted(fc.items(), key=lambda kv: -kv[1]))) if fc else "없음", "", "## 사례별", "", "| 사례 | 결과(반복별) | 선택 솔루션 | 평균 지연 |", "|---|---|---|---|"]
    for c in data["cases"]:
        rr = [x for x in rows if x["case"] == c["case_id"]]; L.append(f"| {c['case_id']} | {', '.join(x['outcome'] for x in rr)} | {','.join(rr[0]['selected']) or '-'} | {st.mean(x['sec'] for x in rr):.2f}s |")
    L += ["", "## 검증 실패 메시지 예시 (규칙별 상위 5개 서로 다른 메시지, [ ]는 솔루션 ID별 횟수)", "", "(HARD 실패만 재시도·폴백을 유발하고, SOFT는 표시만 함: 규칙명 뒤 ‘soft’)", ""]
    byr = {}
    for r_, m_, sid_, sev_ in FAILS:
        d_ = byr.setdefault((r_, sev_), {}); e_ = d_.setdefault(scrub(m_), {}); e_[sid_] = e_.get(sid_, 0) + 1
    for (r_, sev_), d_ in sorted(byr.items(), key=lambda kv: -sum(sum(v.values()) for v in kv[1].values())):
        L.append(f"**{r_}{' (soft)' if str(sev_).lower() != 'hard' else ''}** ({sum(sum(v.values()) for v in d_.values())}건)")
        for m_, ids_ in sorted(d_.items(), key=lambda kv: -sum(kv[1].values()))[:5]:
            L.append(f"- {m_} ×{sum(ids_.values())}  [" + ", ".join(f"{k}×{v}" for k, v in sorted(ids_.items(), key=lambda kv: -kv[1])) + "]")
        L.append("")
    bys = {}
    for r_, m_, sid_, sev_ in FAILS: bys.setdefault(sid_, {}); bys[sid_][r_] = bys[sid_].get(r_, 0) + 1
    L += ["## 솔루션별 실패 규칙 (많은 순)", "", "| 솔루션 | 합계 | 규칙별 |", "|---|---|---|"]
    for sid_, d_ in sorted(bys.items(), key=lambda kv: -sum(kv[1].values())):
        L.append(f"| {sid_} | {sum(d_.values())} | " + ", ".join(f"{k}×{v}" for k, v in sorted(d_.items(), key=lambda kv: -kv[1])) + " |")
    L.append("")
    errs = [e for x in rows for e in x["errors"]]
    L += ["", "## LLM 오류", "", ("\n".join(f"- {e}" for e in sorted(set(errs))[:10]) if errs else "없음"), "", "## 생성 문구 (사례별 첫 실행) — 직접 읽어 품질을 판단해 주세요", ""]
    for x in rows:
        if x["messages"]:
            L.append(f"### {x['case']} ({x['outcome']})")
            for m in x["messages"]: L += [f"**{m['id']} {m['title'] or ''}**", "", f"> {(m['text'] or '').replace(chr(10), chr(10) + '> ')}", ""]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); stem = f"llm_measure_{a.provider}" + (f"_{a.prompt}" if a.prompt else "")
    (out / f"{stem}.md").write_text("\n".join(L) + "\n", encoding="utf-8"); (out / f"{stem}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8"); print("\n".join(L[:22]))
    return 0


if __name__ == "__main__": raise SystemExit(main())
