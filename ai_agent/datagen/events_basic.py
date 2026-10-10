"""페이지 4-1: 사용량(usage_thinq)·결제(payment)·안내 이력(notice_log) 생성.

build_events_basic(cfg, pop) -> dict(usage_thinq, payment, notice_log)   (pop은 페이지 3 결과)
- 정상 구독: 사용량 안정(개인차만), 납부 정상(연체 3%), 안내 열람 60%.
- 신호: T04(사용량 감소·무가동 주), T01(최근 3개월 청구액 상승·연체 15%), T03(재계약 안내 접촉 증가·열람).
- usage_id / payment_id / notice_id 는 DB IDENTITY가 채운다(여기서는 만들지 않음).
"""
from __future__ import annotations
import datetime as dt
import numpy as np, pandas as pd
from .config import as_of
from .modulation import lerp


def _rng(seed: int, k: int):
    return np.random.default_rng(np.random.SeedSequence(seed).spawn(8)[k])


def week_starts(today: dt.date, n: int) -> list[dt.date]:
    """일요일(주 마지막 날)이 기준일 이하인 마지막 주부터 n주 거슬러 올라간 월요일 목록(오래된 순)."""
    last = today - dt.timedelta(days=(today.weekday() + 1) % 7 + 6)          # 기준일 이하의 마지막 일요일 − 6일 = 그 주 월요일
    return [last - dt.timedelta(weeks=n - 1 - i) for i in range(n)]


# ------------------------------------------------------------------ usage
def build_usage(cfg, pop) -> pd.DataFrame:
    r = _rng(int(cfg["run"]["seed"]), 5); ev = cfg["events"]; today = as_of(cfg)
    sub = pop["subscription"]; cu = pop["customer"].set_index("customer_id"); n = len(sub); W = int(ev["usage_weeks"]); RW = int(ev["usage_recent_weeks"])
    weeks = week_starts(today, W)
    prod = sub["product_category"].to_numpy(); P = cfg["population"]["products"]
    base = np.array([P[p]["base_usage_per_week"] for p in prod]); mins = np.array([ev["usage_minutes_per_run"][p] for p in prod])
    lam = base * r.lognormal(0.0, ev["usage_person_sigma"], n)                   # 구독자별 평소 가동 횟수
    mult = np.ones((n, W)); zero = np.zeros((n, W), dtype=bool)

    t04 = pop["truth_type"].query("type_id == 'T04'").set_index("subscription_id")["activation_strength"]
    idx = pd.Series(np.arange(n), index=sub["subscription_id"])
    lo_z, hi_z = cfg["signals"]["T04"]["zero_use_weeks_12w"]; r_lo, r_hi = cfg["signals"]["T04"]["recent_vs_prior_usage_ratio"]
    ramp_w = np.exp(0.25 * np.arange(RW)); ramp_w = ramp_w / ramp_w.sum()           # 최근 주일수록 무가동이 될 확률이 큼
    for sid, s in t04.items():
        i = int(idx[sid]); z = int(round(lerp(lo_z, hi_z, s))); end_m = lerp(r_lo, r_hi, s)
        pos = r.choice(RW, size=z, replace=False, p=ramp_w)                          # 최근 12주 중 무가동 주
        zero[i, W - RW + pos] = True
        mult[i, W - RW:] = 1 - (1 - end_m) * (np.arange(RW) / (RW - 1))              # 남은 주도 점점 감소(최근 4주 / 이전 4주 비 하락)
    counts = r.poisson(lam[:, None] * mult); counts[zero] = 0
    minutes = np.rint(counts * mins[:, None] * r.lognormal(0.0, 0.2, (n, W))).astype(int)
    installed = (cu.loc[sub["customer_id"], "thinq_app_installed"].to_numpy() == "Y")
    act = np.clip(counts / (lam[:, None] + 1e-9), 0, 1.5)
    p_login = np.clip((0.10 + 0.45 * act) * np.where(mult < 1, np.maximum(mult, 0.0) * 0.5 + 0.5, 1.0), 0, 0.9)   # T04면 앱 접속도 후반부 감소
    login = np.where(installed[:, None], r.binomial(7, p_login), 0)
    # 가입 이전 주는 행을 만들지 않는다(주의 마지막 날 ≥ 시작일인 주만)
    start = np.array(sub["start_date"], dtype="datetime64[D]"); wk = np.array(weeks, dtype="datetime64[D]")
    valid = (wk[None, :] + 6) >= start[:, None]
    ii, jj = np.nonzero(valid)
    return pd.DataFrame({"subscription_id": sub["subscription_id"].to_numpy()[ii], "week_start_date": np.array(weeks, dtype=object)[jj],
                         "run_count": counts[ii, jj].astype("int16"), "run_minutes": minutes[ii, jj], "app_login_days": login[ii, jj].astype("int8")})


# ------------------------------------------------------------------ payment
def _months(today: dt.date, n: int) -> list[dt.date]:
    out = []; y, m = today.year, today.month
    for _ in range(n): out.append(dt.date(y, m, 1)); y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def build_payment(cfg, pop) -> pd.DataFrame:
    r = _rng(int(cfg["run"]["seed"]), 6); ev = cfg["events"]; today = as_of(cfg)
    sub = pop["subscription"]; n = len(sub); M = int(ev["payment_months"]); months = _months(today, M)
    fee = sub["monthly_fee"].to_numpy().astype(float); amt = sub["promo_discount_amount"].to_numpy()
    pend = np.array([np.datetime64(d, "D") if d is not None else np.datetime64("NaT") for d in sub["promo_end_date"]])
    mon = np.array(months, dtype="datetime64[D]")
    billed = np.tile(fee[:, None], (1, M))
    t01 = pop["truth_type"].query("type_id == 'T01'").set_index("subscription_id")["activation_strength"]
    idx = pd.Series(np.arange(n), index=sub["subscription_id"]); p_late = np.full(n, ev["late_prob"]["normal"])
    k = int(ev["t01_price_increase"]["months"]); lo, hi = ev["t01_price_increase"]["rate"]
    for sid, s in t01.items():
        i = int(idx[sid]); p_late[i] = ev["late_prob"]["t01"]
        billed[i, M - k:] = np.round(fee[i] * (1 + lerp(lo, hi, s)) / 100) * 100       # 최근 k개월 청구액 인상
    disc = np.where((~np.isnat(pend))[:, None] & (mon[None, :] <= pend[:, None]), amt[:, None], 0)
    start_m = np.array(sub["start_date"], dtype="datetime64[M]").astype("datetime64[D]")
    valid = mon[None, :] >= start_m[:, None]
    late = r.random((n, M)) < p_late[:, None]
    due = np.array([dt.date(m.year, m.month, int(ev["payment_due_day"])) for m in months], dtype="datetime64[D]")
    early = r.integers(ev["early_pay_days"][0], ev["early_pay_days"][1] + 1, (n, M)); ld = r.integers(ev["late_days"][0], ev["late_days"][1] + 1, (n, M))
    paid_date = np.where(late, due[None, :] + ld, due[None, :] - early)
    unpaid = late & (paid_date > np.datetime64(today, "D"))                              # 납부일이 기준일을 넘으면 아직 미납
    ii, jj = np.nonzero(valid)
    st = np.where(unpaid, "미납", np.where(late, "연체", "정상"))[ii, jj]
    net = (billed - disc)[ii, jj]
    return pd.DataFrame({"subscription_id": sub["subscription_id"].to_numpy()[ii], "billing_month": np.array(months, dtype=object)[jj],
                         "billed_amount": billed[ii, jj].astype(int), "discount_amount": disc[ii, jj].astype(int),
                         "paid_amount": pd.array(np.where(st == "미납", np.nan, net), dtype="Float64").astype("Int64"),
                         "due_date": due.astype(object)[jj], "paid_date": pd.array(np.where(st == "미납", None, paid_date[ii, jj].astype(object)), dtype=object),
                         "payment_status": st})


# ------------------------------------------------------------------ notice
def build_notices(cfg, pop) -> pd.DataFrame:
    r = _rng(int(cfg["run"]["seed"]), 7); nt = cfg["events"]["notices"]; today = as_of(cfg); now = dt.datetime.combine(today, dt.time(23, 59))
    sub = pop["subscription"]; t3 = pop["truth_type"].query("type_id == 'T03'").set_index("subscription_id")["activation_strength"]
    lo, hi = cfg["signals"]["T03"]["renewal_notice_cnt"]; rows = []
    window_start = dt.datetime.combine(today - dt.timedelta(days=180), dt.time(0, 0))

    def add(sid, kind, sent, open_p):
        o = sent + dt.timedelta(hours=int(r.integers(1, 49))) if r.random() < open_p else None
        if o is not None and o > now: o = None
        rows.append((sid, kind, sent, o))

    for sid, start, end in zip(sub["subscription_id"], sub["start_date"], sub["end_date"]):
        d = (end - today).days
        if d <= 90:                                                                       # 갱신안내: 만료 D-90 기본 1회, T03면 접촉 횟수만큼
            ks = [k for k in nt["renewal_ks"] if k >= d]
            n_send = int(round(lerp(lo, hi, t3[sid], 0.3, 1.0))) if sid in t3.index else 1
            for k in ks[:max(n_send, 1)]:
                add(sid, "갱신안내", dt.datetime.combine(end - dt.timedelta(days=k), dt.time(10, 0)), nt["t03_renewal_open_prob"] if sid in t3.index else nt["open_prob"])
        for kind, cfgk in (("사용팁", nt["tips"]), ("계약조건안내", nt["terms"])):
            if r.random() < cfgk["prob"]:
                for _ in range(int(r.integers(cfgk["count"][0], cfgk["count"][1] + 1))):
                    sent = window_start + dt.timedelta(days=int(r.integers(0, 180)), hours=10)
                    if sent.date() > start: add(sid, kind, sent, nt["open_prob"])
    return pd.DataFrame(rows, columns=["subscription_id", "notice_type", "sent_at", "opened_at"])


def build_events_basic(cfg: dict, pop: dict) -> dict:
    return dict(usage_thinq=build_usage(cfg, pop), payment=build_payment(cfg, pop), notice_log=build_notices(cfg, pop))
