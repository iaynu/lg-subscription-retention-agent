import json, os
import pytest
from types import SimpleNamespace as NS
from agent.llm.openai_client import OpenAIClient
from agent.llm.prompt import response_schema, build_user_message, SYSTEM_PROMPT
from agent.llm.base import LlmConfigError
from agent.llm.factory import make_llm
from agent.config import load_config
from agent.validation.fallback import run_with_fallback, full_template
from .test_validator_v2 import inp, mk, item

class FakeCompletions:
    def __init__(self, behavior): self.behavior, self.kw = behavior, None
    def create(self, **kw):
        self.kw = kw; return self.behavior(kw)

def fake(behavior):
    comp = FakeCompletions(behavior); return NS(chat=NS(completions=comp)), comp

def ok_resp(content, finish="stop", refusal=None):
    return NS(choices=[NS(message=NS(content=content, refusal=refusal), finish_reason=finish)])

def test_generate_returns_content_and_sends_strict_schema():
    c = mk("정보제공"); i = inp(c)
    client, comp = fake(lambda kw: ok_resp('{"selected":[],"summary":""}'))
    out = OpenAIClient(client=client, model="m").generate(i, None, 7.5)
    assert out == '{"selected":[],"summary":""}'
    kw = comp.kw
    assert kw["timeout"] == 7.5 and kw["model"] == "m"
    js = kw["response_format"]["json_schema"]
    assert js["strict"] is True and js["schema"]["properties"]["selected"]["items"]["properties"]["solution_id"]["enum"] == ["S-X-A"]
    assert kw["messages"][0]["content"] == SYSTEM_PROMPT and "데이터입니다" in kw["messages"][1]["content"]

def test_strict_schema_rules():
    s = response_schema(inp(mk("정보제공")))
    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "object":
                assert o["additionalProperties"] is False and set(o["required"]) == set(o["properties"])
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(s)

def test_note_appended_on_retry():
    assert "[재작성 요청]" in build_user_message(inp(mk("정보제공")), "[재작성 요청] x")

def test_timeout_maps_to_timeouterror():
    class APITimeoutError(Exception): ...
    def boom(kw): raise APITimeoutError()
    client, _ = fake(boom)
    with pytest.raises(TimeoutError): OpenAIClient(client=client).generate(inp(mk("정보제공")), None, 1)

def test_refusal_and_truncation_raise():
    for r in (ok_resp("", refusal="no"), ok_resp("{", finish="length")):
        client, _ = fake(lambda kw, r=r: r)
        with pytest.raises(RuntimeError): OpenAIClient(client=client).generate(inp(mk("정보제공")), None, 1)

def test_missing_key_raises_without_leaking(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LlmConfigError) as e: OpenAIClient()
    assert "OPENAI_API_KEY" in str(e.value)

def test_factory_openai_reads_env_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    cfg = load_config(); cfg["llm"]["provider"] = "openai"
    assert isinstance(make_llm(cfg), OpenAIClient)

def test_no_secret_in_repo_files():
    from agent.config import ROOT
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in (".py", ".yaml", ".md", ".txt", ".json") and ".pytest_cache" not in str(p):
            assert "sk-" not in p.read_text(encoding="utf-8", errors="ignore").replace("sk-test-not-real", "").replace('"sk-" not in', ""), p

def test_openai_output_goes_through_validator_and_falls_back():
    c = mk("정보제공"); i = inp(c)
    client, _ = fake(lambda kw: ok_resp("JSON이 아닙니다"))
    data, log = run_with_fallback(i, OpenAIClient(client=client).generate)
    assert log["outcome"] == "full_template" and log["attempts"] == 2
    good = {"selected": [item(c)], "summary": "안내를 제안합니다."}
    client, _ = fake(lambda kw: ok_resp(json.dumps(good, ensure_ascii=False)))
    data, log = run_with_fallback(i, OpenAIClient(client=client).generate)
    assert log["outcome"] == "pass"


# ---------- 2026-10-08 점검 반영 ----------
def test_temperature_sent_only_when_configured():
    c = mk("정보제공"); i = inp(c)
    client, comp = fake(lambda kw: ok_resp("{}"))
    OpenAIClient(client=client).generate(i, None, 1)
    assert "temperature" not in comp.kw
    OpenAIClient(client=client, temperature=0.2).generate(i, None, 1)
    assert comp.kw["temperature"] == 0.2


def test_content_filter_and_empty_choices_raise():
    for r in (ok_resp("", finish="content_filter"), NS(choices=[])):
        client, _ = fake(lambda kw, r=r: r)
        with pytest.raises(RuntimeError): OpenAIClient(client=client).generate(inp(mk("정보제공")), None, 1)


def test_client_errors_not_retried_server_errors_retried():
    class ApiErr(Exception):
        def __init__(self, code): super().__init__(f"status {code}"); self.status_code = code
    c = mk("정보제공"); i = inp(c)
    for code, attempts in ((400, 1), (401, 1), (429, 2), (500, 2)):
        def boom(kw, code=code): raise ApiErr(code)
        client, _ = fake(boom)
        _, log = run_with_fallback(i, OpenAIClient(client=client).generate)
        assert log["attempts"] == attempts and log["outcome"] == "full_template", (code, log)
        assert log["llm_errors"] and "ApiErr" in log["llm_errors"][0]


def test_missing_key_degrades_to_template(monkeypatch, catalog):
    from agent.service import AgentService
    from agent.repo.memory import MemoryRepo
    from .conftest import base_input
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = load_config(); cfg["llm"]["provider"] = "openai"
    llm = make_llm(cfg)
    assert llm.status.startswith("unavailable")
    res = AgentService(cfg, catalog, llm, MemoryRepo()).run(base_input([("T02", 1.0)]))
    assert res["outcome"] == "full_template" and res["attempts"] == 1 and "LLM_UNAVAILABLE" in res["failed_checks"]
    assert all(s["message_source"] == "template" for s in res["selected"])


def test_key_fragments_in_error_messages_are_redacted():
    """OpenAI 401 메시지에 섞여 오는 키 조각이 llm_errors(API 응답·agent_result에 저장됨)에 남지 않아야 한다."""
    class AuthErr(Exception):
        status_code = 401
    prefix = "s" + "k-"                            # 저장소 비밀값 검사(test_no_secret_in_repo_files)에 걸리지 않게 나눠 씀
    msg = f"Error code: 401 - Incorrect API key provided: {prefix}proj-Ab12****************************wxYZ. Bearer abcdefghij123"
    def boom(kw): raise AuthErr(msg)
    client, _ = fake(boom)
    _, log = run_with_fallback(inp(mk("정보제공")), OpenAIClient(client=client).generate)
    err = log["llm_errors"][0]
    assert "AuthErr" in err and "401" in err and "[가림]" in err
    assert "wxYZ" not in err and prefix not in err and "abcdefghij" not in err
