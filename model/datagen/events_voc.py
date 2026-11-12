"""페이지 4-2b: 상담·문의(voc_contact) 생성.

build_voc(cfg, pop, visits) -> DataFrame(voc_contact)   (voc_id는 DB IDENTITY)
- 정상 구독: 180일 내 0~1건(기타·사용법·요금 위주).
- 유형 신호(최근 90일): T01 요금·총비용  T02 위약금·해지·이사 + 해지 처리 지연  T03 갱신문의
  T05 재방문요청·케어결과불만(케어 직후)  T06 일정변경요청  T07 담당자교체요청·태도불만  T08 영업불만
  T10 응대불만·미해결·1차 미해결·응답 지연
- 요약 문장(summary_text)은 가상의 템플릿 문장이다(실제 상담 내용이 아님).
"""
from __future__ import annotations
import datetime as dt
import numpy as np, pandas as pd
from .config import as_of
from .modulation import lerp

COLS = ["subscription_id", "contact_date", "channel", "category", "repeat_contact_yn", "resolved_yn", "first_response_unresolved_yn",
        "cancel_delay_days", "response_hours", "sentiment_score", "summary_text"]
CATEGORIES = ["요금문의", "총비용문의", "위약금문의", "해지문의", "이사문의", "갱신문의", "일정변경요청", "재방문요청", "케어결과불만", "담당자교체요청",
              "태도불만", "영업불만", "응대불만", "사용법문의", "기타"]
SENT = {"해지문의": -0.40, "위약금문의": -0.40, "응대불만": -0.65, "태도불만": -0.70, "영업불만": -0.65, "케어결과불만": -0.60, "담당자교체요청": -0.50,
        "재방문요청": -0.40, "일정변경요청": -0.20, "요금문의": -0.15, "총비용문의": -0.20, "이사문의": -0.10, "갱신문의": -0.05, "사용법문의": 0.05, "기타": 0.0}
SUMMARY = {
    "요금문의": ["이번 달 청구 요금이 오른 이유를 문의", "프로모션 종료 후 요금이 어떻게 바뀌는지 문의"],
    "총비용문의": ["남은 기간 동안 내야 할 총비용이 얼마인지 문의", "구매로 바꿀 때와 비교한 총비용 문의"],
    "위약금문의": ["지금 해지하면 위약금이 얼마인지 문의", "의무사용기간이 남아 있을 때의 위약금 계산 문의"],
    "해지문의": ["해지 절차와 접수 방법 문의", "해지 접수 후 처리 일정 문의"],
    "이사문의": ["이사 가는 곳으로 제품을 옮겨 설치할 수 있는지 문의", "이전 설치 비용과 일정 문의"],
    "갱신문의": ["계약 만료 후 재계약 조건 문의", "새 모델로 바꿀 수 있는지 문의"],
    "일정변경요청": ["예정된 방문 일정을 다른 날로 바꿔 달라고 요청", "평일 낮에는 어려워 방문 시간대 변경 요청"],
    "재방문요청": ["케어 방문 후 다시 방문해 달라고 요청", "점검이 충분하지 않아 재방문 요청"],
    "케어결과불만": ["케어 후에도 문제가 그대로라는 불만", "점검 시간이 너무 짧았다는 불만"],
    "담당자교체요청": ["담당 매니저를 바꿔 달라고 요청", "담당 매니저가 자주 바뀌어 불편하다는 요청"],
    "태도불만": ["방문 매니저의 응대 태도에 대한 불만", "방문 중 불친절했다는 불만"],
    "영업불만": ["방문 때마다 다른 상품 가입을 권유받아 불편하다는 불만", "거절했는데도 계속 권유한다는 불만"],
    "응대불만": ["문의했는데 한 번에 해결되지 않았다는 불만", "상담 연결과 답변이 늦다는 불만"],
    "사용법문의": ["제품 설정과 사용 방법 문의", "앱 연동 방법 문의"],
    "기타": ["기타 일반 문의", "서비스 관련 일반 문의"],
}


def _rng(seed: int):
    return np.random.default_rng(np.random.SeedSequence(seed).spawn(10)[9])


def build_voc(cfg: dict, pop: dict, visits: pd.DataFrame) -> pd.DataFrame:
    r = _rng(int(cfg["run"]["seed"])); today = as_of(cfg); V = cfg["voc"]; sg = cfg["signals"]
    sub = pop["subscription"]; st_by = {sid: {} for sid in sub["subscription_id"]}
    for sid, t, s in pop["truth_type"][["subscription_id", "type_id", "activation_strength"]].itertuples(index=False): st_by[sid][t] = float(s)
    care = visits[(visits.visit_type == "정기점검") & visits.visit_status.isin(["완료", "지연완료"])]
    care_dates = care.groupby("subscription_id").completed_date.apply(list).to_dict()
    ch_k = list(V["channel_weights"]); ch_p = np.array([V["channel_weights"][k] for k in ch_k]); ch_p = ch_p / ch_p.sum()
    nc_k = list(V["normal_category_weights"]); nc_p = np.array([V["normal_category_weights"][k] for k in nc_k]); nc_p = nc_p / nc_p.sum()
    fu0 = V["first_unresolved_normal"]; p_resolved_nofu = (V["resolved_prob_normal"] - fu0 * V["first_unresolved_later_resolved_prob"]) / (1 - fu0)   # 전체 해결률이 설정값이 되도록
    out = []

    for sid, start in zip(sub["subscription_id"], sub["start_date"]):
        st = st_by[sid]; rows = []                                       # (category, date, forced: dict)
        lo180 = max(start + dt.timedelta(days=1), today - dt.timedelta(days=V["window_days"])); lo90 = max(start + dt.timedelta(days=1), today - dt.timedelta(days=V["signal_window_days"]))
        day = lambda lo: None if lo > today else lo + dt.timedelta(days=int(r.integers(0, (today - lo).days + 1)))

        def add(cat, lo, forced=None, when=None):
            d = when if when is not None else day(lo)
            if d is not None: rows.append((cat, d, forced or {}))

        for _ in range(int(r.poisson(V["normal_rate"]))): add(str(r.choice(nc_k, p=nc_p)), lo180)           # 정상 노이즈
        cnt = lambda key, s, a=None: int(round(lerp(*(a or sg[key[0]][key[1]]), s)))
        if "T01" in st:
            for _ in range(cnt(("T01", "voc_fee_cnt_90d"), st["T01"])): add("총비용문의" if r.random() < V["t01_total_cost_share"] else "요금문의", lo90)
        if "T02" in st:
            s = st["T02"]; delay = lambda: {"cancel_delay_days": int(max(2, round(lerp(*sg["T02"]["cancel_delay_days"], s) + r.integers(-1, 2))))}
            for _ in range(cnt(("T02", "voc_cancel_cnt_90d"), s)):
                if r.random() < V["t02"]["penalty_share"]: add("위약금문의", lo90)
                else: add("해지문의", lo90, delay())
            if not any(c == "해지문의" for c, _, _ in rows): add("해지문의", lo90, delay())                 # 해지 처리 지연 신호가 비지 않게
            if r.random() < V["t02"]["move_prob"]: add("이사문의", lo90)
        if "T03" in st:
            for _ in range(int(round(lerp(V["t03_renewal_voc"][0], V["t03_renewal_voc"][1], st["T03"], 0.3, 1.0)))): add("갱신문의", lo90)
        if "T05" in st:
            s = st["T05"]; cds = [c for c in care_dates.get(sid, []) if (today - c).days <= 90]
            for cat, rng_ in (("재방문요청", V["t05_voc"]["revisit"]), ("케어결과불만", V["t05_voc"]["result_complaint"])):
                for _ in range(int(round(lerp(*rng_, s)))):
                    when = None
                    if cds:
                        w = cds[int(r.integers(0, len(cds)))] + dt.timedelta(days=int(r.integers(1, 11))); when = w if w <= today else None
                    add(cat, lo90, when=when)
        if "T06" in st:
            for _ in range(cnt(("T06", "reschedule_voc_cnt"), st["T06"])): add("일정변경요청", lo90)
        if "T07" in st:
            s = st["T07"]
            for _ in range(cnt(("T07", "attitude_voc_cnt"), s)): add("태도불만", lo90)
            for _ in range(int(round(lerp(*V["t07_manager_change_request"], s)))): add("담당자교체요청", lo90)
        if "T08" in st:
            for _ in range(int(round(lerp(*V["t08_sales_voc"], st["T08"])))): add("영업불만", lo90)
        if "T10" in st:
            s = st["T10"]; n = int(round(lerp(*V["t10"]["rows"], s))); n_un = int(round(lerp(*sg["T10"]["unresolved_voc_cnt"], s))); fu = lerp(*V["t10"]["first_unresolved"], s)
            k = list(V["t10"]["category_weights"]); p = np.array([V["t10"]["category_weights"][x] for x in k]); p = p / p.sum()
            for i in range(n): add(str(r.choice(k, p=p)), lo90, {"t10": True, "force_unresolved": i < n_un, "fu": fu})

        rows.sort(key=lambda x: x[1])
        for cat, d, f in rows:
            t10 = f.get("t10", False)
            fu = r.random() < (f["fu"] if t10 else V["first_unresolved_normal"])                    # 1차 응대 미해결
            later = V["first_unresolved_later_resolved_prob"]
            if t10: resolved = (not f["force_unresolved"]) and r.random() < V["t10"]["resolved_prob"]      # 미해결 건수는 신호(1~3건)로 고정, 나머지는 대부분 해결
            else: resolved = r.random() < (later if fu else p_resolved_nofu)
            if t10 and f["force_unresolved"] and not fu: fu = r.random() < 0.5
            med = V["response_hours"]["t10_median"] if t10 else V["response_hours"]["median"]
            hours = None if r.random() < V["response_hours"]["none_prob"] else round(float(r.lognormal(np.log(med), V["response_hours"]["sigma"])), 1)
            delay = f.get("cancel_delay_days") if cat == "해지문의" else None
            if cat == "해지문의" and delay is None: delay = int(r.integers(V["cancel_delay_normal"][0], V["cancel_delay_normal"][1] + 1))
            out.append((sid, d, str(r.choice(ch_k, p=ch_p)), cat, "N", "Y" if resolved else "N", "Y" if fu else "N", delay, hours,
                        round(float(np.clip(r.normal(SENT[cat], 0.2), -1, 1)), 2), SUMMARY[cat][int(r.integers(0, 2))]))
    df = pd.DataFrame(out, columns=COLS).sort_values(["subscription_id", "contact_date"], kind="stable").reset_index(drop=True)
    # 동일 카테고리 14일 내 재문의
    prev = df.groupby(["subscription_id", "category"]).contact_date.shift(1)
    gap = pd.Series([(a - b).days if pd.notna(b) else None for a, b in zip(df.contact_date, prev)])
    df["repeat_contact_yn"] = np.where(gap.notna() & (gap <= 14), "Y", "N")
    return df
