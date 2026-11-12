from __future__ import annotations
import logging
from .base import LlmConfigError, UnavailableLlm
from .mock import RuleBasedMock
from .openai_client import OpenAIClient

log = logging.getLogger(__name__)


def make_llm(cfg: dict):
    """설정에 맞는 LLM 클라이언트. OpenAI 키가 없으면 앱은 기동하고 템플릿으로만 동작한다(/health에 표시)."""
    l = cfg["llm"]
    if l["provider"] == "mock":
        llm = RuleBasedMock(); llm.status = "mock"; return llm
    if l["provider"] == "openai":
        try:
            llm = OpenAIClient(l["model"], l["api_key_env"], l.get("temperature"))
            llm.status = "ready"; return llm
        except LlmConfigError as e:
            log.warning("LLM 비활성: %s → 템플릿으로 동작", e)
            llm = UnavailableLlm(str(e)); llm.status = f"unavailable: {e}"; return llm
    raise ValueError(f"unknown llm provider: {l['provider']}")
