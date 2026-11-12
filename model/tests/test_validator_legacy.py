import json, copy, sys
import pytest
from agent.validation.validator import *
from .fixtures import *

def run(name, inp, out, expect_rules=None, raw=None):
    res = validate(raw if raw is not None else json.dumps(out, ensure_ascii=False), inp)
    hard = sorted({r for r, i, m, s in res["failures"] if s == HARD})
    soft = sorted({r for r, i, m, s in res["failures"] if s == SOFT})
    if expect_rules is None: ok = res["ok"] and not hard
    else: ok = all(any(h.startswith(e) for h in hard + soft) for e in expect_rules) and (not res["ok"] if any(e for e in expect_rules if not e.startswith("soft:")) else True)
    return ok, name, hard, soft

def mut(base, fn):
    o = copy.deepcopy(base); fn(o); return o
T = []
# 정상
T.append(run("정상 A", INP_A, OUT_A))
T.append(run("정상 B(NO_TYPE)", INP_B, OUT_B))
T.append(run("코드블록 감싸기는 자동 복구", INP_A, None, raw="```json\n" + json.dumps(OUT_A, ensure_ascii=False) + "\n```"))
# 위반
c = []
c.append(("R1 후보 밖 ID", INP_A, mut(OUT_A, lambda o: o["selected"][1].update(solution_id="S-9-9-Z")), ["R1"]))
c.append(("R1_COUNT 3개 선택", INP_A, mut(OUT_A, lambda o: o["selected"].append(copy.deepcopy(o["selected"][0]) | {"priority": 3})), ["R1_COUNT"]))
c.append(("R1_EMPTY 빈 선택", INP_A, mut(OUT_A, lambda o: o.update(selected=[])), ["R1_EMPTY"]))
c.append(("R1_TYPE type_id 불일치", INP_A, mut(OUT_A, lambda o: o["selected"][0].update(type_id="T03")), ["R1_TYPE"]))
c.append(("R2 지어낸 금액", INP_A, mut(OUT_A, lambda o: o["selected"][1].update(message_text="계약 만료가 가까워 월 20,000원 할인 혜택을 안내드립니다. 총 비용과 위약금 조건을 함께 안내드리며 신청을 원하시면 말씀해 주세요.")), ["R2"]))
c.append(("R2 한글 금액 6만 8천 원은 허용", INP_A, mut(OUT_A, lambda o: o["selected"][0].update(message_text="고객님, 월 요금 6만 8천 원 기준 총 납부 금액을 안내드립니다. 자세한 내용은 앱 또는 상담으로 확인하실 수 있습니다.")), None))
c.append(("R2 한글 금액 5만 원은 불가", INP_A, mut(OUT_A, lambda o: o["selected"][0].update(message_text="고객님, 월 요금 5만 원 기준 총 납부 금액을 안내드립니다. 자세한 내용은 앱 또는 상담으로 확인하실 수 있습니다.")), ["R2"]))
c.append(("M3 문의 횟수 노출", INP_A, mut(OUT_A, lambda o: o["selected"][0].update(message_text="고객님, 위약금을 4번 문의해 주셨습니다. 위약금 산정 방식과 총 납부 금액을 안내드립니다. 자세한 내용은 앱 또는 상담으로 확인하실 수 있습니다.")), ["M3"]))
c.append(("M3 이탈 표현", INP_A, mut(OUT_A, lambda o: o["selected"][1].update(message_text="이탈 위험이 높아 갱신 혜택을 안내드립니다. 총 비용과 위약금 조건을 함께 안내드리며 신청을 원하시면 말씀해 주세요.")), ["M3"]))
c.append(("M7 금지어 무조건", INP_A, mut(OUT_A, lambda o: o["selected"][1].update(message_text="무조건 갱신하시면 좋습니다. 총 비용과 위약금 조건을 함께 안내드리며 신청을 원하시면 말씀해 주세요.")), ["M7"]))
c.append(("M7 띄어쓰기 변형 '지금 아니면 안'", INP_A, mut(OUT_A, lambda o: o["selected"][1].update(message_text="지금 아니면 안 됩니다. 총 비용과 위약금 조건을 함께 안내드리며 신청을 원하시면 말씀해 주세요.")), ["M7"]))
c.append(("M1 400자 초과", INP_A, mut(OUT_A, lambda o: o["selected"][0].update(message_text="고객님, " + "위약금 산정 방식과 총 납부 금액을 자세히 안내드립니다. " * 14 + "앱 또는 상담으로 확인하실 수 있습니다.")), ["M1"]))
c.append(("M4 혜택에 총비용 없음", INP_A, mut(OUT_A, lambda o: o["selected"][1].update(message_text="갱신 시 이용하실 수 있는 혜택을 안내드립니다. 신청을 원하시면 말씀해 주세요.")), ["M4"]))
c.append(("M6 확정 지급", INP_A, mut(OUT_A, lambda o: o["selected"][1].update(message_text="갱신 혜택이 지급됩니다. 총 비용과 위약금 조건을 함께 안내드리며 신청을 원하시면 말씀해 주세요.")), ["M6"]))
c.append(("M8 정보제공에 확인 방법 없음", INP_A, mut(OUT_A, lambda o: o["selected"][0].update(message_text="위약금 산정 방식과 총 납부 금액을 안내드립니다. 궁금하신 점이 있으시면 알려 주세요.")), ["M8"]))
c.append(("M2 후보에 없는 혜택(무료 설치)", INP_A, mut(OUT_A, lambda o: o["selected"][1].update(message_text="갱신 시 무료 설치 혜택을 안내드립니다. 총 비용과 위약금 조건을 함께 안내드리며 신청을 원하시면 말씀해 주세요.")), ["M2"]))
c.append(("E3 선택 이유 숫자 불일치", INP_A, mut(OUT_A, lambda o: o["selected"][0].update(selection_reason="최근 90일 위약금 문의가 7회로 반복되어 안내가 우선입니다.")), ["E3"]))
c.append(("E2 evidence 값 불일치", INP_A, mut(OUT_A, lambda o: o["selected"][0].update(evidence=[{"feature": "voc_penalty_cnt_90d", "value": 5}])), ["E2"]))
c.append(("E2 evidence 피처 입력에 없음", INP_A, mut(OUT_A, lambda o: o["selected"][0].update(evidence=[{"feature": "as_cnt_365d", "value": 3}])), ["E2"]))
c.append(("LEAK 솔루션 ID가 문구에", INP_A, mut(OUT_A, lambda o: o["selected"][0].update(message_text="고객님, S-T02-A 안내입니다. 위약금 산정 방식과 총 납부 금액은 앱 또는 상담으로 확인하실 수 있습니다.")), ["LEAK"]))
c.append(("LEAK 유형 ID가 문구에", INP_A, mut(OUT_A, lambda o: o["selected"][0].update(message_text="고객님, T02 유형 안내입니다. 위약금 산정 방식과 총 납부 금액은 앱 또는 상담으로 확인하실 수 있습니다.")), ["LEAK"]))
c.append(("LANG 한자 혼입", INP_A, mut(OUT_A, lambda o: o["selected"][0].update(message_text="고객님, 違約金 산정 방식과 총 납부 금액을 안내드립니다. 자세한 내용은 앱 또는 상담으로 확인하실 수 있습니다.")), ["LANG"]))
c.append(("SCHEMA priority 중복", INP_A, mut(OUT_A, lambda o: o["selected"][1].update(priority=1)), ["SCHEMA"]))
c.append(("N1 NO_TYPE에서 불만 단정", INP_B, mut(OUT_B, lambda o: o["selected"][0].update(message_text="고객님, 불만이 많으신 것으로 보여 설문을 안내드립니다. 의견을 들려주시면 반영하겠습니다.")), ["N1"]))
c.append(("N2 NO_TYPE에서 평서형 단정", INP_B, mut(OUT_B, lambda o: o["selected"][0].update(message_text="고객님, 만족도 설문에 참여해 주세요. 설문 의견은 개선에 반영합니다.")), ["N2"]))
# fee_impact 증가
INP_C = copy.deepcopy(INP_A); INP_C["candidates"][1].update(fee_impact="증가", category="혜택제안", name="상위 제품군 변경 제안", detail="상위 제품군으로 변경 안내", message_hint="구독료 변동과 총 비용 안내")
c.append(("M5 fee_impact 증가인데 구독료 변동 언급 없음", INP_C, OUT_A, ["M5"]))
OUT_C = copy.deepcopy(OUT_A); OUT_C["selected"][1]["message_text"] = "상위 제품군으로 변경하시면 월 구독료가 달라질 수 있습니다. 기존 구독 유지 여부와 총 비용을 함께 확인해 드리며, 신청을 원하시면 말씀해 주세요."
T.append(run("M5 충족(정상)", INP_C, OUT_C))
for name, inp, out, exp in c: T.append(run(name, inp, out, exp))

@pytest.mark.parametrize("ok,name,hard,soft", T, ids=[x[1] for x in T])
def test_validator(ok, name, hard, soft):
    assert ok, (name, hard, soft)
