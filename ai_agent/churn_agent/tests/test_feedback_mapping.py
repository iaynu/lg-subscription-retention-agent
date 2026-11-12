import pytest
from feedback import questionnaire as Q
from feedback.mapping import MappingError, action_codes, check_mapping, load_config, load_mapping
from feedback.rng import make_rng, pick
from agent.catalog import load_catalog
from agent.config import ROOT


def test_mapping_is_consistent_with_questionnaire():
    assert check_mapping(load_mapping()) == []


def test_every_selectable_solution_has_an_action_code():
    """카탈로그의 선택 가능 솔루션 33개는 모두 설문 '받은 조치' 보기로 기록할 수 있다(대응 없으면 other)."""
    cat = load_catalog(ROOT / "data/solution_catalog.json")
    sel = [c["solution_id"] for c in cat if c.get("active_yn", "Y") == "Y" and c.get("agent_selectable_yn", "Y") == "Y" and c["solution_id"] != "S-NT-A"]
    assert len(sel) == 33
    m = load_mapping()["solution_to_action"]
    for s in sel:
        codes = action_codes(s)
        assert codes and all(c in Q.QUESTIONS["action"][1] for c in codes), s
    assert sum(1 for s in sel if s in m) == 15 and sum(1 for s in sel if s not in m) == 18       # 설계서(2026-10-09): 대응 15 / 미대응 18
    assert action_codes("S-T01-A") == ["other"] and action_codes("S-T07-D") == ["manager"] and action_codes("S-T06-D") == ["apology", "fee"]


def test_mapped_solutions_exist_in_catalog():
    cat = {c["solution_id"] for c in load_catalog(ROOT / "data/solution_catalog.json")}
    assert set(load_mapping()["solution_to_action"]) <= cat


def test_check_mapping_reports_bad_codes():
    m = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in load_mapping().items()}
    m["type_to_issue"]["T01"] = "zzz"; m["solution_to_action"]["S-T01-C"] = ["nope"]; m["issue_to_wanted"]["cost"] = ["fee"]
    errs = check_mapping(m)
    assert any("불편 코드 zzz" in e for e in errs) and any("action 코드 nope" in e for e in errs) and any("주·부 2개" in e for e in errs)


def test_rng_is_reproducible_and_depends_on_inputs():
    a = [make_rng("S1", "V1").random() for _ in range(3)]
    assert a == [make_rng("S1", "V1").random() for _ in range(3)] and a[0] != make_rng("S2", "V1").random() and a[0] != make_rng("S1", "V2").random()
    assert make_rng("S1", "V1", "x").random() != make_rng("S1", "V1", "y").random()
    r1, r2 = make_rng("S1", "V1"), make_rng("S1", "V1")
    assert [pick(r1, {"a": 1, "b": 2, "c": 3}) for _ in range(20)] == [pick(r2, {"a": 1, "b": 2, "c": 3}) for _ in range(20)]


def test_pick_follows_weights():
    r = make_rng("S", "V"); n = 4000; c = {"a": 0, "b": 0}
    for _ in range(n): c[pick(r, {"a": 0.7, "b": 0.3})] += 1
    assert abs(c["a"] / n - 0.7) < 0.03
    with pytest.raises(ValueError): pick(r, {"a": 0})


def test_config_probabilities_sum_to_one():
    c = load_config()
    for k in ("issues_k", "issue_source", "status_post_churn"): assert abs(sum(c[k].values()) - 1) < 1e-9, k
    for k, v in c["status_given_before_churn"].items(): assert abs(sum(v.values()) - 1) < 1e-9, k
    for k, v in c["form_given_events"].items(): assert abs(sum(v.values()) - 1) < 1e-9, k


def test_yaml_keys_are_strings():
    """YAML 1.1은 no/yes/on/off 를 불리언으로 읽는다. 설문 코드 'no'가 False로 바뀌는 사고를 막는다."""
    def walk(x, path=""):
        if isinstance(x, dict):
            for k, v in x.items():
                assert isinstance(k, (str, int)) and not isinstance(k, bool), f"{path}/{k!r}: 키가 불리언"
                walk(v, f"{path}/{k}")
        elif isinstance(x, list):
            for i in x: walk(i, path)
    walk(load_config()); walk(load_mapping())
