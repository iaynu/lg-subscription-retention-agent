"""팀원 Agent 엔진(model/agent: Rule Engine v3 → LLM → 검증기 → 템플릿 폴백)을 우리 DB·화면에 연결하는 어댑터.

- model/ 폴더는 팀원이 준 churn_agent 를 그대로 둔 것이다(수정 없음). 팀원이 새 버전을 주면 model/ 만 바꿔 끼우면 된다.
- 입력 조립(SqlSource)은 팀원 코드가 SQLAlchemy 를 쓰므로, 같은 계산을 우리 DB 어댑터(db/conn.py)로 읽도록 옮겼다.
  (계산식은 팀원 SqlSource 의 _context / _event_facts 를 그대로 상속해 쓴다.)
- 화면(UI)이 기대하는 응답 모양(plan.actions, rule_trace, meta ...)은 그대로 유지한다.
"""
import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path

from config import ROOT, settings

CORE = ROOT.parent / 'model'
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from agent.catalog import load_catalog  # noqa: E402
from agent.config import load_config  # noqa: E402
from agent.facts import derive_from_subscription  # noqa: E402
from agent.llm import prompt as _prompt  # noqa: E402
from agent.llm.base import LlmUnavailable, UnavailableLlm  # noqa: E402
from agent.llm.mock import RuleBasedMock  # noqa: E402
from agent.repo.memory import MemoryRepo  # noqa: E402
from agent.rules.engine import run_rule_engine  # noqa: E402
from agent.schemas import AgentInput  # noqa: E402
from agent.service import AgentService  # noqa: E402
from agent.sources import SqlSource  # noqa: E402

# 제품군별 정기점검 주기(개월) — 팀원 datagen 설정(product_profile) 값
VISIT_INTERVAL = {'의류관리기': 12, '건조기': 12, '냉장고': 12, '세탁기': 12, '에어컨': 12, '정수기': 4}


# ------------------------------------------------------------------ 날짜 정규화
def _date(v):
    if v is None or v == '':
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


_DATE_COLS = ('start_date', 'end_date', 'care_renewal_date', 'promo_end_date', 'contact_date', 'scheduled_date',
              'completed_date', 'as_received_date', 'expected_complete_date', 'week_start_date', 'billing_month',
              'as_of_date')


def _norm(row):
    out = dict(row)
    for k in _DATE_COLS:
        if k in out:
            out[k] = _date(out[k])
    for k, v in list(out.items()):
        if isinstance(v, Decimal):
            out[k] = int(v) if v == int(v) else float(v)
    return out


# ------------------------------------------------------------------ 입력 조립
class DbSource(SqlSource):
    """우리 DB(db/conn.py)에서 AgentInput 을 만든다. _context·_event_facts 는 팀원 코드를 그대로 쓴다."""

    def __init__(self, facts_cfg=None):
        self.cfg = facts_cfg or {}
        fd = json.loads((CORE / 'data' / 'feature_dictionary.json').read_text(encoding='utf-8'))
        self.labels = {f['feature']: f['display_ko'] for f in fd['features']}

    def get_input(self, s, subscription_id, recent_actions=None):
        p = s.fetchone('SELECT * FROM churn_prediction WHERE subscription_id=?', (subscription_id,))
        sb = s.fetchone('SELECT * FROM subscription WHERE subscription_id=?', (subscription_id,))
        if not p or not sb:
            return None
        p, sb = _norm(p), _norm(sb)
        as_of = p['as_of_date']
        cust = s.fetchone('SELECT customer_id, customer_name, ad_consent_yn FROM customer WHERE customer_id=?',
                          (sb['customer_id'],)) or {}
        types = s.fetchall('SELECT t.type_rank, t.type_id, ct.type_name, t.contribution_share, t.top_features_json '
                           'FROM churn_prediction_type t LEFT JOIN churn_type ct ON ct.type_id=t.type_id '
                           'WHERE t.subscription_id=? ORDER BY t.type_rank', (subscription_id,))
        lo = as_of - dt.timedelta(days=365)
        voc = [_norm(r) for r in s.fetchall('SELECT * FROM voc_contact WHERE subscription_id=?', (subscription_id,))]
        voc = [v for v in voc if v['contact_date'] and lo < v['contact_date'] <= as_of]
        visits = [_norm(r) for r in s.fetchall('SELECT * FROM care_visit WHERE subscription_id=?', (subscription_id,))]
        usage = [_norm(r) for r in s.fetchall('SELECT week_start_date, run_count FROM usage_thinq WHERE subscription_id=?',
                                              (subscription_id,))]
        usage = [u for u in usage if u['week_start_date'] and u['week_start_date'] <= as_of]
        pays = [_norm(r) for r in s.fetchall('SELECT billing_month, paid_amount FROM payment WHERE subscription_id=?',
                                             (subscription_id,))]
        paid_total = int(sum((x['paid_amount'] or 0) for x in pays if x['billing_month'] and x['billing_month'] <= as_of))
        pp = {'visit_interval_months': VISIT_INTERVAL.get(sb['product_category'])}

        sub = {'subscription_id': sb['subscription_id'], 'product_category': sb['product_category'],
               'monthly_fee': sb['monthly_fee'], 'contract_months': sb['contract_months'],
               'start_date': sb['start_date'].isoformat(), 'end_date': sb['end_date'].isoformat(),
               'days_to_end': (sb['end_date'] - as_of).days, 'care_type': sb['care_type'],
               'care_renewal_date': sb['care_renewal_date'].isoformat() if sb['care_renewal_date'] else None,
               'promo_end_date': sb['promo_end_date'].isoformat() if sb['promo_end_date'] else None,
               'new_model_released_yn': sb['new_model_released_yn'], 'model_discontinued_yn': sb['model_discontinued_yn']}
        facts, mfacts = derive_from_subscription(sub, as_of.isoformat(), self.cfg)
        f2, m2 = self._event_facts(as_of, sb, pp, voc, visits, usage, paid_total)
        facts.update(f2)
        mfacts.update(m2)
        types_out = []
        for t in types:
            feats = json.loads(t['top_features_json'] or '[]')
            types_out.append(dict(
                type_rank=t['type_rank'], type_id=t['type_id'], type_name=t['type_name'] or '',
                contribution_share=float(t['contribution_share']),
                top_features=[dict(feature=f['feature'], label=self.labels.get(f['feature'], f['feature']),
                                   value=f.get('value'), shap=f.get('shap', 0)) for f in feats]))
        return dict(as_of_date=as_of.isoformat(), subscription=sub,
                    customer={'customer_id': cust.get('customer_id'), 'customer_name': cust.get('customer_name'),
                              'ad_consent_yn': cust.get('ad_consent_yn')},
                    prediction={'churn_probability': float(p['churn_probability']), 'risk_level': p['risk_level'],
                                'risk_rank_pct': float(p['risk_rank_pct'])},
                    types=types_out, context=self._context(as_of, voc, visits), facts=facts, message_facts=mfacts,
                    recent_actions=recent_actions or [])


# ------------------------------------------------------------------ LLM (urllib — 별도 패키지 불필요)
class HttpLlm:
    """OpenAI Chat Completions(Structured Outputs). 팀원 OpenAIClient 와 같은 인터페이스(generate)."""

    def __init__(self, model, api_key, base_url, temperature=None):
        self.model, self.key, self.base, self.temperature = model, api_key, base_url, temperature
        self.status = 'ready'
        self.last_usage = None

    def generate(self, inp, note, timeout):
        import urllib.error
        import urllib.request
        body = {'model': self.model,
                'messages': [{'role': 'system', 'content': _prompt.SYSTEM_PROMPT},
                             {'role': 'user', 'content': _prompt.build_user_message(inp, note)}],
                'response_format': {'type': 'json_schema',
                                    'json_schema': {'name': 'agent_output', 'strict': True,
                                                    'schema': _prompt.response_schema(inp)}}}
        if self.temperature is not None:                      # null 이면 보내지 않음(일부 추론형 모델은 temperature 를 거부)
            body['temperature'] = self.temperature
        req = urllib.request.Request(self.base + '/chat/completions', data=json.dumps(body).encode('utf-8'),
                                     method='POST', headers={'Content-Type': 'application/json',
                                                             'Authorization': f'Bearer {self.key}'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            err = RuntimeError(f'OpenAI HTTP {e.code}: {e.read().decode("utf-8", "ignore")[:200]}')
            err.status_code = e.code
            raise err
        except TimeoutError as e:
            raise TimeoutError() from e
        except urllib.error.URLError as e:
            if 'timed out' in str(e.reason):
                raise TimeoutError() from e
            raise RuntimeError(f'OpenAI 연결 실패: {e.reason}')
        ch = (data.get('choices') or [{}])[0]
        u = data.get('usage') or {}                           # 측정용: 마지막 호출의 토큰 수
        self.last_usage = {'prompt': u.get('prompt_tokens'), 'completion': u.get('completion_tokens')} if u else None
        if ch.get('finish_reason') == 'length':
            raise RuntimeError('output truncated')
        if ch.get('finish_reason') == 'content_filter':
            raise RuntimeError('content filtered')
        msg = ch.get('message') or {}
        if msg.get('refusal'):
            raise RuntimeError('model refusal')
        return msg.get('content') or ''


def make_llm(mode='auto'):
    """LLM_MODE: auto(키 있으면 OpenAI) | openai | template(템플릿만) | mock(검증용 모의 LLM)."""
    m = settings.LLM_MODE
    if m == 'mock':
        x = RuleBasedMock()
        x.status = 'mock'
        return x
    if m == 'template' or not settings.OPENAI_API_KEY:
        reason = 'LLM_MODE=template' if m == 'template' else 'OPENAI_API_KEY 없음'
        x = UnavailableLlm(reason)
        x.status = f'unavailable: {reason}'
        return x
    llm_cfg = load_config()['llm']
    model = settings.OPENAI_MODEL or llm_cfg['model']          # .env 의 OPENAI_MODEL 이 있으면 우선, 없으면 model/config.yaml
    return HttpLlm(model, settings.OPENAI_API_KEY, settings.OPENAI_BASE_URL, llm_cfg.get('temperature'))


# ------------------------------------------------------------------ 서비스
class DbRepo(MemoryRepo):
    """결과·조치 이력은 우리 DB(agent_run, action_log)가 따로 저장하므로, 엔진에는 메모리 저장소만 준다."""


def load_catalog_with_ext(s):
    """팀원 카탈로그(JSON) + 관리자가 채택한 신규 솔루션(catalog_ext 테이블, 기본 비활성)."""
    cfg = load_config()
    p = Path(cfg['catalog_path'])
    p = p if p.is_absolute() else CORE / p
    cat = load_catalog(p)
    try:
        for r in s.fetchall('SELECT row_json, active_yn FROM catalog_ext'):
            row = json.loads(r['row_json'])
            row['active_yn'] = r['active_yn']
            row.setdefault('name', row.get('solution_name'))          # 채택 계획의 컬럼명 → 엔진 카탈로그 컬럼명
            row.setdefault('category', row.get('solution_category'))
            row.setdefault('detail', row.get('solution_detail'))
            cat.append(load_catalog_row(row))
    except Exception:
        s.rollback()
    return cat


def load_catalog_row(row):
    row = dict(row)
    row.setdefault('priority', 50)
    row.setdefault('eligibility', None)
    return row


_svc = {}


def _service(s, llm):
    cfg = load_config()
    cfg['llm'] = dict(cfg['llm'], provider=('openai' if isinstance(llm, HttpLlm) else cfg['llm']['provider']),
                      model=getattr(llm, 'model', cfg['llm']['model']),
                      first_timeout_s=min(max(settings.LLM_TIMEOUT_SEC, 10), 30), retry_timeout_s=15,
                      deadline_s=max(settings.LLM_TIMEOUT_SEC, 25))
    cfg['clock'] = 'simulated'
    return cfg, AgentService(cfg, load_catalog_with_ext(s), llm, DbRepo())


TARGET = {'고객 메시지': '사용자', '상담·앱 안내': '사용자', '내부 전달': 'LG 내부 전달', '전사 개선': 'LG 내부 전달'}


def make_plan(s, subscription_id, use_llm=True):
    """화면이 쓰는 plan 응답을 만든다(기존 agent_v1.service.make_plan 과 같은 모양)."""
    from agent_v1.context import build_context
    ctx = build_context(s, subscription_id)          # 화면용 상세(예측·유형·SHAP 값)
    if ctx is None:
        return None
    acts = s.fetchall('SELECT solution_id, created_at, status, offer_cost FROM action_log WHERE subscription_id=?',
                      (subscription_id,))
    # 반려는 쿨다운·비용 누계에 세지 않는다(엔진은 cooldown_count_statuses=['조치완료'] 만 센다)
    recent = [dict(solution_id=a['solution_id'], action_status=('반려' if a['status'] == '반려' else '조치완료'),
                   offer_cost=int(a['offer_cost'] or 0), created_at=str(a['created_at'])[:10]) for a in acts]
    src = DbSource(load_config().get('facts'))
    ai = src.get_input(s, subscription_id, recent)
    ai = AgentInput(**ai).model_dump()
    llm = make_llm() if use_llm else _template_only()
    cfg, svc = _service(s, llm)
    res = svc.run(ai)
    rr = run_rule_engine(ai, svc.catalog, svc._rules_cfg())
    by_id = svc.by_id

    actions = []
    for it in res['selected']:
        c = by_id[it['solution_id']]
        tgt = TARGET.get(c.get('delivery_kind'), '사용자')
        internal = (it.get('internal_memo') or '').strip() or None
        cust_msg = (it.get('message_text') or '').strip() or None
        if c.get('delivery_kind') == '고객 메시지' and c.get('internal_forward_yn') == 'Y' and internal:
            tgt = '둘 다'
        actions.append({
            'solution_id': it['solution_id'], 'customer_message': cust_msg if tgt in ('사용자', '둘 다') else None,
            'internal_note': internal if tgt in ('LG 내부 전달', '둘 다') else None,
            'solution_name': c.get('name') or it.get('title'), 'type_id': it['type_id'], 'target': tgt,
            'message_kind': 'ad' if c.get('ad_yn') == 'Y' else 'info', 'offer_cost': int(c.get('offer_cost') or 0),
            'why': it.get('selection_reason') or c.get('detail') or '', 'channel': c.get('channel'),
            'buttons': it.get('buttons') or [], 'title': it.get('title'), 'message_source': it.get('message_source'),
            'priority': it.get('priority')})
    outcome, llm_ok = res['outcome'], res['outcome'] in ('pass', 'retry_pass', 'partial_template')
    errs = res.get('meta', {}).get('llm_errors') or []
    reason = None
    if not llm_ok:
        if not use_llm:
            reason = '템플릿 모드(요청에서 LLM 사용 안 함)'
        elif isinstance(llm, UnavailableLlm):
            reason = f'{llm.reason} → 템플릿 사용'
        elif outcome in ('survey_template', 'no_action'):
            reason = None
        else:
            reason = '검증 실패/LLM 오류 → 템플릿으로 대체' + (f' ({errs[0]})' if errs else '')
    plan = {'summary': res.get('summary') or ('조건을 만족하는 조치가 없어요.' if not actions else ''), 'actions': actions,
            'total_cost': sum(a['offer_cost'] for a in actions),
            'limits': _limits(res.get('limits') or {}, ai['subscription']['monthly_fee'])}
    cand = []
    for c in rr['candidates']:
        cand.append({'solution_id': c['solution_id'], 'solution_name': c.get('name'), 'type_id': c['type_id'],
                     'priority': c.get('priority'), 'target': TARGET.get(c.get('delivery_kind'), '사용자'),
                     'offer_cost': int(c.get('offer_cost') or 0), 'why': c.get('detail') or '',
                     'type_share': c.get('contribution_share')})
    excl = [{'solution_id': e['solution_id'], 'reason': f"{_REASON.get(e['reason'], e['reason'])}" + (f" — {e['detail']}" if e.get('detail') else '')}
            for e in res['excluded']]
    meta = {'llm_used': 'Y' if llm_ok else 'N', 'model': (getattr(llm, 'model', None) or ('mock' if getattr(llm, 'status', '') == 'mock' else None)) if llm_ok else None,
            'fallback_reason': reason, 'validation_errors': res.get('failed_checks') or [], 'outcome': outcome,
            'mode': res['mode'], 'prompt_version': res['meta'].get('prompt_version'), 'attempts': res.get('attempts')}
    return {
        'subscription_id': subscription_id, 'as_of': ctx['today'],
        'prediction': ctx['prediction'], 'types': ctx['types'],
        'customer': {'customer_id': ctx['customer']['customer_id'], 'name': ctx['customer']['customer_name']},
        'plan': plan, 'meta': meta,
        'rule_trace': {'gate_ok': res['mode'] != 'NONE', 'gate_reason': '' if res['mode'] != 'NONE' else '위험도가 낮아 조치 대상이 아니에요',
                       'types_used': [t['type_id'] for t in ai['types']], 'eligible': cand, 'excluded': excl,
                       'mode': res['mode'], 'fallback': res.get('fallback')},
        'forward': res.get('forward'), 'messages': res.get('messages'), 'engine': 'team-v3'}


_REASON = {'NO_AD_CONSENT': '광고 수신 미동의', 'NOT_ELIGIBLE': '발동 조건 불충족', 'COOLDOWN_SOLUTION': '쿨다운(최근 조치)',
           'MAX_ACTIONS': '조치 수 상한', 'DUP_CATEGORY': '같은 범주 중복', 'COST_CAP': '1회 비용 상한', 'COST_CAP_CUM': '누적 비용 상한'}


def _limits(lim, fee):
    """화면에 보여 줄 한도. 엔진 규칙(1회 ≤ 월 구독료×비율, 90일 누적 ≤ 월 구독료×비율)에서 계산된 값 그대로."""
    cap = int(lim.get('cost_cap') or 0)
    rem = lim.get('remaining_cumulative')
    return {'max_actions': lim.get('max_actions'), 'monthly_fee': fee, 'cost_cap': cap,
            'cumulative_cap': int(lim.get('cumulative_cap') or 0), 'spent_90d': int(lim.get('spent_90d') or 0),
            'remaining_cumulative': int(rem) if rem is not None else None,
            'cost_limit': min(cap, int(rem)) if rem is not None else cap}


def _template_only():
    x = UnavailableLlm('템플릿 모드')
    x.status = 'unavailable: template'
    return x


def health():
    llm = make_llm()
    return {'engine': 'team-v3', 'llm_status': getattr(llm, 'status', None),
            'model': getattr(llm, 'model', None) if getattr(llm, 'status', '') == 'ready' else None}
