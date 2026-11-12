# churn_model — 피처 → XGBoost → SHAP 유형 → 예측 반영 (가설 기반 synthetic data)

```bash
pip install -r churn_model/requirements.txt
python -m churn_model.feature_report --data data_out      # M1 피처 점검
python -m churn_model.train --data data_out               # M2 학습·평가 (churn_model/out/m2_report.md, xgb_model.json)
python -m churn_model.shap_types --data data_out          # M3 SHAP 유형 산출·복원 검증 (m3_report.md)
python -m churn_model.run_m4 --data data_out --url "$DATABASE_URL"   # M4 5-겹 교차 적합 → 예측 저장 → DB 교체 (m4_report.md)
python -m datagen.make_demo && python -m datagen.run_demo_agent --url "$DATABASE_URL"   # 시연 12건
python -m pytest -q churn_model/tests                     # 테스트(PG 테스트는 TEST_DATABASE_URL 필요)
```
- 입력은 `datagen`이 만든 Parquet(`data_out/`). 정답 라벨(`synth_churn_label`)은 학습 정답으로만, 정답 유형(`synth_truth_type`)은 복원 검증에만 쓴다.
- 합성 데이터 성능·유형 복원율은 심어 둔 규칙을 다시 찾은 정도라 실제 성능이 아니다.
- DB 접속 정보는 환경변수로만 받는다.
