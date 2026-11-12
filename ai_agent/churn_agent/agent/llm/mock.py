"""테스트·오프라인용 LLM. RuleBasedMock은 후보 순서대로 템플릿 문구를 JSON으로 낸다(검증 통과 보장용 기준선)."""
from __future__ import annotations
import json
from ..validation.fallback import template_item, SUMMARY_TPL

class RuleBasedMock:
    status = "mock"

    def generate(self, inp, note, timeout):
        items = []
        for i, c in enumerate(inp["candidates"][:inp["constraints"]["max_actions"]]):
            it = template_item(c, i + 1, inp); it.pop("message_source", None)
            it["selection_reason"] = "규칙 엔진이 허용한 후보를 유형 기여도와 우선순위 순서로 제안합니다."
            items.append(it)
        return json.dumps({"selected": items, "summary": SUMMARY_TPL}, ensure_ascii=False)

class ScriptedLlm:
    """호출마다 script의 다음 항목을 돌려준다. 항목: {"output": dict} | {"raw": str} | {"error": "timeout"|메시지}"""
    status = "scripted"

    def __init__(self, script): self._it = iter(script); self.calls = []
    def generate(self, inp, note, timeout):
        self.calls.append(note)
        x = next(self._it)
        if "error" in x: raise TimeoutError() if x["error"] == "timeout" else RuntimeError(x["error"])
        return x["raw"] if "raw" in x else json.dumps(x["output"], ensure_ascii=False)
