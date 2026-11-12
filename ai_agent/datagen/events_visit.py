"""페이지 4-2a: 방문 이벤트(care_visit) 생성 — 정기점검·A/S수리·이전설치·최초설치.

build_visits(cfg, pop) -> DataFrame(care_visit)   (visit_id는 DB IDENTITY)
- 정상 구독: 정기점검은 제품 주기(대형가전 12개월, 정수기 4개월)의 앵커 날짜, A/S는 월 고장 확률.
- 유형 신호(설계서 §6 매트릭스·신호 세기 표):
    T02 이전설치 요청·지연   T05 케어 소요시간 단축·케어 후 14일 내 A/S   T06 지연·취소·미이행·일정 변경·예약 대기
    T07 담당자 교체·낮은 만족도   T08 방문 중 영업 권유·재권유   T09 반복 A/S·점검 이상   T10 긴 수리·부품 지연·분리 접수
- 대형가전은 정기점검이 연 1건뿐이라 신호를 만들 수 없으므로, 필요한 만큼 '추가 방문'을 정기점검 행으로 더한다 [가정].
"""
from __future__ import annotations
import datetime as dt
import numpy as np, pandas as pd
from .config import as_of
from .modulation import lerp
from .population import add_months

COLS = ["subscription_id", "manager_id", "visit_type", "scheduled_date", "completed_date", "visit_status", "reschedule_cnt", "duration_min",
        "booking_lead_days", "sales_pitch_yn", "repitch_after_refusal_yn", "split_as_receipt_yn", "symptom_code", "repeat_symptom_yn",
        "parts_delay_yn", "inspection_anomaly_yn", "as_received_date", "expected_complete_date", "repair_days", "satisfaction_score"]


def _rng(seed: int):
    return np.random.default_rng(np.random.SeedSequence(seed).spawn(10)[8])


class _Sub:
    """구독 하나의 방문 행을 만드는 작업대(난수는 공유 rng에서 순서대로 뽑는다)."""
    def __init__(self, cfg, r, today, row, st, mgr_pool, rating):
        self.cfg, self.r, self.today, self.row, self.st = cfg, r, today, row, st      # st: {유형: 강도}
        self.v = cfg["visits"]; self.rows = []; self.start = row["start_date"]; self.mgr_pool = mgr_pool; self.rating = rating
        self.win_lo = max(today - dt.timedelta(days=self.v["window_days"]), self.start + dt.timedelta(days=7))

    # ---- 보조 ----
    def rand_date(self, lo: dt.date, hi: dt.date):
        if hi < lo: return None
        return lo + dt.timedelta(days=int(self.r.integers(0, (hi - lo).days + 1)))

    def new(self, vtype, sched, status=None, **kw):
        v, r = self.v, self.r
        d = dict(subscription_id=self.row["subscription_id"], manager_id=self.row["care_manager_id"], visit_type=vtype, scheduled_date=sched,
                 completed_date=None, visit_status=status, reschedule_cnt=0, duration_min=None, booking_lead_days=None, sales_pitch_yn="N",
                 repitch_after_refusal_yn="N", split_as_receipt_yn="N", symptom_code=None, repeat_symptom_yn="N", parts_delay_yn="N",
                 inspection_anomaly_yn="N", as_received_date=None, expected_complete_date=None, repair_days=None, satisfaction_score=None)
        d.update(kw)
        if d["visit_status"] is None:
            k = list(v["care_status_weights"]); p = np.array([v["care_status_weights"][x] for x in k]); d["visit_status"] = str(r.choice(k, p=p / p.sum()))
        if sched > self.today and d["visit_status"] != "예정": d["visit_status"] = "예정"
        self._finish(d); self.rows.append(d); return d

    def _finish(self, d):
        v, r, st = self.v, self.r, self.st
        if d["visit_status"] == "지연완료":
            dd = dt.timedelta(days=int(r.integers(v["delay_days"][0], v["delay_days"][1] + 1)))
            if d["scheduled_date"] + dd <= self.today: d["completed_date"] = d["scheduled_date"] + dd
            else: d["visit_status"] = "완료"
        if d["visit_status"] == "완료" and d["completed_date"] is None: d["completed_date"] = d["scheduled_date"]
        done = d["visit_status"] in ("완료", "지연완료")
        if d["visit_type"] == "정기점검":
            d["reschedule_cnt"] = int(r.poisson(v["reschedule_lambda"]))
            lead = 1 + r.poisson(v["booking_lead_mean"] - 1); d["booking_lead_days"] = int(lead)
            if done:
                d["duration_min"] = int(max(10, round(r.normal(v["duration"]["mean"], v["duration"]["sd"]))))
                d["sales_pitch_yn"] = "Y" if r.random() < v["sales_pitch_prob"] else "N"
                d["repitch_after_refusal_yn"] = "Y" if d["sales_pitch_yn"] == "Y" and r.random() < v["repitch_prob_normal"] else "N"
                d["inspection_anomaly_yn"] = "Y" if r.random() < v["anomaly_prob_normal"] else "N"

    # ---- 윈도우 안 행 ----
    def in_win(self, d): return self.win_lo <= d["scheduled_date"] <= self.today
    def care_done_win(self): return [d for d in self.rows if d["visit_type"] == "정기점검" and d["visit_status"] in ("완료", "지연완료") and self.in_win(d)]

    def extra_care(self, hi_gap=25, status=None, **kw):
        """추가 방문(정기점검 행). 지연완료가 기준일을 넘지 않도록 기준일 - 25일까지만."""
        s = self.rand_date(self.win_lo, self.today - dt.timedelta(days=hi_gap))
        return None if s is None else self.new("정기점검", s, status, **kw)


def build_visits(cfg: dict, pop: dict) -> pd.DataFrame:
    r = _rng(int(cfg["run"]["seed"])); today = as_of(cfg); v = cfg["visits"]; sg = cfg["signals"]; A = v["as"]
    sub = pop["subscription"]; cust = pop["customer"].set_index("customer_id"); mg = pop["care_manager"].set_index("manager_id")
    prof = cfg["population"]["products"]
    st_by = {sid: {} for sid in sub["subscription_id"]}
    for sid, t, s in pop["truth_type"][["subscription_id", "type_id", "activation_strength"]].itertuples(index=False): st_by[sid][t] = float(s)
    pool_by_region = {g: x.index.to_numpy() for g, x in mg.groupby("region_sido")}
    out = []
    for row in sub.to_dict("records"):
        sid = row["subscription_id"]; st = st_by[sid]; region = cust.at[row["customer_id"], "region_sido"]
        S = _Sub(cfg, r, today, row, st, pool_by_region[region], mg["avg_rating"]); P = prof[row["product_category"]]
        start, big = row["start_date"], row["product_category"] != "정수기"

        # --- 최초설치 (가입일이 윈도우 안일 때만) ---
        if start >= today - dt.timedelta(days=v["window_days"]): S.new("최초설치", start, "완료", completed_date=start)

        # --- 정기점검: 가입일 + k × 주기 ---
        if row["care_type"] == "방문관리":
            k = 1
            while True:
                s = add_months(start, int(P["visit_interval_months"]) * k); k += 1
                if s > today + dt.timedelta(days=v["upcoming_horizon_days"]): break
                if s < today - dt.timedelta(days=v["window_days"]): continue
                S.new("정기점검", s, "예정" if s > today else None)

        # --- A/S: 월 고장 확률(과거 12개월) ---
        monthly = P["base_fault_rate_monthly"]
        for m in range(12):
            if r.random() >= monthly: continue
            day = S.rand_date(today - dt.timedelta(days=30 * (m + 1)), today - dt.timedelta(days=30 * m + 1))
            if day is None or day < S.win_lo: continue
            _add_as(S, day, 1 + int(r.poisson(A["repair_lambda"])))

        # --- 진행 중(예정) A/S ---
        p_open = A["open_prob"]["T10"] if "T10" in st else A["open_prob"]["T09"] if "T09" in st else A["open_prob"]["normal"]
        if r.random() < p_open and start < today - dt.timedelta(days=3):
            rec = today - dt.timedelta(days=int(r.integers(0, 6)))
            if "T10" in st:
                lo_e, hi_e = sg["T10"]["repair_days"]; exp = max(7, int(round(lerp(lo_e, hi_e, st["T10"]) + r.integers(-2, 3))))
            else: exp = int(r.integers(A["expected_days"][0], A["expected_days"][1] + 1))
            S.new("AS수리", today + dt.timedelta(days=int(r.integers(1, 8))), "예정", as_received_date=rec, expected_complete_date=rec + dt.timedelta(days=exp),
                  symptom_code=f"SYM{int(r.integers(1, A['symptom_codes'] + 1)):02d}")

        _apply_types(S, cfg, big, row)
        _assign_managers(S, cfg, st, region, mg, pool_by_region)
        _finish_all(S, cfg, st)
        out.extend(S.rows)
    df = pd.DataFrame(out, columns=COLS)
    return df.sort_values(["subscription_id", "scheduled_date"], kind="stable").reset_index(drop=True)


def _add_as(S, visit_day, repair_days):
    """완료된 A/S 한 건: 방문일 = 완료일, 접수일 = 완료일 - 수리 소요일."""
    r, A = S.r, S.v["as"]
    rec = visit_day - dt.timedelta(days=int(repair_days))
    if rec < S.start: rec = S.start; repair_days = (visit_day - rec).days
    exp = rec + dt.timedelta(days=int(r.integers(A["expected_days"][0], A["expected_days"][1] + 1)))
    return S.new("AS수리", visit_day, "완료", completed_date=visit_day, as_received_date=rec, expected_complete_date=exp, repair_days=int(repair_days),
                 symptom_code=f"SYM{int(r.integers(1, A['symptom_codes'] + 1)):02d}")


def _apply_types(S, cfg, big, row):
    r, st, v, sg, today = S.r, S.st, S.v, cfg["signals"], S.today

    # T05: 케어 소요시간 단축 + 케어 후 14일 내 A/S
    if "T05" in st:
        s = st["T05"]
        if not S.care_done_win(): S.extra_care(30, "완료")
        recent = lambda: [d for d in S.care_done_win() if (today - d["completed_date"]).days <= 80]
        if not recent():                                                        # 케어 직후 문의·A/S 신호가 90일 윈도우에 들어오도록 최근 케어를 보장
            day = S.rand_date(max(S.win_lo, today - dt.timedelta(days=80)), today - dt.timedelta(days=25))
            if day is not None: S.new("정기점검", day, "완료")
        for d in S.care_done_win(): d["duration_min"] = int(max(8, round(r.normal(lerp(*sg["T05"]["care_duration_min"], s), 3))))
        if r.random() < lerp(*v["t05_short_care_prob"], s) and (recent() or [d for d in S.care_done_win() if (today - d["completed_date"]).days < 90]):                       # 가장 최근 케어는 '짧은 케어'(기준 20분 미만)로 만든다 — S-T05-A 조건과 같은 의미
            pool = recent() or [d for d in S.care_done_win() if (today - d["completed_date"]).days < 90]
            last = max(pool, key=lambda d: d["completed_date"]); last["duration_min"] = int(r.integers(10, 19))
        n = int(round(lerp(*sg["T05"]["as_within_14d_cnt"], s)))
        done = S.care_done_win()
        for i in range(n):
            if not done: break
            c = done[i % len(done)]; day = c["completed_date"] + dt.timedelta(days=int(r.integers(v["t05_post_care_as_days"][0], v["t05_post_care_as_days"][1] + 1)))
            if day <= today - dt.timedelta(days=1): _add_as(S, day, 1 + int(r.poisson(v["as"]["repair_lambda"])))

    # T06: 지연·취소·미이행 합 2~5, 일정 변경, 예약 대기 14~35일
    if "T06" in st:
        s = st["T06"]; n_bad = int(round(lerp(*sg["T06"]["late_cancel_noshow_cnt_365d"], s)))
        bad = lambda: [d for d in S.rows if d["visit_type"] == "정기점검" and d["visit_status"] in ("지연완료", "취소", "미이행") and S.in_win(d)]
        k = list(v["t06"]["bad_status_weights"]); p = np.array([v["t06"]["bad_status_weights"][x] for x in k]); p = p / p.sum()
        for d in [d for d in S.rows if d["visit_type"] == "정기점검" and d["visit_status"] == "완료" and S.in_win(d)][:n_bad]:   # 기존 정상 행부터 불량으로 전환
            d["visit_status"] = str(r.choice(k, p=p)); d["completed_date"] = None; d["duration_min"] = None
            d["sales_pitch_yn"] = d["repitch_after_refusal_yn"] = d["inspection_anomaly_yn"] = "N"; S._finish(d)
        while len(bad()) < n_bad:
            if S.extra_care(25, str(r.choice(k, p=p))) is None: break
        for d in S.rows:
            if d["visit_type"] == "정기점검" and S.in_win(d):
                d["booking_lead_days"] = int(max(1, round(r.normal(lerp(*sg["T06"]["booking_lead_days"], s), 3))))
                if d["visit_status"] in ("지연완료", "취소", "미이행"): d["reschedule_cnt"] = int(r.poisson(lerp(*v["t06"]["reschedule_mean"], s)))

    # T07: 365일 내 방문 ≥ 교체 횟수+1 (담당자 배정은 _assign_managers)
    if "T07" in st:
        n_change = int(round(lerp(*sg["T07"]["manager_change_cnt_365d"], st["T07"])))
        while len([d for d in S.rows if S.in_win(d) and d["visit_status"] in ("완료", "지연완료")]) < n_change + 1:
            if S.extra_care(25, "완료") is None: break

    # T08: 180일 내 영업 권유 방문
    if "T08" in st:
        s = st["T08"]; n = int(round(lerp(*sg["T08"]["sales_pitch_cnt_180d"], s))); lo = max(S.win_lo, today - dt.timedelta(days=v["t08_window_days"]))
        cand = lambda: [d for d in S.rows if d["visit_type"] in ("정기점검", "AS수리") and d["visit_status"] in ("완료", "지연완료") and lo <= d["scheduled_date"] <= today]
        while len(cand()) < n:
            day = S.rand_date(lo, today - dt.timedelta(days=5))
            if day is None: break
            S.new("정기점검", day, "완료")
        for d in cand()[:n]:
            d["sales_pitch_yn"] = "Y"; d["repitch_after_refusal_yn"] = "Y" if r.random() < lerp(*sg["T08"]["repitch_prob"], s) else "N"

    # T09: A/S 건수 2~5(정수기 3~6), 동일 증상 반복 1~3회, 점검 이상
    if "T09" in st:
        s = st["T09"]; key = "as_cnt_365d_large" if big else "as_cnt_365d_water"; n_as = int(round(lerp(*sg["T09"][key], s))); rep = int(round(lerp(*sg["T09"]["same_symptom_cnt"], s)))
        past_as = lambda: [d for d in S.rows if d["visit_type"] == "AS수리" and d["visit_status"] == "완료"]
        while len(past_as()) < n_as:
            day = S.rand_date(S.win_lo, today - dt.timedelta(days=2))
            if day is None: break
            _add_as(S, day, 1 + int(r.poisson(v["as"]["repair_lambda"])))
        main = f"SYM{int(r.integers(1, v['as']['symptom_codes'] + 1)):02d}"; rows = sorted(past_as(), key=lambda d: d["scheduled_date"])[-n_as:]
        for d in rows[: rep + 1]: d["symptom_code"] = main                                              # 동일 증상 rep+1건 = 반복 rep회
        pa = lerp(*v["anomaly_prob_t09"], s)
        for d in S.care_done_win(): d["inspection_anomaly_yn"] = "Y" if r.random() < pa else "N"

    # T10: 긴 수리·부품 지연·분리 접수 (A/S 행 속성)
    if "T10" in st:
        s = st["T10"]
        if not [d for d in S.rows if d["visit_type"] == "AS수리"]:                                       # 신호를 담을 A/S가 하나는 있어야 함
            day = S.rand_date(S.win_lo, today - dt.timedelta(days=15))
            if day is not None: _add_as(S, day, 5)
        lo_r, hi_r = sg["T10"]["repair_days"]
        for d in [d for d in S.rows if d["visit_type"] == "AS수리"]:
            d["parts_delay_yn"] = "Y" if r.random() < v["as"]["parts_delay_t10"] else "N"
            d["split_as_receipt_yn"] = "Y" if r.random() < lerp(*sg["T10"]["split_receipt_prob"], s) else "N"
            if d["visit_status"] == "완료":
                days = int(max(4, round(r.normal(lerp(lo_r, hi_r, s), 3)))); d["repair_days"] = days
                d["as_received_date"] = max(S.start, d["scheduled_date"] - dt.timedelta(days=days)); d["repair_days"] = (d["scheduled_date"] - d["as_received_date"]).days
            else:
                d["split_as_receipt_yn"] = "Y" if r.random() < lerp(*sg["T10"]["split_receipt_prob"], s) else "N"

    # T02: 이전설치 요청·지연
    if "T02" in st:
        s = st["T02"]
        if r.random() < lerp(*v["t02_relocation"]["prob"], s):
            day = S.rand_date(max(S.win_lo, today - dt.timedelta(days=180)), today - dt.timedelta(days=25))
            if day is not None:
                delayed = r.random() < v["t02_relocation"]["delayed_prob"]
                d = S.new("이전설치", day, "지연완료" if delayed else "완료")
                if delayed and d["visit_status"] == "지연완료":
                    d["completed_date"] = day + dt.timedelta(days=int(round(lerp(*v["t02_relocation"]["delay_days"], s)))); d["completed_date"] = min(d["completed_date"], today)


def _assign_managers(S, cfg, st, region, mg, pool_by_region):
    """T07: 365일 내 담당자 1~3회 교체(과거 방문일수록 이전 담당자). 그 외는 구독의 현재 담당자."""
    if "T07" not in st or S.row["care_manager_id"] is None: return
    r = S.r; n_change = int(round(lerp(*cfg["signals"]["T07"]["manager_change_cnt_365d"], st["T07"]))); cur = S.row["care_manager_id"]
    rows = sorted([d for d in S.rows if S.in_win(d) and d["visit_status"] in ("완료", "지연완료")], key=lambda d: d["scheduled_date"])
    others = [m for m in pool_by_region[region] if m != cur]
    if not rows or not others: return
    prev = list(r.choice(others, size=min(n_change, len(others)), replace=False))                       # 이전 담당자들(오래된 순서로 쓰도록 섞음)
    groups = np.array_split(np.arange(len(rows)), min(len(prev) + 1, len(rows)))
    for gi, g in enumerate(groups):
        m = cur if gi == len(groups) - 1 else prev[gi]
        for i in g: rows[i]["manager_id"] = m


def _finish_all(S, cfg, st):
    """만족도 응답, 반복 증상 표시."""
    r, v = S.r, S.v["satisfaction"]
    for d in S.rows:
        if d["visit_status"] not in ("완료", "지연완료") or d["visit_type"] not in ("정기점검", "AS수리", "최초설치"): continue
        t07 = "T07" in st and S.in_win(d)
        if r.random() < (v["t07_response_prob"] if t07 else v["response_prob"]):
            mean = lerp(*cfg["signals"]["T07"]["satisfaction_avg"], st["T07"]) if t07 else v["mean"]
            d["satisfaction_score"] = int(min(5, max(1, round(r.normal(mean, v["t07_sd"] if t07 else v["sd"])))))
    as_rows = sorted([d for d in S.rows if d["visit_type"] == "AS수리" and d["symptom_code"]], key=lambda d: d["scheduled_date"])
    for i, d in enumerate(as_rows):
        if any(p["symptom_code"] == d["symptom_code"] and 0 < (d["scheduled_date"] - p["scheduled_date"]).days <= 90 for p in as_rows[:i]): d["repeat_symptom_yn"] = "Y"
