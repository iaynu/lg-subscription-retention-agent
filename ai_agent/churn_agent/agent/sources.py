"""예측 소스: 구독 1건의 AgentInput을 만든다.

- DemoSource: 데모 8건(data/demo_subscribers.json). request_id는 넣지 않는다(서버가 실행마다 새로 만든다).
- SqlSource: PostgreSQL(sql/schema_postgres.sql)에서 예측·유형·구독·고객·이벤트를 읽어 AgentInput을 조립한다.
  facts(자격 조건 판정용)와 message_facts(고객 공개 값)도 여기서 계산한다. 스키마로 구할 수 없는 값은 None으로 둔다
  (그 값을 'required'로 쓰는 솔루션은 후보에서 빠진다: S-T07-D 매니저 교체 확정, S-T09-C 점검 이상 소견, S-T10-D 예상 수리 기간).
"""
from __future__ import annotations
import datetime as dt, json
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from .config import ROOT
from .facts import contract_year_bounds, derive_from_subscription


class PredictionSource(Protocol):
    def list_subscriptions(self) -> list[dict]: ...
    def get_input(self, subscription_id: str, as_of_date: str | None = None) -> dict | None: ...


class DemoSource:
    def __init__(self, path=None):
        d = json.loads(Path(path or ROOT / "data" / "demo_subscribers.json").read_text(encoding="utf-8"))["demos"]
        self.demos = {x["agent_input"]["subscription"]["subscription_id"]: x for x in d}

    def list_subscriptions(self):
        """title은 테스트 카탈로그·스크립트 LLM 기준 시나리오. expected_path는 확정 카탈로그 + mock에서 실제로 나오는 경로."""
        def path(v):
            e = v.get("expect_real") or {}
            p = " + ".join(e.get("selected", []))
            return p + (" (후보 0건 → 만족도 조사 대체)" if e.get("fallback") == "NO_ELIGIBLE_CANDIDATE" else "")
        return [dict(subscription_id=k, demo_id=v["id"], title=v["title"], expected_path=path(v),
                     risk_level=v["agent_input"]["prediction"]["risk_level"],
                     risk_rank_pct=v["agent_input"]["prediction"]["risk_rank_pct"]) for k, v in self.demos.items()]

    def get_input(self, subscription_id, as_of_date=None):
        d = self.demos.get(subscription_id)
        if not d: return None
        ai = json.loads(json.dumps(d["agent_input"]))
        ai.pop("request_id", None)
        if as_of_date: ai["as_of_date"] = as_of_date
        return ai


# ---------------------------------------------------------------- SQL
VOC_FEE_CANCEL = ("요금문의", "총비용문의", "해지문의", "위약금문의")


def _plain(v):
    if isinstance(v, Decimal): return int(v) if v == int(v) else float(v)
    if isinstance(v, (dt.date, dt.datetime)): return v.isoformat()[:10]
    return v


def _days(a: dt.date, b: dt.date) -> int: return (a - b).days


def _next_peak(as_of: dt.date, mmdd: str) -> dt.date:
    m, d = map(int, mmdd.split("-"))
    peak = dt.date(as_of.year, m, d)
    return peak if peak >= as_of else dt.date(as_of.year + 1, m, d)


class SqlSource:
    def __init__(self, url: str | None = None, engine=None, facts_cfg: dict | None = None, feature_dict_path=None):
        from sqlalchemy import create_engine
        self.engine = engine or create_engine(url, future=True)
        self.cfg = facts_cfg or {}
        fd = json.loads(Path(feature_dict_path or ROOT / "data" / "feature_dictionary.json").read_text(encoding="utf-8"))
        self.labels = {f["feature"]: f["display_ko"] for f in fd["features"]}

    def _q(self, _conn, _sql, **params):
        from sqlalchemy import text
        return [dict(r._mapping) for r in _conn.execute(text(_sql), params)]

    def list_subscriptions(self, as_of_date: str | None = None, limit: int = 200):
        with self.engine.connect() as c:
            if not as_of_date:
                r = self._q(c, "SELECT max(as_of_date) AS d FROM churn_prediction")
                if not r or r[0]["d"] is None: return []
                as_of_date = r[0]["d"].isoformat()
            rows = self._q(c, """
                SELECT p.subscription_id, p.as_of_date, p.risk_level, p.risk_rank_pct, p.churn_probability,
                       string_agg(t.type_id, ',' ORDER BY t.type_rank) AS type_ids
                FROM churn_prediction p LEFT JOIN churn_prediction_type t ON t.prediction_id = p.prediction_id
                WHERE p.as_of_date = :a GROUP BY p.prediction_id ORDER BY p.risk_rank_pct LIMIT :n""", a=as_of_date, n=limit)
        return [{k: _plain(v) for k, v in r.items()} for r in rows]

    def get_input(self, subscription_id: str, as_of_date: str | None = None) -> dict | None:
        with self.engine.connect() as c:
            preds = self._q(c, """SELECT * FROM churn_prediction WHERE subscription_id = :s
                                   AND (CAST(:a AS DATE) IS NULL OR as_of_date = CAST(:a AS DATE))
                                   ORDER BY as_of_date DESC, created_at DESC LIMIT 1""", s=subscription_id, a=as_of_date)
            if not preds: return None
            p = preds[0]; as_of = p["as_of_date"]
            subs = self._q(c, "SELECT * FROM subscription WHERE subscription_id = :s", s=subscription_id)
            if not subs: return None
            s = subs[0]
            cust = (self._q(c, "SELECT customer_id, customer_name, ad_consent_yn FROM customer WHERE customer_id = :cid", cid=s["customer_id"]) or [{}])[0]
            pp = (self._q(c, "SELECT * FROM product_profile WHERE product_category = :p", p=s["product_category"]) or [{}])[0]
            types = self._q(c, """SELECT t.type_rank, t.type_id, ct.type_name, t.contribution_share, t.top_features_json
                                   FROM churn_prediction_type t LEFT JOIN churn_type ct ON ct.type_id = t.type_id
                                   WHERE t.prediction_id = :p ORDER BY t.type_rank""", p=p["prediction_id"])
            voc = self._q(c, """SELECT * FROM voc_contact WHERE subscription_id = :s AND contact_date <= :a
                                 AND contact_date > CAST(:a AS DATE) - 365""", s=subscription_id, a=as_of)
            visits = self._q(c, "SELECT * FROM care_visit WHERE subscription_id = :s", s=subscription_id)
            usage = self._q(c, "SELECT week_start_date, run_count FROM usage_thinq WHERE subscription_id = :s AND week_start_date <= :a",
                            s=subscription_id, a=as_of)
            paid = self._q(c, """SELECT coalesce(sum(paid_amount), 0) AS paid FROM payment
                                  WHERE subscription_id = :s AND billing_month <= :a AND paid_amount IS NOT NULL""", s=subscription_id, a=as_of)
        sub = {"subscription_id": s["subscription_id"], "product_category": s["product_category"],
               "monthly_fee": _plain(s["monthly_fee"]), "contract_months": s["contract_months"],
               "start_date": s["start_date"].isoformat(), "end_date": s["end_date"].isoformat(),
               "days_to_end": _days(s["end_date"], as_of), "care_type": s["care_type"],
               "care_renewal_date": _plain(s["care_renewal_date"]), "promo_end_date": _plain(s["promo_end_date"]),
               "new_model_released_yn": s["new_model_released_yn"], "model_discontinued_yn": s["model_discontinued_yn"]}
        facts, mfacts = derive_from_subscription(sub, as_of.isoformat(), self.cfg)
        f2, m2 = self._event_facts(as_of, s, pp, voc, visits, usage, int(_plain(paid[0]["paid"])))
        facts.update(f2); mfacts.update(m2)
        types_out = []
        for t in types:
            feats = json.loads(t["top_features_json"] or "[]")
            types_out.append(dict(type_rank=t["type_rank"], type_id=t["type_id"], type_name=t["type_name"] or "",
                                  contribution_share=float(t["contribution_share"]),
                                  top_features=[dict(feature=f["feature"], label=self.labels.get(f["feature"], f["feature"]),
                                                     value=f.get("value"), shap=f.get("shap", 0)) for f in feats]))
        return dict(as_of_date=as_of.isoformat(), subscription=sub,
                    customer={"customer_id": cust.get("customer_id"), "customer_name": cust.get("customer_name"),
                              "ad_consent_yn": cust.get("ad_consent_yn")},
                    prediction={"churn_probability": float(p["churn_probability"]), "risk_level": p["risk_level"],
                                "risk_rank_pct": float(p["risk_rank_pct"])},
                    types=types_out, context=self._context(as_of, voc, visits),
                    facts=facts, message_facts=mfacts, recent_actions=[])

    # ---------- 계산 ----------
    def _context(self, as_of, voc, visits):
        v90 = [v for v in voc if _days(as_of, v["contact_date"]) < 90]
        by = {}
        for v in v90: by[v["category"]] = by.get(v["category"], 0) + 1
        y = [x for x in visits if x["scheduled_date"] <= as_of and _days(as_of, x["scheduled_date"]) < 365]
        return {"voc_summary": {"window_days": 90, "total": len(v90), "by_category": by,
                                "unresolved": sum(v["resolved_yn"] == "N" for v in v90)},
                "visit_summary": {"window_days": 365, "visits": sum(x["visit_status"] in ("완료", "지연완료") for x in y),
                                  "delays": sum(x["visit_status"] == "지연완료" for x in y),
                                  "cancels": sum(x["visit_status"] in ("취소", "미이행") for x in y)}}

    def _event_facts(self, as_of, s, pp, voc, visits, usage, paid_total):
        f, m = {}, {}
        in30 = lambda d: 0 <= _days(as_of, d) < 30
        v30 = [v for v in voc if in30(v["contact_date"])]
        f["voc_fee_or_cancel_cnt_30d"] = sum(v["category"] in VOC_FEE_CANCEL for v in v30)
        f["move_signal_yn"] = "Y" if any(v["category"] == "이사문의" and _days(as_of, v["contact_date"]) < 90 for v in voc) else "N"
        f["attitude_complaint_cnt_30d"] = sum(v["category"] == "태도불만" for v in v30)
        f["sales_voc_cnt_30d"] = sum(v["category"] == "영업불만" for v in v30)
        f["setting_inquiry_yn"] = "Y" if any(v["category"] == "사용법문의" for v in v30) else "N"
        f["first_response_unresolved_yn"] = "Y" if any(v["first_response_unresolved_yn"] == "Y" and v["resolved_yn"] == "N" for v in v30) else "N"
        f["unapplied_discount_yn"] = "Y" if (s["discount_eligible_yn"] == "Y" and s["discount_applied_yn"] == "N") else "N"
        # 사용
        if usage:
            pos = [u["week_start_date"] for u in usage if u["run_count"] > 0]
            last = (max(pos) + dt.timedelta(days=6)) if pos else s["start_date"]
            f["days_since_last_use"] = max(_days(as_of, last), 0); m["days_since_last_use"] = f["days_since_last_use"]
        # 방문(정기점검·A/S·설치)
        done = [x for x in visits if x["visit_status"] in ("완료", "지연완료") and x["completed_date"] and x["completed_date"] <= as_of]
        care = [x for x in done if x["visit_type"] == "정기점검"]
        if care:
            last_care = max(x["completed_date"] for x in care)
            f["days_since_last_care"] = _days(as_of, last_care); m["last_care_date"] = last_care.isoformat()
        f["care_short_visit_cnt"] = sum(1 for x in care if _days(as_of, x["completed_date"]) < 90 and x["duration_min"] is not None
                                        and x["duration_min"] < int(self.cfg.get("short_care_minutes", 20)))
        if done:
            last_visit = max(x["completed_date"] for x in done)
            f["days_since_last_visit"] = _days(as_of, last_visit); m["last_visit_date"] = last_visit.isoformat()
        upcoming = sorted(x["scheduled_date"] for x in visits if x["visit_status"] == "예정" and x["scheduled_date"] >= as_of)
        f["has_upcoming_visit"] = "Y" if upcoming else "N"
        if upcoming: m["next_visit_date"] = upcoming[0].isoformat()
        f["missed_or_late_visit_cnt_30d"] = sum(1 for x in visits if x["scheduled_date"] <= as_of and in30(x["scheduled_date"])
                                                 and x["visit_status"] in ("취소", "미이행", "지연완료"))
        # 남은 케어(계약 연도 기준)
        interval = pp.get("visit_interval_months")
        if s["care_type"] == "방문관리" and interval:
            y0, _ = contract_year_bounds(s["start_date"], as_of)
            expected = max(12 // int(interval), 1)
            used = sum(1 for x in care if y0 <= x["completed_date"] <= as_of)
            f["remaining_care_cnt"] = max(expected - used, 0)
        else:
            f["remaining_care_cnt"] = 0
        if f["remaining_care_cnt"]: m["remaining_care_cnt"] = f["remaining_care_cnt"]
        # 계절(성수기) — 설정에 없는 제품군은 '계절 없음'으로 확정(9999일)
        peak = (self.cfg.get("season_peak_mmdd") or {}).get(s["product_category"])
        f["days_to_season_peak"] = _days(_next_peak(as_of, peak), as_of) if peak else 9999
        # A/S
        as_rows = [x for x in visits if x["visit_type"] == "AS수리"]
        f["open_as_yn"] = "Y" if any(x["visit_status"] == "예정" for x in as_rows) else "N"
        sym = {}
        for x in as_rows:
            if x["symptom_code"] and 0 <= _days(as_of, x["scheduled_date"]) < 365: sym[x["symptom_code"]] = sym.get(x["symptom_code"], 0) + 1
        f["same_symptom_cnt"] = max(sym.values()) if sym else 0
        # 예상 수리 기간: 열린 A/S 중 접수일·예상 완료일이 모두 있는 건의 최댓값(없으면 None → required 조건이면 후보 제외)
        spans = [(x["expected_complete_date"] - x["as_received_date"]).days for x in as_rows
                 if x["visit_status"] == "예정" and x.get("expected_complete_date") and x.get("as_received_date")]
        f["expected_as_days"] = max(spans) if spans else None
        if spans: m["expected_as_days"] = f["expected_as_days"]
        # 점검 이상: 가장 최근 완료된 정기점검이 30일 안이고 이상이 기록된 경우
        f["care_inspection_anomaly_yn"] = "Y" if care and in30(last_care) and any(
            x["completed_date"] == last_care and x.get("inspection_anomaly_yn") == "Y" for x in care) else "N"
        f["manager_change_confirmed_yn"] = s.get("manager_change_confirmed_yn") or "N"
        if paid_total: m["paid_total"] = paid_total
        return f, m
