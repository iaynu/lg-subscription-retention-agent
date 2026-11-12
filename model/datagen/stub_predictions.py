"""페이지 5-2(b): 임시 예측(stub) — 학습된 모델이 나오기 전에 Agent를 돌려 보기 위한 대체값.

build_stub_predictions(cfg, t) -> (churn_prediction DataFrame, churn_prediction_type DataFrame; prediction_id 대신 subscription_id·type_rank 키)
- churn_probability = 생성 확률(label_detail.p_churn), 순위 백분위·위험 등급은 그 순위로 계산 (HIGH 상위 10%, MID 다음 10%).
- 유형 = 생성 시 활성화한 정답 유형(상위 3개), 기여도 = 유형 가중치×활성 강도를 합 1로 정규화.
- top_features = 그 유형의 대표 피처 2개를 데이터에서 직접 계산(값은 실제 데이터 기준, shap은 기여도 기반 임시값).
- model_version = 'stub-gen' → 실제 XGBoost/SHAP 결과가 아님. 모델 단계에서 같은 테이블을 같은 형식으로 교체한다.
"""
from __future__ import annotations
import json, datetime as dt
import numpy as np, pandas as pd
from .config import as_of

MODEL_VERSION = "stub-gen"


def _feature_tables(cfg, t) -> dict:
    today = as_of(cfg); sub = t["subscription"].set_index("subscription_id"); idx = sub.index
    v = t["care_visit"]; voc = t["voc_contact"]; us = t["usage_thinq"]; nt = t["notice_log"]; F = {}
    cnt = lambda df: df.groupby("subscription_id").size().reindex(idx).fillna(0)
    d90 = today - dt.timedelta(days=90); d180 = today - dt.timedelta(days=180); d365 = today - dt.timedelta(days=365)
    done = v[v.visit_status.isin(["완료", "지연완료"])]
    vc = lambda c, since: cnt(voc[(voc.category == c) & (voc.contact_date >= since) & (voc.contact_date <= today)])
    F["voc_fee_cnt_90d"] = vc("요금문의", d90); F["voc_move_cnt_90d"] = vc("이사문의", d90); F["voc_cancel_cnt_90d"] = vc("해지문의", d90)
    F["promo_days_to_end"] = pd.Series([(e - today).days if e is not None and e == e else np.nan for e in sub.promo_end_date], index=idx)
    F["days_to_end"] = pd.Series([(e - today).days for e in sub.end_date], index=idx)
    op = nt[(nt.notice_type == "갱신안내") & nt.opened_at.notna()]; F["renewal_notice_opened_yn"] = cnt(op).clip(upper=1)
    u = us.sort_values("week_start_date").groupby("subscription_id").run_count
    F["zero_use_weeks_12w"] = u.apply(lambda s: int((s.iloc[-12:] == 0).sum())).reindex(idx)
    F["run_count_recent4w_vs_prev4w"] = u.apply(lambda s: round(float(s.iloc[-4:].sum()) / max(float(s.iloc[-8:-4].sum()), 1.0), 2)).reindex(idx)
    care = done[(done.visit_type == "정기점검") & (done.completed_date >= d365)]
    F["care_duration_avg_min"] = care.groupby("subscription_id").duration_min.mean().reindex(idx).round(0)
    F["post_care_as_cnt_14d"] = cnt(v[(v.visit_type == "AS수리") & (v.visit_status != "예정") & (v.as_received_date >= d90)])
    F["visit_delay_cnt_365d"] = cnt(v[(v.visit_status == "지연완료") & (v.scheduled_date >= d365)])
    F["reschedule_sum_365d"] = v[v.scheduled_date >= d365].groupby("subscription_id").reschedule_cnt.sum().reindex(idx).fillna(0)
    sat = done[(done.completed_date >= d180) & done.satisfaction_score.notna()]
    F["satisfaction_avg_180d"] = sat.groupby("subscription_id").satisfaction_score.mean().reindex(idx).round(1)
    F["voc_manager_change_cnt_180d"] = vc("담당자교체요청", d180)
    F["sales_pitch_cnt_180d"] = cnt(v[(v.sales_pitch_yn == "Y") & (v.scheduled_date >= d180)])
    F["repitch_after_refusal_cnt"] = cnt(v[(v.repitch_after_refusal_yn == "Y") & (v.scheduled_date >= d180)])
    asv = v[(v.visit_type == "AS수리") & (v.as_received_date >= d365)]
    F["as_cnt_365d"] = cnt(asv); F["repeat_symptom_cnt_90d"] = cnt(v[(v.repeat_symptom_yn == "Y") & (v.visit_type == "AS수리") & (v.as_received_date >= d90)])
    F["unresolved_voc_cnt"] = cnt(voc[(voc.resolved_yn == "N") & (voc.contact_date <= today)])
    F["repair_days_avg_365d"] = asv.groupby("subscription_id").repair_days.mean().reindex(idx).round(1)
    F["unapplied_discount_yn"] = ((sub.discount_eligible_yn == "Y") & (sub.discount_applied_yn == "N")).astype(int)
    return F


FEATURES = {"T01": ["voc_fee_cnt_90d", "promo_days_to_end"], "T02": ["voc_move_cnt_90d", "voc_cancel_cnt_90d"], "T03": ["days_to_end", "renewal_notice_opened_yn"],
            "T04": ["zero_use_weeks_12w", "run_count_recent4w_vs_prev4w"], "T05": ["care_duration_avg_min", "post_care_as_cnt_14d"],
            "T06": ["visit_delay_cnt_365d", "reschedule_sum_365d"], "T07": ["satisfaction_avg_180d", "voc_manager_change_cnt_180d"],
            "T08": ["sales_pitch_cnt_180d", "repitch_after_refusal_cnt"], "T09": ["as_cnt_365d", "repeat_symptom_cnt_90d"],
            "T10": ["unresolved_voc_cnt", "repair_days_avg_365d"]}


def build_stub_predictions(cfg: dict, t: dict):
    today = as_of(cfg); L = cfg["label"]; det = t["label_detail"].set_index("subscription_id"); n = len(det)
    rank = det.p_churn.rank(ascending=False, method="first") - 1
    pct = (rank / n).clip(upper=0.99999)
    level = np.where(pct < 0.10, "HIGH", np.where(pct < 0.20, "MID", "LOW"))
    pred = pd.DataFrame({"subscription_id": det.index, "as_of_date": today, "churn_probability": det.p_churn.clip(0, 1).round(4).to_numpy(),
                         "risk_level": level, "risk_rank_pct": pct.round(5).to_numpy(), "model_version": MODEL_VERSION})
    F = _feature_tables(cfg, t); truth = t["synth_truth_type"].copy()
    truth["w"] = [L["type_w"][x] * float(s) for x, s in zip(truth.type_id, truth.activation_strength)]
    rows = []
    for sid, g in truth.groupby("subscription_id"):
        g = g.sort_values("w", ascending=False).head(3); tot = g.w.sum()
        shares = np.array((g.w / tot).round(4)); shares[0] = round(1 - shares[1:].sum(), 4) if len(shares) > 1 else 1.0
        for rk, ((_, r), sh) in enumerate(zip(g.iterrows(), shares), start=1):
            feats = []
            for f in FEATURES[r.type_id]:
                val = F[f].get(sid)
                if val is None or val != val: continue
                feats.append({"feature": f, "value": float(val) if float(val) % 1 else int(val), "shap": round(float(sh) * 0.5, 3)})
            rows.append(dict(subscription_id=sid, type_rank=rk, type_id=r.type_id, contribution_share=float(sh), top_features_json=json.dumps(feats, ensure_ascii=False)))
    return pred, pd.DataFrame(rows)
