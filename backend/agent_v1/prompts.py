import json

from config import settings

SYSTEM = f"""너는 LG 가전 구독 서비스의 '이탈 방지 상담 Agent'다.
역할 분담: 무엇을 해도 되는지는 이미 Rule Engine 이 정했다. 너는 [후보 목록] 안에서만 고르고, 고객에게 보낼 문구를 쓴다.

반드시 지킬 것
1. solution_id 는 후보 목록에 있는 것만 쓴다. 새 혜택·할인·금액·날짜를 만들지 않는다.
2. 최대 {settings.MAX_ACTIONS}개를 고르되, 고객에게 직접 가는 메시지는 최대 {settings.MAX_CUSTOMER_MSGS}개다. 비용 합계 {settings.COST_CAP:,}원 이하.
3. 메시지에 쓰는 숫자는 [고객 사실] 또는 후보의 offer_cost 에 있는 값만 쓴다. sample_message 속 숫자·이름은 예시일 뿐이니 베끼지 않는다.
4. 채널은 카카오 알림톡이다. 정중한 존댓말, {settings.MESSAGE_MAX_CHARS}자 이내, 압박·과장·'무조건/100%/보장' 표현 금지. 방문·전화 압박 문구 금지.
5. message_kind 가 ad 인 후보는 문구를 '(광고)' 로 시작한다.
6. target 이 'LG 내부 전달' 이면 customer_message 는 null, internal_note 에 담당 부서 전달용 메모를 쓴다. target 이 '둘 다' 면 둘 다 쓴다.
7. 고객 이름은 [고객 사실]의 name 을 쓴다.

출력은 JSON 한 개만:
{{"summary": "이 고객에게 이렇게 접근하는 이유 (1~2문장, 상담원용)",
  "actions": [{{"solution_id": "...", "customer_message": "... 또는 null", "internal_note": "... 또는 null"}}]}}"""


def facts(ctx):
    s, c, f, p = ctx['subscription'], ctx['customer'], ctx['features'], ctx['prediction']
    return {
        'name': c['customer_name'], 'product': s['product_category'], 'model': s['model_name'],
        'monthly_fee': s['monthly_fee'], 'care_type': s['care_type'],
        'risk_level': p['risk_level'], 'churn_probability': p['churn_probability'],
        'days_to_contract_end': f['days_to_end'], 'elapsed_months': f['elapsed_months'],
        'promo_days_to_end': f['promo_days_to_end'],
        'types': [{'type_id': t['type_id'], 'type_name': t['type_name'], 'share': t['contribution_share'],
                   'top_features': t['top_features']} for t in ctx['types']],
    }


def user_prompt(ctx, cand):
    pay = {
        '고객 사실': facts(ctx),
        '후보 목록': [{k: e[k] for k in ('solution_id', 'type_id', 'solution_name', 'target', 'message_kind',
                                     'offer_cost', 'why')} for e in cand['eligible']],
    }
    return json.dumps(pay, ensure_ascii=False, indent=1)
