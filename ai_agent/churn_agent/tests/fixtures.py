import copy
INP_A = {
 "mode": "ACTION",
 "subscription": {"product_category": "에어컨", "monthly_fee": 68000, "contract_months": 60, "end_date": "2026-12-20",
                  "days_to_end": 81, "care_type": "방문관리", "care_renewal_date": None, "promo_end_date": None},
 "prediction": {"churn_probability": 0.71, "risk_level": "HIGH", "risk_rank_pct": 0.04},
 "types": [
  {"type_rank": 1, "type_id": "T02", "type_name": "해지·이사 장벽형", "contribution_share": 0.53,
   "top_features": [{"feature": "voc_penalty_cnt_90d", "label": "최근 90일 위약금 문의 횟수", "value": 4, "shap": 0.41},
                    {"feature": "voc_cancel_cnt_90d", "label": "최근 90일 해지 문의 횟수", "value": 2, "shap": 0.22}]},
  {"type_rank": 2, "type_id": "T03", "type_name": "재계약·갈아타기 갈등형", "contribution_share": 0.35,
   "top_features": [{"feature": "days_to_end", "label": "계약 만료까지 남은 일수", "value": 81, "shap": 0.33},
                    {"feature": "renewal_notice_opened_yn", "label": "갱신 안내 열람 여부", "value": "N", "shap": 0.18}]}],
 "context": {"voc_summary": {"window_days": 90, "total": 7, "by_category": {"위약금문의": 4, "해지문의": 2, "갱신문의": 1}, "unresolved": 1}},
 "candidates": [
  {"solution_id": "S-T02-A", "type_id": "T02", "category": "정보제공", "name": "위약금 산정·약정 조건 안내",
   "detail": "중도 해지 시 위약금 산정 방식과 월 요금, 총 납부 금액을 안내", "offer_cost": 0, "fee_impact": "없음", "message_hint": "확인 방법(앱·상담) 안내"},
  {"solution_id": "S-T03-B", "type_id": "T03", "category": "혜택제안", "name": "갱신·재구독 혜택",
   "detail": "갱신 시 이용 가능한 혜택 안내(구체 조건은 신청 시 상담원 안내)", "offer_cost": 30000, "fee_impact": "없음", "message_hint": "총 비용·위약금 조건과 함께 안내"}],
 "constraints": {"max_actions": 2, "banned_phrases_extra": [], "config_numbers": [20, 30]}}
OUT_A = {
 "selected": [
  {"priority": 1, "solution_id": "S-T02-A", "type_id": "T02",
   "selection_reason": "최근 90일 위약금 문의가 4회로 반복되어, 혜택보다 위약금 산정 기준 안내가 우선입니다.",
   "evidence": [{"feature": "voc_penalty_cnt_90d", "value": 4}, {"feature": "voc_cancel_cnt_90d", "value": 2}],
   "message_text": "고객님, 최근 위약금과 해지 조건에 대해 문의해 주셨습니다. 현재 계약 기준으로 중도 해지 시 위약금이 산정되는 방식과 월 요금, 총 납부 금액을 함께 안내드립니다. 자세한 내용은 앱 또는 상담으로 확인하실 수 있습니다."},
  {"priority": 2, "solution_id": "S-T03-B", "type_id": "T03",
   "selection_reason": "만료까지 81일 남았고 갱신 안내를 아직 열람하지 않아 갱신 혜택을 안내할 시점입니다.",
   "evidence": [{"feature": "days_to_end", "value": 81}, {"feature": "renewal_notice_opened_yn", "value": "N"}],
   "message_text": "계약 만료(12월 20일)가 가까워지고 있습니다. 갱신 시 이용하실 수 있는 혜택을 총 비용과 위약금 조건과 함께 안내드립니다. 신청을 원하시면 말씀해 주세요."}],
 "summary": "위약금 문의가 반복되고 계약 만료가 임박한 고객입니다. 조건 안내를 먼저, 갱신 혜택을 다음 순서로 제안합니다."}
INP_B = {
 "mode": "NO_TYPE",
 "subscription": {"product_category": "세탁기", "monthly_fee": 52000, "contract_months": 48, "end_date": "2027-08-01", "days_to_end": 305, "care_type": "방문관리", "care_renewal_date": None, "promo_end_date": None},
 "prediction": {"churn_probability": 0.2, "risk_level": "MID", "risk_rank_pct": 0.14},
 "types": [], "context": {},
 "candidates": [{"solution_id": "S-NT-A", "type_id": "유형없음", "category": "의견수집", "name": "만족도 조사", "detail": "서비스 이용 경험 설문", "offer_cost": 0, "fee_impact": "없음", "message_hint": "열린 질문"}],
 "constraints": {"max_actions": 1, "banned_phrases_extra": [], "config_numbers": [20, 30]}}
OUT_B = {"selected": [{"priority": 1, "solution_id": "S-NT-A", "type_id": "유형없음",
  "selection_reason": "뚜렷한 이탈 유형이 식별되지 않았고 위험 순위가 상위 20% 안이어서 만족도 조사로 이유를 확인합니다.",
  "evidence": [{"feature": "risk_level", "value": "MID"}, {"feature": "risk_rank_pct", "value": 0.14}],
  "message_text": "고객님, 서비스를 이용하시면서 불편하셨던 점이 있었는지 의견을 들려주시면 개선에 반영하겠습니다. 간단한 설문으로 참여하실 수 있습니다."}],
 "summary": "뚜렷한 이탈 유형이 없는 고객이라 만족도 조사로 의견을 확인하는 것을 제안합니다."}
