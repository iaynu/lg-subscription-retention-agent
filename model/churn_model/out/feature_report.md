# 피처 점검 리포트 (M1)

> 가설 기반 synthetic data. 정답 유형은 신호가 보이는지 진단하는 데만 썼고 모델 입력에는 쓰지 않습니다.

- 구독 30,000건 × 피처 72개 (사전 73개 중 `region_changed_flag`는 원천 컬럼이 없어 제외)
- 결측이 있는 피처 13개 (아래 표)

## 결측률(0 초과)

| 피처 | 결측률 | 결측의 뜻 |
|---|---|---|
| satisfaction_drop | 93.7% | 최근/이전 구간 중 한쪽에 평가 없음 |
| promo_days_to_end | 87.6% | 프로모션 없음 |
| repair_days_avg_365d | 79.1% | 최근 1년 A/S 없음 |
| days_since_last_as | 79.1% | A/S 이력 없음 |
| response_hours_avg | 67.7% | 최근 90일 상담 없음 |
| satisfaction_avg_180d | 67.0% | 최근 6개월 평가 없음 |
| care_duration_avg_min | 53.8% | 최근 1년 케어 방문 없음(자가관리·없음 포함) |
| days_overdue_visit | 53.8% | 방문관리 아님/케어 이력 없음 |
| visit_gap_ratio | 53.8% | 방문관리 아님/케어 이력 없음 |
| booking_lead_days_avg | 52.6% | 최근 1년 정기점검 없음 |
| manager_avg_rating | 47.0% | 담당 매니저 없음 |
| days_since_last_visit | 34.3% | 완료된 방문 없음 |
| billed_change_rate_3m | 3.4% | 결제 이력 4개월 미만 |

## 유형별 신호 분리도 (활성 vs 비활성, 표준화 평균 차)

각 유형에서 분리도가 가장 큰 피처 3개입니다. 자기 유형 그룹 피처가 위에 올라와야 모델이 유형을 구분할 수 있습니다.

| 유형 | 상위 피처 (분리도) |
|---|---|
| T01 | billed_change_rate_3m (+4.89), voc_fee_cnt_90d (+4.50), voc_totalcost_cnt_90d (+3.80) |
| T02 | cancel_processing_delay_days (+6.04), voc_cancel_cnt_90d (+5.72), voc_penalty_cnt_90d (+5.48) |
| T03 | voc_renewal_cnt_90d (+5.69), renewal_notice_contact_cnt (+4.84), renewal_notice_opened_yn (+4.20) |
| T04 | zero_use_weeks_12w (+5.59), run_count_trend_12w (-1.72), run_minutes_trend (-1.29) |
| T05 | care_result_complaint_cnt_90d (+3.61), post_care_revisit_req_cnt (+3.60), care_short_visit_cnt (+2.82) |
| T06 | schedule_change_req_cnt_365d (+4.44), reschedule_sum_365d (+3.83), visit_delay_cnt_365d (+3.54) |
| T07 | attitude_complaint_cnt_90d (+4.08), voc_manager_change_cnt_180d (+4.06), manager_change_actual_cnt_365d (+3.98) |
| T08 | sales_voc_cnt_180d (+6.05), sales_pitch_cnt_180d (+5.83), repitch_after_refusal_cnt (+4.70) |
| T09 | as_cnt_cum (+3.73), as_cnt_365d (+3.72), repeat_symptom_cnt_90d (+2.53) |
| T10 | voc_complaint_cnt_90d (+5.25), unresolved_voc_cnt (+3.97), parts_delay_cnt_365d (+3.84) |

유형별 최대 분리도: T01 4.89, T02 6.04, T03 5.69, T04 5.59, T05 3.61, T06 4.44, T07 4.08, T08 6.05, T09 3.73, T10 5.25
