"""페이지 2: 기본 모집단(케어 매니저·고객·구독) + 유형 활성화/강도 추첨.

build_population(cfg) -> dict(care_manager, customer, subscription, truth_type, models)  (모두 pandas.DataFrame)
- 유형 활성화로 달라지는 구독·고객 컬럼(T01 프로모션, T03 만료일 등)은 여기서 건드리지 않고 페이지 3에서 변조한다.
  그래서 T03 강도(만료 임박도 함수)는 비워 두고(NaN), 페이지 3에서 채운다.
- 같은 설정·시드면 항상 같은 결과. 섹션별 난수 흐름을 따로 써서 한 섹션 설정을 바꿔도 다른 섹션이 흔들리지 않는다.
- 이름은 가상(성+이름 조합)이며 실제 인물과 무관하다.
"""
from __future__ import annotations
import calendar, datetime as dt
import numpy as np, pandas as pd
from .config import as_of, TYPE_IDS

SURNAMES = list("김이박최정강조윤장임한오서신권황안송류홍")
GIVEN = ["하늘", "도윤", "서준", "유나", "민재", "지우", "세린", "아라", "지민", "수빈", "현우", "예린", "준호", "소연", "태민", "나은", "건우", "다은", "시우", "채원"]
MODEL_LETTERS = {"의류관리기": "STY", "건조기": "DRY", "냉장고": "REF", "세탁기": "WSH", "에어컨": "AIR", "정수기": "WTR"}


def add_months(d: dt.date, n: int) -> dt.date:
    """월 가감. 말일 초과는 그 달 말일로 자른다(1/31 + 1개월 = 2/28)."""
    y, m = divmod(d.month - 1 + n, 12); y += d.year
    return dt.date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def _rngs(seed: int) -> dict:
    names = ["managers", "customers", "subscriptions", "types"]
    return dict(zip(names, (np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(len(names)))))


def _names(rng, n):
    return [SURNAMES[i] + GIVEN[j] for i, j in zip(rng.integers(0, len(SURNAMES), n), rng.integers(0, len(GIVEN), n))]


def _norm(d: dict):
    keys = list(d); w = np.array([d[k] for k in keys], dtype=float)
    return keys, w / w.sum()


def _exact_counts(total: int, probs) -> np.ndarray:
    """확률을 합이 정확히 total인 정수 개수로(가장 큰 나머지 순으로 보정)."""
    raw = np.asarray(probs) * total; base = np.floor(raw).astype(int)
    for i in np.argsort(-(raw - base))[: total - base.sum()]: base[i] += 1
    return base


def build_managers(cfg, rng) -> pd.DataFrame:
    regions, p = _norm(cfg["customers"]["region_weights"]); n = int(cfg["run"]["care_manager_count"])
    counts = np.ones(len(regions), dtype=int)                       # 모든 시도에 최소 1명
    counts += _exact_counts(n - len(regions), p)
    region = np.repeat(regions, counts); rng.shuffle(region)
    m = cfg["managers"]
    return pd.DataFrame({
        "manager_id": [f"M{i + 1:03d}" for i in range(n)], "manager_name": _names(rng, n), "region_sido": region,
        "avg_rating": np.clip(rng.normal(m["rating_mean"], m["rating_std"], n), 1, 5).round(2),
        "active_yn": np.where(rng.random(n) < m["active_prob"], "Y", "N")})


def build_models(cfg, rng) -> dict:
    """제품별 모델 풀. 단종·후속 신모델 여부는 모델 단위로 고정. 제품마다 단종 모델 최소 1개."""
    mp = cfg["population"]["model_pool"]; out = {}
    for prod in cfg["population"]["products"]:
        k = int(rng.integers(mp["size"][0], mp["size"][1] + 1))
        out[prod] = [(f"{MODEL_LETTERS[prod]}-A{i + 1:02d}", "Y" if rng.random() < mp["discontinued_prob"] else "N",
                      "Y" if rng.random() < mp["new_model_released_prob"] else "N") for i in range(k)]
        if not any(m[1] == "Y" for m in out[prod]):                     # 제품마다 단종 모델이 최소 1개(T03 '단종 모델 우선'이 모든 제품에서 가능하도록)
            i = int(rng.integers(0, k)); out[prod][i] = (out[prod][i][0], "Y", out[prod][i][2])
    return out


def build_population(cfg: dict) -> dict:
    R = _rngs(int(cfg["run"]["seed"])); n = int(cfg["run"]["n_subscriptions"]); today = as_of(cfg)
    pop, cu = cfg["population"], cfg["customers"]
    mgr = build_managers(cfg, R["managers"])

    # ---- 구독 기본 ----
    r = R["subscriptions"]; prods, pp = _norm({k: v["weight"] for k, v in pop["products"].items()})
    prod = np.array(prods)[_exact_assign(r, n, pp)]
    contract = np.array([pop["products"][p]["contract_months"] for p in prod])
    elapsed = np.array([int(r.integers(1, c)) for c in contract])           # U(1, 계약개월-1) [확정]
    jitter = r.integers(0, 15, n)                                           # 같은 달 안에서 시작일이 겹치지 않게 0~14일 앞당김
    start = [add_months(today, -int(e)) - dt.timedelta(days=int(j)) for e, j in zip(elapsed, jitter)]
    end = [add_months(s, int(c)) for s, c in zip(start, contract)]
    mand = np.array([c if p in pop["mandatory_equals_contract"] else pop["mandatory_months"] for p, c in zip(prod, contract)])
    fmin = np.array([pop["products"][p]["fee_min"] for p in prod]); fmax = np.array([pop["products"][p]["fee_max"] for p in prod])
    step = int(pop["fee_round_to"]); fee = (np.round((fmin + r.random(n) * (fmax - fmin)) / step) * step).astype(int)

    care_keys, care_p = _norm(pop["care_type_weights"]); care = np.array(care_keys)[r.choice(len(care_keys), n, p=care_p)]
    for p_name, w in pop.get("care_type_by_product", {}).items():
        idx = np.where(prod == p_name)[0]; k, pr = _norm(w); care[idx] = np.array(k)[r.choice(len(k), len(idx), p=pr)]
    has_care = care != "없음"
    auto = np.where(has_care & (r.random(n) < pop["care_auto_renew_prob"]), "Y", "N")
    renew = [_next_anniversary(s, today) if h else None for s, h in zip(start, has_care)]

    d_el = r.random(n) < pop["discount"]["eligible_prob"]
    d_ap = d_el & (r.random(n) < pop["discount"]["applied_prob_normal"])
    pr_cfg = pop["promo"]; holder = r.random(n) < pr_cfg["holder_prob"]
    amt = r.choice(pr_cfg["amounts"], n); pend = [add_months(s, int(r.integers(pr_cfg["end_after_start_months"][0], pr_cfg["end_after_start_months"][1] + 1))) for s in start]
    live = holder & np.array([e > today for e in pend])                      # 이미 끝난 프로모션은 없는 것으로

    models = build_models(cfg, R["subscriptions"]); mname, mdisc, mnew = [], [], []
    for p_name in prod:
        m = models[p_name][int(r.integers(0, len(models[p_name])))]; mname.append(m[0]); mdisc.append(m[1]); mnew.append(m[2])

    # ---- 고객 (구독과 1:1) ----
    rc = R["customers"]; ages, ap = _norm(cu["age_group_weights"]); regs, rp = _norm(cu["region_weights"])
    yn = lambda prob: np.where(rc.random(n) < prob, "Y", "N")
    lo, hi = cu["joined_before_start_days"]
    customer = pd.DataFrame({
        "customer_id": [f"C{i + 1:06d}" for i in range(n)], "customer_name": _names(rc, n),
        "age_group": np.array(ages)[rc.choice(len(ages), n, p=ap)], "region_sido": np.array(regs)[rc.choice(len(regs), n, p=rp)],
        "joined_at": [s - dt.timedelta(days=int(d)) for s, d in zip(start, rc.integers(lo, hi + 1, n))],
        "thinq_app_installed": yn(cu["thinq_app_installed_prob"]), "notify_consent_yn": yn(cu["notify_consent_prob"]),
        "ad_consent_yn": yn(cu["ad_consent_prob"]), "weekday_daytime_available_yn": yn(cu["weekday_daytime_available_prob"])})

    # ---- 담당 매니저 배정(방문관리만, 같은 시도 매니저 중 무작위) ----
    by_region = {g: v["manager_id"].to_numpy() for g, v in mgr.groupby("region_sido")}
    cm = [(r.choice(by_region[reg]) if c == "방문관리" else None) for reg, c in zip(customer["region_sido"], care)]

    subscription = pd.DataFrame({
        "subscription_id": [f"S{i + 1:06d}" for i in range(n)], "customer_id": customer["customer_id"], "product_category": prod,
        "model_name": mname, "model_discontinued_yn": mdisc, "new_model_released_yn": mnew,
        "start_date": start, "contract_months": contract, "mandatory_months": mand, "end_date": end, "monthly_fee": fee,
        "discount_eligible_yn": np.where(d_el, "Y", "N"), "discount_applied_yn": np.where(d_ap, "Y", "N"),
        "care_type": care, "care_auto_renew_yn": auto, "care_renewal_date": renew,
        "promo_discount_amount": np.where(live, amt, 0), "promo_end_date": [e if l else None for e, l in zip(pend, live)],
        "care_manager_id": cm, "manager_change_confirmed_yn": "N", "status": "활성"})

    truth = assign_types(cfg, subscription["subscription_id"].to_numpy(), R["types"])
    return dict(care_manager=mgr, customer=customer, subscription=subscription, truth_type=truth, models=models)


def _exact_assign(rng, n, probs) -> np.ndarray:
    """제품군 같은 범주를 비중대로 정확한 개수로 배정한 뒤 섞는다."""
    out = np.repeat(np.arange(len(probs)), _exact_counts(n, probs)); rng.shuffle(out); return out


def _next_anniversary(start: dt.date, today: dt.date) -> dt.date:
    k = 1
    while add_months(start, 12 * k) <= today: k += 1
    return add_months(start, 12 * k)


def assign_types(cfg, sub_ids, rng) -> pd.DataFrame:
    """정상/활성 구분(정확한 비율) → 활성 구독의 유형 개수(1/2/3) → 유형 선택(type_weights, 비복원) → 강도 U(0.4, 1.0).
    T03 강도는 만료 임박도 함수라 NaN으로 두고 페이지 3에서 채운다. is_primary_yn은 라벨 단계에서 정한다."""
    n = len(sub_ids); ty = cfg["types"]; n_act = round(n * (1 - ty["normal_share"]))
    active = rng.permutation(n)[:n_act]
    ks, kp = _norm({str(k): v for k, v in ty["active_count_weights"].items()}); kcount = _exact_counts(n_act, kp)
    k_per = rng.permutation(np.repeat([int(k) for k in ks], kcount))
    tp = np.array([ty["type_weights"][t] for t in TYPE_IDS]); tp = tp / tp.sum()
    lo, hi = ty["strength"]["low"], ty["strength"]["high"]; rows = []
    for si, k in zip(active, k_per):
        for ti in rng.choice(len(TYPE_IDS), int(k), replace=False, p=tp):
            t = TYPE_IDS[ti]; s = np.nan if t == "T03" else round(float(rng.uniform(lo, hi)), 2)
            rows.append((sub_ids[si], t, s))
    return pd.DataFrame(rows, columns=["subscription_id", "type_id", "activation_strength"])
