"""단계 E: 심화 문항 deepA / deepB (설계: survey_simulator_design.md §3.5).
Subject.facts(관측 피처 값 dict)가 있으면 거기서 보기를 정하고, 없으면 기본 분포를 쓴다. 키 이름은 data_schema_design.md의 컬럼명을 따른다.
DK(잘 모르겠어요)는 보기에 DK가 있는 문항에서 5~10%를 무작위로 대체한다. 설계서 확률은 [가정] 시작값."""
from __future__ import annotations
from . import questionnaire as Q
from .rng import pick

DK = Q.DK


def _f(c, key, default=None):
    v = c.s.facts.get(key, default)
    return default if v is None else v


def _uniform(c, opts):
    opts = [o for o in opts if o != DK]
    return opts[int(c.rng.random() * len(opts))]


def _cond(fixed: dict, cond_on: dict, rest: str, floor: float = 0.05) -> dict:
    """조건이 참인 보기에 지정 확률을 주고 남은 몫을 rest 보기에 준다(남은 몫이 floor보다 작으면 floor). 합이 1이 아니어도 pick이 정규화한다."""
    w = dict(fixed); w.update({k: v for k, v in cond_on.items() if v is not None})
    w[rest] = max(floor, 1 - sum(w.values()))
    return w


def _num(c, key):
    v = c.s.facts.get(key)
    return None if v is None else float(v)


def _bucket3(n, edges, labels):
    for e, lab in zip(edges, labels):
        if n <= e: return lab
    return labels[-1]


def expected_penalty(c):
    if c.s.facts.get("expected_penalty_krw") is not None: return float(c.s.facts["expected_penalty_krw"])
    fee, months, zone = _num(c, "monthly_fee"), _num(c, "remaining_months"), c.s.facts.get("penalty_zone")
    if fee is None or months is None or zone is None: return None
    return fee * months * c.cfg["deep"]["penalty_zone_ratio"].get(int(zone), 0.0)


# ---- deepA ----
def a_cost(c):
    s = c.s.type_strengths.get("T01", 0.0); d = c.cfg["deep"]["cost_a_strength"]
    c.truth["deepA_data"] = "p31" if s >= d["p31"] else ("p30" if s >= d["p30"] else "p10")
    return DK if c.rng.random() < c.cfg["deep"]["cost_a_dk"] else c.truth["deepA_data"]


def a_exit(c):
    pen, mv = (_f(c, "voc_penalty_cnt_90d", 0) or 0) > 0, ((_f(c, "voc_move_cnt_90d", 0) or 0) > 0 or bool(_f(c, "region_changed_flag", False)))
    P = {"pen": .55, "wait": .25, "rm": .10, "reinst": .05, "other": .05}
    M = {"reinst": .50, "rm": .25, "wait": .15, "pen": .05, "other": .05}
    base = {"wait": .60, "other": .20, "pen": .10, "rm": .05, "reinst": .05}
    if pen and mv: w = {k: (P[k] + M[k]) / 2 for k in P}
    else: w = P if pen else (M if mv else base)
    return pick(c.rng, w)


def a_renew(c):
    cond = {"newmodel": .35 if _f(c, "new_model_released_yn", "N") == "Y" else None,
            "eol": .50 if _f(c, "model_discontinued_yn", "N") == "Y" else None,
            "push": .45 if (_f(c, "renewal_notice_contact_cnt", 0) or 0) >= 2 else None}
    return pick(c.rng, _cond({"other": .05}, cond, "price", floor=.10))


def a_idle(c):
    u = _num(c, "weekly_use_avg")
    if u is None: return pick(c.rng, {"week": .4, "month": .3, "rare": .2, "daily": .1})
    v = "daily" if u >= 6 else ("week" if u >= 2 else ("month" if u >= .25 else "rare"))
    c.truth["deepA_data"] = v
    if v == "daily" and c.rng.random() >= c.cfg["deep"]["idle_daily_cap"]: v = "week"     # idle을 고른 응답자는 daily를 20% 이하로 제한
    return v


def a_care(c):
    if (_f(c, "care_as_14d_cnt", 0) or 0) >= 1: return pick(c.rng, {"dmg": .50, "clean": .25, "skip": .20, "other": .05})
    return pick(c.rng, {"clean": .40, "skip": .35, "dmg": .15, "other": .10})


def a_visit(c):
    d, k = _num(c, "visit_delay_cnt_365d"), _num(c, "visit_cancel_cnt_365d")
    if d is None and k is None: return _uniform(c, Q.options("deepA", c.a))
    n = int((d or 0) + (k or 0))
    v = str(min(n, 3))
    if c.s.facts.get("sched_difficulty"): v = str(min(max(n, 1), 2))                       # 일정 조율 어려움 위주 응답자는 1~2로 둠
    c.truth["deepA_data"] = v
    return v


def a_manager(c):
    if (_f(c, "tone_complaint_cnt", 0) or 0) >= 1: return pick(c.rng, {"attitude": .55, "privacy": .15, "skill": .15, "other": .15})
    if (_f(c, "manager_change_cnt", 0) or 0) >= 2: return pick(c.rng, {"skill": .55, "attitude": .20, "privacy": .10, "other": .15})
    return pick(c.rng, {"attitude": .40, "skill": .35, "privacy": .10, "other": .15})


def a_sales(c):
    n = _num(c, "sales_pitch_cnt")
    if n is None: return pick(c.rng, {"1": .30, "2": .45, "4": .25})
    v = "1" if n <= 1 else ("2" if n <= 3 else "4")
    c.truth["deepA_data"] = v
    return v


def a_fail(c):
    code = c.s.facts.get("symptom_code")
    for g, codes in c.cfg["deep"]["symptom_groups"].items():
        if code in codes:
            c.truth["deepA_data"] = g; return g
    return _uniform(c, Q.options("deepA", c.a))


def a_cs(c):
    h = _num(c, "response_hours_avg")
    if _f(c, "split_as_receipt_yn", "N") == "Y": return pick(c.rng, {"split": .50, "slow": .20, "blame": .15, "other": .15})
    if h is not None and h >= c.cfg["deep"]["cs_slow_hours"]: return pick(c.rng, {"slow": .40, "blame": .30, "split": .10, "other": .20})
    return _uniform(c, Q.options("deepA", c.a))


# ---- deepB ----
def b_cost(c):
    promo = (_num(c, "billed_change_rate_3m") or 0) > 0 or (c.s.facts.get("promo_days_to_end") is not None and _num(c, "promo_days_to_end") <= 0)
    cond = {"promo": .40 if promo else None, "missed": .35 if _f(c, "missed_discount_yn", "N") == "Y" else None}
    return pick(c.rng, _cond({"total": .25, "other": .05}, cond, "fixed", floor=.10))


def b_exit(c):
    p = expected_penalty(c)
    if p is None: return _uniform(c, Q.options("deepB", c.a))
    lo, hi = c.cfg["deep"]["penalty_bucket_krw"]
    v = "0" if p <= 0 else ("u10" if p < lo else ("u30" if p < hi else "o30"))
    c.truth["deepB_data"] = v
    if c.a.get("status") == "think" and c.rng.random() < c.cfg["deep"]["exit_b_zero_if_think"]: return "0"
    return v


def b_renew(c):
    if c.a.get("status") in ("done", "ing"): return pick(c.rng, {"switch": .35, "refuse": .35, "think": .15, "accept": .15})
    return pick(c.rng, {"think": .45, "accept": .20, "refuse": .175, "switch": .175})


def b_idle(c):
    if c.s.type_strengths.get("T04", 0) >= c.cfg["active_strength_min"]: return pick(c.rng, {"noneed": .4, "season": .3, "hassle": .1, "gap": .1, "other": .1})
    if "H-A" in c.s.hidden_topics: return pick(c.rng, {"gap": .5, "noneed": .125, "season": .125, "hassle": .125, "other": .125})
    return _uniform(c, Q.options("deepB", c.a))


def b_care(c): return pick(c.rng, {"none": .50, "part": .30, "full": .20})


def b_visit(c):
    d = _num(c, "visit_max_delay_days")
    if d is None: return _uniform(c, Q.options("deepB", c.a))
    v = _bucket3(d, (7, 14, 30), ("w1", "w2", "m1", "m2"))
    c.truth["deepB_data"] = v
    return v


def b_manager(c):
    n = _num(c, "manager_change_cnt")
    if n is None: return DK if c.rng.random() < c.cfg["deep"]["manager_b_dk_if_missing"] else _uniform(c, Q.options("deepB", c.a))
    v = str(min(int(n), 3)); c.truth["deepB_data"] = v
    return v


def b_sales(c):
    if _f(c, "repitch_after_refusal_yn", "N") == "Y": return pick(c.rng, {"keep": .70, "stop": .15, "unsaid": .15})
    return pick(c.rng, {"keep": .40, "unsaid": .30, "stop": .30})


def b_fail(c):
    n = _num(c, "as_cnt_365d")
    if n is None and c.s.facts.get("repeat_symptom_cnt_90d") is not None: n = _num(c, "repeat_symptom_cnt_90d") + 1    # 반복 n회 = 같은 증상 n+1건
    if n is None: return _uniform(c, Q.options("deepB", c.a))
    v = "once" if n <= 1 else ("2" if n == 2 else "3")
    c.truth["deepB_data"] = v
    return v


def b_cs(c):
    if (_f(c, "unresolved_voc_cnt", 0) or 0) > 0 and c.rng.random() < c.cfg["deep"]["cs_open_if_unresolved"]: return "open"
    days = _num(c, "repair_days")
    if days is None and c.s.facts.get("response_hours") is not None: days = _num(c, "response_hours") / 24
    if days is None: return _uniform(c, Q.options("deepB", c.a))
    v = "d3" if days <= 3 else ("d7" if days <= 7 else ("d14" if days <= 14 else "d15"))
    c.truth["deepB_data"] = v
    return v


A = {"cost": a_cost, "exit": a_exit, "renew": a_renew, "idle": a_idle, "care": a_care, "visit": a_visit, "manager": a_manager, "sales": a_sales, "fail": a_fail, "cs": a_cs}
B = {"cost": b_cost, "exit": b_exit, "renew": b_renew, "idle": b_idle, "care": b_care, "visit": b_visit, "manager": b_manager, "sales": b_sales, "fail": b_fail, "cs": b_cs}


def _with_dk(c, qid, v):
    """보기에 DK가 있고 아직 DK가 아니면 5~10% 확률로 DK로 대체(문항마다 확률을 5~10%에서 뽑는다)."""
    if DK in Q.options(qid, c.a) and v != DK:
        lo, hi = c.cfg["dk_rate"]
        if c.rng.random() < lo + c.rng.random() * (hi - lo): return DK
    return v


def _gen(c, qid, table):
    v = table[Q.top_key(c.a)](c)
    if qid == "deepA" and Q.top_key(c.a) == "cost": return v                 # cost A는 자체 DK 10%
    v = _with_dk(c, qid, v)
    assert v in Q.options(qid, c.a), (qid, Q.top_key(c.a), v)
    return v


def gen_deepA(c): return _gen(c, "deepA", A)
def gen_deepB(c): return _gen(c, "deepB", B)
