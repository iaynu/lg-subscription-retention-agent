"""페이지 3: 유형 활성 구독의 기본 정보 변조 + T03·T01 강도 확정.

apply_type_modulation(cfg, pop) -> 새 pop (정상 구독은 한 칸도 바뀌지 않는다)
변조는 설계서 §6 '유형별 활성화 규칙 매트릭스' 중 구독·고객·담당자 컬럼에 해당하는 것만:
  T01 프로모션 종료 D-45 이내·미적용 할인  T02 약정 초반 편향  T03 만료 0~90일·신모델·단종 모델
  T06 평일 낮 방문 불가  T07 낮은 평점 매니저·교체 확정(시연용)  T05~T08 케어 유형을 방문관리로(방문 신호 전제)
이벤트(사용·방문·문의·결제)로 나타나는 변조는 페이지 4.
"""
from __future__ import annotations
import datetime as dt
import numpy as np, pandas as pd
from .config import as_of
from .population import add_months, _next_anniversary


def lerp(lo, hi, strength, s_lo=0.4, s_hi=1.0):
    """강도 s_lo → lo, s_hi → hi 로 선형 보간(범위 밖은 끝값)."""
    f = min(max((strength - s_lo) / (s_hi - s_lo), 0.0), 1.0)
    return lo + (hi - lo) * f


def _rng(seed: int):
    return np.random.default_rng(np.random.SeedSequence(seed).spawn(5)[4])


def apply_type_modulation(cfg: dict, pop: dict) -> dict:
    r = _rng(int(cfg["run"]["seed"])); today = as_of(cfg); md = cfg["modulation"]; P = cfg["population"]
    sub = pop["subscription"].copy(); cu = pop["customer"].copy(); mg = pop["care_manager"]; truth = pop["truth_type"].copy()
    sub = sub.set_index("subscription_id", drop=False); cu = cu.set_index("customer_id", drop=False)
    by_type = {t: g.set_index("subscription_id")["activation_strength"] for t, g in truth.groupby("type_id")}
    act = lambda t: by_type[t] if t in by_type else pd.Series(dtype=float)
    models = pop["models"]

    def move_start(sid, new_start):
        """시작일 변경 → 만료일·케어 갱신일·고객 최초 가입일·프로모션을 일관되게 다시 맞춘다."""
        row = sub.loc[sid]; sub.at[sid, "start_date"] = new_start
        sub.at[sid, "end_date"] = add_months(new_start, int(row.contract_months))
        if row.care_type != "없음": sub.at[sid, "care_renewal_date"] = _next_anniversary(new_start, today)
        c = row.customer_id
        if cu.at[c, "joined_at"] > new_start: cu.at[c, "joined_at"] = new_start - dt.timedelta(days=int(r.integers(0, 30)))

    # --- T02: 약정 초반 편향 ---
    t3 = set(act("T03").index)
    for sid in act("T02").index:
        if sid in t3 or r.random() >= md["t02"]["early_start_prob"]: continue            # T03(만료 임박)과 겹치면 T03이 우선
        lo, hi = md["t02"]["early_start_months"]; move_start(sid, add_months(today, -int(r.integers(lo, hi + 1))) - dt.timedelta(days=int(r.integers(0, 15))))

    # --- T03: 만료 0~90일, 강도 = 0.3 + 0.7 × (1 − 만료까지 일수/90) ---
    s3 = {}
    for sid in act("T03").index:
        d = int(r.integers(md["t03"]["end_days"][0], md["t03"]["end_days"][1] + 1)); end = today + dt.timedelta(days=d)
        move_start(sid, add_months(end, -int(sub.at[sid, "contract_months"]))); sub.at[sid, "end_date"] = end
        s = round(0.3 + 0.7 * (1 - d / 90), 2); s3[sid] = s
        lo, hi = md["t03"]["new_model_prob"]; sub.at[sid, "new_model_released_yn"] = "Y" if r.random() < lerp(lo, hi, s, 0.3, 1.0) else "N"
        pool = [m for m in models[sub.at[sid, "product_category"]] if m[1] == "Y"]
        if pool and r.random() < md["t03"]["discontinued_pick_prob"]:
            m = pool[int(r.integers(0, len(pool)))]; sub.at[sid, "model_name"] = m[0]; sub.at[sid, "model_discontinued_yn"] = "Y"

    # --- 프로모션 정합성: 시작~만료 사이에 없는 프로모션은 없는 것으로 ---
    bad = (sub.promo_end_date.notna()) & ((sub.promo_end_date > sub.end_date) | (sub.promo_end_date < sub.start_date))
    sub.loc[bad, "promo_end_date"] = None; sub.loc[bad, "promo_discount_amount"] = 0

    # --- T01: 프로모션 종료 D-45 이내, 미적용 할인 확률 ---
    s1 = {}
    for sid, base in act("T01").items():
        d = int(r.integers(md["t01"]["promo_end_days"][0], md["t01"]["promo_end_days"][1] + 1))
        pe = min(today + dt.timedelta(days=d), sub.at[sid, "end_date"]); d = (pe - today).days
        sub.at[sid, "promo_end_date"] = pe
        if sub.at[sid, "promo_discount_amount"] == 0: sub.at[sid, "promo_discount_amount"] = int(r.choice(md["t01"]["promo_amounts"]))
        s = round(max(base, min(1.0, 1 - d / 45)) if d < 45 else base, 2); s1[sid] = max(s, 0.4)   # 종료 임박할수록 강도 상향
        if r.random() < lerp(*md["t01"]["unapplied_discount_prob"], s1[sid]):
            sub.at[sid, "discount_eligible_yn"] = "Y"; sub.at[sid, "discount_applied_yn"] = "N"

    # --- T06: 평일 낮 방문 불가 확률 ---
    for sid, s in act("T06").items():
        if r.random() < lerp(*md["t06"]["weekday_unavailable_prob"], s): cu.at[sub.at[sid, "customer_id"], "weekday_daytime_available_yn"] = "N"

    # --- T05~T08: 방문 신호가 전제 → 케어 유형을 방문관리로 ---
    need = set()
    for t in md["visit_care_types"]: need |= set(act(t).index)
    mgr_by_region = {g: v for g, v in mg.groupby("region_sido")}
    def pick_manager(region, low=False):
        v = mgr_by_region[region]
        if low:
            cut = v["avg_rating"].quantile(md["t07"]["low_rating_quantile"]); v = v[v["avg_rating"] <= cut]
        return v["manager_id"].to_numpy()[int(r.integers(0, len(v)))]
    upgraded = 0
    for sid in sub.index.intersection(list(need)):
        if sub.at[sid, "care_type"] != "방문관리":
            upgraded += 1; sub.at[sid, "care_type"] = "방문관리"
            sub.at[sid, "care_auto_renew_yn"] = "Y" if r.random() < P["care_auto_renew_prob"] else "N"
            sub.at[sid, "care_renewal_date"] = _next_anniversary(sub.at[sid, "start_date"], today)
            sub.at[sid, "care_manager_id"] = pick_manager(cu.at[sub.at[sid, "customer_id"], "region_sido"])

    # --- T07: 지역 내 낮은 평점 매니저, 교체 확정(시연용) ---
    for sid in act("T07").index:
        sub.at[sid, "care_manager_id"] = pick_manager(cu.at[sub.at[sid, "customer_id"], "region_sido"], low=True)
        if r.random() < md["t07"]["manager_change_confirmed_prob"]: sub.at[sid, "manager_change_confirmed_yn"] = "Y"

    # --- 강도 확정 ---
    truth = truth.set_index(["subscription_id", "type_id"])
    for sid, s in s3.items(): truth.at[(sid, "T03"), "activation_strength"] = s
    for sid, s in s1.items(): truth.at[(sid, "T01"), "activation_strength"] = s
    truth = truth.reset_index()
    out = dict(pop, subscription=sub.reset_index(drop=True), customer=cu.reset_index(drop=True), truth_type=truth)
    out["notes"] = dict(care_upgraded=upgraded)
    return out
