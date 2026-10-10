"""엑셀(유형요약 + 알림톡 보고서) → solution_catalog.json / seed_solution_catalog.sql 생성.
이름·발송 시점·버튼·채널·전달 부서는 알림톡 보고서 원문에서 읽고, 구분·비용·힌트 등 설계값은 아래 DESIGN에서 지정한다."""
import json, re, sys
from pathlib import Path
import openpyxl

# 사용법: python scripts/build_catalog.py "<유형확정_솔루션_합본_v3 1 1.xlsx 경로>"
# 결과: data/solution_catalog.json, sql/seed_solution_catalog.sql (패키지 안). 루트 문서 폴더 사본은 별도로 복사한다.
ROOT = Path(__file__).resolve().parents[1]
XLSX = sys.argv[1] if len(sys.argv) > 1 else "/root/.claude/uploads/6804b2ab-c67f-53b9-9512-2809e4ede1ca/c4bcdafb-____________v3_1_1.xlsx"
OUT_JSON = ROOT / "data" / "solution_catalog.json"
OUT_SQL = ROOT / "sql" / "seed_solution_catalog.sql"

wb = openpyxl.load_workbook(XLSX, data_only=True)
rep = wb["알림톡 보고서"]
summ = wb["유형요약"]

# --- 알림톡 보고서에서 번호별 원문 읽기 ---
src = {}
for r in rep.iter_rows(min_row=28, values_only=True):
    if isinstance(r[0], int):
        src[r[0]] = dict(name=r[1].strip(), channel_raw=r[2].strip(), trigger=(r[3] or "").replace("\n", " ").strip(),
                         buttons=" / ".join(x.strip() for x in (r[6] or "").split("\n") if x.strip()),
                         screen=(r[7] or "").replace("\n", " | "), note=(r[8] or "").strip(),
                         report_title=" ".join((r[4] or "").split()))
assert len(src) == 38, len(src)

# --- 유형요약에서 유형명·작명 이유 ---
types = []
cur = None
for r in summ.iter_rows(min_row=3, values_only=True):
    if r[0] and r[0].strip():
        types.append(dict(name=r[0].strip(), desc=" ".join((r[1] or "").split())))
assert len(types) == 10, len(types)
for i, t in enumerate(types, 1):
    t["type_id"] = f"T{i:02d}"

# --- 설계값 (번호: 구분, offer_cost, fee_impact, 선택가능, 전달종류, 부서, 힌트, 상세) ---
# 구분 정의: 정보제공=정보·상태 안내, 담당자조치=사람이 개입(상담·전달·배정), 혜택제안=포인트·할인, 제품조치=서비스·제품 직접 조치(재케어·대체품·구성 변경)
D = {
 1: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "지금까지 낸 금액·남은 금액·약정 종료 시 인수 금액을 구매가와 나란히 안내. 월 요금만 강조하지 않음", "가입 후 6개월마다 납부 내역을 정리해 구매가와 비교할 수 있게 안내"),
 2: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "현재 금액·변경 후 금액·변경일을 나란히 안내하고 더 낮은 요금제 확인 방법을 함께 안내", "프로모션 종료 30일 전 정상 요금 전환을 미리 고지"),
 3: ("혜택제안", 0, "감소", "Y", "고객 메시지", None, "미적용 할인 항목별 월 절감액과 합계, 신청 방법 안내. 제목 앞 (광고) 표기", "아직 적용되지 않은 할인(동시 구독·제휴카드 등)을 찾아 신청하도록 안내"),
 4: ("제품조치", 0, "감소", "Y", "고객 메시지", None, "현재 구성과 변경 후 구성의 월 요금, 계약 기간 중 변경 가능 횟수를 안내. 해지 대신 조정 선택지임을 중립적으로 안내. (광고) 표기", "케어 주기 등 구성을 바꿔 월 요금을 낮추는 선택지 안내(신규 정책)"),
 5: ("혜택제안", 20000, "없음", "Y", "고객 메시지", None, "구독 기간과 감사 혜택(포인트·추가 구독 할인)을 안내. 금액은 원가 확정 후 결정. (광고) 표기", "유지 36개월 도달 고객에게 장기 구독 감사 혜택 제공(신규 정책). 20,000원은 가정값"),
 6: ("정보제공", 0, "없음", "Y", "상담·앱 안내", None, "고객 발송 메시지 없음. 상담사용 필수 고지: 현재 계약 기간·해지 시 잔여 청구 개월·자동 연장일", "해지·가입 상담 시 잔여 청구 기준을 사전 고지(압박으로 읽힐 수 있어 메시지 미발송)"),
 7: ("담당자조치", 0, "없음", "Y", "고객 메시지", None, "철거·재설치 지원 대상 여부, 새 집 설치 가능 여부, 해지 시 총비용 비교를 안내하고 전담 상담 예약으로 연결", "이사 신호 감지 시 전담 상담사를 연결(유형요약 F8: 보류 검토 의견 있음)"),
 8: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "재구독 할인·제품 인수 금액·신모델 재구독·해지 비용 4가지를 한 번에 비교해 안내", "만기 124일 전 선택지를 비교해 안내"),
 9: ("제품조치", 0, "없음", "Y", "고객 메시지", None, "지금 쓰는 모델·신모델·월 요금 변동 없음·혜택 기한을 안내. (광고) 표기", "후속 모델이 나온 만기 6개월 이내 고객에게 같은 요금 갈아타기 제안"),
 10: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "부품 지원 종료 예정일과 종료 후 영향을 안내. 교체 혜택은 본문이 아니라 버튼 뒤 앱 화면에서 안내", "단종 예정 모델 고객에게 부품 지원 종료를 미리 알리고 교체 방법 안내"),
 11: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "마지막 사용일과 계절·날씨에 맞는 추천 사용법·영상을 안내. (광고) 표기", "오래 안 쓴 제품을 계절에 맞게 쓰도록 사용법 안내"),
 12: ("정보제공", 0, "없음", "N", "전사 개선", "제품기획팀, 구독상품기획팀", "분기 집계 리포트로 Agent 후보가 아님", "품목별 미사용률·미사용 기능 Top 5를 분기 1회 집계해 전달"),
 13: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "올해 받은 케어 횟수·남은 횟수(요금 포함)·사용 기한과 예약 방법을 안내", "계약 연도 종료 60일 전 남은 케어를 소진하도록 안내"),
 14: ("담당자조치", 0, "없음", "Y", "고객 메시지", None, "남은 방문 케어 횟수와 사용 시즌에 맞는 추천 시기·케어 내용을 안내하고 예약으로 연결", "품목 성수기 30일 전 케어 일정을 잡도록 안내(유형요약에 없고 알림톡 보고서에만 있는 솔루션)"),
 15: ("제품조치", 0, "없음", "Y", "고객 메시지", None, "설치일·위약금 없는 해지 가능 기한·다른 선택지(케어 다운그레이드·시즌 케어)를 중립적으로 안내. 해지를 부추기지도 막지도 않음", "설치 후 체험 기간 종료 5일 전 계속 사용·구성 변경·해지 선택지 안내(신규 정책)"),
 16: ("담당자조치", 0, "없음", "Y", "내부 전달", "케어서비스운영팀", "내부 전달서: 대상 케어 건·미완료 체크 항목·요청 조치·전달 부서. 고객에게 발송하지 않음", "체크 미완료 케어 발생 건을 운영팀에 전달(정책 수립 공지는 별도 운영 업무)"),
 17: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "담당 매니저·완료 항목·케어 전후 사진 수를 안내", "케어 완료 직후 결과 리포트 발송"),
 18: ("제품조치", 0, "없음", "Y", "고객 메시지", None, "케어 일시·담당 매니저를 안내하고 문제가 있으면 48시간 안에 무상 재방문함을 안내", "케어 직후 1탭 평가를 받고 불만이면 48시간 내 무상 재케어(문제 대응이라 비용 0)"),
 19: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "기존·변경 일정과 변경 사유, 앱에서 일정을 바꿀 수 있음을 안내", "일정 확정·변경·지연 시 알림"),
 20: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "예약번호·일시·담당 매니저 연락처를 안내하고 방문 전날 다시 알려드린다고 안내", "예약 확정서 발송"),
 21: ("담당자조치", 0, "없음", "Y", "고객 메시지", None, "방문 가능한 요일·시간대 선택 방법을 안내하고 등록한 시간대로 담당 매니저가 먼저 일정을 맞춘다고 안내(담당자 언급)", "선호 요일·시간대를 등록받아 매칭에 우선 반영"),
 22: ("혜택제안", 5000, "없음", "Y", "고객 메시지", None, "예정 일시와 사과 포인트 지급 완료를 안내하고 새 일정 선택 방법 안내. 사과를 먼저 씀", "미방문·당일 취소·30분 이상 지각 시 포인트 자동 지급(신규 정책). 5,000원은 가정값"),
 23: ("담당자조치", 0, "없음", "Y", "고객 메시지", None, "담당 매니저 평가를 요청하고 불만을 선택하면 다음 방문부터 다른 매니저가 배정됨을 안내", "방문 직후 1탭 평가, 저평점이면 해당 고객 배정 제외"),
 24: ("정보제공", 0, "없음", "Y", "고객 메시지", "케어서비스운영팀", "방문 진행 순서·소요 시간·동의 후에만 여는 공간·보호 매트 사용을 안내. 내부 전달: 금지 항목 지침", "방문 전날 행동 기준을 알리고 내부에는 금지 항목 지침을 전달"),
 25: ("혜택제안", 5000, "없음", "Y", "고객 메시지", "지점 책임자", "접수 내용·조치(매니저 교체·재교육)·사과 포인트 지급을 안내. 지점 책임자의 사과 전화 뒤에 발송", "태도 불만 접수 후 사과 포인트 지급(보고서에 있고 유형요약에 없음). 5,000원은 가정값. 설명은 사과 전화 중심이라 이름과 불일치"),
 26: ("담당자조치", 0, "없음", "Y", "고객 메시지", "새 담당 매니저", "새 담당 매니저 정보·첫 방문 일정, 이전 케어 기록을 인수했음을 안내. 내부: 인수인계서", "담당 매니저 교체 시 인수인계서를 새 매니저에게 전달하고 고객에게 사전 고지"),
 27: ("담당자조치", 0, "없음", "N", "전사 개선", "지점 교육 담당", "매니저 단위 조치로 Agent 후보가 아님", "동행 평가·재교육 대상 매니저를 교육 담당에 전달"),
 28: ("담당자조치", 0, "없음", "Y", "내부 전달", "케어서비스운영팀, 영업관리팀", "내부 전달서: 방문 중 판매 권유 금지·제한 지침 위반 접수 건을 해당 지점에 통보. 고객에게 발송하지 않음", "영업 불만 접수 시 해당 지점에 자동 통보(정책 수립 공지는 별도 운영 업무)"),
 29: ("혜택제안", 0, "없음", "Y", "고객 메시지", None, "추가 구독 혜택(결합 할인 등)은 앱에서만 안내하고 방문 때는 권유하지 않음을 안내. 관심 없음 버튼 포함. (광고) 표기", "케어 완료 3일 후 방문과 분리해 앱 중심 혜택 안내. 영업 압박 고객에게 광고성 메시지가 역효과일 수 있음"),
 30: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "무상 A/S 기간·무상 대상·유상 대상(고객 과실)을 구분해 안내", "가입 직후와 고장 접수 시 무상 A/S 범위 안내"),
 31: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "문의 증상에 맞는 설정 방법과 소프트웨어 업데이트를 안내하고 해결 안 되면 A/S 접수로 연결", "설정으로 해결되는 문의에 앱 설정 안내(원문 '푸시' 대신 알림톡)"),
 32: ("정보제공", 0, "없음", "Y", "고객 메시지", "A/S 부서", "점검 항목·결과·다음 단계(A/S 예약)를 안내. 내부: 이상 리포트를 A/S 부서에 전달", "케어 중 점검 이상을 선제 리포트하고 A/S 부서에 전달"),
 33: ("제품조치", 0, "없음", "Y", "내부 전달", "품질보증팀, A/S 운영팀", "내부 전달서: 동일 증상 3회 이상 접수 고객과 요청 조치(펌웨어 개선판·부품 우선 투입·무상 교환). 고객에게 발송하지 않음", "반복 불만 고객에 개선판·부품 우선 적용 요청(문제 대응이라 비용 0)"),
 34: ("정보제공", 0, "없음", "N", "전사 개선", "제품개발팀, 품질보증팀", "모델 단위 월간 리포트로 Agent 후보가 아님", "결함 다발 모델을 월 1회 집계해 개선·리콜 검토를 요청"),
 35: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "접수·기사 배정·부품 수급·완료 단계와 담당 기사·예상 완료일을 안내", "A/S 단계가 바뀔 때마다 진행 상황 알림"),
 36: ("정보제공", 0, "없음", "Y", "고객 메시지", None, "처음 안내한 완료일·변경된 완료일·지연 사유를 안내하고 진행 확인·대체품 신청 방법 안내", "접수 시 평균 처리기간을 알리고 지연되면 자동 알림"),
 37: ("담당자조치", 0, "없음", "Y", "내부 전달", "고객센터 전담팀", "내부 전달서: 구독자·문의 내용(재접수 횟수)·1차 응대 결과·요청 조치(전담자 지정, 24시간 내 콜백). 고객에게 발송하지 않음", "1차 응대 미해결 건에 전담자를 지정하고 콜백을 보장"),
 38: ("제품조치", 0, "없음", "Y", "고객 메시지", "물류·A/S 부서", "수리 예상 기간·대체품(동급 임시 설치 무료)·설치 가능일을 안내. 내부: 대체품 지원 요청을 물류·A/S 부서에 전달", "A/S 7일 이상 지연 시 대체품 임시 대여(문제 대응이라 비용 0, 물류 비용은 상한 계산에서 제외)"),
}
NEW_POLICY = {4, 5, 15, 22}  # 알림톡 보고서 비고의 '신규 정책'
SEQ_TYPE = {}
rows = []
# 번호 → 유형: 보고서의 ■ 헤더 순서로 결정
tid = 0; cur_t = None
for r in rep.iter_rows(min_row=28, values_only=True):
    if isinstance(r[0], str) and r[0].startswith("■"):
        tid += 1; cur_t = f"T{tid:02d}"
    elif isinstance(r[0], int):
        SEQ_TYPE[r[0]] = cur_t
assert tid == 10

letters = {}
for n in range(1, 39):
    t = SEQ_TYPE[n]
    letters[t] = letters.get(t, 0) + 1
    sid = f"S-{t}-{chr(64 + letters[t])}"
    cat, cost, fee, sel, kind, dept, hint, detail = D[n]
    s = src[n]
    raw = s["channel_raw"]
    primary = "친구톡" if raw.startswith("친구톡") else "알림톡" if raw.startswith("알림톡") else "내부 전달" if raw.startswith("LG 내부") else "상담·앱"
    internal_also = "Y" if ("내부" in raw and primary != "내부 전달") or (n in (24, 25, 26)) else "N"
    if n == 7: internal_also = "N"
    rows.append(dict(
        solution_id=sid, seq_no=n, type_id=t, category=cat, name=s["name"], detail=detail,
        channel=primary, channel_report=raw, ad_yn="Y" if primary == "친구톡" else "N",
        internal_forward_yn="Y" if (primary == "내부 전달" or internal_also == "Y") else "N",
        forward_dept=dept, delivery_kind=kind, agent_selectable_yn=sel,
        offer_cost=cost, fee_impact=fee, trigger_note=s["trigger"], buttons=s["buttons"],
        message_hint=hint, new_policy_yn="Y" if n in NEW_POLICY else "N",
        staff_screen=s["screen"], report_note=s["note"], report_title=s["report_title"], active_yn="Y"))

# 실측(nano n=200)에서 고객 문구 검증(M3·M4)에 반복 실패한 솔루션에만 주의 문구를 message_hint 끝에 덧붙인다 [제안, 2026-10-08].
# M3 숫자 출처는 입력의 top_features[].label("최근 90일 …", "최근 180일 …", "최근 1년 …")과 context 요약 건수. 전역 프롬프트(C8)를 고치면 다른 규칙이 흔들려 후보별로 막는다.
HINT_GUARD = {
    "S-T07-D": "※ 고객 문구에 민원·불만·교체 요청의 횟수나 기간(최근 N일·N회)을 쓰지 말고, 요금·구독료도 언급하지 않는다",
    "S-T04-A": "※ 고객 문구에 기간(N주·N일)이나 횟수를 쓰지 말고, 요금·할인·구독료를 언급하지 않는다",
    "S-T06-C": "※ 고객 문구에 기간(최근 N일·N년)이나 횟수를 쓰지 않는다",
    "S-T01-C": "※ 고객 문구에 '총 비용'이나 '위약금' 단어를 넣어, 할인을 적용해도 총 비용·위약금 조건이 바뀌지 않는지 앱에서 확인하도록 안내한다",
}
for r in rows:
    if r["solution_id"] in HINT_GUARD: r["message_hint"] = r["message_hint"].rstrip() + " " + HINT_GUARD[r["solution_id"]]

# 유형요약 시트에 없는 항목은 Agent 후보에서 뺀다(비활성화, 행은 유지) [제안 → 팀 결정 2026-10-08: 유형요약이 기준].
# S-T04-D(알림톡 보고서 14번), S-T07-C(알림톡 보고서 25번)는 알림톡 보고서에만 있다. 되살리려면 여기서 지우고 다시 생성한다.
NOT_IN_TYPE_SUMMARY = {"S-T04-D", "S-T07-C"}
for r in rows:
    if r["solution_id"] in NOT_IN_TYPE_SUMMARY:
        r["active_yn"] = "N"
        r["report_note"] = (r["report_note"] + " / " if r["report_note"] else "") + "유형요약에 없어 Agent 후보에서 제외(비활성)"

# 유형없음 pseudo-type (엑셀에 없음, 기존 설계의 만족도 조사 유지 [제안])
rows.append(dict(solution_id="S-NT-A", seq_no=0, type_id="유형없음", category="의견수집", name="만족도 조사",
    detail="서비스 이용 경험 설문(열린 질문)으로 이탈 원인을 확인", channel="알림톡", channel_report="(엑셀 밖) 기존 설계 유지", ad_yn="N",
    internal_forward_yn="N", forward_dept=None, delivery_kind="고객 메시지", agent_selectable_yn="Y", offer_cost=0, fee_impact="없음",
    trigger_note="유형 없음(NO_TYPE) 또는 설문 추천 구간", buttons="설문 참여하기", message_hint="열린 질문",
    new_policy_yn="N", staff_screen="알림톡 미리보기 → [발송하기]", report_note="", report_title="", active_yn="Y"))


# --- 우선순위(낮을수록 먼저)와 시점 조건(eligibility) [제안] ---
# 우선순위 원칙: 10 = 고객 문제를 직접 해결하는 조치, 20 = 고객 접점 정보 안내, 30 = 광고성·혜택 제안, 40 = 내부 보고성 전달, 50 = 유형없음, 90 = 전사 개선(후보 아님)
PRIORITY = {
 "S-T01-A": 20, "S-T01-B": 20, "S-T01-C": 10, "S-T01-D": 30, "S-T01-E": 30,
 "S-T02-A": 20, "S-T02-B": 10,
 "S-T03-A": 20, "S-T03-B": 30, "S-T03-C": 20,
 "S-T04-A": 30, "S-T04-B": 90, "S-T04-C": 20, "S-T04-D": 20, "S-T04-E": 30,
 "S-T05-A": 40, "S-T05-B": 20, "S-T05-C": 10,
 "S-T06-A": 20, "S-T06-B": 20, "S-T06-C": 10, "S-T06-D": 10,
 "S-T07-A": 10, "S-T07-B": 20, "S-T07-C": 10, "S-T07-D": 10, "S-T07-E": 90,
 "S-T08-A": 10, "S-T08-B": 30,
 "S-T09-A": 20, "S-T09-B": 20, "S-T09-C": 10, "S-T09-D": 10, "S-T09-E": 90,
 "S-T10-A": 20, "S-T10-B": 20, "S-T10-C": 10, "S-T10-D": 10,
 "S-NT-A": 50,
}
# 조건은 AND. [fact, 연산자, 값]. fact가 입력에 없으면 '판정 불가'로 통과시킨다. 조건 없는 솔루션은 항상 후보.
ELIGIBILITY = {
 "S-T01-A": [["months_since_start", ">=", 6]],
 "S-T01-B": [["days_to_promo_end", ">=", 0], ["days_to_promo_end", "<=", 30]],
 "S-T01-C": [["unapplied_discount_yn", "==", "Y"]],
 "S-T01-D": [["voc_fee_or_cancel_cnt_30d", ">=", 1]],
 "S-T01-E": [["months_since_start", ">=", 36]],
 "S-T02-B": [["move_signal_yn", "==", "Y"]],
 "S-T03-A": [["days_to_end", ">=", 0], ["days_to_end", "<=", 124]],
 "S-T03-B": [["new_model_released_yn", "==", "Y"], ["days_to_end", ">=", 0], ["days_to_end", "<=", 180]],
 "S-T03-C": [["model_discontinued_yn", "==", "Y"]],
 "S-T04-A": [["days_since_last_use", ">=", 30]],
 "S-T04-C": [["days_to_contract_year_end", ">=", 0], ["days_to_contract_year_end", "<=", 60], ["remaining_care_cnt", ">=", 1]],
 "S-T04-D": [["days_to_season_peak", ">=", 0], ["days_to_season_peak", "<=", 30], ["remaining_care_cnt", ">=", 1]],
 "S-T04-E": [["days_to_trial_end", ">=", 0], ["days_to_trial_end", "<=", 5]],
 "S-T05-A": [["care_short_visit_cnt", ">=", 1]],
 "S-T05-B": [["days_since_last_care", "<=", 7]],
 "S-T05-C": [["days_since_last_care", "<=", 7]],
 "S-T06-A": [["has_upcoming_visit", "==", "Y"]],
 "S-T06-B": [["has_upcoming_visit", "==", "Y"]],
 "S-T06-D": [["missed_or_late_visit_cnt_30d", ">=", 1]],
 "S-T07-A": [["days_since_last_visit", "<=", 7]],
 "S-T07-B": [["has_upcoming_visit", "==", "Y"]],
 "S-T07-C": [["attitude_complaint_cnt_30d", ">=", 1]],
 "S-T07-D": [["manager_change_confirmed_yn", "==", "Y"]],
 "S-T08-A": [["sales_voc_cnt_30d", ">=", 1]],
 "S-T08-B": [["days_since_last_care", ">=", 3], ["days_since_last_care", "<=", 14]],
 "S-T09-B": [["setting_inquiry_yn", "==", "Y"]],
 "S-T09-C": [["care_inspection_anomaly_yn", "==", "Y"]],
 "S-T09-D": [["same_symptom_cnt", ">=", 3]],
 "S-T10-A": [["open_as_yn", "==", "Y"]],
 "S-T10-B": [["open_as_yn", "==", "Y"]],
 "S-T10-C": [["first_response_unresolved_yn", "==", "Y"]],
 "S-T10-D": [["open_as_yn", "==", "Y"], ["expected_as_days", ">=", 7]],
}
# --- 고객용 제목 [제안] ---
# 알림톡 보고서의 '메시지 제목'에서 예시 고객명·금액·날짜를 빼고 일반화한 문장. 폴백 템플릿의 제목·첫 문장과
# LLM의 제목 참고값으로 쓴다(카탈로그 솔루션 이름은 내부 용어라 고객 문구에 쓰지 않는다). 내부 전달·전사 개선은 없음.
CUSTOMER_TITLE = {
 "S-T01-A": "고객님, 지금까지 낸 구독료를 정리했어요",
 "S-T01-B": "고객님, 프로모션 종료 후 구독료가 바뀌어요",
 "S-T01-C": "(광고) 고객님, 매달 더 아낄 수 있는 할인이 있어요",
 "S-T01-D": "(광고) 고객님, 케어 주기만 바꿔도 요금이 내려가요",
 "S-T01-E": "(광고) 고객님, 오래 함께해 주셔서 감사해요",
 "S-T02-A": "고객님, 케어 서비스 해지 시 남은 기간 청구 기준을 안내드립니다",   # 상담 고지 스크립트 첫 문장(발송하지 않음)
 "S-T02-B": "고객님, 이사 준비 중이신가요?",
 "S-T03-A": "고객님, 구독 만료가 다가오고 있어요",
 "S-T03-B": "(광고) 고객님, 새로 나온 모델로 바꿔 보세요",
 "S-T03-C": "고객님, 사용 중인 제품의 부품 지원이 종료될 예정이에요",
 "S-T04-A": "(광고) 고객님, 제품을 이렇게 활용해 보세요",
 "S-T04-C": "고객님, 올해 받을 수 있는 케어가 남았어요",
 "S-T04-D": "고객님, 사용 시즌 전에 케어 받아보세요",
 "S-T04-E": "고객님, 제품 써보시니 어떠세요?",
 "S-T05-B": "고객님, 케어가 끝났어요",
 "S-T05-C": "고객님, 케어 어떠셨나요?",
 "S-T06-A": "고객님, 방문 일정을 안내드려요",
 "S-T06-B": "고객님, 방문 케어 예약이 확정됐어요",
 "S-T06-C": "고객님, 방문 받기 편한 시간을 알려주세요",
 "S-T06-D": "고객님, 약속한 방문을 지키지 못해 죄송합니다",
 "S-T07-A": "고객님, 최근 방문한 매니저는 어떠셨나요?",
 "S-T07-B": "고객님, 방문 케어 진행 순서를 안내드려요",
 "S-T07-C": "고객님, 불편을 드려 진심으로 죄송합니다",
 "S-T07-D": "고객님, 담당 매니저가 바뀌게 되었어요",
 "S-T08-B": "(광고) 고객님만을 위한 추가 구독 혜택이 있어요",
 "S-T09-A": "고객님, 구독 제품 무상 A/S 범위를 안내드려요",
 "S-T09-B": "고객님, 설정만 바꾸면 해결될 수 있어요",
 "S-T09-C": "고객님, 케어 점검에서 확인이 필요한 부분이 있었어요",
 "S-T10-A": "고객님, A/S 진행 상황을 알려드려요",
 "S-T10-B": "고객님, A/S 처리가 늦어지고 있어 안내드려요",
 "S-T10-D": "고객님, 수리 기간 동안 쓰실 제품을 빌려드려요",
 "S-NT-A": "고객님, 서비스 이용 의견을 들려주세요",
}
# --- ▶ 항목으로 보여 줄 고객 공개 값(agent/facts.py MESSAGE_FACT_DEFS의 키) [제안] ---
# 알림톡 보고서 본문 예시의 ▶ 항목을 스키마로 구할 수 있는 값에 대응시킨 것. 값이 없으면 그 줄은 빠진다.
MESSAGE_ITEMS = {
 "S-T01-A": ["paid_total", "months_since_start", "end_date"],
 "S-T01-B": ["monthly_fee", "promo_end_date"],
 "S-T01-D": ["monthly_fee"],
 "S-T01-E": ["months_since_start"],
 "S-T02-A": ["end_date", "care_renewal_date"],
 "S-T03-A": ["end_date", "days_to_end"],
 "S-T03-B": ["end_date"],
 "S-T04-A": ["days_since_last_use"],
 "S-T04-C": ["remaining_care_cnt", "contract_year_end_date"],
 "S-T04-D": ["remaining_care_cnt"],
 "S-T04-E": ["trial_end_date"],
 "S-T05-B": ["last_care_date"],
 "S-T05-C": ["last_care_date"],
 "S-T06-A": ["next_visit_date"],
 "S-T06-B": ["next_visit_date"],
 "S-T07-A": ["last_visit_date"],
 "S-T07-B": ["next_visit_date"],
 "S-T07-D": ["next_visit_date"],
 "S-T09-A": ["end_date"],
 "S-T09-C": ["last_care_date"],
 "S-T10-B": ["expected_as_days"],
 "S-T10-D": ["expected_as_days"],
}
# --- '사건이 있었다'고 말하는 조건: 값이 없으면 후보에서 뺀다 [제안] ---
# 예: 담당 매니저 교체가 확인되지 않았는데 "담당 매니저가 바뀌게 되었어요"를 보내면 사실과 다른 안내가 된다.
REQUIRED_FACTS = {"days_to_promo_end", "unapplied_discount_yn", "new_model_released_yn", "model_discontinued_yn",
                  "remaining_care_cnt", "days_to_trial_end", "days_since_last_care", "has_upcoming_visit",
                  "missed_or_late_visit_cnt_30d", "days_since_last_visit", "attitude_complaint_cnt_30d",
                  "manager_change_confirmed_yn", "sales_voc_cnt_30d", "setting_inquiry_yn", "care_inspection_anomaly_yn",
                  "same_symptom_cnt", "open_as_yn", "first_response_unresolved_yn", "expected_as_days",
                  "care_short_visit_cnt", "days_to_end"}
REQUIRED_RULES = {("S-T01-E", "months_since_start")}

for r in rows:
    sid = r["solution_id"]
    r["priority"] = PRIORITY[sid]
    el = ELIGIBILITY.get(sid)
    r["eligibility"] = None if el is None else [x + ["required"] if (x[0] in REQUIRED_FACTS or (sid, x[0]) in REQUIRED_RULES) else x for x in el]
    r["customer_title"] = CUSTOMER_TITLE.get(sid) if r["delivery_kind"] in ("고객 메시지", "상담·앱 안내") else None
    r["message_items"] = MESSAGE_ITEMS.get(sid, [])
missing = [r["solution_id"] for r in rows if r["delivery_kind"] in ("고객 메시지", "상담·앱 안내") and not r["customer_title"]]
assert not missing, missing
assert all(r["customer_title"].startswith("(광고)") == (r["ad_yn"] == "Y") for r in rows if r["customer_title"]), "광고 제목 표기 불일치"

json.dump({"_note": "솔루션 카탈로그 v1.2 [제안]. v1.2: 고객용 제목(customer_title)·▶ 항목(message_items)·필수 조건 표시(eligibility 4번째 값 'required') 추가. 출처: 유형확정_솔루션_합본_v3 1 1.xlsx의 '유형요약'·'알림톡 보고서' 두 시트(알림톡 보고서 38건 기준). "
              "offer_cost 20,000·5,000은 가정값(원가 확정 전). 구분·offer_cost·fee_impact·agent_selectable_yn·delivery_kind는 설계 지정값으로 팀 확인 필요.",
              "types": types, "solutions": rows}, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# --- SQL 시드 ---
def q(v):
    if v is None: return "NULL"
    if isinstance(v, (int, float)): return str(v)
    return "'" + str(v).replace("'", "''") + "'"
sql = ["-- ============================================================",
       "-- 시드: churn_type 11행(T01~T10 + 유형없음) + solution_catalog 39행(알림톡 보고서 38건 + 만족도 조사 1건). 이 중 S-T04-D·S-T07-C는 유형요약에 없어 active_yn='N'(Agent 후보 아님)",
       "-- 출처: 유형확정_솔루션_합본_v3 1 1.xlsx 중 '유형요약'·'알림톡 보고서' (2026-10-07). 파일은 solution_catalog.json에서 자동 생성.",
       "-- offer_cost 20,000·5,000은 가정값. 가설 기반 synthetic data 프로젝트의 설계 자료이며 실제 LG전자 정책이 아님.",
       "-- ============================================================", ""]
for t in types:
    sql.append(f"INSERT INTO churn_type (type_id, type_name, description) VALUES ({q(t['type_id'])}, {q(t['name'])}, {q(t['desc'])});")
sql.append("INSERT INTO churn_type (type_id, type_name, description) VALUES ('유형없음', '유형 없음', '유형 확정 기준(SHAP 비중 20% 이상)에 못 미치는 경우의 pseudo-type');")
sql.append("")
cols = ["solution_id","seq_no","type_id","category","name","detail","channel","ad_yn","internal_forward_yn","forward_dept","delivery_kind",
        "agent_selectable_yn","offer_cost","fee_impact","trigger_note","buttons","message_hint","new_policy_yn","priority","eligibility",
        "customer_title","message_items","active_yn"]
dbc = ["solution_id","seq_no","type_id","solution_category","solution_name","solution_detail","channel","ad_yn","internal_forward_yn","forward_dept","delivery_kind",
       "agent_selectable_yn","offer_cost","fee_impact","trigger_note","buttons","message_hint","new_policy_yn","priority","eligibility",
       "customer_title","message_items","active_yn"]
def cell(r, c):
    v = r[c]
    if c in ("eligibility", "message_items"): return q(json.dumps(v, ensure_ascii=False) if v else None)
    return q(v)
for r in rows:
    sql.append(f"INSERT INTO solution_catalog ({', '.join(dbc)}) VALUES ({', '.join(cell(r, c) for c in cols)});")
open(OUT_SQL, "w", encoding="utf-8").write("\n".join(sql) + "\n")

# --- 집계 점검 ---
from collections import Counter
print("총", len(rows), "건 (유형없음 포함)")
r38 = rows[:38]
print("채널(주):", Counter(r["channel"] for r in r38))
print("내부 전달 포함(복합 포함):", sum(r["internal_forward_yn"] == "Y" for r in r38))
print("선택가능:", Counter(r["agent_selectable_yn"] for r in r38))
print("전달종류:", Counter(r["delivery_kind"] for r in r38))
print("구분:", Counter(r["category"] for r in r38))
print("offer_cost>0:", [(r["solution_id"], r["offer_cost"]) for r in r38 if r["offer_cost"] > 0])
print("fee_impact:", Counter(r["fee_impact"] for r in r38))
byt = {}
for r in r38:
    byt.setdefault(r["type_id"], []).append((r["solution_id"], r["category"], r["agent_selectable_yn"]))
for t, v in byt.items(): print(t, v)
