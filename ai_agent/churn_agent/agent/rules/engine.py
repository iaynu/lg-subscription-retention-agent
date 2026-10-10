"""Rule Engine v3.1 (agent_design.md §4). 순수 함수: 시스템 시계·DB 접근 없음.

v3.1(2026-10-08 점검 반영)
- 광고 수신 동의: customer.ad_consent_yn → subscription.ad_consent_yn 순으로 읽고, 값이 없으면 '미동의'(법상 사전 동의가 원칙).
- eligibility의 'required' 조건: 값이 없으면 후보 제외(사건을 단정하는 안내 방지). facts는 구독 속성에서 계산한 값으로 보충.
- SURVEY_ONLY도 만족도 조사(S-NT-A)의 활성 여부·쿨다운을 확인.
- 기준일보다 뒤 날짜의 조치 이력은 '오늘 한 조치'로 본다(쿨다운·누적 비용 모두 포함, 표시 일수는 0).
"""
from __future__ import annotations
import datetime as dt
from ..facts import merged_facts
from .eligibility import check_eligibility

SURVEY_ID = "S-NT-A"
DEFAULT_CFG = dict(cooldown_same_solution_days=14, cooldown_count_statuses=["조치완료"], cost_cap_ratio=1.0,
                   cost_cap_cumulative_ratio=2.0, cost_cap_cumulative_days=90, max_actions=2,
                   survey_recommend_rank_range=[0.20, 0.30], fallback_survey_when_empty=True,
                   unknown_required_fact="fail", facts_cfg={})


def _days(today, d):
    """조치일로부터 지난 일수. 기준일보다 뒤 날짜는 0일(오늘)로 본다."""
    return max((today - dt.date.fromisoformat(str(d)[:10])).days, 0)


def decide_mode(inp, cfg):
    p = inp["prediction"]
    if p["risk_level"] in ("HIGH", "MID"): return "ACTION" if inp["types"] else "NO_TYPE"
    lo, hi = cfg["survey_recommend_rank_range"]
    return "SURVEY_ONLY" if lo <= p["risk_rank_pct"] < hi else "NONE"


def ad_consent(inp) -> bool:
    v = (inp.get("customer") or {}).get("ad_consent_yn") or inp["subscription"].get("ad_consent_yn")
    return v == "Y"


def run_rule_engine(inp: dict, catalog: list[dict], cfg: dict | None = None) -> dict:
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    today = dt.date.fromisoformat(inp["as_of_date"])
    mode = decide_mode(inp, cfg)
    fee = inp["subscription"]["monthly_fee"]
    facts, _ = merged_facts(inp, cfg.get("facts_cfg"))
    done = [a for a in inp.get("recent_actions", []) if a["action_status"] in cfg["cooldown_count_statuses"]]
    spent = sum(a["offer_cost"] for a in done if _days(today, a["created_at"]) < cfg["cost_cap_cumulative_days"])
    cap, cum_cap = fee * cfg["cost_cap_ratio"], fee * cfg["cost_cap_cumulative_ratio"]
    cum_left = max(cum_cap - spent, 0)
    limits = dict(max_actions=cfg["max_actions"], cost_cap=cap, cost_used=0, cumulative_cap=cum_cap,
                  spent_90d=spent, remaining_cumulative=cum_left)

    def ex(c, reason, detail=""): return dict(solution_id=c["solution_id"], type_id=c["type_id"], reason=reason, detail=detail)

    def cooldown_hit(sid):
        return next((a for a in done if a["solution_id"] == sid and _days(today, a["created_at"]) < cfg["cooldown_same_solution_days"]), None)

    if mode == "NONE":
        return dict(mode=mode, candidates=[], excluded=[], limits=limits, uncovered_types=[], fallback=None, survey_solution=None)
    if mode == "SURVEY_ONLY":
        nt = next((s for s in catalog if s["solution_id"] == SURVEY_ID and s["active_yn"] == "Y"), None)
        excluded, survey = [], None
        if nt:
            hit = cooldown_hit(SURVEY_ID)
            if hit: excluded.append(ex(nt, "COOLDOWN_SOLUTION", f"같은 솔루션을 {_days(today, hit['created_at'])}일 전에 조치완료"))
            else: survey = SURVEY_ID
        return dict(mode=mode, candidates=[], excluded=excluded, limits=limits, uncovered_types=[], fallback=None, survey_solution=survey)

    types = inp["types"] if mode == "ACTION" else [dict(type_id="유형없음", type_rank=1, contribution_share=1.0)]
    cands = [dict(s, type_rank=t["type_rank"], contribution_share=t["contribution_share"])
             for t in types for s in catalog
             if s["type_id"] == t["type_id"] and s["active_yn"] == "Y" and s.get("agent_selectable_yn", "Y") == "Y"]
    excluded, kept = [], []
    ad_ok = ad_consent(inp)
    for c in cands:
        if c.get("ad_yn", "N") == "Y" and not ad_ok:
            excluded.append(ex(c, "NO_AD_CONSENT", "광고성(친구톡) 솔루션인데 광고 수신 동의 없음(값이 없으면 미동의)")); continue
        ok, why = check_eligibility(c.get("eligibility"), facts, cfg["unknown_required_fact"])
        if not ok:
            excluded.append(ex(c, "NOT_ELIGIBLE", why)); continue
        hit = cooldown_hit(c["solution_id"])
        if hit:
            excluded.append(ex(c, "COOLDOWN_SOLUTION", f"같은 솔루션을 {_days(today, hit['created_at'])}일 전에 조치완료")); continue
        kept.append(c)
    kept.sort(key=lambda c: (-c["contribution_share"], c["type_rank"], c.get("priority", 50), c["offer_cost"], c["solution_id"]))
    final, cats, used, why = [], set(), 0, {}

    def try_add(c):
        nonlocal used
        if len(final) >= cfg["max_actions"]: return "MAX_ACTIONS"
        if c["category"] in cats: return "DUP_CATEGORY"
        counts = c["offer_cost"] > 0
        if counts and used + c["offer_cost"] > cap: return "COST_CAP"
        if counts and used + c["offer_cost"] > cum_left: return "COST_CAP_CUM"
        final.append(c); cats.add(c["category"])
        if counts: used += c["offer_cost"]
        return None

    for tid in dict.fromkeys(c["type_id"] for c in kept):          # 1라운드: 유형별 대표 1건(커버리지 우선)
        for c in (x for x in kept if x["type_id"] == tid):
            r = try_add(c)
            if r is None: break
            why.setdefault(c["solution_id"], r)
    for c in kept:                                                  # 2라운드: 남는 슬롯 채우기
        if c not in final:
            r = try_add(c)
            if r: why.setdefault(c["solution_id"], r)
    excluded += [ex(c, why.get(c["solution_id"], "MAX_ACTIONS")) for c in kept if c not in final]
    covered = {c["type_id"] for c in final}
    uncovered = [t["type_id"] for t in types if t["type_id"] not in covered] if mode == "ACTION" else ([] if final else ["유형없음"])
    limits["cost_used"] = used
    fallback = None
    if mode == "ACTION" and not final and cfg["fallback_survey_when_empty"]:
        # 조건이 맞는 솔루션이 하나도 없으면 만족도 조사(S-NT-A)로 대체한다(유형 없음 경로와 같은 처리)
        nt = next((s for s in catalog if s["solution_id"] == SURVEY_ID and s["active_yn"] == "Y"), None)
        if nt and not cooldown_hit(SURVEY_ID):
            final = [dict(nt, type_rank=1, contribution_share=1.0)]; mode = "NO_TYPE"; fallback = "NO_ELIGIBLE_CANDIDATE"
            uncovered = [t["type_id"] for t in types]
    return dict(mode=mode, candidates=final, excluded=excluded, limits=limits, uncovered_types=uncovered,
                fallback=fallback, survey_solution=None)
