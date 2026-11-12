"""생성 결과 검증(페이지 5-1(b)): 분포 목표, 데이터 품질, 논리 관계(고아 행), 누수 점검 → 마크다운 리포트.

validate_all(cfg, tables) -> dict(metrics, checks, orphans)       checks: [(이름, 통과 여부, 설명)]
render_report(cfg, result) -> str (마크다운)
고아 행 검사는 sql/check_integrity.sql의 논리 관계 29개 중 이 생성기가 만드는 테이블 사이 10개와 같은 내용이다.
"""
from __future__ import annotations
import datetime as dt
import numpy as np, pandas as pd
from .config import as_of, TYPE_IDS
from .labels import risk_levels

REL = [("subscription", "customer_id", "customer", "customer_id"), ("subscription", "care_manager_id", "care_manager", "manager_id"),
       ("usage_thinq", "subscription_id", "subscription", "subscription_id"), ("care_visit", "subscription_id", "subscription", "subscription_id"),
       ("care_visit", "manager_id", "care_manager", "manager_id"), ("voc_contact", "subscription_id", "subscription", "subscription_id"),
       ("payment", "subscription_id", "subscription", "subscription_id"), ("notice_log", "subscription_id", "subscription", "subscription_id"),
       ("synth_churn_label", "subscription_id", "subscription", "subscription_id"), ("synth_truth_type", "subscription_id", "subscription", "subscription_id")]


def orphans(t: dict) -> dict:
    out = {}
    for ct, cc, pt, pc in REL:
        c = t[ct][cc].dropna(); out[f"{ct}.{cc} -> {pt}.{pc}"] = int((~c.isin(set(t[pt][pc]))).sum())
    return out


def validate_all(cfg: dict, t: dict) -> dict:
    today = as_of(cfg); L = cfg["label"]; tol = L["tolerance"]; tg = L["targets"]
    sub, lab, truth, det = t["subscription"], t["synth_churn_label"], t["synth_truth_type"], t["label_detail"]
    act = set(truth.subscription_id); is_act = lab.subscription_id.isin(act); lb = lab.set_index("subscription_id")
    m = {"rows": {k: len(v) for k, v in t.items() if k != "label_detail"}}
    m["rates"] = dict(overall=float(lab.churn_30d.mean()), normal=float(lab[~is_act].churn_30d.mean()), active=float(lab[is_act].churn_30d.mean()))
    k = truth.groupby("subscription_id").size(); m["by_type_count"] = {int(i): float(lab[lab.subscription_id.isin(k[k == i].index)].churn_30d.mean()) for i in sorted(k.unique())}
    rl = risk_levels(det); m["by_risk_rank"] = {g: float(lb.loc[rl[rl == g].index, "churn_30d"].mean()) for g in ("HIGH", "MID", "LOW")}
    m["churn_from_normal_share"] = float(lab[~is_act].churn_30d.sum() / lab.churn_30d.sum())
    one = k[k == 1].index; x = truth[truth.subscription_id.isin(one)].merge(det, on="subscription_id"); m["single_type_mean_p"] = {t_: float(v) for t_, v in x.groupby("type_id").p_churn.mean().items()}
    m["type_counts"] = truth.type_id.value_counts().reindex(TYPE_IDS).to_dict()
    m["product_counts"] = sub.product_category.value_counts().to_dict(); m["care_type_share"] = sub.care_type.value_counts(normalize=True).to_dict()
    ch = lab[lab.churn_30d == 1]; m["cancel_day_range"] = [int(min((d - today).days for d in ch.cancel_requested_date)), int(max((d - today).days for d in ch.cancel_requested_date))]
    o = orphans(t); checks = []
    add = lambda name, ok, note="": checks.append((name, bool(ok), note))
    add("정상군 이탈률 ≈ 목표", abs(m["rates"]["normal"] - tg["normal_rate"]) <= tol["normal_rate"], f'{m["rates"]["normal"]:.3f} (목표 {tg["normal_rate"]} ±{tol["normal_rate"]})')
    add("활성군 이탈률 ≈ 목표", abs(m["rates"]["active"] - tg["active_rate"]) <= tol["active_rate"], f'{m["rates"]["active"]:.3f} (목표 {tg["active_rate"]} ±{tol["active_rate"]})')
    add("전체 이탈률 ≈ 목표", abs(m["rates"]["overall"] - tg["overall_rate"]) <= tol["overall_rate"], f'{m["rates"]["overall"]:.3f} (목표 {tg["overall_rate"]} ±{tol["overall_rate"]})')
    add("유형 개수가 많을수록 이탈률 상승", m["by_type_count"][1] < m["by_type_count"][2] < m["by_type_count"][3], str({i: round(v, 3) for i, v in m["by_type_count"].items()}))
    add("순위 HIGH > MID > LOW 이탈률", m["by_risk_rank"]["HIGH"] > m["by_risk_rank"]["MID"] > m["by_risk_rank"]["LOW"], str({g: round(v, 3) for g, v in m["by_risk_rank"].items()}))
    add("모든 유형 단독 활성 확률 ≥ 정상군×4", min(m["single_type_mean_p"].values()) >= 4 * m["rates"]["normal"] * 0.9, f'최소 {min(m["single_type_mean_p"].values()):.3f}')
    add("해지 접수일 기준일 +1~30일", m["cancel_day_range"][0] >= 1 and m["cancel_day_range"][1] <= 30, str(m["cancel_day_range"]))
    add("논리 관계 고아 행 0", sum(o.values()) == 0, f"{len(o)}개 관계")
    add("구독 상태 전부 '활성'(누수 방지)", (sub.status == "활성").all())
    add("구독 ID 유일·라벨과 1:1", sub.subscription_id.is_unique and set(lab.subscription_id) == set(sub.subscription_id))
    mx = {"care_visit(완료·지연완료)": max(d for d in t["care_visit"].query("visit_status in ['완료','지연완료']").completed_date), "voc_contact": max(t["voc_contact"].contact_date),
          "payment.billing_month": max(t["payment"].billing_month), "usage week_start": max(t["usage_thinq"].week_start_date), "notice_log.sent_at": max(t["notice_log"].sent_at).date()}
    add("기준일 이후 완료·접수 기록 없음(누수)", all(v <= today for v in mx.values()), str({k_: str(v) for k_, v in mx.items()}))
    add("기준일 이후 이탈 정보가 이벤트에 없음", all(not (c is not None and c <= today) for c in ch.cancel_requested_date))
    pay = t["payment"]; add("결제 UNIQUE(구독,월)", not pay.duplicated(["subscription_id", "billing_month"]).any())
    us = t["usage_thinq"]; add("사용량 UNIQUE(구독,주)", not us.duplicated(["subscription_id", "week_start_date"]).any())
    add("NOT NULL 컬럼 결측 없음(구독·고객·라벨)", not sub.drop(columns=["care_renewal_date", "promo_end_date", "care_manager_id"]).isna().any().any() and not t["customer"].isna().any().any() and not lab.drop(columns=["cancel_requested_date"]).isna().any().any())
    return dict(metrics=m, checks=checks, orphans=o)


def render_report(cfg: dict, res: dict) -> str:
    m, c, tg = res["metrics"], res["checks"], cfg["label"]["targets"]
    L = [f"# 더미 데이터 검증 리포트 (시드 {cfg['run']['seed']}, N={cfg['run']['n_subscriptions']:,}, 기준일 {cfg['run']['as_of_date']})", "",
         "> 가설 기반 synthetic data입니다. 아래 수치는 설정한 생성 규칙이 목표를 맞췄는지 확인한 것이며 실제 이탈 구조를 반영하지 않습니다.", "",
         f"## 점검 결과: {sum(ok for _, ok, _ in c)}/{len(c)} 통과", "", "| 점검 | 결과 | 값 |", "|---|---|---|"]
    L += [f"| {n} | {'통과' if ok else '**실패**'} | {note} |" for n, ok, note in c]
    L += ["", "## 테이블 행 수", "", "| 테이블 | 행 수 |", "|---|---|"] + [f"| {k} | {v:,} |" for k, v in m["rows"].items()]
    r = m["rates"]
    L += ["", "## 이탈률", "", "| 구분 | 이탈률 | 목표 |", "|---|---|---|", f"| 전체 | {r['overall']:.1%} | {tg['overall_rate']:.0%} |", f"| 정상군 | {r['normal']:.1%} | {tg['normal_rate']:.0%} |",
          f"| 유형 활성군 | {r['active']:.1%} | {tg['active_rate']:.0%} |", "", "| 활성 유형 개수 | 이탈률 |", "|---|---|"] + [f"| {i}개 | {v:.1%} |" for i, v in m["by_type_count"].items()]
    L += ["", "| 생성 확률 순위 | 이탈률 |", "|---|---|"] + [f"| {g} | {v:.1%} |" for g, v in m["by_risk_rank"].items()]
    L += ["", f"이탈자 중 정상군 출신 비중: {m['churn_from_normal_share']:.1%}", "", "## 유형별 활성 건수와 단독 활성 평균 이탈 확률", "", "| 유형 | 활성 건수 | 단독 활성 평균 확률 |", "|---|---|---|"]
    L += [f"| {t} | {m['type_counts'][t]:,} | {m['single_type_mean_p'].get(t, float('nan')):.1%} |" for t in TYPE_IDS]
    L += ["", "## 논리 관계(고아 행 수)", "", "| 관계 | 고아 행 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in res["orphans"].items()]
    return "\n".join(L) + "\n"
