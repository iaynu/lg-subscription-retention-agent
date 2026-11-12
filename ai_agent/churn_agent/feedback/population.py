"""더미 데이터 폴더(Parquet) -> 설문 합성기 입력(Subject). 관측 값(facts)은 이탈 예측 모델과 같은 피처(churn_model.features.build_features)에서 가져와
설문 응답이 이벤트 데이터와 일치하게 한다. 숨은 정답(유형 강도·숨은 주제)은 응답 합성에만 쓴다."""
from __future__ import annotations
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from . import questionnaire as Q
from .mapping import load_config
from .simulator import Subject
from .topics import assign_topics

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = {"전화": "phone", "앱": "app", "챗봇": "chatbot"}
VOC_NEEDED = ("contact_date", "channel")


def load_tables(folder) -> dict:
    """합성에 필요한 테이블을 Parquet에서 읽는다(날짜는 datetime64)."""
    import sys
    sys.path.insert(0, str(ROOT.parent))
    from churn_model.io import load_parquet
    t = load_parquet(folder)
    for n in ("churn_prediction", "churn_prediction_type", "synth_churn_label", "synth_truth_type"):
        p = Path(folder) / f"{n}.parquet"
        if p.exists(): t[n] = pd.read_parquet(p)
    return t


def _clean(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if v is None or v is pd.NA or (isinstance(v, (float, np.floating)) and math.isnan(v)): continue
        out[k] = v.item() if isinstance(v, np.generic) else v
    return out


def build_facts(t: dict, as_of: str | None = None) -> pd.DataFrame:
    """구독별 관측 값 표(설문 합성에 쓰는 이름). 모델 피처 + 몇 가지 추가 집계."""
    from churn_model.features import build_features, load_cfg
    cfg = load_cfg()
    if as_of: cfg = dict(cfg, as_of_date=as_of)
    today = pd.Timestamp(cfg["as_of_date"]); F = build_features(t, cfg)
    idx = F.index; f = pd.DataFrame(index=idx)
    g = lambda c, default=None: F[c] if c in F else pd.Series(default, index=idx)
    # 직접 대응
    for k, c in {"voc_penalty_cnt_90d": "voc_penalty_cnt_90d", "voc_move_cnt_90d": "voc_move_cnt_90d", "penalty_zone": "penalty_zone", "monthly_fee": "monthly_fee",
                 "days_to_end": "days_to_end", "visit_delay_cnt_365d": "visit_delay_cnt_365d", "as_cnt_365d": "as_cnt_365d", "repeat_symptom_cnt_90d": "repeat_symptom_cnt_90d",
                 "care_as_14d_cnt": "post_care_as_cnt_14d", "sales_pitch_cnt": "sales_pitch_cnt_180d", "tone_complaint_cnt": "attitude_complaint_cnt_90d",
                 "manager_change_cnt": "manager_change_actual_cnt_365d", "response_hours_avg": "response_hours_avg", "unresolved_voc_cnt": "unresolved_voc_cnt",
                 "billed_change_rate_3m": "billed_change_rate_3m", "promo_days_to_end": "promo_days_to_end", "renewal_notice_contact_cnt": "renewal_notice_contact_cnt",
                 "tenure_months": "tenure_months"}.items():
        f[k] = g(c)
    f["visit_cancel_cnt_365d"] = g("visit_cancel_cnt_365d", 0).fillna(0) + g("visit_noshow_cnt_365d", 0).fillna(0)
    f["repair_days"] = g("repair_days_avg_365d").round()
    f["remaining_months"] = (g("days_to_end") / 30.4375).clip(lower=0).round(1)
    f["new_model_released_yn"] = g("new_model_released_yn").map({1: "Y", 0: "N"}); f["model_discontinued_yn"] = g("model_discontinued_yn").map({1: "Y", 0: "N"})
    f["missed_discount_yn"] = g("unapplied_discount_yn").map({1: "Y", 0: "N"}); f["split_as_receipt_yn"] = g("split_as_receipt_yn").map({1: "Y", 0: "N"})
    f["repitch_after_refusal_yn"] = np.where(g("repitch_after_refusal_cnt", 0).fillna(0) > 0, "Y", "N")
    sub = t["subscription"].set_index("subscription_id"); cu = t["customer"].set_index("customer_id")
    f["visit_care_yn"] = np.where(sub.care_type.reindex(idx) == "방문관리", "Y", "N")
    f["large_appliance_yn"] = np.where(sub.product_category.reindex(idx) == "정수기", "N", "Y")
    cust = sub.customer_id.reindex(idx)
    f["senior_yn"] = np.where(cu.age_group.reindex(cust).values == "60대이상", "Y", "N")
    f["sched_difficulty"] = (g("weekday_daytime_available_yn", 1).fillna(1) == 0) | (g("booking_lead_days_avg", 0).fillna(0) > 14)
    # 추가 집계: 최근 12주 주당 평균 가동, 방문 지연 최대일, 증상 코드(최빈), 최근 90일 문의 건수·채널
    u = t["usage_thinq"]; u = u[(u.week_start_date <= today) & (u.week_start_date > today - pd.Timedelta(days=84))]
    f["weekly_use_avg"] = (u.groupby("subscription_id").run_count.sum() / 12).reindex(idx).fillna(0)
    v = t["care_visit"]; v = v[(v.scheduled_date <= today) & (v.scheduled_date > today - pd.Timedelta(days=365))]
    late = v[v.visit_status == "지연완료"].assign(d=lambda x: (x.completed_date - x.scheduled_date).dt.days)
    f["visit_max_delay_days"] = late.groupby("subscription_id").d.max().reindex(idx)
    a = v[(v.visit_type == "AS수리") & v.symptom_code.notna() & v.visit_status.isin(["완료", "지연완료"])]
    f["symptom_code"] = a.groupby("subscription_id").symptom_code.agg(lambda s: s.value_counts().sort_index().idxmax()).reindex(idx)
    voc = t["voc_contact"]; voc = voc[(voc.contact_date <= today) & (voc.contact_date > today - pd.Timedelta(days=90))]
    f["voc_cnt"] = voc.groupby("subscription_id").size().reindex(idx).fillna(0).astype(int)
    f["voc_channel"] = voc.groupby("subscription_id").channel.agg(lambda s: CHANNEL.get(s.value_counts().sort_index().idxmax())).reindex(idx)
    f["other_subs_cnt"] = (cust.map(sub.groupby("customer_id").size()).values - 1)
    # 불편 코드별 이벤트 건수(설계서 §3.4)와 반복 플래그
    z = lambda c: g(c, 0).fillna(0)
    ev = pd.DataFrame({"visit": z("visit_delay_cnt_365d") + f.visit_cancel_cnt_365d, "fail": z("as_cnt_365d"), "care": z("post_care_as_cnt_14d") + z("care_result_complaint_cnt_90d"),
                       "manager": z("manager_change_actual_cnt_365d") + z("attitude_complaint_cnt_90d"), "cs": z("unresolved_voc_cnt") + z("repeat_voc_cnt"),
                       "sales": z("sales_pitch_cnt_180d"), "cost": z("voc_fee_cnt_90d"), "exit": z("voc_penalty_cnt_90d") + z("voc_cancel_cnt_90d"), "renew": z("voc_renewal_cnt_90d")}).astype(int)
    f["_event_counts"] = ev.to_dict("records") if len(ev) else []
    f["_repeat"] = [frozenset(k for k, c in (("fail", rs), ("cs", rc)) if c > 0) for rs, rc in zip(z("repeat_symptom_cnt_90d"), z("repeat_voc_cnt"))]
    return f


def load_catalog_solutions(path=None) -> list[dict]:
    d = json.loads(Path(path or ROOT / "data" / "solution_catalog.json").read_text(encoding="utf-8"))
    return [s for s in d["solutions"] if s.get("active_yn") == "Y" and s.get("agent_selectable_yn") == "Y"]


def stand_in_solution(type_id: str | None, solutions: list[dict]) -> str | None:
    """Agent를 돌리지 않는 일괄 합성에서 '조치완료'로 보는 대표 솔루션: 해당 유형의 활성 솔루션 중 우선순위 값이 가장 작은 것 [가정]."""
    c = [s for s in solutions if s["type_id"] == type_id]
    return min(c, key=lambda s: (s["priority"], s["solution_id"]))["solution_id"] if c else None


def plan_survey(risk: str, rank_pct: float, typed: bool, churned: bool, cfg: dict) -> str | None:
    """설문 종류(설계서 §3.1): 조치 후에도 해지 접수 -> post_churn / MID 이상: 유형 있음 post_action, 없음 no_type / LOW 순위 구간 survey_only / 그 외 발송 안 함.
    no_candidate(후보 0건 대체)는 Agent 실행 결과가 있어야 정해지므로 일괄 합성에서는 만들지 않는다."""
    if churned and risk in ("HIGH", "MID"): return "post_churn"
    if risk in ("HIGH", "MID"): return "post_action" if typed else "no_type"
    lo, hi = cfg["survey_plan"]["survey_only_rank_range"]
    return "survey_only" if risk == "LOW" and lo <= rank_pct < hi else None


def build_subjects(t: dict, cfg: dict | None = None, ids: list[str] | None = None, as_of: str | None = None) -> list[Subject]:
    cfg = cfg or load_config()
    f = build_facts(t, as_of); sols = load_catalog_solutions()
    pred = t["churn_prediction"].set_index("subscription_id")
    pt = t["churn_prediction_type"].sort_values(["subscription_id", "type_rank"])
    primary = pt.groupby("subscription_id").type_id.first().to_dict()
    truth = {s: dict(zip(g.type_id, g.activation_strength)) for s, g in t["synth_truth_type"].groupby("subscription_id")}
    lab = t.get("synth_churn_label"); churn_col = cfg["survey_plan"]["churn_label_column"]
    churned = set(lab[lab[churn_col] == 1].subscription_id) if lab is not None else set()
    order = {s: i for i, s in enumerate(f.index)}
    out = []
    for sid in (ids or list(pred.index)):
        p = pred.loc[sid]; typed = sid in primary
        kind = plan_survey(p.risk_level, float(p.risk_rank_pct), typed, sid in churned, cfg)
        if kind is None: continue
        hidden = assign_topics(sid, no_type_mid_plus=(not typed and p.risk_level in ("HIGH", "MID")), cfg=cfg)
        row = f.iloc[order[sid]]
        facts = _clean({k: row[k] for k in f.columns if not k.startswith("_")})
        facts["sched_difficulty"] = bool(facts.get("sched_difficulty"))
        sol = stand_in_solution(primary.get(sid), sols) if kind in ("post_action", "post_churn") and typed else None
        out.append(Subject(sid, f"SV-{sid}", kind, p.risk_level, truth.get(sid, {}), tuple(hidden), row["_event_counts"], row["_repeat"], sol, dict(hidden), facts))
    return out
