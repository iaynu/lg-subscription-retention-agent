"""측정 스크립트: mock으로 끝까지 돌고, 결과 파일에 키 모양 문자열이 없으며, 오류 문자열의 키 조각을 지운다."""
import json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def test_measure_runs_with_mock_and_scrubs(tmp_path):
    inp = ROOT / "data" / "demo_inputs_12.json"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "measure_llm.py"), "--inputs", str(inp), "--provider", "mock", "--repeats", "1", "--out", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rows = json.loads((tmp_path / "llm_measure_mock.json").read_text(encoding="utf-8")); assert len(rows) == 12 and all(x["outcome"] != "error" for x in rows)
    assert ("s" + "k-") not in (tmp_path / "llm_measure_mock.md").read_text(encoding="utf-8")


def test_scrub_removes_key_fragments():
    sys.path.insert(0, str(ROOT / "scripts")); import measure_llm
    assert "abcdef123456" not in measure_llm.scrub("Incorrect API key provided: " + "s" + "k-abcdef123456XYZ") and "abcdef123456" not in measure_llm.scrub("Bearer abcdef123456")


def test_no_openai_call_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "measure_llm.py"), "--inputs", str(ROOT / "data" / "demo_inputs_12.json"), "--provider", "openai", "--out", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 2 and "OPENAI_API_KEY" in r.stdout


def test_failure_messages_carry_solution_id():
    """검증 실패 메시지에 솔루션 ID가 붙는다(원인 분석용)."""
    sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts")); import measure_llm
    from agent.catalog import load_catalog; from agent.config import ROOT as AROOT, load_config
    from agent.llm.mock import RuleBasedMock; from agent.repo.memory import MemoryRepo; from agent.schemas import AgentInput; from agent.service import AgentService

    class Corrupt(RuleBasedMock):
        def generate(self, inp, note, timeout):
            d = json.loads(super().generate(inp, note, timeout))
            for it in d["selected"]: it["evidence"] = [{"feature": "bogus_feature", "value": 1}]
            return json.dumps(d, ensure_ascii=False)
    cfg = load_config(); measure_llm.FAILS.clear()
    case = json.loads((ROOT / "data" / "demo_inputs_12.json").read_text(encoding="utf-8"))["cases"][0]
    AgentService(cfg, load_catalog(AROOT / "data/solution_catalog.json"), Corrupt(), MemoryRepo()).run(AgentInput(**case["input"]).model_dump())
    e2 = [f for f in measure_llm.FAILS if f[0] == "E2"]
    assert e2 and all(f[2].startswith("S-") for f in e2)


def test_prompt_variants_switch_and_restore():
    sys.path.insert(0, str(ROOT)); from agent.llm import prompt as p
    try:
        p.set_variant("base"); assert "[최종 점검]" not in p.SYSTEM_PROMPT and p._EVIDENCE_NAMES is False
        p.set_variant("v3.2"); assert "C8." in p.SYSTEM_PROMPT and "C9." not in p.SYSTEM_PROMPT
        p.set_variant("v3.2c9"); assert "C9." in p.SYSTEM_PROMPT and p._EVIDENCE_NAMES is False
        p.set_variant("v3.3"); assert "evidence_allowed_features" in p.SYSTEM_PROMPT and p._EVIDENCE_NAMES is True
        p.set_variant("v3.4"); assert "C9." in p.SYSTEM_PROMPT and p._EVIDENCE_NAMES is False
        import pytest
        with pytest.raises(ValueError): p.set_variant("nope")
    finally:
        p.set_variant("v3.4")
