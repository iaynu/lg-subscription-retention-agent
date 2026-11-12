# Agent 설계 (5) 테스트 사례 — Rule Engine 단위 테스트 + 시연용 구독자 8명

> v0.3 · 2026-10-08 · 유형 10개(T01~T10) 기준 · 솔루션 확정 반영 · 검증기(설계 4)와 같은 형식
> 현행 데이터·시험은 `churn_agent/data/`와 `churn_agent/tests/`(시험 249건)입니다. `test_cases/`는 2026-10-07 프로토타입 기록본입니다.

## 1. 이 문서에서 정하는 것

- Rule Engine 단위 테스트 38건(JSON)과 시연용 대표 구독자 8명(JSON)의 **입력과 기대 결과**
- 테스트를 짜다가 발견한 **명세 보완 사항**과 **검증기 폴백 버그 1건**(수정 완료)
- 현행 패키지의 시험 구성(§6)

범위 밖: 실제 LLM 호출(모델명 미정, 설계 4 §6.1 평가 하네스), 모델·SHAP 결과(mock 입력). 규칙 테스트는 **규칙 검증용 고정 카탈로그**(`catalog_test.json`)를 씁니다 `[가정]`. 확정 카탈로그(`solution_catalog.json`)는 시연 구독자의 `expect_real`(§5)과 DB 샘플 7건(`agent_code_design.md` §5)에서 씁니다.

## 2. 파일 구성

| 파일 | 내용 |
|---|---|
| `churn_agent/data/catalog_test.json` | 테스트 전용 솔루션 카탈로그(유형 10개 × 2~3개 + 유형없음 1개, 비활성 1개 포함). `offer_cost`·`fee_impact`는 `[가정]` |
| `churn_agent/data/rule_cases.json` | Rule Engine 단위 테스트 38건(RE-34~38은 케이스 안의 `catalog_extra`로 광고성·전사 개선형 솔루션을 추가해 씀) |
| `churn_agent/data/demo_subscribers.json` | 시연용 구독자 8명: `agent_input`, 기대 RuleResult(`expect_rule`), 모의 LLM 응답 순서(`llm_script`), 기대 흐름(`expect_flow`), **확정 카탈로그 경로(`expect_real`)** |
| `churn_agent/data/feature_dictionary.json` | 피처 사전(유형 그룹 68개 + 컨텍스트 5개, `display_ko`). 시연 입력의 피처명·라벨·유형 그룹을 이 사전과 대조 |
| `churn_agent/tests/` | pytest 시험 249건(§6). `bash scripts/pg_test.sh`가 임시 PostgreSQL 16을 띄워 전부 실행 |
| `test_cases/` (기록본) | 2026-10-07 시점의 JSON과 Rule Engine v2 프로토타입·실행기(`run_rule_tests.py`, `run_demo_tests.py`). 그 시점 기대값으로 지금도 38/38, 8/8 통과. RE-36 등 이후 바뀐 기대값은 반영하지 않음 |
| `validator_prototype/` (기록본) | 설계 4 검증기 초기 프로토타입. 현행은 `churn_agent/agent/validation/` |

## 3. 형식 (검증기 테스트와 같은 방식)

검증기 테스트는 "정상 출력 하나를 변형해 어느 규칙 코드가 잡는지" 확인했습니다. 같은 방식으로 **기대값은 코드(사유 코드)로** 적습니다.

- **Rule Engine 케이스**: 입력(기준일, 월 요금, 위험 등급·순위, 유형 목록과 기여도, 이력)과 기대값(`mode`, `selected` 순서까지, `excluded` = `{솔루션: 사유 코드}`, `uncovered_types`, 필요 시 `limits`)
  - 이력은 `days_ago`로 적고 실행기가 `created_at`으로 바꿉니다(기준일을 옮겨도 같은 결과 → 재현성 테스트 가능).
  - `excluded`는 **정확히 일치**해야 통과합니다(적지 않은 제외 항목이 생겨도 실패). 규칙이 바뀌어 사유가 달라지면 바로 드러나게 하려는 것입니다.
- **시연 구독자**: 실제 `AgentInput`을 통째로 쓰고, 아래 순서로 검증합니다.
  0. 입력의 피처명·라벨·유형 그룹이 `feature_dictionary.json`과 일치
  1. Rule Engine 결과가 `expect_rule`과 일치(테스트 카탈로그)
  2. 후보가 있으면 모의 LLM(`llm_script`)을 넣고, 결과(`outcome`, 시도 횟수, 실패한 검사 코드, 문구 출처 `llm`/`template`)가 `expect_flow`와 일치
  3. 확정 카탈로그 + 규칙 기반 mock으로 돌린 결과가 `expect_real`과 일치하고, 채널·`(광고)` 제목·내부 전달 규칙을 지킴
  4. LLM을 부르지 않는 경로(`SURVEY_ONLY`, 후보 소진)는 호출 0회
- 모든 케이스는 JSON이라 pytest 파라미터로, 나중에 평가 하네스에서 실제 LLM 입력으로 그대로 재사용합니다.

## 4. Rule Engine 단위 테스트 (38건)

현행 Rule Engine(v3.1)은 이 38건을 **후보 0건 대체(`fallback_survey_when_empty`)를 끈 설정**으로 돌려 원래 기대값을 그대로 확인합니다(`tests/test_rules_legacy.py`). 대체 경로 자체는 `tests/test_rules_v3.py`·`test_survey_fallback.py`가 확인합니다.

| ID | 상황 | 확인하는 것 |
|---|---|---|
| RE-01 | 유형 1개·이력 없음 | 후보 생성, 정렬키(비용 낮은 순), 구분별 1개 |
| RE-02 | 같은 솔루션 13일 전 조치완료 → 쿨다운 | COOLDOWN_SOLUTION |
| RE-03 | 같은 솔루션 14일 전 → 쿨다운 해제(경계) | 쿨다운 경계: days < 14만 제외 |
| RE-04 | 같은 솔루션 15일 전 → 통과 | 쿨다운 만료 |
| RE-05 | 같은 구분 6일 전(다른 솔루션) → 쿨다운 없음 | 같은 구분 쿨다운 삭제 [확정 10/06] |
| RE-06 | 반려 이력만 있음 → 쿨다운 없음 | 반려는 쿨다운·누적 모두 제외 |
| RE-07 | 두 유형이 같은 구분 → 구분 중복 제거, 대안 보존 | DUP_CATEGORY, 1라운드가 같은 유형의 다음 후보로 넘어감 |
| RE-08 | 상위 유형 혜택이 1회 상한 초과 → COST_CAP, 무료 대안 선택 | COST_CAP, offer_cost=0은 상한 대상 아님 |
| RE-09 | 구분 중복 때문에 하위 유형의 대안이 사라지지 않음 | 구분 중복을 먼저 지우면 안 됨(v2 문제 1) |
| RE-10 | 슬롯 1개 → 1위 후보만, 나머지 MAX_ACTIONS | MAX_ACTIONS |
| RE-11 | 월 요금 낮아도 문제 대응 제품조치(0원)는 통과, 혜택은 상한 적용 | offer_cost=0 면제 / offer_cost>0 상한(v2 문제 2) |
| RE-12 | 유형 3개·슬롯 2개 → 유형 1·2가 1개씩, 유형 3 미커버 | coverage_first, uncovered_types |
| RE-13 | 90일 누적 한도 부족 → COST_CAP_CUM | 누적 한도 = 월 요금×2.0 − 90일 내 조치완료 혜택 합 |
| RE-14 | 91일 전 혜택은 누적에서 제외 | 누적 90일 경계 |
| RE-15 | 반려한 혜택은 누적에서 제외 | 반려 ≠ 지출 |
| RE-16 | 같은 이력, 월 요금이 높으면 한도 더 남음(구독 규모 비례) | 누적 한도가 월 요금에 비례 |
| RE-17 | 누적 한도 완전 소진 → 혜택 불가, 무료만 | COST_CAP_CUM, 무료 솔루션은 계속 제안 |
| RE-18 | 후보가 전부 쿨다운 → 선택 없음, 유형 미커버 | 대체를 끄면 제안할 조치 없음. **현행 기본 설정에서는 만족도 조사로 대체**(§7.2) |
| RE-19 | 한 유형만 전부 제외 → 다른 유형으로 진행 | uncovered_types 일부 |
| RE-20 | 비활성 솔루션·카탈로그에 없는 유형 | active_yn=N은 후보 생성 단계에서 제외, 미등록 유형은 미커버 |
| RE-21 | 동일 기여도 → type_rank로 결정(결정적 정렬) | 정렬키 동률 |
| RE-22 | 한 접촉 안의 혜택 합계가 1회 상한 초과 → 두 번째가 COST_CAP | used 누적(1회 상한) |
| RE-23 | 같은 구성, 월 요금이 충분하면 둘 다 채택 | used 합계 ≤ 상한 |
| RE-24 | fee_impact=증가 제안은 월 요금이 낮아도 비용 상한 대상 아님 | 업그레이드 offer_cost=0 |
| RE-25 | 유형 없음 + HIGH → NO_TYPE(만족도 조사 후보) | mode NO_TYPE |
| RE-26 | 유형 없음 + MID → NO_TYPE | mode NO_TYPE |
| RE-27 | NO_TYPE인데 만족도 조사를 **10일 전** 이미 조치완료 → 쿨다운 | NO_TYPE에도 쿨다운 적용 (제목의 "14일 전"을 이력 값에 맞게 고침, 2026-10-08) |
| RE-28 | LOW 순위 20% 정확히 → SURVEY_ONLY(경계 포함) | survey_recommend_rank_range 하한 포함 |
| RE-29 | LOW 순위 25% → SURVEY_ONLY | mode SURVEY_ONLY |
| RE-30 | LOW 순위 30% 정확히 → NONE(상한 제외) | survey_recommend_rank_range 상한 제외 |
| RE-31 | LOW 순위 50% → NONE | mode NONE |
| RE-32 | LOW인데 유형이 있어도 ACTION 아님 | LOW는 유형과 무관하게 SURVEY_ONLY/NONE |
| RE-33 | 기준일만 바꿔도 같은 이력이면 같은 결과(재현성) | as_of_date 고정 사용, 시스템 시계 금지 |
| RE-34 | 광고 수신 동의 없음 → 광고성(친구톡) 솔루션 제외, 정보성 솔루션은 선택 | `NO_AD_CONSENT` |
| RE-35 | 광고 수신 동의 있음 → 광고성 솔루션도 후보(서로 다른 구분 2개 선택) | `ad_consent_yn=Y` |
| RE-36 | `ad_consent_yn` 값이 없으면 **미동의**로 간주 → 광고성 제외 | **2026-10-08 변경**(광고는 사전 동의가 원칙). 이전 기대값은 "동의로 간주" |
| RE-37 | 광고성 솔루션만 있고 동의 없음 → 후보 0개, 유형 미커버 | `NO_AD_CONSENT` + `uncovered_types` |
| RE-38 | `agent_selectable_yn='N'`(전사 개선형)은 후보에서 제외되고 `excluded`에도 기록하지 않음 | 전사 개선형 제외 |

`agent_design.md` §4.6의 초안 13항목은 모두 위 케이스에 들어 있습니다(1→RE-01, 2→RE-02·04, 3→RE-05, 4→RE-06, 5→RE-07, 6→RE-08·09, 7→RE-10, 8→RE-25~32, 9→RE-33, 10→RE-11·24, 11→RE-12, 12→RE-13~15·17, 13→RE-16). 이 문서의 목록이 정식본입니다.

v3에서 추가된 규칙(eligibility `required`, 사실 계산, 광고 동의 출처, `SURVEY_ONLY`의 만족도 조사 쿨다운, 후보 0건 대체, 미래 날짜 이력)은 `tests/test_rules_v3.py`(15건)가 확인합니다.

## 5. 시연용 대표 구독자 8명

구독자·수치는 모두 가설 기반 합성입니다 `[가정]`. 발표에서 "왜 이 구독자를 골랐는가"가 보이도록 한 명이 한 가지 장면을 맡게 했습니다. **장면·Rule Engine 결과·LLM 흐름은 테스트 카탈로그와 스크립트 LLM 기준**이고, API 데모 모드(확정 카탈로그 + 규칙 기반 mock)에서 실제로 나오는 경로는 마지막 열(`expect_real`)입니다. `GET /subscriptions`(데모 모드)도 이 경로를 `expected_path`로 같이 보여 줍니다.

| ID | 장면 | 제품·위험 | Rule Engine 결과(테스트 카탈로그) | LLM 흐름 | 확정 카탈로그 + mock (`expect_real`) |
|---|---|---|---|---|---|
| D1 | 정석 ACTION: 두 유형, 정보제공 + 담당자조치, 1회 통과 | 정수기 HIGH 4% | ACTION → S-T01-A, S-T06-B | pass | S-T01-C + S-T06-C |
| D2 | 쿨다운 + 혜택제안, 첫 출력 위반 → 재시도 통과 | 에어컨 HIGH 7% | ACTION → S-T01-B, S-T07-A | retry_pass | S-T01-B + S-T07-A |
| D3 | 90일 누적 한도 소진 → 혜택 제외, 두 번 위반 → 부분 템플릿 | 의류관리기 HIGH 8% | ACTION → S-T04-A | partial_template | 조건 불충족·광고 미동의로 후보 0건 → S-NT-A 대체 |
| D4 | fee_impact=증가(신모델 교체 제안): 구독료 변동·총 비용·기존 구독 안내 | 에어컨 HIGH 4% | ACTION → S-T02-A, S-T03-C | pass | S-T02-A |
| D5 | NO_TYPE: 뚜렷한 유형 없음 → 만족도 조사 | 세탁기 MID 14% | NO_TYPE → S-NT-A | pass | S-NT-A |
| D6 | SURVEY_ONLY: LOW 중 위험 순위 25% → LLM 없이 템플릿 설문 추천 | 냉장고 LOW 25% | SURVEY_ONLY | survey_template | S-NT-A(설문 템플릿) |
| D7 | LLM 타임아웃 → 재시도 없이 전체 템플릿 폴백 | 정수기 HIGH 2% | ACTION → S-T09-A, S-T10-A | full_template | S-T09-A + S-T10-C |
| D8 | 후보 소진 → '제안할 조치 없음' (LLM 호출 안 함) | 정수기 HIGH 6% | ACTION → 선택 없음 | no_action | 쿨다운·광고 미동의로 후보 0건 → S-NT-A 대체 |

- D3 제품군은 6개 제품군 밖의 공기청정기에서 의류관리기로 고쳤습니다(2026-10-08).
- 2026-10-08부터 각 데모에 구독 시작일, 광고 수신 동의(`customer.ad_consent_yn`: D1·D2·D5·D7 Y, D3·D4·D6·D8 N), `facts`, `message_facts`를 넣었습니다.
- LLM 관련 장면(D2 재시도, D3 부분 템플릿, D7 타임아웃)은 스크립트 LLM으로만 재현됩니다. mock·실제 LLM에서는 일어나지 않을 수 있습니다.
- 시연 순서 제안 `[제안]`: D1(기본 흐름) → D2(쿨다운과 재시도) → D4(구독료 증가 안내) → D3(누적 한도, 부분 템플릿) → D7(LLM 장애에도 화면 유지) → D5·D6·D8(예외 경로). 발표 시간이 짧으면 D1·D2·D4·D7 네 명으로 줄입니다. DB를 쓰는 시연은 샘플 7건(S100001~S100007)이 확정 카탈로그 경로를 보여 줍니다.
- 8명이 맡은 유형: T01, T02, T03, T04, T06, T07, T08, T09, T10(T05는 단위 테스트에서만 다룸).

## 6. 실행 결과

### 6.1 현행 패키지 (2026-10-08)

`bash scripts/pg_test.sh` → **249 passed**(PostgreSQL 시험 10건 포함). PostgreSQL 없이 `pytest`만 돌리면 239 passed, 10 skipped.

| 시험 파일 | 건수 | 내용 |
|---|---|---|
| `test_rules_legacy.py` | 38 | 위 RE-01~38 |
| `test_rules_v3.py` | 15 | v3 규칙(eligibility `required`, 광고 동의 출처, 쿨다운 경계, 미래 날짜 등) |
| `test_survey_fallback.py` | 5 | SURVEY_ONLY의 만족도 조사 쿨다운, 후보 0건 대체 |
| `test_pipeline_demos.py` | 28 | D1~D8 흐름(테스트 카탈로그 + 스크립트 LLM), 확정 카탈로그 + mock 결과와 `expect_real` 고정, 광고 동의, 피처 사전 대조 |
| `test_validator_legacy.py`, `test_validator_v2.py`, `test_validator_v3.py` | 30, 10, 49 | 검증기(설계 4 §9.1) |
| `test_fallback_legacy.py`, `test_fallback_v3.py` | 8, 10 | 폴백 시나리오, 36개 솔루션 템플릿 전부 통과 등 |
| `test_prompt_frames.py` | 4 | 문구 틀 예시 검증기 통과 |
| `test_openai_client.py` | 14 | OpenAI 어댑터(가짜 클라이언트), 재시도·키 처리 |
| `test_merge.py`, `test_forward.py` | 6, 9 | 메시지 묶기, 부서 전달 |
| `test_api.py` | 13 | REST API(요청 ID, 422·404·409, 결정·전달 흐름) |
| `test_sql_repo.py`, `test_sql_source.py` | 6, 4 | PostgreSQL 저장소, DB → Agent 종단(샘플 7건) |

**뮤테이션 시험**: 규칙·검증·폴백·서비스 코드를 일부러 망가뜨려 시험이 실패하는지 확인했습니다. Rule Engine 7가지(2026-10-07, 아래)와 검증·폴백·서비스 15가지(2026-10-08)가 모두 검출됐습니다.

### 6.2 프로토타입 (2026-10-07, 기록)

| 항목 | 결과 |
|---|---|
| Rule Engine 단위 테스트 | 38/38 통과 (RE-34~38 추가, 광고 동의·전사 개선 규칙을 각각 일부러 끄는 변이로 실패 확인) |
| 시연 구독자 | 8/8 통과 |
| 검증기 기존 테스트(설계 4) | 30/30(유형 ID 누출 케이스 1건 추가), 폴백 시나리오 7/7 통과(1건 추가) |

**변이 테스트**: 쿨다운 경계(`<`→`<=`), 누적 한도 검사 제거, 구분 중복 검사 제거, 비용 합산 제거, 슬롯 수 off-by-one, 누적 한도 계산 제거, 반려 이력을 완료로 취급 — 7가지 모두 단위 테스트가 실패해 잡혔습니다. (하한 경계 변이 1건은 코드 표현상 같은 식이라 제외했고, LOW 20%·30% 경계는 RE-28·RE-30이 지킵니다.)

## 7. 테스트하면서 확인된 것

### 7.1 검증기 폴백 버그 (수정 완료)
후보에 없는 `solution_id`(R1 위반)를 두 번 받으면 항목 단위 복구 단계에서 `KeyError`가 났습니다. 존재하지 않는 솔루션은 템플릿으로 바꿀 수도 없으므로 **구조 오류**로 분류해 전체 템플릿으로 가도록 `R1`을 구조 오류 목록에 추가했습니다.

### 7.2 명세를 더 분명히 한 곳

| 항목 | 현재 동작 | 상태 |
|---|---|---|
| 쿨다운 경계 | `days < 14`만 제외 → **14일 전은 통과** | "14일째부터 재제안 가능"(RE-03) |
| `INACTIVE` 사유 코드 | 후보 생성 단계에서 아예 빠져 `excluded`에 나오지 않음 | 사유 코드 목록에서 삭제(RE-20) |
| 쿨다운 제외 항목 | `excluded`에 `COOLDOWN_SOLUTION`으로 남음 | 의도대로. 화면의 "왜 안 나왔나" 표시에 쓰임 |
| `MAX_ACTIONS` vs `DUP_CATEGORY` | 한 후보가 두 사유에 다 해당하면 판정 순서상 처음 걸린 것(슬롯 → 구분 → 비용) | 화면은 첫 사유만 표시 |
| 싼 것 먼저 정렬의 부작용 | 같은 유형 안에서 `offer_cost`가 낮은 순이라, 무료 안내가 있으면 혜택은 슬롯이 남을 때만 제안 | v2 확정 규칙이라 유지. 솔루션 `priority`로 일부 조정(설계 `agent_design.md` §4.8) |
| `NO_TYPE` 쿨다운 | 만족도 조사를 14일 안에 이미 보냈으면 후보 없음 | 의도대로(RE-27) |
| **후보 소진** | 후보가 0건이면 **만족도 조사(`S-NT-A`)로 대체**(`fallback='NO_ELIGIBLE_CANDIDATE'`, mode는 NO_TYPE). 만족도 조사도 쿨다운이면 제안할 조치 없음 | `[확정]` (2026-10-07 결정, `rules.fallback_survey_when_empty: true`). RE-18·D8의 원래 기대값은 대체를 끈 설정에서 확인 |
| 광고 수신 동의 값 없음 | 미동의로 간주 | `[확정]` 2026-10-08(RE-36) |
| `required` 사실 값이 없음 | "사건이 있었다"고 말하는 조건(예: 매니저 교체 확정)의 값이 없으면 후보 제외 | `[제안]` 2026-10-08. `rules.unknown_required_fact: pass`로 되돌릴 수 있음 |

### 7.3 새 신호의 `display_ko` (해결)
D1~D8의 `top_features`에는 10유형 개정에서 생긴 신규 신호(`unapplied_discount_yn`, `zero_use_weeks_12w`, `manager_change_actual_cnt_365d`, `attitude_complaint_cnt_90d`, `new_model_released_yn`, `sales_pitch_cnt_180d` 등)가 들어 있습니다. 라벨을 `agent_prompt_design.md` §4.3 사전(68+5)에 추가했고, 같은 내용을 `feature_dictionary.json`으로 두어 **시험이 시연 입력의 피처명·라벨·유형 그룹을 사전과 대조**합니다(처음 작성한 시연 입력에서 사전에 없는 피처 2개와 라벨 불일치 5건이 이 대조로 드러나 고쳤습니다).

## 8. 아직 남은 것

| 항목 | 상태 |
|---|---|
| 테스트 카탈로그 유지 | 단위 테스트는 규칙 검증용 고정 카탈로그를 계속 씀(교체하면 기대값이 카탈로그와 함께 흔들림). 확정 카탈로그 경로는 `expect_real`과 DB 샘플 7건으로 고정 |
| 시연용 구독자 확정 | 위 8명은 시연 후보 `[제안]`. 더미 데이터를 만든 뒤 실제 모델·SHAP 결과에서 같은 장면을 만드는 구독자를 찾아 교체 |
| 실제 LLM으로 `llm_script` 대체 | 모델명이 정해지면 평가 하네스(설계 4 §6.1)에서 같은 8명 + 샘플 7건으로 실행 |
| 단위 테스트의 월 요금·솔루션 비용 | 모두 `[가정]`. 규칙 동작 점검 목적이며 실제 비용 정책과 무관 |
