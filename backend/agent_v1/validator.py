"""LLM 출력 검증기 — 규칙을 어기면 그 계획은 버리고 템플릿으로 대체한다."""
import re

from config import settings

BANNED = ['무조건', '100%', '보장', '반드시 해지', '위약금 면제', '위약금 없', '공짜', '절대', '지금 당장', '마지막 기회']
_NUM = re.compile(r'\d[\d,]*')


def _numbers(text):
    return {n.replace(',', '') for n in _NUM.findall(text or '')}


def allowed_numbers(ctx, cand):
    import json
    from agent_v1.prompts import facts
    nums = _numbers(json.dumps(facts(ctx), ensure_ascii=False))
    for e in cand['eligible']:
        nums |= _numbers(str(e['offer_cost'])) | _numbers(e['why'])
    return nums


def validate(plan, ctx, cand):
    """반환: (ok, 문제목록)"""
    errs = []
    if not isinstance(plan, dict) or not isinstance(plan.get('actions'), list):
        return False, ['형식 오류: actions 리스트 없음']
    by_id = {e['solution_id']: e for e in cand['eligible']}
    seen, cost, cust_msgs = set(), 0, 0
    allowed = allowed_numbers(ctx, cand)
    for a in plan['actions']:
        sid = a.get('solution_id')
        e = by_id.get(sid)
        if not e:
            errs.append(f'후보에 없는 solution_id: {sid}')
            continue
        if sid in seen:
            errs.append(f'중복 선택: {sid}')
        seen.add(sid)
        cost += e['offer_cost']
        cm, note = a.get('customer_message'), a.get('internal_note')
        needs_cust = e['target'] in ('사용자', '둘 다')
        needs_int = e['target'] in ('LG 내부 전달', '둘 다')
        if needs_cust:
            if not cm or not str(cm).strip():
                errs.append(f'{sid}: 고객 메시지 없음')
            else:
                cust_msgs += 1
                if len(cm) > settings.MESSAGE_MAX_CHARS:
                    errs.append(f'{sid}: 메시지 {len(cm)}자 > {settings.MESSAGE_MAX_CHARS}')
                if e['message_kind'] == 'ad' and not cm.lstrip().startswith('(광고)'):
                    errs.append(f'{sid}: 광고성인데 (광고) 표기 없음')
                bad = [b for b in BANNED if b in cm]
                if bad:
                    errs.append(f'{sid}: 금지 표현 {bad}')
                extra = _numbers(cm) - allowed
                extra = {n for n in extra if len(n) >= 3}  # 한 두 자리(예: 1탭, 48시간 등)는 무시
                if extra:
                    errs.append(f'{sid}: 근거 없는 숫자 {sorted(extra)}')
        elif cm:
            errs.append(f'{sid}: 내부 전달 건에 고객 메시지가 있음')
        if needs_int and not (note and str(note).strip()):
            errs.append(f'{sid}: 내부 전달 메모 없음')
    if len(plan['actions']) > settings.MAX_ACTIONS:
        errs.append(f'조치 수 {len(plan["actions"])} > {settings.MAX_ACTIONS}')
    if cust_msgs > settings.MAX_CUSTOMER_MSGS:
        errs.append(f'고객 메시지 {cust_msgs} > {settings.MAX_CUSTOMER_MSGS}')
    if cost > settings.COST_CAP:
        errs.append(f'비용 합계 {cost:,} > {settings.COST_CAP:,}')
    if not plan['actions']:
        errs.append('조치가 비어 있음')
    return (not errs), errs
