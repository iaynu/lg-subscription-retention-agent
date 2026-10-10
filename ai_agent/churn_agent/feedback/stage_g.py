"""단계 G: motive, gap, firstact, research, after, channel, 생활 환경(hh·daytime·exp·who), interview, open (설계: survey_simulator_design.md §3.8~3.9).
입력은 Subject.type_strengths, hidden_topics, facts(household, voc_channel, voc_cnt, unresolved_voc_yn, tenure_months, other_subs_cnt, senior_yn, agent_inquiry_yn). 확률은 config.yaml 의 stage_g ([가정])."""
from __future__ import annotations
from . import questionnaire as Q
from .pool import sample
from .rng import pick


def _strength(c, t): return c.s.type_strengths.get(t, 0.0)
def _active(c, *ts): return any(_strength(c, t) >= c.cfg["active_strength_min"] for t in ts)
def _max_strength(c): return max(c.s.type_strengths.values(), default=0.0)


def _pin(base: dict, key: str, share: float) -> dict:
    """key의 몫을 share로 고정하고 나머지는 기존 비율대로 (1-share)에 나눠 담는다."""
    rest = {k: v for k, v in base.items() if k != key}; tot = sum(rest.values())
    return {key: share, **{k: v / tot * (1 - share) for k, v in rest.items()}}


def gen_motive(c):
    sg = c.cfg["stage_g"]; w = dict(sg["motive"])
    if _active(c, "T04"):
        for k in ("care", "new"): w[k] *= sg["motive_t04_boost"]
    a, b = "H-A" in c.s.hidden_topics, "H-B" in c.s.hidden_topics
    if a and b:
        d, ca = sg["motive_hidden_both"]["default"], sg["motive_hidden_both"]["care"]
        rest = {k: v for k, v in w.items() if k not in ("default", "care")}; tot = sum(rest.values())
        w = {"default": d, "care": ca, **{k: v / tot * (1 - d - ca) for k, v in rest.items()}}
    elif a: w = _pin(w, "default", sg["motive_hidden_a_default"])
    elif b: w = _pin(w, "care", sg["motive_hidden_b_care"])
    return pick(c.rng, w)


def gen_gap(c):
    sg = c.cfg["stage_g"]
    if c.a.get("motive") == "default":
        return pick(c.rng, sg["gap_default"]["hidden_a"] if "H-A" in c.s.hidden_topics else sg["gap_default"]["other"])
    g = sg["gap_other"]
    if "H-B" in c.s.hidden_topics: return pick(c.rng, g["hidden_b"])
    if _max_strength(c) >= sg["gap_strong_min"]: return pick(c.rng, g["strong"])
    return pick(c.rng, g["base"])


def gen_firstact(c):
    sg = c.cfg["stage_g"]; f = c.s.facts
    if (f.get("voc_cnt") or 0) > 0 and f.get("voc_channel") in sg["firstact_channel"]:
        return sg["firstact_channel"][f["voc_channel"]]
    return pick(c.rng, sg["firstact_no_voc"])


def _bernoulli_multi(c, probs: dict, order):
    chosen = [k for k in order if c.rng.random() < probs[k]]
    return chosen


def gen_research(c):
    r = c.cfg["stage_g"]["research"]; f = c.s.facts
    unresolved = f.get("unresolved_voc_yn") in ("Y", True) or (f.get("unresolved_voc_cnt") or 0) > 0
    cond = {"penalty": _active(c, "T02"), "compare": _active(c, "T01"), "ask": (f.get("voc_cnt") or 0) > 0,
            "agency": any(_strength(c, t) >= c.cfg["stage_g"]["research_agency_min"] for t in ("T07", "T10")) and unresolved}
    probs = {k: (r[k]["cond"] if cond.get(k) else r[k]["base"]) for k in ("penalty", "compare", "ask", "agency")}
    probs["review"] = r["review"]["base"]
    got = _bernoulli_multi(c, probs, ("penalty", "review", "compare", "agency", "ask"))
    if c.rng.random() < r["none"]["base"] or not got: return ["none"]
    return got


def gen_after(c):
    sg = c.cfg["stage_g"]; b, w = sg["after_boost"], dict(sg["after_base"])
    if _active(c, "T04"): w["none"] *= b["t04_none"]
    if _active(c, "T01"):
        for k in ("sub", "used"): w[k] *= b["t01_sub_used"]
    if _active(c, "T02"): w["sub"] *= b["t02_sub"]
    if "H-B" in c.s.hidden_topics:
        for k in ("keep", "buy"): w[k] *= b["hb_keep_buy"]
    if c.a.get("status") == "think": w = _pin(w, "unsure", sg["after_think_unsure"])
    return pick(c.rng, w)


def gen_channel(c):
    ch = c.cfg["stage_g"]["channel"]; w = dict(ch["weights"])
    if c.s.facts.get("senior_yn") in ("Y", True):
        for k in ("sms", "call"): w[k] *= ch["senior_boost"]
    n = int(pick(c.rng, ch["count"])); out = []
    for _ in range(n):
        k = pick(c.rng, {k: v for k, v in w.items() if k not in out})
        if k == "none": return ["none"]
        out.append(k)
    return [x for x in Q.QUESTIONS["channel"][1] if x in out]


def gen_hh(c):
    h = c.s.facts.get("household")
    return h if h in Q.QUESTIONS["hh"][1] else pick(c.rng, c.cfg["stage_g"]["hh"])


def gen_daytime(c):
    d = c.cfg["stage_g"]["daytime"]
    return pick(c.rng, d["t06"] if _active(c, "T06") else d["base"])


def gen_exp(c):
    e = c.cfg["stage_g"]["exp"]; f = c.s.facts
    if (f.get("tenure_months") or 0) >= e["long_tenure_months"] and (f.get("other_subs_cnt") or 0) > 0:
        return pick(c.rng, e["long_tenure_with_other_subs"])
    return pick(c.rng, e["base"])


def gen_who(c):
    w = c.cfg["stage_g"]["who"]
    return pick(c.rng, w["agent_inquiry"] if c.s.facts.get("agent_inquiry_yn") in ("Y", True) else w["base"])


def gen_interview(c):
    iv = c.cfg["stage_g"]["interview"]
    p = iv["base"] * (iv["intensity_scale"] + _max_strength(c))            # 불편 강도가 높을수록 가중
    if c.rng.random() < p:
        c.other_text["interview"] = f"dummy-{int(c.rng.random() * 9999) + 1:04d}@example.test"      # 가상 연락처
        return "yes"
    return "no"


def open_key(c) -> str:
    """주관식 문장을 뽑을 풀 키: 숨은 주제 보유 시 일정 확률로 그 주제, 만족·불편 없음이면 sat, 그 밖에는 top 불편 코드."""
    sg = c.cfg["stage_g"]; hidden = tuple(c.s.hidden_topics)
    if hidden and c.rng.random() < sg["open_hidden_share"]: return hidden[int(c.rng.random() * len(hidden))]
    if c.a.get("status") == "sat" or not Q.real_issues(c.a): return "sat"
    k = Q.top_key(c.a) or Q.real_issues(c.a)[0]
    return k


def gen_open(c):
    if c.rng.random() >= c.cfg["stage_g"]["open_write_rate"]: return None          # 작성하지 않음
    key = open_key(c); c.truth["open_key"] = key
    return sample(c.rng, key)
