"""Agent 본체: 컨텍스트 → Rule Engine → (LLM) → 검증 → 저장."""
import datetime as dt
import json

from config import settings
from agent_v1 import llm, prompts, rules, validator
from agent_v1.context import build_context


def load_catalog(s):
    return s.fetchall('SELECT * FROM solution_catalog WHERE active_yn=? ORDER BY type_id, priority', ('Y',))


# ---------- 템플릿(= LLM 이 없거나 검증에 실패했을 때) ----------
def _render(sol, ctx, why):
    name = ctx['customer']['customer_name']
    s = ctx['subscription']
    cm = note = None
    if sol['target'] in ('사용자', '둘 다'):
        # 고객용 문구에는 내부 판단 근거(why)를 넣지 않는다. 문구 다듬기는 LLM 의 역할.
        cm = (f"{name}님, {s['product_category']} 구독 이용에 도움이 되는 안내를 드려요.\n"
              f"▶ {sol['solution_name']}\n자세한 내용은 LG ThinQ 앱에서 확인하실 수 있어요.")
        if sol['message_kind'] == 'ad':
            cm = '(광고) ' + cm + '\n수신거부: 앱 > 설정 > 알림'
    if sol['target'] in ('LG 내부 전달', '둘 다'):
        note = (f"[내부 전달] 구독 {s['subscription_id']} ({s['product_category']} {s['model_name']}) — "
                f"{sol['solution_name']}. 근거: {why}.")
    return cm, note


def template_plan(ctx, cand):
    picked, cost, cust = [], 0, 0
    for e in cand['eligible']:
        if len(picked) >= settings.MAX_ACTIONS:
            break
        if cost + e['offer_cost'] > settings.COST_CAP:
            continue
        needs_cust = e['target'] in ('사용자', '둘 다')
        if needs_cust and cust >= settings.MAX_CUSTOMER_MSGS:
            continue
        cust += 1 if needs_cust else 0
        cost += e['offer_cost']
        cm, note = _render(e, ctx, e['why'])
        picked.append({'solution_id': e['solution_id'], 'customer_message': cm, 'internal_note': note})
    top = ctx['types'][0]['type_name'] if ctx['types'] else '-'
    return {'summary': f"주요 이탈 유형 '{top}' 기준으로 규칙을 통과한 조치를 우선순위대로 선택했어요.", 'actions': picked}


# ---------- 계획 생성 ----------
def make_plan(s, subscription_id, use_llm=True, catalog=None, llm_fn=None):
    ctx = build_context(s, subscription_id)
    if ctx is None:
        return None
    catalog = catalog or load_catalog(s)
    cand = rules.build_candidates(ctx, catalog)
    meta = {'llm_used': 'N', 'model': None, 'fallback_reason': None, 'validation_errors': []}
    plan = {'summary': '', 'actions': []}
    if not cand['gate_ok']:
        plan['summary'] = cand['gate_reason']
    elif not cand['eligible']:
        plan['summary'] = '지금 시점에 발동 조건을 만족하는 조치가 없어요.'
    else:
        want_llm = use_llm and (llm_fn is not None or llm.llm_enabled())
        if want_llm:
            try:
                fn = llm_fn or llm.chat_json
                raw = fn(prompts.SYSTEM, prompts.user_prompt(ctx, cand))
                ok, errs = validator.validate(raw, ctx, cand)
                if ok:
                    plan, meta['llm_used'], meta['model'] = raw, 'Y', settings.OPENAI_MODEL
                else:
                    meta['validation_errors'] = errs
                    meta['fallback_reason'] = '검증 실패 → 템플릿으로 대체'
            except llm.LLMError as e:
                meta['fallback_reason'] = f'LLM 호출 실패 → 템플릿으로 대체 ({e})'
        elif use_llm:
            meta['fallback_reason'] = 'OPENAI_API_KEY 없음 → 템플릿 사용'
        if meta['llm_used'] == 'N':
            plan = template_plan(ctx, cand)
    # 응답에 화면용 정보 붙이기
    by_id = {e['solution_id']: e for e in cand['eligible']}
    for a in plan['actions']:
        e = by_id[a['solution_id']]
        a.update(solution_name=e['solution_name'], type_id=e['type_id'], target=e['target'],
                 message_kind=e['message_kind'], offer_cost=e['offer_cost'], why=e['why'])
    plan['total_cost'] = sum(a['offer_cost'] for a in plan['actions'])
    result = {
        'subscription_id': subscription_id, 'as_of': ctx['today'],
        'prediction': ctx['prediction'], 'types': ctx['types'],
        'customer': {'customer_id': ctx['customer']['customer_id'], 'name': ctx['customer']['customer_name']},
        'plan': plan, 'meta': meta,
        'rule_trace': {'gate_ok': cand['gate_ok'], 'gate_reason': cand['gate_reason'], 'types_used': cand['types_used'],
                       'eligible': cand['eligible'], 'excluded': cand['excluded']},
    }
    return result


def save_run(s, result):
    run_id = s.insert_returning_id(
        'INSERT INTO agent_run (subscription_id, created_at, llm_used, model, plan_json) VALUES (?,?,?,?,?)',
        (result['subscription_id'], dt.datetime.now().isoformat(sep=' ', timespec='seconds'),
         result['meta']['llm_used'], result['meta']['model'], json.dumps(result, ensure_ascii=False)), 'run_id')
    result['run_id'] = run_id
    return run_id


def _decision_time(res):
    """조치 기록 시각 = 예측 기준일(시뮬레이션의 '오늘') + 현재 시각. 엔진의 clock=simulated 와 같은 축을 쓴다."""
    now = dt.datetime.now()
    try:
        day = dt.date.fromisoformat(str(res.get('as_of'))[:10])
    except ValueError:
        return now.isoformat(sep=' ', timespec='seconds')
    return dt.datetime.combine(day, now.time()).isoformat(sep=' ', timespec='seconds')


def _action_rows(res, solution_ids, part):
    """기록할 (조치, 대상) 목록. part: 'customer' | 'internal' | None(예전 방식: 조치의 target 그대로)."""
    out = []
    for a in res['plan']['actions']:
        if solution_ids is not None and a['solution_id'] not in solution_ids:
            continue
        if part == 'customer':
            if a['target'] in ('사용자', '둘 다'):
                out.append((a, '사용자'))
        elif part == 'internal':
            if a['target'] in ('LG 내부 전달', '둘 다'):
                out.append((a, 'LG 내부 전달'))
        else:
            out.append((a, a['target']))
    return out


def _text_for(a, tgt, message_text, internal_memo):
    if tgt == '사용자':
        return message_text if message_text else (a.get('customer_message') or a.get('internal_note') or '')
    return internal_memo if internal_memo else (a.get('internal_note') or a.get('customer_message') or '')


def approve_run(s, run_id, solution_ids=None, part=None, message_text=None, internal_memo=None,
                forward_depts=None, decision_note=None, edited=False):
    """상담원이 승인 → action_log 에 '시뮬레이션'으로 기록(실제 발송은 하지 않음).
    part 를 주면 고객 발송분/협업 전달분을 따로 승인할 수 있다('둘 다' 조치도 두 번 기록 가능).
    message_text·internal_memo 는 상담자가 고친 최종 문구, forward_depts 는 협업 전달 부서 이름 목록."""
    row = s.fetchone('SELECT * FROM agent_run WHERE run_id=?', (run_id,))
    if not row:
        return None
    res = json.loads(row['plan_json'])
    now = _decision_time(res)
    # 같은 고객에게 같은 조치(같은 대상)는 쿨다운 기간 안에서만 중복 기록을 막는다(반려·직접 요청 기록은 세지 않음)
    def _within(created):
        try:
            d0 = dt.datetime.fromisoformat(str(created)[:19].replace('T', ' '))
            return 0 <= (dt.datetime.fromisoformat(now) - d0).days < settings.COOLDOWN_DAYS
        except ValueError:
            return True
    done = {(a['solution_id'], a['target']) for a in
            s.fetchall("SELECT solution_id, target, created_at FROM action_log WHERE subscription_id=? AND status<>'반려' "
                       "AND solution_id<>'MANUAL'", (row['subscription_id'],)) if _within(a['created_at'])}
    depts = ','.join(str(d) for d in (forward_depts or [])) or None
    out = []
    for a, tgt in _action_rows(res, solution_ids, part):
        if (a['solution_id'], tgt) in done:
            continue
        aid = s.insert_returning_id(
            'INSERT INTO action_log (run_id, subscription_id, solution_id, type_id, target, message_kind, offer_cost, '
            'message_text, status, created_at, decision_note, forward_depts, memo_edited_yn) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (run_id, row['subscription_id'], a['solution_id'], a['type_id'], tgt, a['message_kind'],
             a['offer_cost'], _text_for(a, tgt, message_text, internal_memo), 'simulated', now,
             (decision_note or None), (depts if tgt == 'LG 내부 전달' else None), 'Y' if edited else 'N'), 'action_id')
        out.append(aid)
    return out


def reject_run(s, run_id, reason, solution_ids=None, part=None):
    """상담원이 반려 → action_log 에 status='반려' 로 남긴다(쿨다운·비용 누계에는 세지 않음). 사유 필수."""
    row = s.fetchone('SELECT * FROM agent_run WHERE run_id=?', (run_id,))
    if not row:
        return None
    res = json.loads(row['plan_json'])
    now = _decision_time(res)
    out = []
    for a, tgt in _action_rows(res, solution_ids, part):
        text = a.get('customer_message') if tgt == '사용자' else a.get('internal_note')
        aid = s.insert_returning_id(
            'INSERT INTO action_log (run_id, subscription_id, solution_id, type_id, target, message_kind, offer_cost, '
            'message_text, status, created_at, decision_note, memo_edited_yn) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (run_id, row['subscription_id'], a['solution_id'], a['type_id'], tgt, a['message_kind'],
             a['offer_cost'], text or '', '반려', now, reason, 'N'), 'action_id')
        out.append(aid)
    return out
