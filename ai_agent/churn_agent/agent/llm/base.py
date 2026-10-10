from __future__ import annotations
from typing import Protocol


class LlmClient(Protocol):
    def generate(self, inp: dict, note: str | None, timeout: float) -> str:
        """검증기 입력(inp)을 받아 JSON 문자열을 돌려준다. 타임아웃은 TimeoutError, 그 외 실패는 예외."""


class LlmConfigError(RuntimeError):
    """설정 오류(예: API 키 없음). 앱은 기동하되 LLM 없이 템플릿으로 동작한다."""


class LlmUnavailable(RuntimeError):
    """LLM을 쓸 수 없는 상태. 재시도하지 않고 곧바로 템플릿으로 간다."""


class UnavailableLlm:
    """키가 없거나 설정이 잘못됐을 때 대신 쓰는 클라이언트."""
    def __init__(self, reason: str): self.reason = reason
    def generate(self, inp, note, timeout): raise LlmUnavailable(self.reason)
