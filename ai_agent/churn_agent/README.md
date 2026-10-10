# churn_agent
구독 이탈 방지 Agent: 예측 결과 → Rule Engine → LLM(OpenAI) → 검증/폴백 → 상담자 확인 → 조치 기록.
설계는 `agent_code_design.md`, 점검 결과는 `verification_report.md`(이 폴더의 상위 폴더, 검토 패키지에서는 `docs/`). 모든 데이터는 가설 기반 합성 데이터입니다.

## 바로 돌려 보기 (데모 8건, 메모리 저장, mock LLM)
    pip install -r requirements.txt
    python -m pytest -q tests                  # PostgreSQL 시험은 건너뜀
    python -m agent.cli run S000101
    uvicorn agent.api:create_app --factory     # http://127.0.0.1:8000/docs

## PostgreSQL로 처음부터 끝까지 (DB → Agent)
    bash scripts/pg_test.sh                     # 임시 PG16을 띄워 전체 시험(PG 포함) 후 정리
    export DATABASE_URL=postgresql+psycopg2://user:pw@host:5432/db
    python scripts/load_sample_data.py --reset  # DDL·시드·샘플 7건 적재(public 스키마를 지움)
    # config.yaml: source: sql, storage.backend: sql
    uvicorn agent.api:create_app --factory

## OpenAI
    export OPENAI_API_KEY=...                   # 키는 환경변수로만. 파일·로그에 쓰지 않음
    # config.yaml: llm.provider: openai, llm.model: (계정에서 쓸 수 있는 모델)
키가 없으면 앱은 기동하고 템플릿으로만 동작합니다(`/health`의 `llm_status`).

## 폴더
- `agent/` 코드, `data/` 카탈로그·데모·피처 사전·부서 마스터, `sql/` DDL·시드·고아 행 검사, `scripts/` 카탈로그 생성·샘플 적재·PG 시험, `tests/`
- 카탈로그는 `scripts/build_catalog.py "<엑셀 경로>"`로 다시 만든다(엑셀 원본의 '알림톡 보고서'·'유형요약' 시트).

## 실제 OpenAI 측정 (내 컴퓨터에서 실행 — 키를 대화·파일에 넣지 않음)

DB 없이 돕니다. 입력은 시연 12건을 담은 `data/demo_inputs_12.json`(가상 고객).

```bash
cd churn_agent
python -m venv .venv && source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 1) 키 없이 연습(모의 LLM): 스크립트가 도는지만 확인
python scripts/measure_llm.py --inputs data/demo_inputs_12.json --provider mock

# 2) 키를 이 터미널의 환경변수로만 설정 (파일·코드에 쓰지 않음)
export OPENAI_API_KEY=여기에_키                           # Windows PowerShell: $env:OPENAI_API_KEY="여기에_키"

# 3) 실제 측정 (모델명은 계정에서 쓸 수 있는 것으로, 단가는 OpenAI 가격표 기준 100만 토큰당 달러)
python scripts/measure_llm.py --inputs data/demo_inputs_12.json --provider openai --model <모델명> --repeats 3 --price-in <입력단가> --price-out <출력단가>
```
- 결과: `out/llm_measure_openai.md`(요약·사례별·생성 문구)와 `.json`. 키는 들어가지 않으며, 오류 문자열의 키 조각도 지웁니다.
- 공유가 필요하면 이 `.md` 파일만 보내면 됩니다(키 관련 내용 없음). 터미널 기록(history)에 키가 남지 않게, 쓴 뒤 터미널을 닫거나 `unset OPENAI_API_KEY` 하세요.
- 모델이 `temperature`를 거부하면 `config.yaml`의 `llm.temperature`는 `null`(기본) 그대로 두세요. 호출 한도(429)나 시간 초과는 Agent가 재시도/템플릿 폴백으로 처리하고 리포트에 집계됩니다.
- 시연 입력을 새로 뽑으려면(DB가 있을 때): `DATABASE_URL=... python scripts/export_inputs.py --cases ../datagen/out/demo_cases.csv --out data/demo_inputs_12.json`

### 프롬프트 변형 비교 (A/B)
`--prompt`로 시스템 프롬프트 변형을 고르면 결과 파일 이름에 변형이 붙어 서로 덮어쓰지 않는다(`out/llm_measure_openai_v3.2.md` 등).
변형: `v3.4`(현재 기본, = v3.2c9) / `v3.3` / `v3.2` / `v3.2c9`(v3.2 + 복합 후보 규칙 C9) / `base`(최종 점검 블록 없음).
같은 조건으로 표본을 키워 비교한다(키는 환경변수로만):

    python scripts/measure_llm.py --inputs data/demo_inputs_12.json --provider openai --model <모델명> --repeats 10 --prompt v3.2 --price-in <입력단가> --price-out <출력단가>

리포트에는 규칙별 실패 메시지와 솔루션 ID별 횟수, '솔루션별 실패 규칙' 표가 들어 있다.


## 설문 시뮬레이터와 신규 유형 후보 에이전트 (`feedback/`)
가설 기반 합성 설문과, 설문을 읽어 신규 이탈 유형·솔루션을 제안하는 두 번째 에이전트입니다. 모든 결과는 합성 데이터 기준의 파이프라인 검증이며 정확도가 아닙니다.
설계: `survey_simulator_design.md`(§9 방향, §10 구현, §11 후보 에이전트·실측). 키는 환경변수 `OPENAI_API_KEY`로만 읽고, DB 주소는 `DATABASE_URL`로만 읽습니다.

```
# 1) 설문 합성 (9,000건, 약 40초) -> CSV + 검증 리포트
python -m feedback.cli run --data ../data_out --out out/survey
# 2) 후보 생성: 모의(키 불필요) 또는 OpenAI (2단계 분류가 기본, 끄려면 --single-stage)
#    기본은 3회 실행해 과반(2회 이상)에서 반복된 후보만 남김(--runs 3, 비용 ×3). 한 번만 돌리려면 --runs 1
python -m feedback.cli candidates --in out/survey --provider mock --out out/cand
python -m feedback.cli candidates --in out/survey --provider openai --model gpt-5.4-mini --out out/cand
# 3) 사람이 검토: 목록 -> 미리보기 -> 채택/기각 (채택해도 솔루션은 비활성) -> 확인 후 활성화
python -m feedback.cli proposal list
python -m feedback.cli proposal approve 1 --reviewer yj --dry-run [--overrides review.json]
python -m feedback.cli proposal approve 1 --reviewer yj --reason "회의 결정"
python -m feedback.cli proposal reject 2 --reviewer yj --reason "기존 유형과 같음"
python -m feedback.cli proposal activate S-T11-A [--dept 고객경험팀]
```

| 파일 | 역할 |
|---|---|
| `feedback/questionnaire.py`, `survey_mapping.yaml`, `config.yaml` | 설문 문항·보기 코드, 유형↔불편 매핑, 확률 설정 |
| `feedback/simulator.py`, `deep.py`, `stage_f.py`, `stage_g.py`, `topics.py`, `pool.py` | 응답 합성(질문별 독립 난수), 숨은 주제(H-A·H-B) 배정, 주관식 문장 풀(시드 + 승인된 LLM 변형) |
| `feedback/population.py`, `store.py`, `validate.py`, `cli.py` | 모집단 어댑터, long 형식 저장(CSV·PostgreSQL), 6개 검증, 명령어 |
| `feedback/candidates.py` | 근거 묶음 -> (1단계 분류) -> LLM -> 검증기 -> 재시도 -> 폴백 -> `ai_proposal` 저장 |
| `feedback/recovery.py` | 후보가 숨은 주제를 얼마나 찾았는지(정답은 사후에만 붙임) |
| `feedback/approve.py` | 채택(`churn_type`·`solution_catalog`에 비활성 행 추가)·기각·활성화 |
| `scripts/build_sentence_pool.py` | 주관식 문장 변형을 LLM으로 만드는 오프라인 도구(후보 파일 -> 사람 검토 -> `sentence_pool_variants.yaml`) |
