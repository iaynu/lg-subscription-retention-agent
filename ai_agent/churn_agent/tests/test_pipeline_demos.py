"""데모 8건(D1~D8) 재현: 구 테스트 카탈로그 + 스크립트된 LLM으로 흐름 기대값 확인, 확정 카탈로그 + 규칙 기반 mock으로 전 데모 검증기 통과 확인."""
import json
import pytest
from agent.config import ROOT, load_config
from agent.catalog import load_catalog
from agent.llm.mock import ScriptedLlm, RuleBasedMock
from agent.repo.memory import MemoryRepo
from agent.service import AgentService
from agent.validation.validator import validate

DEMOS = json.loads((ROOT / "data/demo_subscribers.json").read_text(encoding="utf-8"))["demos"]
TEST_CAT = load_catalog(ROOT / "data/catalog_test.json")

@pytest.mark.parametrize("d", DEMOS, ids=[d["id"] for d in DEMOS])
def test_demo_flow_with_test_catalog(d):
    llm = ScriptedLlm(d["llm_script"]); cfg = load_config(); cfg["rules"]["fallback_survey_when_empty"] = False   # 구 기대값은 대체 경로가 없던 시절 기준
    svc = AgentService(cfg, TEST_CAT, llm, MemoryRepo())
    ai = json.loads(json.dumps(d["agent_input"])); res = svc.run(ai); ex, er = d["expect_flow"], d["expect_rule"]
    assert res["mode"] == er["mode"]
    assert [s["solution_id"] for s in res["selected"]] == er["selected"] or res["outcome"] in ("survey_template", "no_action")
    assert {e["solution_id"]: e["reason"] for e in res["excluded"]} == er["excluded"]
    assert res["outcome"] == ex["outcome"] and res["attempts"] == ex["attempts"]
    assert (len(llm.calls) > 0) == ex["llm_called"]
    if "message_sources" in ex: assert [s["message_source"] for s in res["selected"]] == ex["message_sources"]
    if "failed_checks_include" in ex: assert set(ex["failed_checks_include"]) <= set(res["failed_checks"])

@pytest.mark.parametrize("d", DEMOS, ids=[d["id"] for d in DEMOS])
def test_demo_with_real_catalog_and_mock(d, catalog):
    svc = AgentService(load_config(), catalog, RuleBasedMock(), MemoryRepo())
    res = svc.run(json.loads(json.dumps(d["agent_input"])))
    assert res["outcome"] in ("pass", "survey_template", "no_action")
    for s in res["selected"]:
        c = svc.by_id[s["solution_id"]]
        assert s["channel"] == c["channel"]
        if c["delivery_kind"] == "내부 전달": assert s["message_text"] == "" and s["internal_memo"]
        if c.get("ad_yn") == "Y": assert s["title"].startswith("(광고)")

@pytest.mark.parametrize("d", DEMOS, ids=[d["id"] for d in DEMOS])
def test_demo_real_catalog_path_is_pinned(d, catalog):
    """API 데모 모드(확정 카탈로그 + mock)의 실제 경로를 고정한다. 카탈로그·규칙이 바뀌면 여기서 드러난다."""
    res = AgentService(load_config(), catalog, RuleBasedMock(), MemoryRepo()).run(json.loads(json.dumps(d["agent_input"])))
    got = {"mode": res["mode"], "selected": [s["solution_id"] for s in res["selected"]], "fallback": res.get("fallback"), "outcome": res["outcome"]}
    assert got == d["expect_real"], d["id"]


def test_demo_listing_shows_real_path():
    from agent.sources import DemoSource
    rows = {r["demo_id"]: r for r in DemoSource().list_subscriptions()}
    assert rows["D1"]["expected_path"] == "S-T01-C + S-T06-C"
    assert rows["D3"]["expected_path"] == "S-NT-A (후보 0건 → 만족도 조사 대체)"


def test_ad_consent_blocks_ad_solutions_end_to_end(catalog):
    for consent in ("N", None):                      # 미동의, 값 없음(=미동의) 모두 광고성 제외
        d = DEMOS[0]; ai = json.loads(json.dumps(d["agent_input"])); ai["customer"] = {"ad_consent_yn": consent}
        ai["subscription"].pop("ad_consent_yn", None)
        res = AgentService(load_config(), catalog, RuleBasedMock(), MemoryRepo()).run(ai)
        assert all(s["channel"] != "친구톡" for s in res["selected"])
        assert any(e["reason"] == "NO_AD_CONSENT" for e in res["excluded"])
    ai = json.loads(json.dumps(DEMOS[0]["agent_input"])); ai["customer"] = {"ad_consent_yn": "Y"}
    res = AgentService(load_config(), catalog, RuleBasedMock(), MemoryRepo()).run(ai)
    assert any(s["channel"] == "친구톡" and s["title"].startswith("(광고)") for s in res["selected"])


def test_demo_features_match_feature_dictionary():
    """데모 입력의 피처 이름·라벨·유형 그룹이 피처 사전(data/feature_dictionary.json)과 같아야 한다."""
    fd = {f["feature"]: f for f in json.loads((ROOT / "data/feature_dictionary.json").read_text(encoding="utf-8"))["features"]}
    for d in DEMOS:
        for t in d["agent_input"]["types"]:
            for f in t["top_features"]:
                e = fd.get(f["feature"])
                assert e, (d["id"], f["feature"])
                assert e["display_ko"] == f["label"] and e["group"] == t["type_id"], (d["id"], f["feature"])


def test_demo_products_are_known_categories():
    known = {"의류관리기", "건조기", "냉장고", "세탁기", "에어컨", "정수기"}
    assert all(d["agent_input"]["subscription"]["product_category"] in known for d in DEMOS)
