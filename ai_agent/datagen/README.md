# datagen — 더미 데이터 생성기 (가설 기반 synthetic data)

```bash
pip install -r datagen/requirements.txt
python -m datagen.config                      # 설정 검증과 기대 규모
python -m pytest -q datagen/tests             # 테스트(약 100초). PG 적재 테스트는 TEST_DATABASE_URL이 있을 때만
export DATABASE_URL=postgresql+psycopg2://USER@HOST/DB   # 비밀번호는 환경변수/.pgpass로, 코드에 쓰지 않음
python -m datagen.load_pg --url "$DATABASE_URL"          # 생성 → 적재 → 무결성 검사 → 임시 예측
python -m datagen.run_demo_agent --url "$DATABASE_URL"   # 시연 12건 Agent 실행
```
CSV/Parquet: `from datagen.export import export_tables`. 설명은 `data_schema_design.md` §6 '구현 요약'.
임시 예측(`model_version='stub-gen'`)은 학습된 모델 결과가 아니라 생성 확률로 채운 값입니다.
