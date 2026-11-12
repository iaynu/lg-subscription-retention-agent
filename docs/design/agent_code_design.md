# Agent 설계 (6) 코드 구조

> LG전자 AX School Build2 · 구독 이탈 예측 & 유형별 맞춤 방지 AI Agent
> 작성 기준일: 2026-10-07 (2026-10-08 점검·수정 반영) / 상태: v0.2 (동작하는 패키지 `churn_agent/` 포함)
> 표기: `[확정]` / `[제안]` / `[가정]` / `[팀 결정 필요]`. 정형 데이터는 가설 기반 synthetic data, 모델 결과는 파이프라인 검증 용도입니다.
> 관련 파일: `agent_design.md`(입출력 계약·Rule Engine), `agent_prompt_design.md`, `agent_validator_design.md`, `solution_catalog_design.md`, `data_schema_design.md`, `verification_report.md`(점검 결과)

## 1. 한 줄 요약

`churn_agent/`는 "예측 결과 → Rule Engine → LLM → 검증기(폴백) → 상담자 확인 → 조치 기록" 흐름을 모듈로 나눈 Python 패키지입니다. 예측 소스는 데모 8건(JSON) 또는 PostgreSQL(예측·이벤트 테이블)이고, LLM은 OpenAI API(키는 환경변수 `OPENAI_API_KEY`), 저장소는 메모리 또는 PostgreSQL입니다. UI는 REST API만 호출합니다.

## 2. 폴더 구조

```
churn_agent/
  config.yaml            설정(규칙 한도, LLM, facts 계산 가정값, 시계, 예측 소스, 저장소). 비밀값 없음
  requirements.txt
  data/                  solution_catalog.json(카탈로그 v1.2), demo_subscribers.json(데모 8건), rule_cases.json(규칙 케이스 38건),
                         feature_dictionary.json(피처 73개), departments.json(협업 부서 8곳), catalog_test.json(구 테스트 카탈로그)
  sql/                   schema_postgres.sql(DDL), seed_solution_catalog.sql(시드), check_integrity.sql(고아 행 검사 29개 관계)
  scripts/               build_catalog.py(엑셀 → 카탈로그·시드), load_sample_data.py(샘플 7건 적재), pg_test.sh(임시 PG16으로 전체 시험)
  agent/
    config.py            config.yaml 로드(기본값 병합)
    catalog.py           카탈로그 로드
    schemas.py           pydantic 입출력 계약과 입력 검증(날짜·필수 값·길이 → 422)
    facts.py             facts(자격 판정용)·message_facts(고객 공개 값) 정의·계산·표시 형식
    rules/engine.py      Rule Engine v3.1 (순수 함수, 시스템 시계 사용 안 함)
    rules/eligibility.py 자격 조건 평가('required' 조건은 값이 없으면 제외)
    llm/prompt.py        시스템 프롬프트 v3.0(알림톡 보고서 형식), 사용자 메시지, 동적 JSON 스키마(후보 enum)
    llm/openai_client.py OpenAI 어댑터(Structured Outputs, temperature 선택, 타임아웃·거절·잘림·필터)
    llm/mock.py          RuleBasedMock(오프라인 기준선), ScriptedLlm(테스트용)
    llm/base.py, factory.py  클라이언트 프로토콜, 키가 없을 때 템플릿 전용 모드
    validation/validator.py  출력 검증 v3(R·E·M·T·D·N·LEAK·LANG)
    validation/fallback.py   재시도 1회 → 항목별/전체 템플릿 폴백(보고서 형식 템플릿)
    forward.py           협업 부서 전달 초안·근거 화면용 분석 블록
    service.py           AgentService.run / decide / forward, 메시지 합치기, 상태 라벨
    sources.py           DemoSource(JSON), SqlSource(PostgreSQL → AgentInput, facts 계산)
    sample_data.py       PostgreSQL 샘플 7건(경로별 시연·통합 시험용)
    repo/memory.py, sql.py   저장소(메모리, SQLAlchemy + PostgreSQL)
    api.py               FastAPI 앱
    cli.py               python -m agent.cli list | run <구독 ID>
  tests/                 pytest 249건(PostgreSQL 통합 10건은 TEST_DATABASE_URL이 있을 때만)
```

의존 방향은 `api → service → (rules, llm, validation, repo)`, `api → sources`입니다. `rules`·`validation`·`facts`는 DB·네트워크를 모르는 순수 코드라 입력 JSON만으로 단위 시험됩니다.

## 3. 요청 한 건의 흐름

1. `POST /agent/run`(구독 ID) → 예측 소스가 AgentInput을 만들고(§5), API가 저장소의 최근 90일 `action_log`를 합칩니다. `request_id`는 서버가 실행마다 새로 만듭니다(`REQ-` + 12자리, 20자 이내).
2. Rule Engine이 모드(ACTION / NO_TYPE / SURVEY_ONLY / NONE)와 후보를 정합니다(`agent_design.md` §4.8). 광고성 솔루션은 광고 수신 동의가 `Y`일 때만, '사건' 조건은 값이 확인될 때만 후보가 됩니다. 후보가 0건이면 만족도 조사(`S-NT-A`)로 대체하고, 그것도 쿨다운이면 '조치 없음'입니다.
3. 후보가 있으면 LLM이 선택·정렬하고 선택 이유, 근거, 제목, 고객 문구, 내부 전달서를 씁니다. 검증기를 통과하지 못하면 1회 재시도하고, 그래도 안 되면 문제 항목만(또는 전체를) 템플릿으로 대체합니다. 총 15초 한도입니다. SURVEY_ONLY는 LLM 없이 설문 템플릿을 씁니다.
4. 같은 채널·같은 광고 여부의 고객 메시지는 한 통으로 합쳐 `messages[]`로, 내부 전달 항목은 협업 요청서 초안 `forward`로 내려줍니다. 결과 전체를 `agent_result`(또는 메모리)에 저장합니다. 실제 발송은 하지 않는 시뮬레이션입니다.
5. 상담자가 톡 부분은 `POST /agent/decision`(조치완료 / 반려, 메모)으로, 내부 전달 부분은 `POST /agent/forward`로 확정하면 `action_log`에 기록합니다. 조치완료만 쿨다운·비용 누계에 세고 반려는 세지 않습니다. 복합 솔루션(톡 + 내부 전달)은 두 부분을 순서와 관계없이 각각 한 번씩 확정할 수 있습니다.

## 4. REST API (UI가 쓰는 계약) `[제안]`

| 메서드·경로 | 용도 |
|---|---|
| `GET /health` | LLM 제공자·상태(`llm_status`: mock / ready / unavailable), 저장소, 예측 소스 |
| `GET /subscriptions` | 구독 목록. SQL 소스는 최신 기준일의 `risk_rank_pct` 순, 유형 ID 목록 포함. 데모 소스는 시나리오 제목(테스트 카탈로그·스크립트 LLM 기준)과 확정 카탈로그에서 실제로 나오는 경로(`expected_path`) |
| `POST /agent/run` `{subscription_id, as_of_date?}` | Agent 실행, `AgentResult` 반환 |
| `POST /agent/run-input` (AgentInput 전체) | 예측 소스 없이 입력을 직접 넣어 실행(UI 개발·시연용). 이미 있는 `request_id`면 409 |
| `POST /agent/decision` `{request_id, subscription_id, solution_id, status, note?, by?}` | 톡·상담 안내 부분의 조치완료/반려. `note`는 `action_log.decision_note`에 저장. 같은 부분 재결정은 409 |
| `POST /agent/forward` `{request_id, subscription_id, solution_ids[], departments[], text, by?}` | 협업 부서 전달 확정(§4.2). 항목마다 `action_log` 1행(`channel='내부 전달'`) |
| `GET /subscriptions/{id}/actions` | 조치 이력(`status_label`: 발송 완료 / 부서 전달 완료 / 상담 안내 완료 / 반려) |
| `GET /departments` | 협업 부서 마스터(8곳) |
| `GET /catalog?type_id=` | 카탈로그 조회 |

오류 규칙: 입력 형식 오류 422(날짜 `YYYY-MM-DD`, 구독 ID·월 구독료 필수, 문자열 길이, 중복 값), 없는 요청·구독 404, 이미 결정된 항목·중복 요청·DB 제약 위반 409.

`AgentResult`의 주요 필드: `request_id, mode, outcome(pass|retry_pass|partial_template|full_template|survey_template|no_action), attempts, failed_checks, fallback, selected[], messages[], forward, analysis, excluded[], uncovered_types[], limits, meta`. `selected[]` 한 항목은 `solution_id, type_id, priority, selection_reason, evidence[], channel, title, message_text, internal_memo, buttons[], message_source(llm|template)`이며 우선순위 순으로 정렬됩니다. `meta`에는 `llm_provider`, `llm_model`, `llm_status`, `prompt_version`, `llm_errors`가 있어, mock이나 템플릿 전용 모드로 돌 때 화면에 표시할 수 있습니다.

알림톡·친구톡은 `messages[]`의 `title` + `message_text` + `buttons` 미리보기 → [발송하기], 순수 내부 전달은 `forward` 표시 → [전달하기], 복합은 둘 다 표시합니다. 응답 필드 이름이 목업의 화면 요소와 다르면 API를 바꾸지 않고 UI 쪽에서 매핑하는 것을 권장합니다.

### 4.1 고객 메시지 형식과 합치기

- **형식** `[확정 2026-10-08]`: 팀 '알림톡 보고서'의 형식을 따릅니다. 제목 한 줄(40자, 숫자 없음, 광고성이면 `(광고)`로 시작) + 본문(첫 문장 → `▶ 라벨: 값` 0~4줄 → 마무리 문장, 400자 이내) + 카탈로그 버튼. ▶ 항목의 값은 고객 본인의 계약·이용 정보인 `message_facts`만 쓰며(§5), 문의 횟수·위험도·SHAP 같은 내부 분석값은 쓰지 않습니다. 이름 대신 "고객님"을 씁니다.
- **합치기** `[제안]`: 조치가 2개이고 둘 다 톡으로 가면, 검증이 끝난 뒤 채널과 `ad_yn`이 같을 때만 한 통으로 묶습니다. 광고성 친구톡과 정보성 알림톡은 섞지 않습니다. 본문은 우선순위 순서로 빈 줄을 두고 이어 붙이고, 제목은 첫 항목 제목 뒤에 `외 N건`(40자 이내), 버튼은 중복을 빼고 최대 5개(넘으면 `buttons_truncated=true`)입니다. 결정은 항목별이므로 UI가 합친 한 통을 보내면 `solution_ids`의 항목마다 조치완료를 기록합니다.
- **확인 필요** `[팀 결정 필요]`: 실제 알림톡은 사전 승인된 템플릿 단위로 발송되고 글자 수·버튼 수 제한이 있어, 합친 한 통이 운영에서 그대로 가능한지는 검증하지 않았습니다.

### 4.2 협업 부서 전달 화면 (팀원 목업 반영) `[제안]`

| 목업 요소 | API 필드 |
|---|---|
| 1 · 부서 칩(복수) | `GET /departments`(8곳). 기본 선택은 `forward.departments`(카탈로그 `forward_dept` → 부서 코드, `data/departments.json`), 매핑되지 않은 이름은 `forward.unmapped` |
| 2 · 근거 | `analysis.risk_percent`, `analysis.types[].share_percent`, `analysis.types[].features[]`(label·value·shap), `analysis.persona_quote`(입력 `context.persona_quote`가 있을 때) |
| 3 · 조치 가이드 | `forward.draft_text`. 머리말(`[협업 요청] 고객ID 이름 고객 (제품 · 위험도 %)`)·수신·처리 기한은 코드가 조립하고, 항목별 본문(요청 조치 1개 + 보조 조치)만 LLM의 `internal_memo`를 씁니다. 고객 이름·ID는 `AgentInput.customer`로만 받아 LLM에는 보내지 않습니다 |
| [AI 초안으로 되돌리기] | UI가 `forward.draft_text`를 보관. 전달 시 `text`가 초안과 다르면 `memo_edited_yn='Y'` |
| [전달하기 (N곳)] | `POST /agent/forward`의 `departments`. `action_log`는 솔루션당 1행, `forward_depts`에 코드를 쉼표로 저장 |

- **[확정 2026-10-07] 전달 상태와 쿨다운**: [전달하기]를 누르면 "부서 전달 완료"로 표시하고, 부서의 처리 완료는 따로 추적하지 않습니다. 쿨다운은 상담자가 확정한 시점(톡 [발송하기] 또는 부서 [전달하기])부터 14일입니다. 시계는 `clock: simulated`(기본)면 '기준일 + 현재 시각', `real`이면 실제 시각입니다.
- **목업과 카탈로그가 어긋나는 곳** `[팀 결정 필요]`: ① 목업 예시(요금제 조정 승인 요청)는 T01 사례인데 확정 카탈로그의 T01에는 내부 전달 솔루션이 없음, ② 카탈로그 부서명과 목업 부서 8곳의 매핑은 추정(케어서비스운영팀→CARE, 고객센터 전담팀→CS, 품질보증팀·A/S 운영팀→QUALITY, 물류·A/S 부서→PARTS, 구독상품기획팀·제품기획팀→PRICING). 매핑 없는 이름: 영업관리팀, 지점 책임자, 새 담당 매니저, 지점 교육 담당, A/S 부서. 쓰이지 않는 칩: 설치·이전센터, ThinQ 앱팀, 계약·법무팀, ③ 처리 기한 영업일 3일은 목업 예시값 `[가정]`.

## 5. 예측 소스와 facts (DB → Agent)

`SqlSource`는 구독 1건에 대해 최신(또는 지정 기준일) `churn_prediction`과 `churn_prediction_type`(SHAP 근거 JSON, 라벨은 피처 사전), `subscription`, `customer`(이름·광고 수신 동의), `product_profile`, 이벤트(`voc_contact`, `care_visit`, `usage_thinq`, `payment`)를 읽어 AgentInput을 만듭니다.

- **facts**(자격 판정용, LLM에 보내지 않음): 구독 속성에서 `days_to_end`, `days_to_promo_end`, `months_since_start`, `days_to_trial_end`(시작일 + 7일), `days_to_contract_year_end`, 신모델·단종 여부를, 이벤트에서 30일 내 요금·해지 문의 수, 90일 내 이사 문의, 태도·영업 불만 수, 사용법 문의, 1차 응대 미해결, 미적용 할인, 마지막 사용 경과일, 최근 케어·방문 경과일, 짧은 케어 수(20분 미만 `[가정]`), 예정 방문, 30일 내 미이행·취소·지연, 계약 연도 남은 케어 수, 성수기까지 일수(제품군별 `[가정]`, 없는 제품군은 '계절 없음'), 열린 A/S, 같은 증상 A/S 수를 계산합니다.
- **사건성 값 3개는 전용 컬럼에서 읽습니다** `[확정 2026-10-08]`: 매니저 교체 확정(`subscription.manager_change_confirmed_yn` → `S-T07-D`), 점검 이상(가장 최근 완료된 정기점검이 30일 안이고 `care_visit.inspection_anomaly_yn = 'Y'` → `S-T09-C`), 예상 수리 기간(열린 A/S의 `expected_complete_date - as_received_date` 최댓값 → `S-T10-D`). 값이 없으면(열린 A/S에 두 날짜가 없는 경우) `None`이고, `required` 조건이라 그 솔루션만 후보에서 빠집니다(사실과 다른 안내 방지).
- **message_facts**(고객 공개 값, ▶ 항목에 사용): 월 구독료, 약정 기간·종료일·남은 일수, 프로모션 종료일, 케어 자동 연장일, 이용 기간, 지금까지 낸 구독료, 올해 남은 케어·사용 기한, 체험 기간 종료일, 최근 케어일·방문일, 다음 방문 예정일, 마지막 사용(○일 전), 예상 수리 기간. 정의는 `agent/facts.py`의 `MESSAGE_FACT_DEFS`이며 라벨·표시 형식(예: "2026년 11월 14일", "39,900원", "1회")까지 코드가 정합니다.

샘플 7건(`agent/sample_data.py`)은 경로마다 하나씩 있습니다: 남은 케어 안내 + 놓친 할인(광고), 정보성 알림톡 2건 합치기 + 협업 전달, 열린 A/S + 전담자 배정(예상 수리 기간이 없어 대체품 안내 제외), 방문 평가 1건, 유형 없음, 설문 추천 구간, 후보 0건 → 만족도 조사 대체.

## 6. OpenAI 연동

- 키: `OPENAI_API_KEY` 환경변수만 사용(설정 파일에는 변수 이름만). 키가 없으면 앱은 기동하고 템플릿 전용으로 동작하며 `/health`와 결과 `meta.llm_status`에 표시합니다. 프록시·호환 서버가 필요하면 `OPENAI_BASE_URL`.
- 호출: Chat Completions + JSON Schema Structured Outputs(`strict`). `solution_id`는 이번 요청의 후보 enum으로 제한하고, 검증기가 다시 확인합니다. `temperature`는 설정했을 때만 보냅니다(일부 추론형 모델은 기본값 외 temperature를 거부).
- 재시도: SDK 자동 재시도는 끄고(`max_retries=0`) `fallback.py`가 첫 호출 10초, 재시도 8초, 전체 15초로 통제합니다. 타임아웃·키 없음은 재시도하지 않고, 400·401·403·404 같은 요청 오류도 재시도하지 않으며, 429·5xx·상태 코드 없는 오류(빈 응답·거부·길이 초과·필터)·검증 실패는 1회 재시도합니다. 오류 요약(200자)은 `meta.llm_errors`에 남기고, 메시지에 섞여 오는 키 조각(`sk-…`, `Bearer …`)은 `[가림]`으로 바꿉니다.
- LLM으로 나가는 데이터: 제품군·월 구독료·약정·종료일 등 구독 속성 일부, 예측·유형·SHAP 근거, 맥락 요약, 고객 공개 값(라벨·표시 문자열), 후보 정보. 구독 ID, 고객 ID·이름, 광고 동의, facts(문의 횟수 등)는 보내지 않습니다.
- 모델명은 `llm.model`(현재 `gpt-4o-mini`는 자리표시) `[팀 결정 필요]`. 실제 API 호출은 키가 없어 하지 못했고, 실제 openai SDK(3.26.0)를 로컬 가짜 서버에 붙여 요청 형식·타임아웃·오류 경로를 확인했습니다(`verification_report.md`).

## 7. 검증기 v3 요약

| 항목 | 내용 |
|---|---|
| 정규화 | 고객에게 가는 글(본문·제목)을 NFKC 정규화하고 보이지 않는 문자를 지운 뒤 검사. 보이지 않는 문자가 있으면 LANG |
| 제목 | 본문과 같은 검사(숫자·금지 표현·내부 표현·식별자·연락처·링크·혜택)를 제목에도 적용. 40자, 광고성은 `(광고)` 시작, 비광고는 '광고' 표기 금지 |
| 숫자(R2·M3) | 값과 단위를 함께 비교(예: 12월 ≠ 12주). 허용: 구독 속성, `message_facts`, 그 후보의 문구 숫자. 한글 숫자(삼만 원, 오천 원, 만 원)·점 찍은 날짜도 읽음. 내부 분석값과 같으면 M3, 아니면 R2 |
| 표현(M7·M3) | 압박·보장(무조건, 100%, 손해, 놓치면, 후회, 보장, 절대, '~않으면 사라짐' 등), 이탈·위험·예측 표현(해지 가능성, 위험도, 예측, 리스크 등) |
| 혜택(M2) | 동의어 묶음(무료·무상·공짜·free, 할인·감면·discount, 캐시백·환급·돌려드림, 포인트·적립·리워드 등)으로 그 항목의 후보 문구에 없는 혜택 표현 차단 |
| 형식(M1) | 본문 400자, ▶ 4줄 이내(초과는 soft), 문장 수·존댓말은 soft |
| 기타 | 영어 문장(4단어 이상 연속)·URL·도메인·시스템 용어·소문자·전각 ID 차단, 형식이 깨진 출력은 예외 없이 SCHEMA로 기록 |
| 팀 금지어 | `config.yaml`의 `llm.banned_phrases_extra`에 넣은 표현을 M7로 추가 검사(기본은 비어 있음) |

자세한 규칙 표는 `agent_validator_design.md` §2.2.

## 8. 실행과 테스트

```
cd churn_agent
pip install -r requirements.txt
python -m pytest -q tests                  # PostgreSQL 10건은 건너뜀
bash scripts/pg_test.sh                    # 임시 PG16을 띄워 전체 249건 실행 후 정리
python -m agent.cli run S000101            # 데모 1건(mock LLM)
uvicorn agent.api:create_app --factory     # REST API (http://127.0.0.1:8000/docs)
# DB 모드: DATABASE_URL 설정 → python scripts/load_sample_data.py --reset → config.yaml에서 source: sql, storage.backend: sql
```

테스트 구성(총 249건): Rule Engine 구 케이스 38 + v3 15, 검증기 구 30 + v2 10 + v3 49(우회 입력 30·오탐 6·형식 오류 9 등), 폴백 구 8 + v3 10(팀 금지어 연결, 우선순위 정렬 포함), 데모 28(구 카탈로그로 기대 흐름 재현, 확정 카탈로그 경로 `expect_real` 고정, 광고 동의, 피처 사전 대조 등), OpenAI 어댑터 14(키 조각 가림 포함), API 13, 협업 부서 전달 9, 메시지 합치기 6, 만족도 조사 대체 5, 프롬프트 4, PostgreSQL 통합 10(저장소 6, DB → Agent 4). 핵심 방어 장치 15개를 하나씩 일부러 꺼 보는 변이 점검을 했고 15개 모두 테스트가 잡았습니다.

> DB 외래키는 두지 않습니다(`data_schema_design.md` §1). 관계 무결성은 앱 단 검사와 `sql/check_integrity.sql`(29개 관계의 고아 행 수)로 확인합니다.

## 9. 아직 비어 있는 것

1. **모델팀 배치 연동**: `SqlSource`는 `churn_prediction`·`churn_prediction_type`(SHAP JSON)이 채워져 있다는 전제입니다. 실제 XGBoost 배치가 `risk_rank_pct`(percent_rank)와 유형 비중을 넣는 코드는 모델팀 몫입니다.
2. **스키마 밖 데이터**: 예상 수리 기간, 케어 점검 이상 소견, 매니저 교체 확정, 부품 지원 종료일(`S-T03-C`는 단종 여부만 사용).
3. 인증·권한, 동시성(같은 항목 동시 결정), 실제 발송 연동, 설문 시뮬레이터 구현은 범위 밖입니다.
4. 결과 스냅샷(`agent_result.result_json`)에는 협업 요청서 머리말의 고객 ID·이름이 들어가므로 DB 접근은 내부 직원으로 제한해야 합니다.
