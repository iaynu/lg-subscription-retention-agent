"""OpenAI 어댑터. API 키는 환경변수에서만 읽는다(코드·설정 파일·로그에 쓰지 않는다).

- Chat Completions + Structured Outputs(json_schema, strict). solution_id는 이번 후보로 enum 제한.
- temperature는 설정했을 때만 보낸다(추론형 모델 일부는 기본값 외 temperature를 거부함).
- SDK 자동 재시도는 끄고(max_retries=0) 재시도·전체 시간 한도는 validation/fallback.py가 통제한다.
- 오류의 status_code(429·5xx 등)는 그대로 올려 보내 fallback이 재시도 여부를 판단한다.
"""
from __future__ import annotations
import os
from .base import LlmConfigError
from . import prompt as _prompt
from .prompt import build_user_message, response_schema


class OpenAIClient:
    def __init__(self, model: str = "gpt-4o-mini", api_key_env: str = "OPENAI_API_KEY", temperature: float | None = None,
                 client=None, base_url_env: str | None = "OPENAI_BASE_URL"):
        self.model, self.temperature = model, temperature
        if client is None:
            key = os.environ.get(api_key_env)
            if not key: raise LlmConfigError(f"환경변수 {api_key_env}가 없습니다.")
            from openai import OpenAI
            kw = dict(api_key=key, max_retries=0)
            if base_url_env and os.environ.get(base_url_env): kw["base_url"] = os.environ[base_url_env]
            client = OpenAI(**kw)
        self.client = client; self.last_usage = None

    def generate(self, inp: dict, note: str | None, timeout: float) -> str:
        kw = dict(model=self.model, timeout=timeout,
                  messages=[{"role": "system", "content": _prompt.SYSTEM_PROMPT},
                            {"role": "user", "content": build_user_message(inp, note)}],
                  response_format={"type": "json_schema",
                                   "json_schema": {"name": "agent_output", "strict": True, "schema": response_schema(inp)}})
        if self.temperature is not None: kw["temperature"] = self.temperature
        try:
            r = self.client.chat.completions.create(**kw)
        except Exception as e:
            if type(e).__name__ in ("APITimeoutError", "Timeout", "TimeoutException"): raise TimeoutError() from e
            raise
        if not getattr(r, "choices", None): raise RuntimeError("empty choices")
        u = getattr(r, "usage", None)                                                        # 측정용: 마지막 호출의 토큰 수(없으면 None)
        self.last_usage = {"prompt": getattr(u, "prompt_tokens", None), "completion": getattr(u, "completion_tokens", None)} if u is not None else None
        ch = r.choices[0]
        if getattr(ch.message, "refusal", None): raise RuntimeError("model refusal")
        if ch.finish_reason == "length": raise RuntimeError("output truncated")
        if ch.finish_reason == "content_filter": raise RuntimeError("content filtered")
        return ch.message.content or ""
