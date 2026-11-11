"""API 로직. FastAPI 에 의존하지 않는 일반 함수라서 단독으로 테스트할 수 있다."""
from agent_v1 import service
from api import engine
from agent_v1.context import build_context
from db.conn import session


class NotFound(Exception):
    pass


class BadRequest(Exception):
    pass


def health():
    with session() as s:
        n = s.scalar('SELECT COUNT(*) FROM subscription')
    e = engine.health()          # 실제 엔진이 쓰는 LLM 상태(ready | mock | unavailable: 이유)
    st = e.get('llm_status') or ''
    return {'ok': True, 'db': s.kind, 'subscriptions': n, 'engine': e.get('engine'), 'llm_status': st,
            'llm': 'openai' if st == 'ready' else ('mock' if st == 'mock' else 'template'), 'model': e.get('model')}


def stats():
    with session() as s:
        risk = {r['risk_level']: r['n'] for r in s.fetchall(
            'SELECT risk_level, COUNT(*) n FROM churn_prediction GROUP BY risk_level')}
        types = s.fetchall(
            'SELECT t.type_id, c.type_name, COUNT(*) n FROM churn_prediction_type t JOIN churn_type c ON c.type_id=t.type_id '
            'JOIN churn_prediction p ON p.subscription_id=t.subscription_id '
            "WHERE t.type_rank=1 AND p.risk_level IN ('HIGH','MID') GROUP BY t.type_id, c.type_name ORDER BY t.type_id")
        actions = s.scalar('SELECT COUNT(*) FROM action_log')
    return {'risk': risk, 'top_type_in_high_mid': types, 'actions_logged': actions}


def list_subscriptions(risk=None, type_id=None, q=None, limit=50, offset=0):
    limit = max(1, min(int(limit), 200))
    where, p = [], []
    if risk:
        rl = [x for x in risk.split(',') if x in ('HIGH', 'MID', 'LOW')]
        if not rl:
            raise BadRequest('risk 는 HIGH, MID, LOW 중에서 고르세요')
        where.append(f"p.risk_level IN ({','.join('?' * len(rl))})")
        p += rl
    if type_id:
        where.append('t.type_id=?')
        p.append(type_id)
    if q:
        where.append('(c.customer_name LIKE ? OR s.subscription_id LIKE ? OR s.customer_id LIKE ?)')
        p += [f'%{q}%'] * 3
    w = ('WHERE ' + ' AND '.join(where)) if where else ''
    base = ('FROM subscription s JOIN customer c ON c.customer_id=s.customer_id '
            'JOIN churn_prediction p ON p.subscription_id=s.subscription_id '
            'LEFT JOIN churn_prediction_type t ON t.subscription_id=s.subscription_id AND t.type_rank=1 '
            'LEFT JOIN churn_type ct ON ct.type_id=t.type_id ' + w)
    with session() as s:
        total = s.scalar('SELECT COUNT(*) ' + base, p)
        # 유형별 위험도 순위(rn)로 번갈아 정렬 -> 상단에 모든 이탈 유형이 골고루 나온다
        rows = s.fetchall(
            'SELECT s.subscription_id, s.customer_id, c.customer_name, s.product_category, s.model_name, s.monthly_fee, '
            's.care_type, p.churn_probability, p.risk_level, t.type_id AS top_type_id, ct.type_name AS top_type_name, '
            't.contribution_share, '
            'ROW_NUMBER() OVER (PARTITION BY t.type_id ORDER BY p.churn_probability DESC, s.subscription_id) AS rn '
            + base + ' ORDER BY rn, p.churn_probability DESC, s.subscription_id LIMIT ? OFFSET ?',
            p + [limit, int(offset)])
        for r in rows:
            r.pop('rn', None)
    return {'total': total, 'items': rows}


def get_subscription(sub_id):
    with session() as s:
        ctx = build_context(s, sub_id)
        if not ctx:
            raise NotFound(sub_id)
        runs = s.fetchall('SELECT run_id, created_at, llm_used FROM agent_run WHERE subscription_id=? '
                          'ORDER BY run_id DESC LIMIT 10', (sub_id,))
        actions = s.fetchall('SELECT * FROM action_log WHERE subscription_id=? ORDER BY action_id DESC LIMIT 20', (sub_id,))
    return {k: ctx[k] for k in ('today', 'subscription', 'customer', 'prediction', 'types', 'manager', 'features', 'recent_voc')} | {
        'runs': runs, 'actions': actions}


def make_plan(sub_id, use_llm=True):
    with session() as s:
        res = engine.make_plan(s, sub_id, use_llm=use_llm)      # 팀원 Agent 엔진(model/) 연결
        if res is None:
            raise NotFound(sub_id)
        service.save_run(s, res)
    return res


def get_run(run_id):
    """저장된 조치안(plan)을 다시 불러온다. 화면을 새로고침해도 승인 대기 중인 안을 이어서 볼 수 있다."""
    import json
    with session() as s:
        row = s.fetchone('SELECT run_id, subscription_id, created_at, plan_json FROM agent_run WHERE run_id=?', (run_id,))
        if not row:
            raise NotFound(f'run {run_id}')
        done = s.fetchall('SELECT action_id, solution_id, status FROM action_log WHERE run_id=? ORDER BY action_id', (run_id,))
    res = json.loads(row['plan_json'])
    res['run_id'] = row['run_id']
    res['logged_actions'] = done
    return res


def _part(part):
    if part in (None, '', 'customer', 'internal'):
        return part or None
    raise BadRequest("part 는 'customer' 또는 'internal' 이어야 해요")


def approve(run_id, solution_ids=None, part=None, message_text=None, internal_memo=None, forward_depts=None,
            decision_note=None, edited=False):
    part = _part(part)
    with session() as s:
        ids = service.approve_run(s, run_id, solution_ids, part, message_text, internal_memo, forward_depts,
                                  decision_note, edited)
        if ids is None:
            raise NotFound(f'run {run_id}')
    note = ('실제 발송은 하지 않고 action_log 에만 기록했어요(시뮬레이션).' if ids else
            '새로 기록한 조치가 없어요. 이미 쿨다운 기간 안에 같은 조치가 기록돼 있거나, 선택한 대상에 해당하는 조치가 없어요.')
    return {'run_id': run_id, 'logged_action_ids': ids, 'status': 'simulated' if ids else 'skipped', 'note': note}


def reject(run_id, reason, solution_ids=None, part=None):
    reason = (reason or '').strip()
    if not reason:
        raise BadRequest('반려 사유를 입력해 주세요')
    part = _part(part)
    with session() as s:
        ids = service.reject_run(s, run_id, reason, solution_ids, part)
        if ids is None:
            raise NotFound(f'run {run_id}')
    return {'run_id': run_id, 'logged_action_ids': ids, 'status': '반려',
            'note': '반려로 기록했어요. 쿨다운·비용 누계에는 세지 않아요.'}


def manual_forward(sub_id, depts, note='', run_id=None):
    """상담원이 Agent 제안 없이 직접 요청한 협업 부서 전달 → action_log 에 MANUAL 로 기록(시뮬레이션)."""
    import datetime as dt
    import json
    depts = [str(d) for d in (depts or []) if str(d).strip()]
    if not depts:
        raise BadRequest('부서를 1곳 이상 선택하세요')
    with session() as s:
        sub = s.fetchone('SELECT subscription_id FROM subscription WHERE subscription_id=?', (sub_id,))
        if not sub:
            raise NotFound(sub_id)
        now = dt.datetime.now()
        if run_id:   # 다른 조치 기록과 같은 시간축(예측 기준일 + 현재 시각)을 쓴다
            row = s.fetchone('SELECT plan_json FROM agent_run WHERE run_id=?', (run_id,))
            try:
                day = dt.date.fromisoformat(str(json.loads(row['plan_json']).get('as_of'))[:10])
                now = dt.datetime.combine(day, now.time())
            except Exception:
                pass
        text = '협업 전달(상담자 직접 요청) → ' + ', '.join(depts) + (' · ' + note if note else '')
        aid = s.insert_returning_id(
            'INSERT INTO action_log (run_id, subscription_id, solution_id, type_id, target, message_kind, offer_cost, '
            'message_text, status, created_at, forward_depts, memo_edited_yn) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (run_id, sub_id, 'MANUAL', None, 'LG 내부 전달', '직접 요청', 0, text, 'simulated',
             now.isoformat(sep=' ', timespec='seconds'), ','.join(depts), 'N'), 'action_id')
    return {'action_id': aid, 'status': 'simulated', 'depts': depts,
            'note': '실제 전달은 하지 않고 action_log 에만 기록했어요(시뮬레이션).'}


def list_actions(sub_id=None, limit=50):
    limit = max(1, min(int(limit), 200))
    with session() as s:
        if sub_id:
            rows = s.fetchall('SELECT l.*, c.customer_name FROM action_log l LEFT JOIN subscription s ON s.subscription_id=l.subscription_id '
                              'LEFT JOIN customer c ON c.customer_id=s.customer_id WHERE l.subscription_id=? ORDER BY l.action_id DESC LIMIT ?', (sub_id, limit))
        else:
            rows = s.fetchall('SELECT l.*, c.customer_name FROM action_log l LEFT JOIN subscription s ON s.subscription_id=l.subscription_id '
                              'LEFT JOIN customer c ON c.customer_id=s.customer_id ORDER BY l.action_id DESC LIMIT ?', (limit,))
    return rows


def catalog():
    with session() as s:
        return {'types': s.fetchall('SELECT * FROM churn_type ORDER BY type_id'),
                'solutions': s.fetchall('SELECT * FROM solution_catalog ORDER BY type_id, priority')}


def overview():
    """개요 탭용 집계: 전체 구독자, 위험 분포, 제품군별, 1순위 유형별."""
    with session() as s:
        total = s.scalar('SELECT COUNT(*) FROM subscription')
        avg = s.scalar('SELECT AVG(churn_probability) FROM churn_prediction')
        risk = {r['risk_level']: r['n'] for r in s.fetchall(
            'SELECT risk_level, COUNT(*) n FROM churn_prediction GROUP BY risk_level')}
        prods = s.fetchall(
            'SELECT s.product_category AS name, COUNT(*) AS n, '
            "SUM(CASE WHEN p.risk_level='HIGH' THEN 1 ELSE 0 END) AS high, "
            "SUM(CASE WHEN p.risk_level='MID' THEN 1 ELSE 0 END) AS mid "
            'FROM subscription s LEFT JOIN churn_prediction p ON p.subscription_id=s.subscription_id '
            'GROUP BY s.product_category ORDER BY n DESC')
        types = s.fetchall('SELECT type_id, COUNT(*) AS n FROM churn_prediction_type WHERE type_rank=1 GROUP BY type_id ORDER BY type_id')
    return {'total': total, 'avg_prob': float(avg or 0), 'risk': risk,
            'products': [{'name': r['name'], 'n': int(r['n']), 'high': int(r['high'] or 0), 'mid': int(r['mid'] or 0)} for r in prods],
            'types': [{'type_id': r['type_id'], 'n': int(r['n'])} for r in types]}


_INS = {'t': 0, 'v': None}
_GENERIC = {'기타 일반 문의', '서비스 관련 일반 문의'}
_NEG = {'케어결과불만', '태도불만', '응대불만', '영업불만', '담당자교체요청', '재방문요청'}


def _ym(d):
    return str(d)[:7]


def _add_month(ym, k):
    y, m = int(ym[:4]), int(ym[5:7])
    t = y * 12 + (m - 1) + k
    return '%04d-%02d' % (t // 12, t % 12 + 1)


def insights():
    """화면 분석 탭용 집계(신규 구독 추이, VOC, 제품군/유형별 지표). 60초 동안 결과를 재사용한다."""
    import time
    from collections import Counter, defaultdict
    if _INS['v'] is not None and time.time() - _INS['t'] < 60:
        return _INS['v']
    with session() as s:
        as_of = s.scalar('SELECT MAX(as_of_date) FROM churn_prediction') or '2026-09-30'
        subs = s.fetchall('SELECT s.subscription_id, s.product_category AS prod, s.start_date, p.risk_level, p.churn_probability AS prob, c.customer_name AS cname, t.type_id '
                          'FROM subscription s JOIN customer c ON c.customer_id=s.customer_id JOIN churn_prediction p ON p.subscription_id=s.subscription_id '
                          'LEFT JOIN churn_prediction_type t ON t.subscription_id=s.subscription_id AND t.type_rank=1')
        voc = s.fetchall('SELECT subscription_id, contact_date, category, summary_text FROM voc_contact')
        visits = s.fetchall('SELECT subscription_id, visit_type, visit_status, repeat_symptom_yn FROM care_visit')
        hist = []
        try:
            hist = s.fetchall('SELECT subscription_id, contact_date, summary_text FROM voc_history')
        except Exception:
            s.rollback()
        from api import reports
        try:
            team = reports.build(s, as_of)
        except Exception as e:  # 리포트 계산 실패가 다른 화면을 막지 않도록
            import traceback; traceback.print_exc(); team = None
    if team:  # AI 해석(실패하면 규칙 문장 그대로)
        try:
            from api import ai_summary
            team = ai_summary.enhance(team)
        except Exception:
            import traceback; traceback.print_exc()
    base = {r['subscription_id']: r for r in subs}
    tix = lambda tid: (int(str(tid)[1:]) - 1) if tid else None
    cur = _ym(as_of)
    months6 = [_add_month(cur, k) for k in range(-6, 0)]
    new_m = Counter(_ym(r['start_date']) for r in subs)
    can_m = Counter(_ym(v['contact_date']) for v in voc if v['category'] == '해지문의')
    trend = {'labels': [str(int(m[5:])) + '월' for m in months6], 'new': [new_m.get(m, 0) for m in months6],
             'cancel_voc': [can_m.get(m, 0) for m in months6]}
    allc = Counter(v['category'] for v in voc if v['category'] != '기타')
    voc_dist = [[k, n] for k, n in allc.most_common(6)]
    # 유형별(1순위 유형 고객 기준) VOC 키워드/대표 문구
    tk, tq = defaultdict(Counter), defaultdict(Counter)
    for v in voc:
        b = base.get(v['subscription_id'])
        if not b or b['type_id'] is None:
            continue
        t = tix(b['type_id'])
        if v['category'] != '기타':
            tk[t][v['category']] += 1
        if v['summary_text'] and v['summary_text'] not in _GENERIC:
            tq[t][v['summary_text']] += 1
    # 리뷰(VOC 문구) — 전체 / 가입연도 고객이 '그 해에' 남긴 것, 유형별 상위 4개와 실제 접수일(가장 최근). 이력(voc_history)은 더미 생성분
    qc, qd = defaultdict(Counter), {}
    for v in list(voc) + list(hist):
        b = base.get(v['subscription_id'])
        if not b or b['type_id'] is None or not v['summary_text'] or v['summary_text'] in _GENERIC:
            continue
        d = str(v['contact_date'])[:10]
        sy = str(b['start_date'])[:4]
        for g in (('ALL', sy) if d[:4] == sy else ('ALL',)):
            k = (g, tix(b['type_id']))
            qc[k][v['summary_text']] += 1
            if qd.get((k, v['summary_text']), '') < d:
                qd[(k, v['summary_text'])] = d
    quotes = {}
    for (g, tt), c in qc.items():
        quotes.setdefault(g, {})[str(tt)] = [[x, n, qd[((g, tt), x)]] for x, n in c.most_common(4)]
    type_kw = {str(t): [[k, n] for k, n in c.most_common(3)] for t, c in tk.items()}
    type_quotes = {str(t): [[k, n] for k, n in c.most_common(4)] for t, c in tq.items()}
    # 제품군별
    last3 = [_add_month(cur, k) for k in (-3, -2, -1)]
    cut90 = None
    pr = defaultdict(lambda: {'n': 0, 'hm': 0, 'types': Counter(), 'new': Counter(), 'can': Counter(), 'kw': Counter(), 'voc': 0, 'neg': 0})
    for r in subs:
        p = pr[r['prod']]
        p['n'] += 1
        p['new'][_ym(r['start_date'])] += 1
        if r['risk_level'] in ('HIGH', 'MID'):
            p['hm'] += 1
            if r['type_id']:
                p['types'][tix(r['type_id'])] += 1
    for v in voc:
        b = base.get(v['subscription_id'])
        if not b:
            continue
        p = pr[b['prod']]
        p['voc'] += 1
        if v['category'] in _NEG:
            p['neg'] += 1
        if v['category'] == '해지문의':
            p['can'][_ym(v['contact_date'])] += 1
        if v['category'] != '기타':
            p['kw'][v['category']] += 1
    vis = defaultdict(lambda: [0, 0, 0, 0])  # 정시, 완료계, AS수리, 재증상
    for x in visits:
        b = base.get(x['subscription_id'])
        if not b:
            continue
        a = vis[b['prod']]
        if x['visit_status'] in ('완료', '지연완료'):
            a[1] += 1
            if x['visit_status'] == '완료':
                a[0] += 1
        if x['visit_type'] == 'AS수리':
            a[2] += 1
            if x['repeat_symptom_yn'] == 'Y':
                a[3] += 1
    product = {}
    for name, p in pr.items():
        tot_t = sum(p['types'].values()) or 1
        product[name] = {
            'n': p['n'], 'risk_pct': round(100.0 * p['hm'] / p['n'], 1),
            'months': [{'m': str(int(m[5:])) + '월', 'new': p['new'].get(m, 0), 'cancel_voc': p['can'].get(m, 0)} for m in last3],
            'top_types': [[t, round(100.0 * n / tot_t)] for t, n in p['types'].most_common(3)],
            'kw': [[k, n] for k, n in p['kw'].most_common(12)],
            'voc_n': p['voc'], 'voc_neg_pct': round(100.0 * p['neg'] / p['voc'], 1) if p['voc'] else 0,
            'ontime_pct': round(100.0 * vis[name][0] / vis[name][1], 1) if vis[name][1] else 0,
            'revisit_pct': round(100.0 * vis[name][3] / vis[name][2], 1) if vis[name][2] else 0}
    hm_types = Counter(tix(r['type_id']) for r in subs if r['risk_level'] in ('HIGH', 'MID') and r['type_id'])
    tt = sum(hm_types.values()) or 1
    # VOC 작성 월별로, 그 달에 문의를 남긴 고·중위험 고객 수(1순위 유형별, 고객 중복 제거)
    vt = defaultdict(lambda: defaultdict(set))
    for v in voc:
        b = base.get(v['subscription_id'])
        if b and b['risk_level'] in ('HIGH', 'MID') and b['type_id']:
            vt[_ym(v['contact_date'])][tix(b['type_id'])].add(v['subscription_id'])
    yt = defaultdict(lambda: [0] * 10)  # 가입 연도별 고·중위험 고객의 1순위 유형 분포
    for r in subs:
        if r['risk_level'] in ('HIGH', 'MID') and r['type_id']:
            yt[str(r['start_date'])[:4]][tix(r['type_id'])] += 1
    ytypes = {y: yt[y] for y in sorted(yt)}
    # 도넛 호버/고정용 대표 고객: 유형별(전체·가입연도별) 이탈확률 상위 8명
    cand = defaultdict(list)
    for r in subs:
        if r['risk_level'] in ('HIGH', 'MID') and r['type_id']:
            k = tix(r['type_id'])
            for g in ('ALL', str(r['start_date'])[:4]):
                cand[(g, k)].append((r['prob'] or 0, r['cname'], r['prod']))
    samples = {}
    for (g, k), arr in cand.items():
        arr.sort(key=lambda x: -x[0])
        samples.setdefault(g, {})[k] = [[n, pd_, round(pb, 3)] for pb, n, pd_ in arr[:8]]
    vtypes = {m: [len(vt[m].get(k, ())) for k in range(10)] for m in sorted(vt)}
    out = {'as_of': str(as_of), 'trend': trend, 'voc_dist': voc_dist, 'voc_total': len(voc), 'type_kw': type_kw,
           'type_quotes': type_quotes, 'product': product, 'vtypes': vtypes, 'quotes': quotes, 'ytypes': ytypes, 'samples': samples, 'vhist': bool(hist),
           'type_share': {str(t): round(100.0 * n / tt, 1) for t, n in hm_types.items()}}
    if team:
        out['team'] = team
    _INS.update(t=time.time(), v=out)
    return out
