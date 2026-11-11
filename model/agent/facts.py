"""facts(자격 조건 판정용 값)와 message_facts(고객에게 보여도 되는 값)의 정의·계산·표시.

- facts: Rule Engine의 eligibility 판정에만 쓴다. 문의 횟수 같은 내부 분석값이 섞여 있으므로 LLM에 보내지 않는다.
- message_facts: 고객 본인의 계약·이용 정보로, 고객 메시지의 ▶ 항목에 그대로 써도 되는 값만 담는다.
  검증기는 고객 문구의 숫자를 이 값(단위까지)과 후보 문구에 있는 숫자로만 허용한다.
"""
from __future__ import annotations
import datetime as dt

# key: (화면 라벨, 종류). 종류가 숫자 단위와 표시 형식을 정한다.
MESSAGE_FACT_DEFS: dict[str, tuple[str, str]] = {
    "monthly_fee": ("현재 월 구독료", "won"),
    "contract_months": ("약정 기간", "month_count"),
    "end_date": ("약정 종료일", "date"),
    "days_to_end": ("약정 종료까지", "days_left"),
    "promo_end_date": ("프로모션 종료일", "date"),
    "care_renewal_date": ("케어 자동 연장일", "date"),
    "months_since_start": ("이용 기간", "month_count"),
    "paid_total": ("지금까지 낸 구독료", "won"),
    "remaining_care_cnt": ("올해 남은 케어", "count"),
    "contract_year_end_date": ("케어 사용 기한", "date"),
    "trial_end_date": ("체험 기간 종료일", "date"),
    "last_care_date": ("최근 케어일", "date"),
    "next_visit_date": ("다음 방문 예정일", "date"),
    "last_visit_date": ("최근 방문일", "date"),
    "days_since_last_use": ("마지막 사용", "days_ago"),
    "expected_as_days": ("예상 수리 기간", "days"),
}

# 종류 → 고객 문구에서 허용하는 (값, 단위) 쌍을 만드는 규칙에 쓰는 단위 이름
KIND_UNIT = {"won": "won", "month_count": "month_count", "count": "count",
             "days_left": "day", "days_ago": "day", "days": "day"}


def _to_date(v):
    if isinstance(v, dt.date): return v
    if isinstance(v, str) and len(v) >= 10:
        try: return dt.date.fromisoformat(v[:10])
        except ValueError: return None
    return None


def months_between(a: dt.date, b: dt.date) -> int:
    """a(시작)부터 b(기준일)까지 꽉 찬 개월 수."""
    m = (b.year - a.year) * 12 + (b.month - a.month)
    return m - (1 if b.day < a.day else 0)


def add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year; m += 1
    for day in (d.day, 30, 29, 28):
        try: return dt.date(y, m, day)
        except ValueError: continue
    raise ValueError(d)


def contract_year_bounds(start: dt.date, as_of: dt.date) -> tuple[dt.date, dt.date]:
    """기준일이 속한 계약 연도 [시작, 끝)."""
    k = max(months_between(start, as_of) // 12, 0)
    return add_months(start, 12 * k), add_months(start, 12 * (k + 1))


def derive_from_subscription(sub: dict, as_of: str, facts_cfg: dict | None = None) -> tuple[dict, dict]:
    """구독 속성만으로 계산할 수 있는 facts와 message_facts. 입력에 이미 있는 값이 우선한다(호출 쪽에서 병합)."""
    cfg = facts_cfg or {}
    today = dt.date.fromisoformat(as_of)
    f, mf = {}, {}
    end = _to_date(sub.get("end_date"))
    if sub.get("days_to_end") is not None: f["days_to_end"] = sub["days_to_end"]
    elif end: f["days_to_end"] = (end - today).days
    if "promo_end_date" in sub:                      # 키가 있고 값이 없으면 '프로모션 없음'으로 확정
        pe = _to_date(sub.get("promo_end_date"))
        f["days_to_promo_end"] = (pe - today).days if pe else None
        if pe and pe >= today: mf["promo_end_date"] = pe.isoformat()
    start = _to_date(sub.get("start_date"))
    if start:
        f["months_since_start"] = months_between(start, today)
        trial_end = start + dt.timedelta(days=int(cfg.get("trial_days", 7)))
        f["days_to_trial_end"] = (trial_end - today).days
        _, year_end = contract_year_bounds(start, today)
        f["days_to_contract_year_end"] = (year_end - today).days
        mf["months_since_start"] = f["months_since_start"]
        if f["days_to_trial_end"] >= 0: mf["trial_end_date"] = trial_end.isoformat()
        mf["contract_year_end_date"] = (year_end - dt.timedelta(days=1)).isoformat()
    for k in ("new_model_released_yn", "model_discontinued_yn"):
        if sub.get(k) in ("Y", "N"): f[k] = sub[k]
    for k in ("monthly_fee", "contract_months"):
        if sub.get(k) is not None: mf[k] = sub[k]
    if end: mf["end_date"] = end.isoformat()
    if f.get("days_to_end") is not None and f["days_to_end"] >= 0: mf["days_to_end"] = f["days_to_end"]
    if _to_date(sub.get("care_renewal_date")): mf["care_renewal_date"] = sub["care_renewal_date"][:10]
    return f, mf


def merged_facts(ai: dict, facts_cfg: dict | None = None) -> tuple[dict, dict]:
    """입력 facts/message_facts에 구독 속성에서 계산한 값을 채운다(입력 값이 우선)."""
    f, mf = derive_from_subscription(ai["subscription"], ai["as_of_date"], facts_cfg)
    f.update({k: v for k, v in (ai.get("facts") or {}).items()})
    mf.update({k: v for k, v in (ai.get("message_facts") or {}).items() if k in MESSAGE_FACT_DEFS and v is not None})
    return f, mf


def format_value(key: str, value) -> str:
    kind = MESSAGE_FACT_DEFS[key][1]
    if kind == "won": return f"{int(value):,}원"
    if kind == "month_count": return f"{int(value)}개월"
    if kind == "count": return f"{int(value)}회"
    if kind == "days_left": return f"{int(value)}일 남음"
    if kind == "days_ago": return f"{int(value)}일 전"
    if kind == "days": return f"{int(value)}일"
    if kind == "date":
        d = _to_date(value)
        return f"{d.year}년 {d.month}월 {d.day}일" if d else str(value)
    return str(value)


def fact_line(key: str, value) -> str:
    return f"▶ {MESSAGE_FACT_DEFS[key][0]}: {format_value(key, value)}"


def message_fact_view(mf: dict) -> list[dict]:
    """LLM에 보여 줄 형태: 라벨과 표시 문자열을 함께 준다(숫자를 바꾸지 않고 그대로 옮겨 쓰게)."""
    return [dict(key=k, label=MESSAGE_FACT_DEFS[k][0], text=format_value(k, v)) for k, v in mf.items() if k in MESSAGE_FACT_DEFS]


def allowed_pairs_from_message_facts(mf: dict) -> set:
    """고객 문구에서 허용할 (값, 단위) 쌍."""
    out = set()
    for k, v in (mf or {}).items():
        if k not in MESSAGE_FACT_DEFS or v is None: continue
        kind = MESSAGE_FACT_DEFS[k][1]
        if kind == "date":
            d = _to_date(v)
            if d: out |= {(d.year, "year"), (d.month, "month"), (d.day, "day")}
            continue
        try: n = int(v)
        except (TypeError, ValueError): continue
        out.add((n, KIND_UNIT[kind]))
        if kind == "month_count" and n and n % 12 == 0: out.add((n // 12, "year"))
    return out
