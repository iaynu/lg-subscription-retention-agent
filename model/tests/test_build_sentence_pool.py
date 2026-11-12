import json, importlib.util, sys
from pathlib import Path
import pytest
from feedback import pool as P

spec = importlib.util.spec_from_file_location("bsp", Path(__file__).resolve().parents[1] / "scripts" / "build_sentence_pool.py")
bsp = importlib.util.module_from_spec(spec); sys.modules["bsp"] = bsp; spec.loader.exec_module(bsp)


class Msg:  # 가짜 OpenAI 응답
    def __init__(s, content, finish="stop", refusal=None): s.message = type("M", (), {"content": content, "refusal": refusal})(); s.finish_reason = finish


class Fake:
    def __init__(self, variants=None, finish="stop", exc=None):
        self.calls = []; self.v = variants; self.finish = finish; self.exc = exc
        self.chat = type("C", (), {"completions": self})()
    def create(self, **kw):
        self.calls.append(kw)
        if self.exc: raise self.exc
        v = self.v if self.v is not None else [f"{kw['messages'][1]['content'][:6]}변형 문장 {i}번이에요" for i in range(30)]
        return type("R", (), {"choices": [Msg(json.dumps({"variants": v}, ensure_ascii=False), self.finish)]})()


def test_clean_filters_pii_duplicates_and_limits():
    seeds = ["가입할 때 안내받은 금액보다 청구 금액이 올라서 당황스러웠어요."]
    raw = ["청구 금액이 안내와 달라서 놀랐어요", seeds[0], "010-1234-5678 로 연락 주세요", "청구 금액이 안내와 달라서 놀랐어요", "", "요금이 갑자기 올라서 당황했어요", "x" * 300, "할인이 끝나고 요금이 늘었어요"]
    out, rej = bsp.clean("cost", seeds, raw, 2)
    assert out == ["청구 금액이 안내와 달라서 놀랐어요", "요금이 갑자기 올라서 당황했어요"]
    assert {w for _, w in rej} >= {"중복", "개인정보 형태", "빈 문장"}


def test_run_uses_one_call_per_code_and_passes_checks():
    f = Fake(); res = bsp.run(f, "m", 10)
    assert len(f.calls) == len(P.required_keys()) == 15
    assert all("response_format" in c and c["response_format"]["json_schema"]["strict"] for c in f.calls)
    assert all(len(v) <= 10 for v in res["variants"].values()) and res["variants"]["issue_other"]
    for k, v in res["variants"].items():
        for s in v: assert P.check_sentence(s) is None


def test_generate_errors_do_not_leak_key():
    f = Fake(exc=RuntimeError("401 bad key " + "s" + "k-abcdefghijklmnop1234"))
    with pytest.raises(RuntimeError) as e: bsp.generate(f, "m", "cost", ["가입 시 안내가 달랐어요."], 5)
    assert ("s" + "k-abcdefghij") not in str(e.value) and "[KEY]" in str(e.value)
    with pytest.raises(RuntimeError): bsp.generate(Fake(finish="length"), "m", "cost", ["가입 시 안내가 달랐어요."], 5)


def test_prompt_has_no_secrets_and_dry_run(capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert bsp.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "=== H-A ===" in out and ("s" + "k-") not in out
    assert bsp.main([]) == 2                                                  # 키 없으면 호출하지 않고 종료


def test_candidate_file_is_not_loaded_by_pool():
    assert P.VARIANTS_FILE.name == "sentence_pool_variants.yaml" and "candidate" not in P.VARIANTS_FILE.name
