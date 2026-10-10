# AI Agent — 구독 이탈 예측 & 유형별 맞춤 방지

> 모든 데이터는 **가설 기반 synthetic data**이며 실제 LG전자 데이터가 아닙니다. 모델·후보 에이전트 결과는 심어 둔 규칙을 다시 찾은 정도(순환 검증)로, 파이프라인 검증용이며 실제 성능이 아닙니다.

## 구성

| 폴더 | 내용 |
|---|---|
| `churn_agent/` | Agent 본체: Rule Engine → LLM(OpenAI) → 검증기 → 템플릿 폴백, REST API(FastAPI), 저장소(메모리·PostgreSQL), 설문 시뮬레이터·신규 유형 후보 에이전트·승인 흐름(`feedback/`), 테스트 490건 |
| `datagen/` | 더미 데이터 생성기(모집단 30,000건, 이벤트, 라벨, 검증, 시연 12건, PostgreSQL 적재) |
| `churn_model/` | 이탈 예측 모델(XGBoost + SHAP 유형), 5-겹 교차 적합 예측, DB 반영 |

설계 문서와 ERD는 저장소 루트의 `docs/ai_agent/`에 있습니다. 먼저 `progress_summary.md`를 읽으세요.

## 실행

```bash
cd churn_agent
pip install -r requirements.txt
python -m pytest -q tests                  # PostgreSQL 시험은 건너뜀
bash scripts/pg_test.sh                    # 임시 PostgreSQL 16에서 전체 시험
python -m agent.cli run S000101            # mock LLM으로 데모 1건
uvicorn agent.api:create_app --factory     # REST API (/docs)
```

생성 데이터는 저장소에 없습니다. 시드 42로 다시 만듭니다(`datagen/README.md`, `churn_agent/README.md`의 설문 시뮬레이터 절).

## 환경변수

비밀값은 코드·설정 파일에 쓰지 않고 환경변수로만 넣습니다. `.env.example`을 참고하세요.

| 변수 | 용도 |
|---|---|
| `OPENAI_API_KEY` | OpenAI 호출. 없으면 템플릿으로만 동작 |
| `DATABASE_URL` | PostgreSQL 연결 |

## 알아둘 점

- 새 이탈 유형은 모델 재학습 전에는 Agent가 쓰지 않습니다. 후보를 채택해도 유형과 비활성 솔루션만 등록됩니다.
- 신규 유형 후보는 기존 유형의 재서술일 수 있어 관리자가 가장 가까운 기존 유형(`closest_type_id`)과 차이 설명(`difference`)을 보고 판단합니다.
- 후보 승인의 REST API와 관리자 화면은 백엔드·프론트에서 구현합니다. 연동 함수는 `churn_agent/feedback/approve.py`입니다.
