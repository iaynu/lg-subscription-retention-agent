"""시연용 대표 사례 선정(페이지 5-1(c)).

select_demo_cases(cfg, tables) -> DataFrame   (사례당 구독 1건, 서로 겹치지 않음)
- 조건에 맞는 구독 중 '필요한 유형 외에 겹친 유형이 가장 적은' 구독을, 그중 생성 이탈 확률(label_detail.p_churn)이 가장 높은 구독을 고른다. 같은 설정·시드면 항상 같은 사례.
- 이 단계에는 학습된 모델이 없으므로 '어떤 안내가 나와야 하는가'는 데이터 조건(facts)으로 표시하고,
  실제 Agent 실행(Rule Engine → LLM → 검증)은 DB 적재 후 별도로 확인한다.
- 조건은 solution_catalog의 eligibility(필수 사실)와 같은 의미를 데이터로 직접 계산한 것이다.
"""
from __future__ import annotations
import datetime as dt
import pandas as pd
from .config import as_of


def _facts(t: dict, today: dt.date) -> pd.DataFrame:
    sub = t["subscription"].set_index("subscription_id"); cu = t["customer"].set_index("customer_id"); v = t["care_visit"]; voc = t["voc_contact"]
    f = pd.DataFrame(index=sub.index)
    f["customer_id"] = sub.customer_id; f["product"] = sub.product_category; f["care_type"] = sub.care_type
    f["ad_consent"] = cu.loc[sub.customer_id, "ad_consent_yn"].to_numpy(); f["customer_name"] = cu.loc[sub.customer_id, "customer_name"].to_numpy()
    f["days_to_end"] = [(e - today).days for e in sub.end_date]
    f["unapplied_discount"] = ((sub.discount_eligible_yn == "Y") & (sub.discount_applied_yn == "N")).to_numpy()
    f["manager_change_confirmed"] = (sub.manager_change_confirmed_yn == "Y").to_numpy()
    up = v[(v.visit_type == "정기점검") & (v.visit_status == "예정")].groupby("subscription_id").size()
    f["has_upcoming_visit"] = f.index.isin(up.index)
    op = v[(v.visit_type == "AS수리") & (v.visit_status == "예정")].copy()
    op["exp_days"] = [(e - r).days for e, r in zip(op.expected_complete_date, op.as_received_date)]
    f["open_as_expected_days"] = op.groupby("subscription_id").exp_days.max().reindex(f.index)
    care = v[(v.visit_type == "정기점검") & v.visit_status.isin(["완료", "지연완료"])].sort_values("completed_date").groupby("subscription_id").tail(1).set_index("subscription_id")
    f["recent_care_anomaly"] = [(i in care.index and care.at[i, "inspection_anomaly_yn"] == "Y" and (today - care.at[i, "completed_date"]).days < 30) for i in f.index]
    mv = voc[voc.category == "이사문의"].groupby("subscription_id").size(); f["move_voc"] = f.index.isin(mv.index)
    ren = t["notice_log"].query("notice_type == '갱신안내' and opened_at == opened_at").groupby("subscription_id").size(); f["renewal_notice_opened"] = f.index.isin(ren.index)
    us = t["usage_thinq"].sort_values("week_start_date").groupby("subscription_id").run_count.apply(lambda s: int((s.iloc[-12:] == 0).sum())); f["zero_use_weeks_12w"] = us.reindex(f.index)
    pitch = v[(v.sales_pitch_yn == "Y") & (v.scheduled_date >= today - dt.timedelta(days=180))].groupby("subscription_id").size(); f["pitch_cnt_180d"] = pitch.reindex(f.index).fillna(0).astype(int)
    sh = v[(v.visit_type == "정기점검") & v.visit_status.isin(["완료", "지연완료"]) & (v.duration_min < 20) & (v.completed_date >= today - dt.timedelta(days=90))].groupby("subscription_id").size()
    f["short_care_cnt"] = sh.reindex(f.index).fillna(0).astype(int)
    f["is_active"] = f.index.isin(set(t["synth_truth_type"].subscription_id))
    return f


def select_demo_cases(cfg: dict, t: dict, pred: pd.DataFrame | None = None, ptype: pd.DataFrame | None = None) -> pd.DataFrame:
    """pred·ptype(모델 예측)을 주면 D01~D10은 '모델이 위험 HIGH/MID로 보고 필요한 유형을 모두 채택한' 구독 중에서 고른다(Agent 시연용 대표 사례).
    이 경우 시연 통과율은 모델 정확도를 뜻하지 않는다(맞게 예측된 사례를 골랐으므로)."""
    today = as_of(cfg); f = _facts(t, today); p = t["label_detail"].set_index("subscription_id").p_churn
    tt = t["synth_truth_type"].groupby("subscription_id").type_id.apply(set); lab = t["synth_churn_label"].set_index("subscription_id")
    f["types"] = [tt.get(i, set()) for i in f.index]; f["p"] = p; f["churn"] = lab.churn_30d
    if pred is not None:
        pr = pred.set_index("subscription_id"); pts = ptype.groupby("subscription_id").type_id.apply(set)
        f["risk"] = pr.risk_level.reindex(f.index); f["rank_pct"] = pr.risk_rank_pct.reindex(f.index); f["pred_types"] = [pts.get(i, set()) for i in f.index]
    model_ok = lambda req: (f["risk"].isin(["HIGH", "MID"]) & f["pred_types"].map(lambda s: set(req) <= s)) if pred is not None else True
    model_noact = lambda lo, hi: (f["rank_pct"].between(lo, hi, inclusive="left") & (f["risk"] == "LOW"))
    has = lambda *ts: f["types"].map(lambda s: set(ts) <= s)
    only = lambda ts: f["types"].map(lambda s: s == set(ts))
    cases = [
        ("D01", {"T01", "T04"}, "복합 유형 + 광고 동의: 사용 저하와 놓친 할인", has("T01", "T04") & (f.ad_consent == "Y") & f.unapplied_discount & model_ok({"T01", "T04"}), "알림톡(정보성)과 친구톡(광고성)이 따로 나뉘어 발송"),
        ("D02", {"T06", "T07"}, "방문 일정 + 매니저 불만, 광고 미동의, 예정 방문 있음", has("T06", "T07") & (f.ad_consent == "N") & f.has_upcoming_visit & model_ok({"T06", "T07"}), "정보성 알림톡이 한 통으로 합쳐지고 협업 부서 전달 초안 생성"),
        ("D03", {"T10"}, "A/S 지연: 예상 수리 7일 이상", has("T10") & (f.open_as_expected_days >= 7) & model_ok({"T10"}), "S-T10-D 대체품 안내 + 물류·A/S 부서 전달, 전담자 배정"),
        ("D04", {"T07"}, "담당 매니저 교체 확정", has("T07") & f.manager_change_confirmed & model_ok({"T07"}), "S-T07-D 인수인계 안내가 후보(같은 카테고리 상위안과 경쟁)"),
        ("D05", {"T09"}, "케어 중 점검 이상 소견", has("T09") & f.recent_care_anomaly & model_ok({"T09"}), "S-T09-C 선제 이상 리포트 + A/S 부서 전달"),
        ("D06", {"T03"}, "만료 30일 이내 재계약 갈등", has("T03") & (f.days_to_end <= 30) & f.renewal_notice_opened & model_ok({"T03"}), "재계약 안내(만료 임박, 갱신안내 열람)"),
        ("D07", {"T02"}, "해지·이사 장벽: 이사 문의", has("T02") & f.move_voc & model_ok({"T02"}), "이사·해지 관련 안내, 위약금·이전 설치 정보"),
        ("D08", {"T08"}, "영업 압박: 방문 중 영업 권유 반복", has("T08") & (f.pitch_cnt_180d >= 2) & model_ok({"T08"}), "영업 압박 관련 안내가 없으면 만족도 조사로 대체(폴백 시연)"),
        ("D09", {"T05"}, "케어 부실: 케어 직후 A/S", only(["T05"]) & (f.short_care_cnt >= 1) & model_ok({"T05"}), "S-T05-A 짧은 케어 후속 안내(최근 90일 내 20분 미만 케어 1회 이상)"),
        ("D10", {"T04"}, "사용 급감(T04 단독)", only(["T04"]) & (f.zero_use_weeks_12w >= 8) & (f.ad_consent == "Y") & model_ok({"T04"}), "T04 단독, 최근 12주 중 8주 이상 무가동, 광고 동의(S-T04-A는 광고성 안내)"),
        ("D11", set(), "유형 없음인데 이탈(라벨 노이즈)", ~f.is_active & (f.churn == 1) & (model_noact(0.2, 0.3) if pred is not None else True), "모델이 '유형 없음'으로 판단 → 만족도 조사(S-NT-A)"),
        ("D12", set(), "정상·저위험", ~f.is_active & (f.churn == 0) & (f.days_to_end > 365) & (model_noact(0.5, 1.0) if pred is not None else True), "위험 낮음 → 조치 없음/설문 추천 범위 확인용"),
    ]
    out, used = [], set()
    for cid, req, title, mask, expect in cases:
        pool = f[mask & ~f.index.isin(used)].copy(); pool["extra"] = pool["types"].map(lambda x: len(x - req))      # 필요한 유형 외에 겹친 유형이 적은 구독을 우선
        pool = pool.sort_values(["extra", "p"], ascending=[True, False], kind="stable")
        if pool.empty: raise ValueError(f"시연 사례 {cid}({title})에 맞는 구독이 없음 — 설정을 확인하세요")
        r = pool.iloc[0]; used.add(pool.index[0])
        out.append(dict(case_id=cid, scenario=title, subscription_id=pool.index[0], customer_id=r.customer_id, customer_name=r.customer_name, product=r["product"],
                        care_type=r.care_type, true_types=",".join(sorted(r.types)) or "(없음)", extra_types=int(r.extra), gen_churn_prob=round(float(r.p), 3), churn_30d=int(r.churn),
                        ad_consent=r.ad_consent, days_to_end=int(r.days_to_end), expected=expect, candidates_matching=int((mask & ~f.index.isin(used)).sum()) + 1))
    return pd.DataFrame(out)
