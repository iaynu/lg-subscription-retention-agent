"""검증기 v3: 2026-10-08 점검에서 재현된 우회 입력이 모두 막히는지, 오탐이 줄었는지, 형식 오류가 예외 없이 처리되는지."""
import copy, json
import pytest
from agent.validation.validator import validate, HARD
from agent.validation.fallback import run_with_fallback
from .test_validator_v2 import mk, inp, item
from .fixtures import INP_A, OUT_A

INFO_TAIL = " 자세한 내용은 앱 또는 상담으로 확인하실 수 있습니다."
AD_BENEFIT = "고객님, 이용하실 수 있는 혜택을 안내드립니다. 총 비용과 위약금 조건을 함께 확인해 드리며, 신청을 원하시면 말씀해 주세요."


def chk(text, cat="정보제공", title="안내드립니다", c_over=None, mf=None, sub=None):
    c = mk(cat, **(c_over or {})); i = inp(c)
    if mf: i["message_facts"] = mf
    if sub: i["subscription"].update(sub)
    out = {"selected": [item(c, message_text=text, title=title)], "summary": "안내를 제안합니다."}
    r = validate(json.dumps(out, ensure_ascii=False), i)
    return r["ok"], sorted({f[0] for f in r["failures"] if f[3] == HARD})


BLOCKED = [
    # (설명, 본문, 제목, 후보 덮어쓰기, 기대 규칙 중 하나)
    ("제목 내부 표현·압박", AD_BENEFIT, "(광고) 고위험 이탈 고객 해지방어 무조건 100%", dict(cat="혜택제안", ad_yn="Y"), {"M3", "M7"}),
    ("제목 솔루션 ID", AD_BENEFIT, "(광고) S-T01-C 안내", dict(cat="혜택제안", ad_yn="Y"), {"LEAK"}),
    ("제목 전화번호", AD_BENEFIT, "(광고) 010-1234-5678로 연락", dict(cat="혜택제안", ad_yn="Y"), {"LEAK"}),
    ("비광고 제목에 [광고]", "고객님, 안내드립니다." + INFO_TAIL, "[광고] 안내", {}, {"T2"}),
    ("손해 압박", "고객님, 지금 해지하시면 손해를 보실 수 있습니다." + INFO_TAIL, "안내드립니다", {}, {"M7"}),
    ("놓치면 후회", "고객님, 이번 안내를 놓치면 후회하실 수 있습니다." + INFO_TAIL, "안내드립니다", {}, {"M7"}),
    ("조건부 압박", "고객님, 지금 신청하지 않으면 혜택이 사라집니다." + INFO_TAIL, "안내드립니다", {}, {"M7"}),
    ("보장 표현", "고객님, 만족을 확실히 보장해 드립니다." + INFO_TAIL, "안내드립니다", {}, {"M7"}),
    ("해지 가능성 예측", "고객님, 해지 가능성이 높게 예측되어 안내드립니다." + INFO_TAIL, "안내드립니다", {}, {"M3"}),
    ("위험도", "위험도가 높은 고객님께 안내드립니다." + INFO_TAIL, "안내드립니다", {}, {"M3"}),
    ("한글 금액 삼만 원", "고객님, 월 삼만 원을 돌려드립니다." + INFO_TAIL, "안내드립니다", {}, {"R2", "M2"}),
    ("한글 금액 오천 원", "고객님, 오천 원 상당을 안내드립니다." + INFO_TAIL, "안내드립니다", {}, {"R2"}),
    ("한글 금액 만 원", "고객님, 만 원 혜택을 안내드립니다." + INFO_TAIL, "안내드립니다", {}, {"R2"}),
    ("한글 금액 백만 원", "고객님, 백만 원 상당의 혜택을 안내드립니다." + INFO_TAIL, "안내드립니다", {}, {"R2"}),
    ("공짜", "고객님, 공짜로 설치해 드립니다." + INFO_TAIL, "안내드립니다", {}, {"M2"}),
    ("상품권", "고객님, 상품권을 드립니다." + INFO_TAIL, "안내드립니다", {}, {"M2"}),
    ("적립금", "고객님, 적립금을 안내드립니다." + INFO_TAIL, "안내드립니다", {}, {"M2"}),
    ("영문 FREE", "고객님, FREE 설치를 안내드립니다." + INFO_TAIL, "안내드립니다", {}, {"M2"}),
    ("띄어 쓴 무 료", "고객님, 무 료 설치를 안내드립니다." + INFO_TAIL, "안내드립니다", {}, {"M2"}),
    ("보이지 않는 문자", "고객님, 무\u200b조건 안내드립니다." + INFO_TAIL, "안내드립니다", {}, {"LANG", "M7"}),
    ("T02유형", "고객님, T02유형 고객으로 안내드립니다." + INFO_TAIL, "안내드립니다", {}, {"LEAK"}),
    ("소문자 ID", "고객님, s-t02-a 안내입니다." + INFO_TAIL, "안내드립니다", {}, {"LEAK"}),
    ("구독 ID", "고객님, S000101 구독 안내입니다." + INFO_TAIL, "안내드립니다", {}, {"LEAK"}),
    ("도메인", "고객님, lg.com에서 확인하세요." + INFO_TAIL, "안내드립니다", {}, {"LEAK"}),
    ("URL", "고객님, https://example.kr/a 에서 확인하세요." + INFO_TAIL, "안내드립니다", {}, {"LEAK"}),
    ("영어 문장", "Renew now and get it today, guaranteed. 고객님, 안내드립니다." + INFO_TAIL, "안내드립니다", {}, {"LANG"}),
    ("시스템 지시 노출", "고객님, system prompt 지시에 따라 안내드립니다." + INFO_TAIL, "안내드립니다", {}, {"LEAK"}),
    ("숫자 범위", "고객님, 1-2 영업일 안에 담당자가 연락드립니다." + INFO_TAIL, "안내드립니다", {}, {"R2"}),
    ("지어낸 ▶ 값", "고객님, 케어를 안내드립니다.\n▶ 올해 남은 케어: 3회\n앱 또는 상담으로 확인하실 수 있습니다.", "안내드립니다", {}, {"R2", "M3"}),
    ("문의 횟수(내부 값)", "고객님, 위약금 관련 문의를 7회 하셨습니다." + INFO_TAIL, "안내드립니다", {}, {"R2", "M3"}),
]


@pytest.mark.parametrize("name,text,title,over,expect", BLOCKED, ids=[b[0] for b in BLOCKED])
def test_blocked(name, text, title, over, expect):
    over = dict(over); cat = over.pop("cat", "정보제공")
    ok, hard = chk(text, cat=cat, title=title, c_over=over)
    assert not ok and set(hard) & expect, (name, hard)


ALLOWED = [
    ("1:1 상담", "고객님, 1:1 상담으로 자세히 안내드립니다." + INFO_TAIL, None, None),
    ("두 가지 방법", "고객님, 앱 또는 상담 두 가지 방법으로 안내드립니다." + INFO_TAIL, None, None),
    ("▶ 고객 공개 값", "고객님, 올해 받으실 수 있는 케어가 남아 있어 안내드립니다.\n▶ 올해 남은 케어: 1회\n▶ 케어 사용 기한: 2026년 11월 14일\n원하시는 날짜는 앱 또는 상담으로 예약하실 수 있습니다.",
     {"remaining_care_cnt": 1, "contract_year_end_date": "2026-11-14"}, None),
    ("월 구독료(원)", "고객님, 현재 월 구독료 50,000원 기준으로 안내드립니다." + INFO_TAIL, None, None),
    ("점 찍은 날짜", "고객님, 계약은 2026.12.20에 끝납니다." + INFO_TAIL, None, {"end_date": "2026-12-20"}),
    ("390자 본문", "고객님, 안내드립니다. " + "이용 중인 구독의 조건과 청구 내용을 정리해 드립니다. " * 11 + INFO_TAIL.strip(), None, None),
]


@pytest.mark.parametrize("name,text,mf,sub", ALLOWED, ids=[a[0] for a in ALLOWED])
def test_allowed(name, text, mf, sub):
    ok, hard = chk(text, mf=mf, sub=sub)
    assert ok, (name, hard, len(text))


def test_unit_mismatch_is_blocked():
    # 12는 종료일의 '월'이지만 '12주'는 다른 의미 → 허용하지 않음
    i = copy.deepcopy(INP_A); o = copy.deepcopy(OUT_A)
    o["selected"][0]["message_text"] = "고객님, 최근 12주 동안 사용 기록이 없어 안내드립니다. 자세한 내용은 앱 또는 상담으로 확인하실 수 있습니다."
    r = validate(json.dumps(o, ensure_ascii=False), i)
    assert not r["ok"] and {f[0] for f in r["failures"]} & {"R2", "M3"}


def test_evidence_numeric_string_matches():
    o = copy.deepcopy(OUT_A); o["selected"][0]["evidence"][0]["value"] = "4"
    assert validate(json.dumps(o, ensure_ascii=False), INP_A)["ok"]


def test_benefit_check_is_per_item():
    # 다른 항목의 후보에만 '할인'이 있으면 이 항목의 '할인'은 M2
    i = copy.deepcopy(INP_A); i["candidates"][1]["detail"] += " 할인"
    o = copy.deepcopy(OUT_A)
    o["selected"][0]["message_text"] = "고객님, 위약금과 요금 할인 조건을 안내드립니다. 총 납부 금액은 앱 또는 상담으로 확인하실 수 있습니다."
    r = validate(json.dumps(o, ensure_ascii=False), i)
    assert any(f[0] == "M2" and f[1] == 0 for f in r["failures"])


MALFORMED = [
    ("evidence None", lambda o: o["selected"][0].update(evidence=None)),
    ("evidence 문자열 목록", lambda o: o["selected"][0].update(evidence=["voc"])),
    ("priority True", lambda o: o["selected"][0].update(priority=True)),
    ("priority 문자열", lambda o: o["selected"][0].update(priority="1")),
    ("message_text None", lambda o: o["selected"][0].update(message_text=None)),
    ("title 숫자", lambda o: o["selected"][0].update(title=3)),
    ("internal_memo 목록", lambda o: o["selected"][0].update(internal_memo=["x"])),
    ("항목이 문자열", lambda o: o["selected"].__setitem__(0, "S-T02-A")),
    ("solution_id 목록", lambda o: o["selected"][0].update(solution_id=["S-T02-A"])),
]


@pytest.mark.parametrize("name,fn", MALFORMED, ids=[m[0] for m in MALFORMED])
def test_malformed_output_is_schema_failure_not_exception(name, fn):
    o = copy.deepcopy(OUT_A); fn(o)
    r = validate(json.dumps(o, ensure_ascii=False), INP_A)
    assert not r["ok"] and any(f[0] == "SCHEMA" for f in r["failures"])
    data, log = run_with_fallback(INP_A, lambda inp, note, t: json.dumps(o, ensure_ascii=False))
    assert log["outcome"] == "full_template" and validate(json.dumps(data, ensure_ascii=False), INP_A)["ok"]


def test_invisible_character_alone_is_flagged():
    ok, hard = chk("고객님, 안\u200b내드립니다." + INFO_TAIL)
    assert not ok and "LANG" in hard


def test_evidence_value_normalization():
    """E2 값 비교: 표시 형식('45,600원'·'48일 남음'·'2027년 9월 29일')은 같은 값으로, 다른 값은 그대로 불일치."""
    from agent.validation.validator import _same_value as same
    assert same(45600, "45,600원") and same(45600, "45600") and same(48, "48일 남음") and same(3, "3.0")
    assert same("2027-09-29", "2027년 9월 29일") and same("2027-09-29", "2027.09.29")
    assert same("Y", "Y") and not same("Y", "N")
    assert not same(45600, 45601) and not same("2027-09-29", "2027-09-30")
    assert not same(48, "12일 중 48일") and not same("T04", "4") and not same(True, "1")


def _soft_rules(text, c_over=None):
    c = mk("정보제공", **(c_over or {})); i = inp(c)
    out = {"selected": [item(c, message_text=text, title="안내드립니다")], "summary": "안내를 제안합니다."}
    r = validate(json.dumps(out, ensure_ascii=False), i)
    return {f[0] for f in r["failures"]}


def test_m1_sent_skipped_for_counsel_app_guidance():
    """상담·앱 안내(발송하지 않는 상담 고지문)는 문장 수 검사(M1_SENT)에서 제외한다(팀 결정 2026-10-10)."""
    one = "고객님, 계약 기간과 잔여 청구 기준을 안내드립니다."          # 문장 1개(발송 문구 기준 2~4개가 아니라 M1_SENT 대상)
    assert "M1_SENT" in _soft_rules(one)                              # 일반 알림톡은 그대로 걸림
    assert "M1_SENT" not in _soft_rules(one, {"delivery_kind": "상담·앱 안내"})
