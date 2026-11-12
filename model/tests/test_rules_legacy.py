"""Rule Engine v2 시절 38개 케이스(구 테스트 카탈로그, priority 없음=50)가 v3에서도 그대로 통과해야 한다."""
import datetime as dt, json
import pytest
from agent.config import ROOT
from agent.rules.engine import run_rule_engine

DATA = json.loads((ROOT / "data/rule_cases.json").read_text(encoding="utf-8"))["cases"]
CAT = json.loads((ROOT / "data/catalog_test.json").read_text(encoding="utf-8"))["solutions"]

def to_inp(c, as_of=None):
    i = c["input"]; as_of = as_of or i["as_of_date"]; d0 = dt.date.fromisoformat(as_of)
    sub = {"monthly_fee": i["monthly_fee"]}
    if "ad_consent_yn" in i: sub["ad_consent_yn"] = i["ad_consent_yn"]
    return {"as_of_date": as_of, "subscription": sub,
            "prediction": {"risk_level": i["risk_level"], "risk_rank_pct": i["risk_rank_pct"]},
            "types": [{"type_rank": k + 1, "type_id": t["type_id"], "contribution_share": t["share"]} for k, t in enumerate(i["types"])],
            "recent_actions": [{"solution_id": h["solution_id"], "action_status": h["status"], "offer_cost": h["offer_cost"],
                                "created_at": (d0 - dt.timedelta(days=h["days_ago"])).isoformat()} for h in i["history"]]}

@pytest.mark.parametrize("c", DATA, ids=[c["id"] + " " + c["title"] for c in DATA])
def test_case(c):
    for as_of in c.get("repeat_as_of", [None]):
        res = run_rule_engine(to_inp(c, as_of), CAT + c.get("catalog_extra", []), {**(c.get("cfg") or {}), "fallback_survey_when_empty": False}); exp = c["expect"]
        assert res["mode"] == exp["mode"]
        assert [x["solution_id"] for x in res["candidates"]] == exp["selected"]
        assert {e["solution_id"]: e["reason"] for e in res["excluded"]} == exp["excluded"]
        assert res["uncovered_types"] == exp["uncovered_types"]
        for k, v in exp.get("limits", {}).items(): assert res["limits"].get(k) == v
