"""페이지 M1: 피처 생성 build_features(tables, cfg) -> DataFrame (구독 1행, 피처 열).

- 피처 정의는 data_schema_design.md §5와 churn_agent/data/feature_dictionary.json을 따른다(유형 그룹 T01~T10 + 컨텍스트).
- 모든 이벤트는 '기준일 이하'만 사용한다(누수 방지). 라벨·정답 유형 테이블은 입력으로 받지 않는다.
- 횟수 피처는 원 횟수로 둔다(월평균 보정 대신 tenure_months를 컨텍스트로 함께 입력) [제안].
- 값이 없을 때: 횟수·합계는 0, 평균·경과일·비율은 NaN(XGBoost가 결측으로 처리). 케어가 없는 구독의 방문 신호는 NaN/0.
- Y/N 컬럼은 0/1로 바꾼다. 범주(제품군·케어 유형)는 encode()에서 정수 코드로 바꾼다.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
DICT = HERE.parent / "churn_agent" / "data" / "feature_dictionary.json"
CAT_COLS = {"product_category": ["의류관리기", "건조기", "냉장고", "세탁기", "에어컨", "정수기"], "care_type": ["방문관리", "자가관리", "없음"]}


def load_cfg(path=None) -> dict:
    return yaml.safe_load(Path(path or HERE / "config.yaml").read_text(encoding="utf-8"))["features"]


def feature_groups(cfg=None) -> dict:
    """{피처: 그룹('T01'..'T10' 또는 '컨텍스트')} — 제외 피처는 뺀다."""
    ex = set((cfg or load_cfg())["excluded"])
    return {f["feature"]: f["group"] for f in json.loads(DICT.read_text(encoding="utf-8"))["features"] if f["feature"] not in ex}


def _slope(m: np.ndarray) -> np.ndarray:
    """행마다 0..k-1 위치에 대한 최소제곱 기울기(결측은 제외)."""
    x = np.arange(m.shape[1], dtype=float); ok = ~np.isnan(m)
    xm = np.where(ok, x, np.nan); mx = np.nanmean(xm, axis=1, keepdims=True); my = np.nanmean(m, axis=1, keepdims=True)
    num = np.nansum((xm - mx) * (m - my), axis=1); den = np.nansum((xm - mx) ** 2, axis=1)
    return np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)


def build_features(t: dict, cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_cfg(); today = pd.Timestamp(cfg["as_of_date"])
    sub = t["subscription"].set_index("subscription_id"); idx = sub.index; cu = t["customer"].set_index("customer_id")
    cnt = lambda d: d.groupby("subscription_id").size().reindex(idx).fillna(0).astype(int)
    agg = lambda d, col, fn: d.groupby("subscription_id")[col].agg(fn).reindex(idx)
    yn = lambda s: (s == "Y").astype(int)
    d = lambda n: today - pd.Timedelta(days=n)
    F = pd.DataFrame(index=idx)

    voc = t["voc_contact"]; voc = voc[voc.contact_date <= today]
    v = lambda days, *cats: voc[(voc.contact_date > d(days)) & voc.category.isin(cats)]
    vis = t["care_visit"]; vis = vis[vis.scheduled_date <= today]                       # 예정(미래) 방문은 제외
    done = vis[vis.visit_status.isin(["완료", "지연완료"]) & (vis.completed_date <= today)]
    care = vis[vis.visit_type == "정기점검"]; care_done = done[done.visit_type == "정기점검"]
    asv = vis[(vis.visit_type == "AS수리") & (vis.as_received_date <= today)]
    pay = t["payment"]; pay = pay[pay.billing_month <= today].sort_values("billing_month")
    nt = t["notice_log"]; nt = nt[nt.sent_at <= today]
    months = ((today - sub.start_date).dt.days / 30.4375).clip(lower=0.5)             # 관찰 개월 수

    # ---- T01 구독료 부담
    F["voc_fee_cnt_90d"] = cnt(v(90, "요금문의")); F["voc_totalcost_cnt_90d"] = cnt(v(90, "총비용문의"))
    F["promo_days_to_end"] = (sub.promo_end_date - today).dt.days
    g = pay.groupby("subscription_id").billed_amount
    last3 = pay.groupby("subscription_id").tail(3).groupby("subscription_id").billed_amount.mean()
    prev3 = pay.groupby("subscription_id").apply(lambda x: x.billed_amount.iloc[-6:-3].mean() if len(x) > 3 else np.nan, include_groups=False)
    F["billed_change_rate_3m"] = ((last3 - prev3) / prev3).reindex(idx).round(4)
    F["late_payment_cnt_6m"] = cnt(pay[(pay.billing_month > d(183)) & pay.payment_status.isin(["연체", "미납"])])
    r3 = pay.groupby("subscription_id").tail(3).assign(r=lambda x: x.discount_amount / x.billed_amount.where(x.billed_amount > 0))
    F["discount_ratio"] = r3.groupby("subscription_id").r.mean().reindex(idx).round(4)
    F["unapplied_discount_yn"] = ((sub.discount_eligible_yn == "Y") & (sub.discount_applied_yn == "N")).astype(int)

    # ---- T02 해지·이사 장벽
    F["voc_penalty_cnt_90d"] = cnt(v(90, "위약금문의")); F["voc_cancel_cnt_90d"] = cnt(v(90, "해지문의"))
    ms = ((today - sub.start_date).dt.days / 30.4375); F["months_since_start"] = ms.round(1)
    F["penalty_zone"] = np.minimum(3, (ms // 12).astype(int) + 1)
    F["early_stage_penalty_voc_flag"] = ((F.voc_penalty_cnt_90d > 0) & (ms <= 12)).astype(int)
    F["voc_move_cnt_90d"] = cnt(v(90, "이사문의"))
    mv = vis[(vis.visit_type == "이전설치") & (vis.scheduled_date > d(365))].copy()
    mv["delay"] = ((mv.completed_date.fillna(today).where(mv.completed_date.notna() | (mv.visit_status != "예정"), today) - mv.scheduled_date).dt.days).clip(lower=0)
    F["relocation_install_requested_yn"] = F.index.isin(mv.subscription_id).astype(int)
    F["relocation_install_delay_days"] = agg(mv, "delay", "max").fillna(0)
    F["cancel_processing_delay_days"] = agg(v(90, "해지문의"), "cancel_delay_days", "max").fillna(0)

    # ---- T03 재계약·갈아타기 갈등
    F["days_to_end"] = (sub.end_date - today).dt.days
    rn = nt[nt.notice_type == "갱신안내"]
    F["renewal_notice_sent_yn"] = F.index.isin(rn.subscription_id).astype(int)
    F["renewal_notice_opened_yn"] = F.index.isin(rn[rn.opened_at.notna() & (rn.opened_at <= today)].subscription_id).astype(int)
    F["voc_renewal_cnt_90d"] = cnt(v(90, "갱신문의")); F["renewal_notice_contact_cnt"] = cnt(rn)
    F["model_discontinued_yn"] = yn(sub.model_discontinued_yn); F["new_model_released_yn"] = yn(sub.new_model_released_yn)

    # ---- T04 사용 빈도 저하·방치 (최근 12주)
    W = cfg["usage_weeks"]; u = t["usage_thinq"]; u = u[u.week_start_date <= today].sort_values(["subscription_id", "week_start_date"])
    u = u.groupby("subscription_id").tail(W).copy(); u["pos"] = u.groupby("subscription_id").cumcount()
    mat = lambda col: u.pivot(index="subscription_id", columns="pos", values=col).reindex(idx).reindex(columns=range(W)).to_numpy(float)
    rc, rm, ap = mat("run_count"), mat("run_minutes"), mat("app_login_days")
    F["run_count_trend_12w"] = _slope(rc).round(4); F["run_minutes_trend"] = _slope(rm).round(3); F["app_login_trend"] = _slope(ap).round(4)
    F["run_count_recent4w_vs_prev4w"] = np.round(np.nansum(rc[:, -4:], 1) / np.maximum(np.nansum(rc[:, -8:-4], 1), 1.0), 3)
    F["zero_use_weeks_12w"] = np.nansum(rc == 0, axis=1)

    # ---- T05 케어 부실·피해
    c365 = care_done[care_done.completed_date > d(365)]
    F["care_duration_avg_min"] = agg(c365, "duration_min", "mean").round(1)
    F["care_short_visit_cnt"] = cnt(care_done[(care_done.completed_date > d(90)) & (care_done.duration_min < cfg["short_care_minutes"])])
    a = asv[asv.as_received_date > d(365)][["subscription_id", "as_received_date"]].reset_index().rename(columns={"index": "aid"})
    m = a.merge(c365[["subscription_id", "completed_date"]], on="subscription_id")
    m = m[(m.as_received_date > m.completed_date) & ((m.as_received_date - m.completed_date).dt.days <= cfg["post_care_as_days"])]
    F["post_care_as_cnt_14d"] = m.drop_duplicates("aid").groupby("subscription_id").size().reindex(idx).fillna(0).astype(int)
    F["post_care_revisit_req_cnt"] = cnt(v(90, "재방문요청")); F["care_result_complaint_cnt_90d"] = cnt(v(90, "케어결과불만"))

    # ---- T06 방문 일정 불편
    c365a = care[care.scheduled_date > d(365)]
    F["visit_delay_cnt_365d"] = cnt(c365a[c365a.visit_status == "지연완료"]); F["visit_cancel_cnt_365d"] = cnt(c365a[c365a.visit_status == "취소"])
    F["visit_noshow_cnt_365d"] = cnt(c365a[c365a.visit_status == "미이행"])
    F["reschedule_sum_365d"] = vis[vis.scheduled_date > d(365)].groupby("subscription_id").reschedule_cnt.sum().reindex(idx).fillna(0).astype(int)
    last_any = agg(done, "completed_date", "max"); F["days_since_last_visit"] = (today - last_any).dt.days
    last_care = agg(care_done, "completed_date", "max"); itv = sub.product_category.map(cfg["visit_interval_months"]) * 30.4375
    gap = (today - last_care).dt.days; F["visit_gap_ratio"] = (gap / itv).round(3)
    F["days_overdue_visit"] = (gap - itv).clip(lower=0).round(0)
    F.loc[sub.care_type != "방문관리", ["days_overdue_visit", "visit_gap_ratio"]] = np.nan     # 방문관리가 아니면 점검 주기 개념 없음
    F["visit_delay_rate_cum"] = (cnt(care[care.visit_status == "지연완료"]) / months).round(4)
    F["schedule_change_req_cnt_365d"] = cnt(v(365, "일정변경요청")); F["booking_lead_days_avg"] = agg(c365a, "booking_lead_days", "mean").round(1)
    F["weekday_daytime_available_yn"] = yn(cu.loc[sub.customer_id, "weekday_daytime_available_yn"]).to_numpy()

    # ---- T07 매니저 품질 불만
    sat = done[done.satisfaction_score.notna()]
    r90 = agg(sat[sat.completed_date > d(90)], "satisfaction_score", "mean"); p90 = agg(sat[(sat.completed_date <= d(90)) & (sat.completed_date > d(180))], "satisfaction_score", "mean")
    F["satisfaction_avg_180d"] = agg(sat[sat.completed_date > d(180)], "satisfaction_score", "mean").round(2); F["satisfaction_drop"] = (p90 - r90).round(2)
    F["voc_manager_change_cnt_180d"] = cnt(v(180, "담당자교체요청"))
    mc = vis[(vis.scheduled_date > d(365)) & vis.manager_id.notna() & (vis.visit_type != "최초설치")].sort_values("scheduled_date")
    F["manager_change_actual_cnt_365d"] = mc.groupby("subscription_id").manager_id.apply(lambda s: int((s != s.shift()).sum() - 1), include_groups=False).reindex(idx).clip(lower=0).fillna(0).astype(int)
    F["manager_avg_rating"] = sub.care_manager_id.map(t["care_manager"].set_index("manager_id").avg_rating).astype(float)
    F["attitude_complaint_cnt_90d"] = cnt(v(90, "태도불만"))

    # ---- T08 영업 압박
    v180 = vis[vis.scheduled_date > d(180)]
    F["sales_pitch_cnt_180d"] = cnt(v180[v180.sales_pitch_yn == "Y"]); F["sales_voc_cnt_180d"] = cnt(v(180, "영업불만"))
    F["repitch_after_refusal_cnt"] = cnt(v180[v180.repitch_after_refusal_yn == "Y"])

    # ---- T09 제품 고장·성능 불만 — 케어 직후(14일 내) A/S는 T05 신호이므로 T09 집계에서 뺀다(설계서 '헷갈리는 쌍' T05 vs T09: 케어와 무관한 반복 고장만 T09) [제안]
    allm = asv[["subscription_id", "as_received_date"]].reset_index().rename(columns={asv.index.name or "index": "aid"}).merge(care_done[["subscription_id", "completed_date"]], on="subscription_id")
    allm = allm[(allm.as_received_date > allm.completed_date) & ((allm.as_received_date - allm.completed_date).dt.days <= cfg["post_care_as_days"])]
    ind = asv[~asv.index.isin(set(allm.aid))]; i365 = ind[ind.as_received_date > d(365)]; i180 = i365[i365.as_received_date > d(180)]
    F["as_cnt_365d"] = cnt(i365); F["repeat_symptom_cnt_90d"] = cnt(ind[(ind.as_received_date > d(90)) & (ind.repeat_symptom_yn == "Y")])
    F["as_cnt_trend"] = cnt(i180) - (cnt(i365) - cnt(i180)); F["as_cnt_cum"] = cnt(ind); F["as_rate_cum"] = (F.as_cnt_cum / months).round(4)
    F["days_since_last_as"] = (today - agg(ind, "as_received_date", "max")).dt.days

    # ---- T10 A/S·고객센터 불만
    a365 = i365                                                                          # 케어 직후 A/S는 T05 신호라 T10 처리지연 집계에서도 뺀다(같은 기준) [제안]
    F["repair_days_avg_365d"] = agg(a365, "repair_days", "mean").round(1); F["parts_delay_cnt_365d"] = cnt(a365[a365.parts_delay_yn == "Y"])
    cc = voc[voc.category == "응대불만"]                    # T10 응대 신호는 '응대 불만' 문의에서만 센다 — 다른 유형의 문의 폭증(상담 건수)이 T10으로 새지 않게 함 [제안]
    F["unresolved_voc_cnt"] = cnt(cc[(cc.contact_date > d(365)) & (cc.resolved_yn == "N")])
    F["repeat_voc_cnt"] = cnt(cc[(cc.contact_date > d(90)) & (cc.repeat_contact_yn == "Y")])
    F["response_hours_avg"] = agg(cc[cc.contact_date > d(90)], "response_hours", "mean").astype(float).round(1)
    F["voc_complaint_cnt_90d"] = cnt(v(90, "응대불만")); F["split_as_receipt_yn"] = F.index.isin(a365[a365.split_as_receipt_yn == "Y"].subscription_id).astype(int)
    F["first_response_unresolved_yn"] = F.index.isin(cc[(cc.contact_date > d(90)) & (cc.first_response_unresolved_yn == "Y")].subscription_id).astype(int)

    # ---- 컨텍스트
    F["product_category"] = sub.product_category; F["tenure_months"] = ms.round(1); F["monthly_fee"] = sub.monthly_fee.astype(float)
    F["contract_months"] = sub.contract_months.astype(int); F["care_type"] = sub.care_type

    cols = list(feature_groups(cfg)); miss = [c for c in cols if c not in F]; extra = [c for c in F if c not in cols]
    if miss or extra: raise ValueError(f"피처 사전과 불일치: 없음 {miss}, 사전에 없음 {extra}")
    return F[cols]


def encode(F: pd.DataFrame) -> pd.DataFrame:
    """범주 열을 정수 코드로(고정 순서). 나머지는 그대로 — 모델 입력용."""
    X = F.copy()
    for c, cats in CAT_COLS.items(): X[c] = pd.Categorical(X[c], categories=cats).codes.astype(int)
    return X.astype(float)
