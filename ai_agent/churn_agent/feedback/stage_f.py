"""단계 F: 해지 고민·해결 경험 — hesitate, action, helped, why, wanted, newsol (설계: survey_simulator_design.md §3.6~3.7).
입력은 Subject.action_solution(마지막 '조치완료' 솔루션 ID), Subject.type_strengths, hidden_topics, facts. 확률은 config.yaml 의 stage_f ([가정])."""
from __future__ import annotations
import re
from . import questionnaire as Q
from .mapping import action_codes
from .rng import pick


def solution_type(solution_id: str | None):
    m = re.match(r"S-(T\d\d)-", solution_id or "")
    return m.group(1) if m else None


def fit_level(subject, cfg) -> str | None:
    """솔루션과 고객 유형의 적합도. 솔루션의 대상 유형이 고객의 가장 강한 활성 유형이면 exact, 다른 활성 유형이면 partial, 그 밖(비활성·유형 없음 솔루션)은 mismatch [가정]."""
    if not subject.action_solution: return None
    t = solution_type(subject.action_solution)
    active = {k: v for k, v in subject.type_strengths.items() if v >= cfg["active_strength_min"]}
    if t is None or t not in active: return "mismatch"
    return "exact" if t == max(sorted(active), key=lambda k: active[k]) else "partial"


def _active(c, *types):
    return any(c.s.type_strengths.get(t, 0) >= c.cfg["active_strength_min"] for t in types)


def gen_hesitate(c):
    h, f = c.cfg["stage_f"]["hesitate"], c.s.facts
    zone = f.get("penalty_zone")
    cond = {"pen": _active(c, "T02") or (c.s.survey_type == "post_churn" and zone in (1, 2)),
            "pick": bool(f.get("large_appliance_yn") in ("Y", True)),
            "term": (f.get("days_to_end") is not None and f["days_to_end"] > 180),
            "visit": bool(f.get("visit_care_yn") in ("Y", True))}
    chosen = []
    for k in ("pen", "pick", "habit", "term", "visit"):
        p = h[k].get("cond", h[k]["base"]) if cond.get(k) else h[k]["base"]
        if c.rng.random() < p: chosen.append(k)
    if c.rng.random() < h["none"]["base"] or not chosen: return ["none"]
    return chosen


def gen_action(c):
    sol = c.s.action_solution
    if not sol: return pick(c.rng, c.cfg["stage_f"]["action_without_solution"])
    codes = action_codes(sol)
    c.truth["action_codes"] = codes
    if len(codes) > 1 and c.rng.random() < c.cfg["stage_f"]["action_secondary_share"]: return codes[1]
    return codes[0]


def gen_helped(c):
    sf = c.cfg["stage_f"]
    fit = fit_level(c.s, c.cfg) or "mismatch"          # 조치 문항에 solution 없이 받은 것으로 답한 경우는 없지만 방어
    c.truth["fit"] = fit
    w = dict(sf["helped_by_fit"][fit])
    if c.s.survey_type == "post_churn":
        moved = w["solved"] * (1 - sf["helped_post_churn_solved_factor"]); w["solved"] -= moved
        lw = w["little"] + w["worse"]; w["little"] += moved * w["little"] / lw; w["worse"] += moved * w["worse"] / lw
    return pick(c.rng, w)


def gen_why(c):
    cfg = c.cfg["stage_f"]["why"]; fit = c.truth_prev.get("fit") or fit_level(c.s, c.cfg) or "mismatch"
    w = {}
    for k, v in cfg.items():
        x = v["base"]
        if k == "late" and _active(c, "T06", "T10"): x += v["add"]
        if k == "mismatch": x += v["add_mismatch"] if fit == "mismatch" else (v["add_partial"] if fit == "partial" else 0)
        if k == "again" and _active(c, "T09"): x += v["add"]
        if k == "diff" and _active(c, "T07", "T10"): x += v["add"]
        if k == "unclear" and (_active(c, "T02") or "H-A" in c.s.hidden_topics): x += v["add"]
        w[k] = x
    n = pick(c.rng, c.cfg["stage_f"]["why_count"])
    out = []
    for _ in range(int(n)):
        k = pick(c.rng, {k: v for k, v in w.items() if k not in out}); out.append(k)
    return [x for x in Q.QUESTIONS["why"][1] if x in out]


def gen_wanted(c):
    sf, rng = c.cfg["stage_f"], c.rng
    real = Q.real_issues(c.a)
    key = Q.top_key(c.a) or (real[0] if real else None)           # top을 건너뛴 응답은 첫 불편 코드 기준
    opts = Q.options("wanted", c.a)
    pair = c.mp["issue_to_wanted"].get(key)
    c.truth["wanted_main"] = pair[0] if pair else None
    if rng.random() < sf["wanted_replace_rate"] or not pair:
        return "hard" if rng.random() < sf["wanted_replace_hard_share"] else [o for o in opts if o != "hard"][int(rng.random() * (len(opts) - 1))]
    return pair[0] if rng.random() < sf["wanted_main_share"] else pair[1]


def gen_newsol(c):
    sf, rng = c.cfg["stage_f"], c.rng
    if rng.random() < sf["newsol_none_rate"]: return ["none"]
    real = set(Q.real_issues(c.a)); out = []
    for n, issues in c.mp["newsol_to_issues"].items():
        if real & set(issues) and rng.random() < sf["newsol_pick_rate"]: out.append(n)
    if "other" in real and rng.random() < sf["newsol_other_given_other_issue"]: out.append("other")
    return [x for x in Q.NEWSOL if x in out] or ["none"]
