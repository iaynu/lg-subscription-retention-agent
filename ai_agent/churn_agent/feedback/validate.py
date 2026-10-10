"""설문 합성기 검증 (설계서 §5). 합성 결과(subjects, results)를 받아 항목별 통과 여부와 수치를 돌려준다."""
from __future__ import annotations
import collections
from . import questionnaire as Q
from .mapping import load_config
from .simulator import synthesize


def _rate(num, den): return num / den if den else float("nan")


def check_skip_consistency(subjects, results):
    bad = [(s.subscription_id, e) for s, r in zip(subjects, results) if r.responded for e in Q.check_answers(r.answers)]
    sat = [s.subscription_id for s, r in zip(subjects, results) if r.answers.get("status") == "sat" and set(r.answers) & (Q.SAT_SKIP - {"status"})]
    return ("건너뛰기 일관성", not bad and not sat, f"규칙 위반 {len(bad)}건, sat 응답의 건너뛰는 문항 응답 {len(sat)}건", bad[:3])


def check_rates(subjects, results, cfg=None):
    cfg = cfg or load_config(); by = collections.defaultdict(lambda: [0, 0])
    for s, r in zip(subjects, results): by[s.survey_type][0] += 1; by[s.survey_type][1] += r.responded
    rows, ok = [], True
    for k, (n, m) in sorted(by.items()):
        tgt = cfg["response_rate"][k]; got = _rate(m, n)
        tol = max(0.03, 3 * (tgt * (1 - tgt) / max(n, 1)) ** 0.5)               # 표본이 작으면 허용 폭을 넓힌다(±3%p 또는 3σ 중 큰 값)
        good = n < 30 or abs(got - tgt) <= tol; ok &= good
        rows.append(f"{k}: 발송 {n}, 응답률 {got:.1%} (목표 {tgt:.1%}±{tol:.1%}) {'OK' if good else '미달'}")
    resp = [r for r in results if r.responded]; comp = _rate(sum(bool(r.completed) for r in resp), len(resp))
    ok_c = len(resp) < 50 or 0.82 <= comp <= 0.97                                 # 중도 이탈 15% 중 뒤쪽 문항이 모두 건너뜀이면 완료로 남는 몫이 있다
    return ("응답률·완료율", ok and ok_c, "; ".join(rows) + f"; 완료율 {comp:.1%}(기대 82~97%)", [])


def check_data_match(subjects, results):
    """데이터로 정해지는 응답이 관측 값과 맞는지(DK 제외). 구간 환산은 설계서 §3.5 표를 따른다."""
    def b_visit_a(f):
        n = f.get("visit_delay_cnt_365d"); k = f.get("visit_cancel_cnt_365d")
        return None if n is None and k is None else str(min(int((n or 0) + (k or 0)), 3))
    def b_visit_b(f):
        d = f.get("visit_max_delay_days"); return None if d is None else ("w1" if d <= 7 else "w2" if d <= 14 else "m1" if d <= 30 else "m2")
    def b_fail_b(f):
        n = f.get("as_cnt_365d"); return None if n is None else ("once" if n <= 1 else "2" if n == 2 else "3")
    def b_sales_a(f):
        n = f.get("sales_pitch_cnt"); return None if n is None else ("1" if n <= 1 else "2" if n <= 3 else "4")
    def b_mgr_b(f):
        n = f.get("manager_change_cnt"); return None if n is None else str(min(int(n), 3))
    def b_cs_b(f):
        d = f.get("repair_days"); return None if d is None else ("d3" if d <= 3 else "d7" if d <= 7 else "d14" if d <= 14 else "d15")
    checks = [("visit", "deepA", b_visit_a, True), ("visit", "deepB", b_visit_b, False), ("fail", "deepB", b_fail_b, False), ("sales", "deepA", b_sales_a, False),
              ("manager", "deepB", b_mgr_b, False), ("cs", "deepB", b_cs_b, False)]
    out, ok = [], True
    for top, q, fn, sched in checks:
        n = m = 0
        for s, r in zip(subjects, results):
            a = r.answers
            if Q.top_key(a) != top or q not in a or a[q] == Q.DK: continue
            if sched and s.facts.get("sched_difficulty"): continue              # 일정 조율 어려움 응답자는 1~2로 한정하므로 제외
            if q == "deepB" and top == "cs" and (s.facts.get("unresolved_voc_cnt") or 0) > 0: continue    # 미해결이 있으면 25%가 'open'
            exp = fn(s.facts)
            if exp is None: continue
            n += 1; m += a[q] == exp
        rate = _rate(m, n); good = n < 20 or rate >= 0.95; ok &= good
        out.append(f"{top}/{q}: {m}/{n} = {rate:.1%} {'OK' if good else '미달'}" if n else f"{top}/{q}: 대상 없음")
    return ("데이터 일치율(≥95%, DK 제외)", ok, "; ".join(out), [])


def check_own_type_share(subjects, results, cfg=None):
    cfg = cfg or load_config(); n = own = 0
    for s, r in zip(subjects, results):
        act = [t for t, v in s.type_strengths.items() if v >= cfg["active_strength_min"]]
        if not act or s.hidden_topics: continue
        for src, _ in r.truth.get("issue_picks", []): n += 1; own += src == "own_type"
    rate = _rate(own, n)
    return ("자기 유형 선택 비율(선택 1회당, 설정 60%±10%p)", n < 100 or abs(rate - 0.60) <= 0.10, f"{own}/{n} = {rate:.1%}", [])


def check_hidden_enrichment(subjects, results):
    by = collections.defaultdict(lambda: [0, 0])
    for s, r in zip(subjects, results):
        if r.responded and r.answers.get("issues"): by[s.survey_type][0] += 1; by[s.survey_type][1] += bool(s.hidden_topics)
    r = {k: _rate(m, n) for k, (n, m) in by.items()}
    nt, pa = r.get("no_type"), r.get("post_action")
    ok = nt is None or pa is None or by["no_type"][0] < 30 or nt >= 2 * pa
    return ("숨은 주제 분포(유형 없음 설문 ≥ 일반 설문의 2배)", ok, ", ".join(f"{k} {v:.1%}(n={by[k][0]})" for k, v in sorted(r.items())), [])


def check_reproducible(subjects, results, k=300, cfg=None):
    sample = list(zip(subjects, results))[:k]
    diff = [s.subscription_id for s, r in sample if synthesize(s, cfg=cfg).answers != r.answers]
    return ("재현성", not diff, f"{len(sample)}건 재합성, 불일치 {len(diff)}건", diff[:3])


def run_all(subjects, results, cfg=None):
    return [check_skip_consistency(subjects, results), check_rates(subjects, results, cfg), check_data_match(subjects, results),
            check_own_type_share(subjects, results, cfg), check_hidden_enrichment(subjects, results), check_reproducible(subjects, results, cfg=cfg)]


def report_md(checks) -> str:
    L = ["# 설문 합성 검증 (설계서 §5) — 가설 기반 synthetic data, 순환 검증", "", "| 검사 | 결과 | 내용 |", "|---|---|---|"]
    for name, ok, detail, _ in checks: L.append(f"| {name} | {'통과' if ok else '**미달**'} | {detail} |")
    return "\n".join(L) + "\n"
