# 더미 데이터 검증 리포트 (시드 42, N=30,000, 기준일 2026-09-30)

> 가설 기반 synthetic data입니다. 아래 수치는 설정한 생성 규칙이 목표를 맞췄는지 확인한 것이며 실제 이탈 구조를 반영하지 않습니다.

## 점검 결과: 15/15 통과

| 점검 | 결과 | 값 |
|---|---|---|
| 정상군 이탈률 ≈ 목표 | 통과 | 0.011 (목표 0.01 ±0.004) |
| 활성군 이탈률 ≈ 목표 | 통과 | 0.369 (목표 0.35 ±0.05) |
| 전체 이탈률 ≈ 목표 | 통과 | 0.083 (목표 0.08 ±0.015) |
| 유형 개수가 많을수록 이탈률 상승 | 통과 | {1: 0.162, 2: 0.399, 3: 0.613} |
| 순위 HIGH > MID > LOW 이탈률 | 통과 | {'HIGH': 0.577, 'MID': 0.161, 'LOW': 0.011} |
| 모든 유형 단독 활성 확률 ≥ 정상군×4 | 통과 | 최소 0.047 |
| 해지 접수일 기준일 +1~30일 | 통과 | [1, 30] |
| 논리 관계 고아 행 0 | 통과 | 10개 관계 |
| 구독 상태 전부 '활성'(누수 방지) | 통과 |  |
| 구독 ID 유일·라벨과 1:1 | 통과 |  |
| 기준일 이후 완료·접수 기록 없음(누수) | 통과 | {'care_visit(완료·지연완료)': '2026-09-30', 'voc_contact': '2026-09-30', 'payment.billing_month': '2026-09-01', 'usage week_start': '2026-09-21', 'notice_log.sent_at': '2026-09-30'} |
| 기준일 이후 이탈 정보가 이벤트에 없음 | 통과 |  |
| 결제 UNIQUE(구독,월) | 통과 |  |
| 사용량 UNIQUE(구독,주) | 통과 |  |
| NOT NULL 컬럼 결측 없음(구독·고객·라벨) | 통과 |  |

## 테이블 행 수

| 테이블 | 행 수 |
|---|---|
| care_manager | 140 |
| customer | 30,000 |
| subscription | 30,000 |
| usage_thinq | 750,663 |
| care_visit | 52,460 |
| voc_contact | 43,606 |
| payment | 174,960 |
| notice_log | 24,457 |
| synth_churn_label | 30,000 |
| synth_truth_type | 11,400 |

## 이탈률

| 구분 | 이탈률 | 목표 |
|---|---|---|
| 전체 | 8.3% | 8% |
| 정상군 | 1.1% | 1% |
| 유형 활성군 | 37.0% | 35% |

| 활성 유형 개수 | 이탈률 |
|---|---|
| 1개 | 16.2% |
| 2개 | 39.9% |
| 3개 | 61.3% |

| 생성 확률 순위 | 이탈률 |
|---|---|
| HIGH | 57.7% |
| MID | 16.1% |
| LOW | 1.1% |

이탈자 중 정상군 출신 비중: 11.0%

## 유형별 활성 건수와 단독 활성 평균 이탈 확률

| 유형 | 활성 건수 | 단독 활성 평균 확률 |
|---|---|---|
| T01 | 1,205 | 15.3% |
| T02 | 717 | 20.2% |
| T03 | 761 | 23.3% |
| T04 | 672 | 4.7% |
| T05 | 2,188 | 15.3% |
| T06 | 1,416 | 14.7% |
| T07 | 1,688 | 12.6% |
| T08 | 736 | 9.8% |
| T09 | 1,088 | 17.8% |
| T10 | 929 | 15.8% |

## 논리 관계(고아 행 수)

| 관계 | 고아 행 |
|---|---|
| subscription.customer_id -> customer.customer_id | 0 |
| subscription.care_manager_id -> care_manager.manager_id | 0 |
| usage_thinq.subscription_id -> subscription.subscription_id | 0 |
| care_visit.subscription_id -> subscription.subscription_id | 0 |
| care_visit.manager_id -> care_manager.manager_id | 0 |
| voc_contact.subscription_id -> subscription.subscription_id | 0 |
| payment.subscription_id -> subscription.subscription_id | 0 |
| notice_log.subscription_id -> subscription.subscription_id | 0 |
| synth_churn_label.subscription_id -> subscription.subscription_id | 0 |
| synth_truth_type.subscription_id -> subscription.subscription_id | 0 |
