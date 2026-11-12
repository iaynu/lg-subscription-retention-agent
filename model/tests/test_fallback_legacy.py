import json, copy
import pytest
from agent.validation.validator import validate, HARD
from agent.validation.fallback import *
from .fixtures import *
def test_templates_pass_validator():
    ok = True
    # 1) 템플릿이 검증기를 100% 통과하는지 (모든 구분 + fee_impact 증가)
    cases = []
    for cat, fi in [("정보제공","없음"),("담당자조치","없음"),("혜택제안","없음"),("제품조치","없음"),("혜택제안","증가"),("정보제공","증가")]:
        inp = copy.deepcopy(INP_A); inp["candidates"] = [dict(inp["candidates"][0], category=cat, fee_impact=fi, name="테스트 솔루션", detail="", message_hint="")]
        inp["constraints"]["max_actions"] = 1
        out = full_template(inp); res = validate(json.dumps(out, ensure_ascii=False), inp)
        h = sorted({r for r,i,m,s in res["failures"] if s==HARD}); print("템플릿", cat, fi, "->", "통과" if res["ok"] else h); ok &= res["ok"]
    res = validate(json.dumps(full_template(INP_B), ensure_ascii=False), INP_B); print("템플릿 의견수집(NO_TYPE) ->", "통과" if res["ok"] else res["failures"]); ok &= res["ok"]
    res = validate(json.dumps(full_template(INP_A), ensure_ascii=False), INP_A); print("템플릿 A 전체 ->", "통과" if res["ok"] else res["failures"]); ok &= res["ok"]
    
    assert ok

# 2) 흐름 시나리오 (모의 LLM)
good = json.dumps(OUT_A, ensure_ascii=False)
bad1 = copy.deepcopy(OUT_A); bad1["selected"][1]["message_text"] = "무조건 갱신하세요. 총 비용과 위약금 조건 신청 말씀해 주세요."
bad_json = "죄송합니다 JSON을 만들 수 없습니다"
bad_id = copy.deepcopy(OUT_A); bad_id["selected"][1]["solution_id"] = "S-9-9-Z"
def mk(seq):
    it = iter(seq)
    def f(inp, note, timeout):
        x = next(it)
        if isinstance(x, Exception): raise x
        return x
    return f
scen = [
 ("1회 통과", [good], "pass", 1),
 ("1회 실패→재시도 통과", [json.dumps(bad1, ensure_ascii=False), good], "retry_pass", 2),
 ("두 번 모두 한 항목만 실패→부분 템플릿", [json.dumps(bad1, ensure_ascii=False)]*2, "partial_template", 2),
 ("JSON 아님 두 번→전체 템플릿", [bad_json, bad_json], "full_template", 2),
 ("후보에 없는 solution_id 두 번→전체 템플릿(항목 단위 복구 불가)", [json.dumps(bad_id, ensure_ascii=False)]*2, "full_template", 2),
 ("타임아웃→재시도 없이 전체 템플릿", [TimeoutError()], "full_template", 1),
 ("API 오류 1회 후 정상", [RuntimeError("500"), good], "retry_pass", 2),
]

@pytest.mark.parametrize("name,seq,exp,n", scen, ids=[s[0] for s in scen])
def test_flow(name, seq, exp, n):
    data, log = run_with_fallback(INP_A, mk(seq))
    res = validate(json.dumps(data, ensure_ascii=False), INP_A)
    assert log["outcome"] == exp and log["attempts"] == n and res["ok"], (log, res["failures"])
