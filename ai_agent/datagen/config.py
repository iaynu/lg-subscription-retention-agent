"""더미 데이터 생성기 설정 로더 + 검증 (페이지 1).

사용:  from datagen.config import load_config;  cfg = load_config()   # 기본 datagen/config.yaml
잘못된 설정(비중 합 불일치, 알 수 없는 유형 ID 등)은 생성 전에 ValueError로 막는다.
"""
from __future__ import annotations
import datetime as dt
from pathlib import Path
import yaml

HERE = Path(__file__).resolve().parent
TYPE_IDS = [f"T{i:02d}" for i in range(1, 11)]
PRODUCTS = ["의류관리기", "건조기", "냉장고", "세탁기", "에어컨", "정수기"]


def _close(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def validate(cfg: dict) -> dict:
    """설정을 검사하고, 문제가 있으면 모아서 한 번에 알린다."""
    err = []
    run, pop, ty, lb, sg = cfg["run"], cfg["population"], cfg["types"], cfg["label"], cfg["signals"]
    if int(run["n_subscriptions"]) < 1000: err.append("run.n_subscriptions는 1000 이상이어야 분포 검증이 의미 있음")
    try: dt.date.fromisoformat(str(run["as_of_date"]))
    except ValueError: err.append("run.as_of_date는 YYYY-MM-DD")
    if set(pop["products"]) != set(PRODUCTS): err.append(f"제품군은 정확히 {PRODUCTS}")
    else:
        if not _close(sum(p["weight"] for p in pop["products"].values()), 1.0, 1e-3): err.append("제품군 비중 합이 1이 아님")
        for n, p in pop["products"].items():
            if not 0 < p["fee_min"] < p["fee_max"]: err.append(f"{n}: 요금 범위 오류")
            if p["contract_months"] <= int(pop["mandatory_months"]): err.append(f"{n}: 계약기간이 의무사용기간보다 길어야 함")
    if not _close(sum(pop["care_type_weights"].values()), 1.0, 1e-3): err.append("케어 유형 비중 합이 1이 아님")
    cu = cfg["customers"]
    if not _close(sum(cu["age_group_weights"].values()), 1.0, 1e-3): err.append("연령대 비중 합이 1이 아님")
    if len(cu["region_weights"]) != 17 or min(cu["region_weights"].values()) <= 0: err.append("지역은 17개 시도, 비중은 양수")
    for n, w in pop.get("care_type_by_product", {}).items():
        if n not in PRODUCTS or not _close(sum(w.values()), 1.0, 1e-3): err.append(f"care_type_by_product.{n} 오류")
    if not set(pop.get("mandatory_equals_contract", [])) <= set(PRODUCTS): err.append("mandatory_equals_contract에 알 수 없는 제품")
    md = cfg["modulation"]
    if not set(md["visit_care_types"]) <= set(TYPE_IDS): err.append("modulation.visit_care_types에 알 수 없는 유형")
    for k in ("promo_end_days", "unapplied_discount_prob"):
        if len(md["t01"][k]) != 2: err.append(f"modulation.t01.{k}는 두 값")
    if not 0 <= md["t07"]["low_rating_quantile"] <= 1: err.append("t07.low_rating_quantile 범위 오류")
    if not 0 < cfg["managers"]["active_prob"] <= 1: err.append("managers.active_prob 범위 오류")
    if not 0 < ty["normal_share"] < 1: err.append("types.normal_share는 0~1 사이")
    if not _close(sum(ty["active_count_weights"].values()), 1.0): err.append("활성 유형 개수 비중 합이 1이 아님")
    if max(int(k) for k in ty["active_count_weights"]) > 3: err.append("활성 유형은 최대 3개 [확정]")
    for name, d in (("type_weights", ty["type_weights"]), ("label.type_w", lb["type_w"]), ("signals", sg), ("review_counts", ty["review_counts"])):
        if sorted(d) != TYPE_IDS: err.append(f"{name}의 유형 ID가 T01~T10과 다름")
    if not _close(sum(ty["type_weights"].values()), 1.0, 2e-3): err.append(f"type_weights 합이 1이 아님: {sum(ty['type_weights'].values()):.4f}")
    if not 0 <= ty["strength"]["low"] < ty["strength"]["high"] <= 1: err.append("strength 범위 오류")
    if len(lb["rank_multipliers"]) != 3: err.append("rank_multipliers는 3개(최대 유형 수)")
    lo, hi = lb["churn_day_range"]
    if not 1 <= lo <= hi <= 30: err.append("churn_day_range는 1~30 안")
    for k in ("normal_rate", "active_rate", "overall_rate"):
        if k not in lb["targets"] or k not in lb["tolerance"]: err.append(f"label.targets/tolerance에 {k} 필요")
    for t, d in sg.items():
        for name, v in d.items():
            if not (isinstance(v, list) and len(v) == 2): err.append(f"signals.{t}.{name}는 [강도0.4, 강도1.0] 두 값")
    if err: raise ValueError("설정 오류:\n- " + "\n- ".join(err))
    return cfg


def load_config(path: str | Path | None = None, **overrides) -> dict:
    """overrides 예: load_config(run={'n_subscriptions': 5000})  → 해당 키만 덮어씀(얕은 병합 한 단계)."""
    cfg = yaml.safe_load(Path(path or HERE / "config.yaml").read_text(encoding="utf-8"))
    for sec, vals in overrides.items(): cfg[sec] = {**cfg[sec], **vals}
    return validate(cfg)


def as_of(cfg) -> dt.date:
    return dt.date.fromisoformat(str(cfg["run"]["as_of_date"]))


def expected_counts(cfg) -> dict:
    """설정만으로 계산한 기대 규모(생성 전 눈으로 확인용)."""
    n = int(cfg["run"]["n_subscriptions"]); act = round(n * (1 - cfg["types"]["normal_share"]))
    return dict(total=n, normal=n - act, active=act,
                by_product={p: round(n * v["weight"]) for p, v in cfg["population"]["products"].items()},
                by_active_count={int(k): round(act * v) for k, v in cfg["types"]["active_count_weights"].items()})


if __name__ == "__main__":
    c = load_config(); print("설정 OK"); print(expected_counts(c))
