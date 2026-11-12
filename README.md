# 구독 이탈 예측 & 유형별 맞춤 방지 AI Agent

LG전자 AX School AI 엔지니어 과정 Build2 (6인 팀, 2026.09.21 ~ 10.13).
가전 구독 고객의 **30일 내 해지를 예측**하고, 이유를 **10개 이탈 유형**으로 설명하고, 유형별 **조치를 AI Agent가 제안**하고, 설문으로 **새 유형을 발굴**하는 폐쇄 루프 프로젝트입니다.

> **모든 데이터는 가설 기반 합성 데이터이고 발송은 시뮬레이션입니다.** 성능 수치는 "파이프라인이 설계한 구조를 되찾는다"는 검증이지 현실 성능이 아닙니다. 자세한 한계는 [docs/04_평가_및_한계.md](docs/04_평가_및_한계.md).

## 화면

> 아래 화면은 `front/`(Streamlit)를 실제로 실행해 캡처한 것입니다(라이트 모드). 화면 오른쪽 위 토글로 다크/라이트를 바꿀 수 있고, 기본은 다크입니다. 데이터는 모두 합성 데이터입니다.

![전체 현황](docs/images/01-overview.png)

| | |
|---|---|
| ![고객 케어](docs/images/02-customer-care.png) | ![AI 추천 솔루션](docs/images/03-agent-plan.png) |
| **고객 케어** — 고객별 이탈 위험, 유형, 판단 근거(SHAP) | **AI 추천 솔루션** — Rule Engine 후보 안에서 조치와 문구 생성, 수정 후 승인/반려 |
| ![제품 분석](docs/images/04-product.png) | ![팀 분석](docs/images/05-team.png) |
| **제품 분석** — 6대 제품군 점유율·위험·VOC 키워드 | **팀 분석** — 부서별 인사이트와 AI 3줄 요약 |
| ![설문 분석](docs/images/06-survey.png) | ![조치 이력](docs/images/08-action-log.png) |
| **설문 분석** — 응답 현황, 불편했던 점, 원하는 조치 | **조치 이력** — 승인·반려·전달 기록 (발송은 시뮬레이션) |

## 배경과 목표

| | |
|---|---|
| 프로젝트명 | **LG CARE BETTER** — LG 구독 케어 이탈 방지 에이전트 ("말하지 않아도 고객을 먼저 찾아가는 LG 베스트 케어") |
| 팀 | 사랑해조 (6인) |
| 문제 | 고객은 불편해도 말하지 않고 조용히 해지하고, 관리자는 해지·민원이 접수된 뒤에야 압니다. 이탈 방어가 개인별이 아니라 사용 기간·제품 라인 같은 큰 범주 기준이고, 케어 매니저 방문은 1회 비용이 커서 해결책이 제한적입니다. (근거: 한국소비자원 가전 구독·렌탈 실태조사 2025.12, 현직자 멘토링) |
| 해결 | 이탈 위험 고객의 불만 유형을 미리 예측하고 유형별 솔루션을 제안합니다. **관리자는 승인만** 합니다. |
| 사용자 | LG 가전 구독 케어 관리자(상담자) |

### 기대 효과 지표 (제안서의 3개년 **목표**이며 측정된 결과가 아닙니다)

| 단계 | 지표 | 도입 전 → 1년차 → 2년차 → 3년차 |
|---|---|---|
| DX 트래커 | 케어 매니저의 에이전트 이용률 | 60% → 80% → 90% |
| 고객가치 | 해지 의사 전에 먼저 케어받은 이탈 위험 고객 비율 | 20% → 40% → 60% |
| 경영성과 | 도입 전 대비 이탈률 감소 | ▼3% → ▼8% → ▼15% |

## 팀과 역할

| 이름 | 역할 | 담당 |
|---|---|---|
| 서성민 | Project Lead · Full-stack · AI Agent | 프로젝트 전체 진행 총괄, 백엔드·프런트엔드 개발, AI Agent 구현 |
| 이용직 | Data Architecture · AI Pipeline | 데이터 스키마 설계, 전체 파이프라인 설계, AI Agent 파이프라인 설계·구현 |
| 신서연 | Data Analysis · DCX | 데이터 수집 및 분석, DCX, 발표 PPT 작성 |
| 김인욱 | Data Analysis · DCX | 데이터 수집 및 분석, DCX, 발표 PPT 작성 |
| 김연상 | Data Analysis · DCX | 데이터 수집 및 분석, DCX, 발표 PPT 작성 |
| 정유림 | Service Design · Presenter | 서비스 화면 기획 및 디자인, 발표 PPT 작성, 발표 |

저장소의 폴더와 담당의 대응: `model/`(Agent 엔진·파이프라인 설계) · `backend/` · `front/` · `docs/`(설계서와 제안서). 출처는 제안서 부록 "팀원별 역할 및 기여도"입니다.

## 목차
1. [구조](#1-구조)
2. [시스템 흐름](#2-시스템-흐름)
3. [빠른 시작](#3-빠른-시작)
4. [설정](#4-설정)
5. [화면 (front)](#5-화면-front)
6. [API (backend)](#6-api-backend)
7. [Agent 엔진 (model)](#7-agent-엔진-model)
8. [데이터](#8-데이터)
9. [테스트](#9-테스트)
10. [문서 (docs)](#10-문서-docs)
11. [알려진 한계와 주의](#11-알려진-한계와-주의)

---

## 1. 구조

```
.
├── front/            Streamlit 화면. backend API 만 호출하고 DB 에는 붙지 않는다
│   ├── app.py
│   ├── assets/       제품 이미지
│   ├── .streamlit/   Streamlit 테마 설정(config.toml)
│   └── requirements.txt
├── backend/          FastAPI + DB + Agent 연결
│   ├── api/          main.py(라우트) · handlers.py(로직) · engine.py(model/ 연결 어댑터)
│   │                 proposals.py(신규 유형 승인) · reports.py(집계) · ai_summary.py(Team Insight 요약)
│   ├── db/           conn.py(PostgreSQL/SQLite 공통) · schema.py · load.py(CSV 적재)
│   ├── agent_v1/     이전 엔진. 지금은 화면용 고객 상세(context.py)와 승인/반려 기록(service.py)만 사용
│   ├── scripts/      make_candidates.py(설문 → 신규 유형 후보) 외 점검 스크립트
│   ├── tests/        test_all.py (PostgreSQL 필요)
│   ├── data/catalog/ 솔루션 카탈로그 JSON
│   ├── data_in/      합성 데이터 CSV 15개 (약 50MB)
│   ├── config.py     설정(.env 읽기)
│   └── .env.example
├── model/            AI 파이프라인 전체: 데이터 생성 → 예측 모델 → Agent (팀원 제공 코드, 새 버전이 오면 해당 폴더만 교체)
│   ├── datagen/      합성 데이터 생성기 (모집단 30,000 구독, 이벤트, 라벨, 검증, 시연 12건)
│   ├── churn_model/  이탈 예측 모델: 피처 → XGBoost → SHAP 이탈 유형 → 5-겹 교차 적합 예측 (결과 리포트 out/*.md)
│   ├── agent/        Agent 엔진: rules(Rule Engine) · llm · validation(검증기·폴백) · service
│   ├── feedback/     설문 시뮬레이터 · 신규 유형 후보 에이전트
│   ├── data/         솔루션 카탈로그 · 피처 사전 · 부서 목록 · 데모 입력
│   ├── sql/          DDL · 카탈로그 시드 · 무결성 검사
│   ├── scripts/      카탈로그 생성 · 샘플 적재 · LLM 측정 · PostgreSQL 시험
│   ├── tests/        Agent 테스트 (datagen/tests, churn_model/tests 는 각 폴더 안)
│   └── config.yaml   규칙 수치 · LLM 모델명
└── docs/             대표 문서 4종 · 제안서(pptx) · design/(상세 설계 정본, ERD) · images/(화면 캡처)
```

의존 방향은 `front → backend → model` 한 방향입니다. `backend/api/engine.py` 가 `model/` 의 엔진을 불러와 우리 DB·화면 응답 형태에 맞춥니다.

> 백엔드에 적재하는 CSV(`backend/data_in/data_out/`)는 `model/datagen` 이 만든 합성 데이터와 `model/churn_model` 이 만든 예측 결과(`churn_prediction*.csv`)입니다. 학습된 모델 파일(`xgb_model.json`)은 용량·재현성 때문에 저장소에 없고, 아래 명령으로 다시 만듭니다.

## 2. 시스템 흐름

```
CSV ─▶ DB ─▶ ① 입력 조립 ─▶ ② Rule Engine ─▶ ③ LLM ─▶ ④ 검증기 ─▶ ⑤ 템플릿 폴백 ─▶ ⑥ 상담자 승인/반려 ─▶ action_log
                            (후보 화이트리스트)  (후보 중 선택·문구)  (실패 시 1회 재시도)

설문 5종 ─▶ 응답 시뮬레이터 ─▶ 집계 ─▶ 후보 Agent(3회 합의) ─▶ ai_proposal ─▶ 관리자 승인 ─▶ 새 유형(비활성으로 등록)
```

핵심 원칙은 **"LLM을 믿는 구조가 아니라 확인하는 구조"** 입니다.

| 원칙 | 구현 |
|---|---|
| 선택지는 규칙이 정한다 | LLM 은 Rule Engine 이 통과시킨 후보 안에서만 고를 수 있다 (`solution_id` enum) |
| 정답은 분리한다 | `synth_*` 테이블(정답 라벨)은 Agent 가 읽지 않는다 |
| LLM 이 틀려도 해가 없다 | 검증기 → 1회 재시도 → 템플릿 폴백. 키가 없거나 타임아웃이어도 템플릿으로 동작 |
| 사람이 결정한다 | 승인해도 실제 발송은 없고 `action_log` 에만 기록 |
| 새 유형은 한 번에 확정하지 않는다 | 최소 지지 수 · 3회 합의 · 관리자 승인 · 비활성으로 시작 |

### 이탈 유형 10가지

| ID | 유형 | ID | 유형 |
|---|---|---|---|
| T01 | 구독료 부담형 | T06 | 방문 일정 불편형 |
| T02 | 해지·이사 장벽형 | T07 | 매니저 품질 불만형 |
| T03 | 재계약·갈아타기 갈등형 | T08 | 영업 압박형 |
| T04 | 사용 빈도 저하·방치형 | T09 | 제품 고장·성능 불만형 |
| T05 | 케어 부실·피해형 | T10 | AS·고객센터 불만형 |

대상 제품군은 의류관리기·건조기·냉장고·세탁기·에어컨·정수기 6종, 데이터는 30,000 구독입니다.

## 3. 빠른 시작

필요한 것: Python 3.12. (PostgreSQL 은 선택 — 없으면 SQLite 파일로 시작)

### 3-1. 백엔드

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate                  # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                  # Mac/Linux: cp .env.example .env
```

저장소에 포함된 CSV 15개(`customer`, `subscription`, `usage_thinq`, `care_visit`, `voc_contact`, `payment`, `notice_log`, `care_manager`, `churn_prediction`, `churn_prediction_type`, `synth_churn_label`, `synth_truth_type`, `survey_send`, `survey_response`, `survey_answer`)를 DB 에 적재합니다.

```bash
python -m db.load --data data_in/data_out --reset
python scripts/make_candidates.py --in data_in/data_out --provider mock   # (선택) 신규 유형 후보 생성
uvicorn api.main:app --port 8000                                          # 문서: http://127.0.0.1:8000/docs
```

> `--reset` 은 신규 유형 승인 기록과 실행 이력(`agent_run`, `action_log`)을 지웁니다.

### 3-2. 프론트엔드 (새 터미널)

```bash
cd front
pip install -r requirements.txt
streamlit run app.py                    # http://localhost:8501
```

백엔드 주소가 다르면 사이드바의 "API 주소"를 바꾸거나 환경변수 `API_URL` 을 지정하세요. 서버가 꺼져 있으면 화면이 안내 메시지를 띄우고 멈춥니다.

### 3-3. 데이터·예측·Agent 를 처음부터 다시 만들기 (model)

저장소에 있는 CSV 를 쓰면 이 단계는 필요 없습니다. 합성 데이터부터 예측까지 다시 만들 때만 따라 하세요. 시드는 42 로 고정되어 같은 결과가 나옵니다. 모든 명령은 `model/` 에서 실행합니다.

```bash
cd model
pip install -r requirements.txt                              # Agent
pip install -r datagen/requirements.txt -r churn_model/requirements.txt

# ① 합성 데이터 (30,000 구독)
python -m datagen.config                                     # 설정 검증과 기대 규모
export DATABASE_URL=postgresql+psycopg2://USER@HOST/DB       # 비밀번호는 환경변수/.pgpass 로, 코드에 쓰지 않음
python -m datagen.load_pg --url "$DATABASE_URL"              # 생성 → 적재 → 무결성 검사 → 임시 예측(stub)

# ② 예측 모델 (XGBoost + SHAP 유형)
python -m churn_model.feature_report --data data_out         # M1 피처 점검
python -m churn_model.train --data data_out                  # M2 학습·평가 (out/m2_report.md)
python -m churn_model.shap_types --data data_out             # M3 SHAP 유형 산출·복원 검증 (out/m3_report.md)
python -m churn_model.run_m4 --data data_out --url "$DATABASE_URL"   # M4 5-겹 교차 적합 → 예측 저장 → DB 교체

# ③ Agent 단독 실행 (백엔드 없이)
python -m agent.cli run S000101                              # mock LLM 으로 데모 1건
python -m datagen.make_demo && python -m datagen.run_demo_agent --url "$DATABASE_URL"   # 시연 12건
uvicorn agent.api:create_app --factory                       # Agent 단독 REST API (/docs)
```

- 입력 Parquet/CSV 는 `datagen` 이 만듭니다. 정답 라벨(`synth_churn_label`)은 학습 정답으로만, 정답 유형(`synth_truth_type`)은 복원 검증에만 씁니다.
- 임시 예측(`model_version='stub-gen'`)은 학습된 모델 결과가 아니라 생성 확률로 채운 값입니다.
- 새 이탈 유형은 모델 재학습 전에는 Agent 가 쓰지 않습니다. 후보를 채택해도 유형과 비활성 솔루션만 등록됩니다.
- 신규 유형 후보는 기존 유형의 재서술일 수 있어, 관리자가 가장 가까운 기존 유형(`closest_type_id`)과 차이 설명(`difference`)을 보고 판단합니다. 승인 연동 함수는 `model/feedback/approve.py` 입니다.
- 생성 데이터와 학습 결과는 저장소에 없습니다(`model/.gitignore`). 리포트(`out/*.md`)만 남깁니다.

## 4. 설정

### `.env` (backend, 공유 금지)

| 변수 | 기본 | 설명 |
|---|---|---|
| `DATABASE_URL` | 비움 | 비우면 SQLite(`backend/churn.db`). PostgreSQL 예: `postgresql://postgres:비밀번호@localhost:5432/churn` |
| `OPENAI_API_KEY` | 비움 | 없으면 템플릿 문구만 사용 |
| `OPENAI_MODEL` | 비움 | 비우면 `model/config.yaml` 의 `llm.model`. 값이 있으면 이 값이 우선 |
| `LLM_MODE` | `auto` | `auto`(키 있으면 OpenAI) · `template`(LLM 안 씀) · `mock`(검증용 모의 LLM) |
| `LLM_TIMEOUT_SEC` | 40 | LLM 호출 제한 시간 |
| `SUMMARY_MODE` / `SUMMARY_MODEL` | `auto` / `gpt-5.4-mini` | Team Insight AI 요약. `off` 면 규칙 문장만 |
| `ALLOWED_ORIGINS` | 로컬 개발 주소들 | CORS 허용 목록(Streamlit 8501 포함) |

`.env` 의 `COOLDOWN_DAYS`, `COST_CAP` 등 옛 수치는 **현재 엔진에 영향이 없습니다**(`agent_v1` 전용).

### `model/config.yaml` (규칙·모델)

| 항목 | 값 | 의미 |
|---|---|---|
| `rules.cooldown_same_solution_days` | 14 | 같은 솔루션 재제안 금지 기간 (확정된 조치만 셈, 반려는 안 셈) |
| `rules.max_actions` | 2 | 동시 조치 최대 개수 |
| `rules.cost_cap_ratio` / `cost_cap_cumulative_ratio` | 1.0 / 2.0 | 1회 ≤ 월 요금×1.0, 90일 누적 ≤ 월 요금×2.0 (비용 있는 솔루션만) |
| `llm.model` | `gpt-5.4-nano` | 조치 문구 생성 |
| `llm.candidate_model` | `gpt-5.4-mini` | 신규 유형 후보 생성 |
| `llm.max_message_chars` | 400 | 알림톡 본문 한도 |

> `gpt-5.4-nano` 는 공식 페이지에 deprecated 표시가 있어 운영 모델은 팀 결정이 필요합니다.

## 5. 화면 (front)

| 탭 | 내용 | 주요 API |
|---|---|---|
| 전체 현황 | 가입 연도별 1순위 이탈 유형 도넛 차트, 전체 구독·위험 분포 | `/insights`, `/overview` |
| 고객 케어 | 위험도·검색 필터 고객 목록 → 페르소나·이탈 위험도·유형·판단 근거(SHAP) → **AI 추천 생성 → 문구 수정 → 승인/반려**, 협업 부서 직접 전달 | `/subscriptions`, `/plan`, `/runs/{id}`, `/approve`, `/reject`, `/forward` |
| 제품 분석 | 6대 제품군 점유율·위험 분포·최근 3개월·주요 유형·VOC 키워드 | `/insights`, `/overview` |
| 팀 분석 | CX혁신·구독운영·고객케어 부서별 추이, AI 3줄 요약, 심층 리포트 | `/insights` |
| 설문 분석 | 발송·응답 현황, 불편했던 점, 원하는 조치, 새 솔루션 아이디어 | `/survey/eda` |
| 신규 유형 | 설문에서 나온 신규 유형 후보 채택/기각/되돌리기, 솔루션 활성화 | `/proposals`, `/solutions/{id}/activate` |
| 조치 이력 | `action_log` 조회 (알림톡 발송 · 부서 전달 · 반려 필터) | `/actions` |

- 승인·반려는 **솔루션 항목별**로 보냅니다. 고객 문구와 협업 메모를 각각 고쳐도 서로 덮어쓰지 않습니다.
- 오른쪽 위 "라이트" 토글로 다크/라이트 모드를 전환합니다(원본 LG Care Better 화면과 같은 색 구성).
- 서버에 OpenAI 키가 없으면 "LLM 으로 문구 작성" 토글이 꺼진 채 템플릿 문구를 씁니다.
- 조치안은 `GET /runs/{id}` 로 다시 불러오므로 새로고침해도 이어서 볼 수 있습니다.

## 6. API (backend)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 연결 상태, DB 종류, LLM 상태(`ready` · `mock` · `unavailable: 이유`) |
| GET | `/stats` `/overview` `/insights` `/catalog` `/departments` | 집계, 유형·솔루션 목록, 부서 목록. `/insights` 는 60초 캐시 |
| GET | `/subscriptions` | 목록. `risk=HIGH,MID` · `type_id=T05` · `q=이름/ID` · `limit` · `offset` |
| GET | `/subscriptions/{id}` | 고객·구독·예측·유형·특징·최근 VOC·이력 |
| POST | `/subscriptions/{id}/plan` | Agent 실행(`use_llm`) → `run_id` 와 조치안, `rule_trace`(후보·제외 사유) |
| GET | `/runs/{run_id}` | 저장된 조치안과 기록 여부 |
| POST | `/runs/{run_id}/approve` | 승인 → `action_log` 기록. `solution_ids` · `part`(`customer`/`internal`) · `message_text` · `internal_memo` · `forward_depts` · `decision_note` · `edited` |
| POST | `/runs/{run_id}/reject` | 반려(사유 필수). 쿨다운·비용 누계에 세지 않음 |
| POST | `/subscriptions/{id}/forward` | Agent 제안 없이 상담자가 직접 요청한 협업 전달 기록 |
| GET | `/actions` | 조치 이력 |
| GET | `/survey/eda` | 설문 집계 |
| GET | `/proposals` | 신규 유형 후보와 채택된 신규 솔루션 |
| POST | `/proposals/{id}/approve` `/reject` `/restore` | 후보 채택·기각·되돌리기 |
| POST | `/solutions/{id}/activate` | 채택된 솔루션 활성화 (혜택제안은 `offer_cost` 필수) |

- 승인 시 같은 고객에게 같은 조치(같은 대상)는 쿨다운 기간 안에서 한 번만 기록합니다. 새로 기록된 것이 없으면 `status: "skipped"` 와 이유를 돌려줍니다.
- 오류는 없는 대상 `404`, 잘못된 요청 `400` 입니다.

## 7. Agent 엔진 (model)

| mode | 조건 | LLM 호출 |
|---|---|---|
| `ACTION` | HIGH/MID + 유형 1~3개 | 예 |
| `NO_TYPE` | HIGH/MID + 유형 없음 → 만족도 조사 제안 | 예 |
| `SURVEY_ONLY` | LOW 중 위험 순위 20~30% | 아니오(템플릿) |
| `NONE` | 그 밖의 LOW | 아니오 |

**Rule Engine** (결정적): 경로 결정 → 후보 생성 → 광고 동의 · 자격 조건 · 쿨다운으로 제외 → 그리디 선택. 광고 동의 값이 없으면 미동의, 필수 사건 조건은 값이 없으면 후보에서 제외합니다.

**검증기**: 형식, 숫자(입력에 있는 값인지), 내부 식별자·용어 노출, 압박·보장 표현, 광고 표기, 구분별 필수 요소, 언어(한자·키릴 문자 등)를 검사합니다. 결과(`outcome`)는 `pass` · `retry_pass` · `partial_template` · `full_template` · `no_action` 입니다.

**피드백 루프**(`model/feedback`, `backend/scripts/make_candidates.py`): 설문 응답 → 규칙 근거 묶음 → LLM → 검증 → 3회 합의 → `ai_proposal`. 승인된 새 유형(T11+)과 솔루션은 `active_yn='N'` 으로 시작합니다.

설계 전문은 [docs/03_AI_Agent_설계서.md](docs/03_AI_Agent_설계서.md).

## 8. 데이터

| 구분 | 테이블 |
|---|---|
| 핵심 | `customer`, `subscription` |
| 이벤트 | `usage_thinq`, `care_visit`, `voc_contact`, `payment`, `notice_log` |
| 예측 결과 | `churn_prediction`(구독당 1행), `churn_prediction_type`(유형 1~3행) |
| 참조 | `care_manager`, `churn_type`, `solution_catalog` |
| 운영 기록 | `agent_run`, `action_log` |
| 설문 | `survey_send`, `survey_response`, `survey_answer`, `ai_proposal` |
| 정답(Agent 접근 금지) | `synth_churn_label`, `synth_truth_type` |

스키마 의도와 합성 데이터 생성 규칙은 [docs/02_데이터_스키마_설계서.md](docs/02_데이터_스키마_설계서.md). 합성 데이터 CSV 는 `backend/data_in/data_out/` 에 포함되어 있습니다.

## 9. 테스트

| 대상 | 명령 | 비고 |
|---|---|---|
| Agent 엔진 | `cd model && python -m pytest -q tests` | PostgreSQL 이 필요한 16건은 건너뜀 |
| 예측 모델 | `cd model && python -m pytest -q churn_model/tests` | `xgboost`, `shap` 필요 |
| 데이터 생성기 | `cd model && python -m pytest -q datagen/tests` | 약 40~100초. Parquet 테스트는 `pyarrow` 필요 |
| 백엔드 | `cd backend && python -m pytest tests/test_all.py -q` | **PostgreSQL 필수** (아래) |

백엔드 테스트는 테이블을 지우고 다시 만들기 때문에 **이름에 `test` 가 들어간 PostgreSQL DB** 에서만 실행됩니다.

```bash
# PowerShell
$env:TEST_DATABASE_URL="postgresql://postgres:비밀번호@localhost:5432/churn_test"
cd backend
python -m pytest tests/test_all.py -q          # tests/legacy 는 실행하지 않음
```

Linux/Mac 은 `bash scripts/pg_test.sh` 가 임시 PostgreSQL 을 띄워 시험하고 정리합니다. `model/` 의 PostgreSQL 시험은 `bash model/scripts/pg_test.sh` 입니다.

마지막 확인(2026-11-12, Windows): Agent 476 통과·16 건너뜀, 예측 모델 20 통과·2 건너뜀, 데이터 생성기 48 통과·1 건너뜀·1 실패(이 PC 에서 `pyarrow` DLL 이 정책으로 차단되어 Parquet 저장 테스트만 실패, 코드 문제 아님). 백엔드 테스트는 PostgreSQL 이 없어 실행하지 못했습니다.

## 10. 문서 (docs)

| 문서 | 내용 |
|---|---|
| [01 프로젝트 기획서](docs/01_프로젝트_기획서.md) | 문제 정의, 4단계 접근, 10개 유형, 시스템 흐름, 역할 |
| [02 데이터 스키마 설계서](docs/02_데이터_스키마_설계서.md) | 테이블 구성, 정답 분리, 합성 데이터 생성 규칙 |
| [03 AI Agent 설계서](docs/03_AI_Agent_설계서.md) | Rule Engine, LLM, 검증기, 폴백, 피드백 루프 |
| [04 평가 및 한계](docs/04_평가_및_한계.md) | 모델·유형·Agent·피드백 루프 결과와 한계 |
| 제안서 (pptx) | `docs/최종_CareBetterLG_구독케어_이탈방지_에이전트_제안서.pptx` — 문제 정의, DCX 과정(리뷰 수집 → 이탈 유형 → 솔루션 도출), Agent 워크플로우와 시연 화면, Performance Tracker |

위 4개 문서는 대표본이고, 상세 설계 정본은 [`docs/design/`](docs/design/) 에 있습니다. 먼저 `progress_summary.md` 를 읽으세요.

| 주제 | 파일 (`docs/design/`) |
|---|---|
| 이탈 유형 정의 · 전체 파이프라인 | `churn_types_10.md`, `overall_pipeline.md` (`.mmd`, `.png`) |
| 스키마 상세 / ERD | `data_schema_design.md`, `erd_schema.png` (`.svg`, `.mmd`) |
| Agent 입출력·Rule Engine·코드 설계 | `agent_design.md`, `agent_code_design.md` |
| 프롬프트 / 검증기 / 테스트 케이스 | `agent_prompt_design.md`, `agent_validator_design.md`, `agent_test_cases.md` |
| 솔루션 카탈로그 · 설문 시뮬레이터 | `solution_catalog_design.md`, `survey_simulator_design.md`, `survey_form_v2.html` |
| 구현 점검 / 진행 현황 | `verification_report.md`, `progress_summary.md` |

## 11. 알려진 한계와 주의

- **합성 데이터 + 순환 검증**: 정답을 만든 규칙을 모델이 되찾는 구조라 현실 일반화는 알 수 없습니다.
- **조치 효과는 가정값**입니다. 실제 효과는 A/B 테스트 없이는 알 수 없습니다.
- **비용 상한은 사실상 안전장치 역할**입니다(비용이 있는 활성 솔루션이 2건뿐).
- **인증이 없습니다.** 승인·반려·채택 API 는 누구나 호출할 수 있으므로 팀 서버에 올릴 때 접근 제한이 필요합니다.
- **`db.load --reset`** 은 실행 이력과 신규 유형 승인 기록을 지웁니다.
- `.env` 에는 API 키가 들어가므로 절대 커밋하지 마세요(`.gitignore` 에 포함).
- 실제 발송, 운영 수준 화면, 예측 모델 서빙(실시간 API)은 범위 밖입니다. 모델은 배치로 예측 결과를 만들어 DB 에 넣는 방식입니다.
