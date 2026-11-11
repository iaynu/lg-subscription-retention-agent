"""신규 유형 후보(ai_proposal) 검토·채택·기각·활성화 + 설문 집계(EDA).

- 채택 계획(plan)은 팀원 feedback/approve.py 의 순수 함수를 그대로 쓴다(검증·ID 부여·기본값 규칙 동일).
- 저장은 우리 DB 어댑터로 한다: 새 유형 → churn_type, 새 솔루션 → catalog_ext(+solution_catalog 조회용), 상태 → ai_proposal.
- 원칙(팀 결정): 채택은 '비활성 등록'까지. Agent 는 활성화 전까지 새 솔루션을 쓰지 않고, 새 유형은 모델 재학습 전에는 예측에 나오지 않는다.
"""
import datetime as dt
import json
import sys
import types as _types

from config import ROOT
from db.conn import session

CORE = ROOT.parent / 'model'
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

try:
    from feedback.approve import ApproveError, plan as _plan  # pandas 가 있으면 그대로
except ImportError:                                           # 서버에 pandas 가 없어도 검토 화면은 돌도록 최소 대체
    _m = _types.ModuleType('feedback.candidates')
    _m.ALLOWED_CATEGORIES = ('정보제공', '담당자조치', '혜택제안', '제품조치', '의견수집')
    sys.modules['feedback.candidates'] = _m
    from feedback.approve import ApproveError, plan as _plan  # noqa: E402


class BadInput(Exception):
    pass


def _ev(row):
    try:
        return json.loads(row['evidence_json'] or '{}')
    except ValueError:
        return {}


def _public(row, with_ev=True):
    ev = _ev(row)
    out = {'proposal_id': row['proposal_id'], 'kind': row['proposal_kind'], 'related_type_id': row['related_type_id'],
           'title': row['title'], 'description': row['description'], 'status': row['status'],
           'reviewed_by': row.get('reviewed_by'), 'reviewed_at': str(row['reviewed_at']) if row.get('reviewed_at') else None,
           'support': ev.get('support') or len(ev.get('response_ids') or []), 'share': ev.get('share_of_responses'),
           'votes': ev.get('votes'), 'n_runs': ev.get('n_runs'), 'source': ev.get('source'),
           'closest_type_id': ev.get('closest_type_id'), 'difference': ev.get('difference'),
           'quotes': (ev.get('quotes') or [])[:5], 'solutions': ev.get('solutions') or [], 'review': ev.get('review')}
    return out


def list_proposals():
    with session() as s:
        rows = s.fetchall('SELECT * FROM ai_proposal ORDER BY CASE status WHEN ? THEN 0 ELSE 1 END, proposal_id', ('검토중',))
        types = {t['type_id']: t['type_name'] for t in s.fetchall('SELECT type_id, type_name FROM churn_type')}
        ext = s.fetchall('SELECT solution_id, type_id, row_json, active_yn FROM catalog_ext ORDER BY solution_id')
    out = []
    for r in rows:
        p = _public(r)
        p['closest_type_name'] = types.get(p['closest_type_id'])
        out.append(p)
    sols = []
    for e in ext:
        j = json.loads(e['row_json'])
        sols.append({'solution_id': e['solution_id'], 'type_id': e['type_id'], 'type_name': types.get(e['type_id']),
                     'name': j.get('solution_name'), 'category': j.get('solution_category'), 'hint': j.get('message_hint'),
                     'channel': j.get('channel'), 'forward': j.get('internal_forward_yn') == 'Y', 'forward_dept': j.get('forward_dept'),
                     'offer_cost': j.get('offer_cost', 0), 'fee_impact': j.get('fee_impact'), 'active': e['active_yn'] == 'Y'})
    return {'proposals': out, 'new_solutions': sols,
            'counts': {k: sum(1 for p in out if p['status'] == k) for k in ('검토중', '채택', '기각')}}


def _catalog_rows(s):
    from api import engine
    cat = engine.load_catalog_with_ext(s)
    return [{'solution_id': c['solution_id'], 'seq_no': c.get('seq_no', 0), 'type_id': c['type_id']} for c in cat]


def _load(s, pid):
    r = s.fetchone('SELECT * FROM ai_proposal WHERE proposal_id=?', (pid,))
    if not r:
        raise BadInput(f'제안 {pid}이(가) 없어요')
    return r


def approve(pid, reviewer, overrides=None, dry_run=False, note=''):
    reviewer = (reviewer or '').strip() or '관리자'
    with session() as s:
        r = _load(s, pid)
        prop = {'proposal_id': r['proposal_id'], 'proposal_kind': r['proposal_kind'], 'related_type_id': r['related_type_id'],
                'title': r['title'], 'description': r['description'], 'status': r['status'], 'evidence': _ev(r)}
        types = s.fetchall('SELECT type_id, type_name FROM churn_type')
        try:
            pl = _plan(prop, types, _catalog_rows(s), overrides)
        except ApproveError as e:
            raise BadInput(str(e))
        out = {'type_id': pl['type_id'], 'type_row': pl['type_row'],
               'solutions': [{'solution_id': x['solution_id'], 'name': x['solution_name'], 'category': x['solution_category'],
                              'channel': x['channel'], 'forward': x['internal_forward_yn'] == 'Y'} for x in pl['solution_rows']],
               'dry_run': bool(dry_run)}
        if dry_run:
            return out
        now = dt.datetime.now().isoformat(sep=' ', timespec='seconds')
        if pl['type_row']:
            s.execute('INSERT INTO churn_type (type_id, type_name, group_name, description) VALUES (?,?,?,?)',
                      (pl['type_row']['type_id'], pl['type_row']['type_name'], '신규(설문 후보)', pl['type_row']['description']))
        for x in pl['solution_rows']:
            s.execute('INSERT INTO catalog_ext (solution_id, type_id, row_json, active_yn, created_at) VALUES (?,?,?,?,?)',
                      (x['solution_id'], x['type_id'], json.dumps(x, ensure_ascii=False), 'N', now))
            s.execute('INSERT INTO solution_catalog (solution_id, type_id, solution_name, solution_category, solution_detail, '
                      'target, message_kind, offer_cost, sample_message, priority, active_yn) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                      (x['solution_id'], x['type_id'], x['solution_name'], x['solution_category'], x['solution_detail'],
                       'LG 내부 전달' if x['internal_forward_yn'] == 'Y' else '사용자', 'info', 0, x['message_hint'],
                       x['priority'], 'N'))
        ev = _ev(r)
        ev['review'] = {'decision': '채택', 'note': note, 'type_id': pl['type_id'], 'reviewer': reviewer,
                        'solution_ids': [x['solution_id'] for x in pl['solution_rows']]}
        s.execute('UPDATE ai_proposal SET status=?, reviewed_by=?, reviewed_at=?, related_type_id=?, evidence_json=? WHERE proposal_id=?',
                  ('채택', reviewer[:30], now, pl['type_id'] if pl['type_row'] else r['related_type_id'],
                   json.dumps(ev, ensure_ascii=False), pid))
        return out


def reject(pid, reviewer, reason):
    reason = (reason or '').strip()
    if not reason:
        raise BadInput('기각 사유를 입력해 주세요')
    with session() as s:
        r = _load(s, pid)
        if r['status'] != '검토중':
            raise BadInput(f"이미 처리된 제안이에요(상태: {r['status']})")
        ev = _ev(r)
        ev['review'] = {'decision': '기각', 'note': reason, 'reviewer': (reviewer or '관리자')}
        s.execute('UPDATE ai_proposal SET status=?, reviewed_by=?, reviewed_at=?, evidence_json=? WHERE proposal_id=?',
                  ('기각', (reviewer or '관리자')[:30], dt.datetime.now().isoformat(sep=' ', timespec='seconds'),
                   json.dumps(ev, ensure_ascii=False), pid))
    return {'proposal_id': pid, 'status': '기각'}


def restore(pid, reviewer='관리자'):
    """채택·기각을 되돌려 '검토중' 으로 돌린다. 채택은 이 솔루션으로 쌓인 조치 이력이 없을 때만 되돌릴 수 있다(활성 상태여도 됨)."""
    with session() as s:
        r = _load(s, pid)
        if r['status'] == '검토중':
            raise BadInput('이미 검토중이에요')
        ev = _ev(r)
        rv = ev.get('review') or {}
        if r['status'] == '채택':
            sids = list(rv.get('solution_ids') or [])
            for sid in sids:
                if s.scalar('SELECT COUNT(*) FROM action_log WHERE solution_id=?', (sid,)):
                    raise BadInput(f'{sid} 로 쌓인 조치 이력이 있어 되돌릴 수 없어요')
            for sid in sids:
                s.execute('DELETE FROM solution_catalog WHERE solution_id=?', (sid,))
                s.execute('DELETE FROM catalog_ext WHERE solution_id=?', (sid,))
            tid = rv.get('type_id')
            if tid and s.fetchone("SELECT 1 AS x FROM churn_type WHERE type_id=? AND group_name='신규(설문 후보)'", (tid,)):
                if not s.scalar('SELECT COUNT(*) FROM solution_catalog WHERE type_id=?', (tid,)):
                    s.execute('DELETE FROM churn_type WHERE type_id=?', (tid,))
        ev.pop('review', None)
        s.execute('UPDATE ai_proposal SET status=?, reviewed_by=?, reviewed_at=?, related_type_id=?, evidence_json=? WHERE proposal_id=?',
                  ('검토중', None, None, ev.get('closest_type_id') or r['related_type_id'],
                   json.dumps(ev, ensure_ascii=False), pid))
    return {'proposal_id': pid, 'status': '검토중'}


FEE_IMPACTS = ("없음", "증가", "감소")


def activate(solution_id, forward_dept=None, offer_cost=None, fee_impact=None):
    with session() as s:
        e = s.fetchone('SELECT * FROM catalog_ext WHERE solution_id=?', (solution_id,))
        if not e:
            raise BadInput('채택으로 만든 신규 솔루션만 활성화할 수 있어요')
        if e['active_yn'] == 'Y':
            raise BadInput('이미 활성 상태예요')
        j = json.loads(e['row_json'])
        if j.get('internal_forward_yn') == 'Y':
            dept = (forward_dept or j.get('forward_dept') or '').strip()
            if not dept:
                raise BadInput('내부 전달 솔루션은 전달 부서가 필요해요')
            j['forward_dept'] = dept
        benefit = j.get('solution_category') == '혜택제안'
        if benefit and offer_cost is None:
            raise BadInput('혜택제안 솔루션은 활성화할 때 1회 오퍼 비용(offer_cost)을 입력해야 해요')
        if offer_cost is not None:
            try:
                oc = int(offer_cost)
            except (TypeError, ValueError):
                raise BadInput('offer_cost는 숫자여야 해요')
            if oc < 0:
                raise BadInput('offer_cost는 0 이상이어야 해요')
            j['offer_cost'] = oc
        if fee_impact is not None:
            if fee_impact not in FEE_IMPACTS:
                raise BadInput('fee_impact는 없음·증가·감소 중 하나여야 해요')
            j['fee_impact'] = fee_impact
        j['agent_selectable_yn'] = 'Y'
        j['active_yn'] = 'Y'
        s.execute('UPDATE catalog_ext SET row_json=?, active_yn=? WHERE solution_id=?',
                  (json.dumps(j, ensure_ascii=False), 'Y', solution_id))
        s.execute('UPDATE solution_catalog SET active_yn=?, offer_cost=? WHERE solution_id=?',
                  ('Y', int(j.get('offer_cost') or 0), solution_id))
    return {'solution_id': solution_id, 'active': True}


def load_proposal_rows(rows, reset=False):
    """scripts/make_candidates.py 가 만든 후보 행을 ai_proposal 에 넣는다."""
    with session() as s:
        if reset:
            s.execute('DELETE FROM ai_proposal WHERE status=?', ('검토중',))
        for r in rows:
            s.execute('INSERT INTO ai_proposal (proposal_kind, related_type_id, title, description, evidence_json, status, created_at) '
                      'VALUES (?,?,?,?,?,?,?)',
                      (r['proposal_kind'], r.get('related_type_id'), r['title'], r['description'], r['evidence_json'],
                       r.get('status') or '검토중', str(r.get('created_at') or dt.datetime.now())[:19]))
    return len(rows)


# ------------------------------------------------------------------ 설문 집계(EDA)
ISSUE_L = {'cost': '구독료·총비용 부담', 'exit': '해지·이사 절차/비용', 'renew': '재계약·갈아타기 부담', 'idle': '제품을 안 쓰게 됨',
           'care': '케어 부실·케어 후 문제', 'visit': '방문 일정 문제', 'manager': '담당 매니저 불편', 'sales': '방문 중 영업 권유',
           'fail': '제품 고장·성능', 'cs': 'A/S·고객센터 불만', 'other': '기타', 'none': '불편 없음'}
SOL_L = {'resched': '일정 재조정', 'free': '무상 방문·수리', 'fee': '요금 감면', 'swap': '대체 제품 제공', 'penalty': '위약금 면제·감면',
         'apology': '사과·안내', 'manager': '담당자 지정', 'change': '약정 중 제품 교체', 'pause': '일시정지', 'other': '기타',
         'hard': '떠오르지 않음', 'no': '받은 조치 없음', 'never': '조치를 못 받음'}
NEWSOL_L = {'alert': '방문 일정 실시간 알림', 'loan': '수리 중 대체 제품 대여', 'mgr': '담당 매니저 지정', 'step': '위약금 단계별 감면',
            'swap': '약정 중 제품 교체', 'pause': '이용 일시정지', 'other': '기타', 'none': '해당 없음'}
STATUS_L = {'done': '해지 완료', 'ing': '해지 진행 중', 'think': '해지 고민 중', 'sat': '만족하며 이용 중'}
HELPED_L = {'solved': '해결됨', 'part': '일부 해결', 'little': '거의 도움 안 됨', 'worse': '오히려 악화'}
FORM_L = {'once': '한 번 겪음', 'repeat': '반복해서 겪음', 'fade': '점점 흐려짐'}
TYPE_L = {'post_action': '조치 후 설문', 'post_churn': '해지 후 설문', 'no_type': '유형 없음 고객', 'no_candidate': '후보 없음 고객',
          'survey_only': '만족도 조사 대상'}


def survey_eda():
    with session() as s:
        n_send = s.scalar('SELECT COUNT(*) FROM survey_send')
        if not n_send:
            return {'ready': False}
        n_resp = s.scalar('SELECT COUNT(*) FROM survey_response')
        n_done = s.scalar("SELECT COUNT(*) FROM survey_response WHERE completed_yn='Y'")
        by_type = s.fetchall("SELECT survey_type, COUNT(*) n, SUM(CASE WHEN responded_yn='Y' THEN 1 ELSE 0 END) r "
                             'FROM survey_send GROUP BY survey_type ORDER BY n DESC')

        def dist(q, labels, top=None):
            tot = s.scalar('SELECT COUNT(DISTINCT response_id) FROM survey_answer WHERE question_id=?', (q,)) or 0
            rows = s.fetchall('SELECT answer_code c, COUNT(DISTINCT response_id) n FROM survey_answer WHERE question_id=? '
                              'GROUP BY answer_code ORDER BY n DESC', (q,))
            items = [{'code': r['c'], 'label': labels.get(r['c'], r['c']), 'n': int(r['n']),
                      'pct': round(100.0 * r['n'] / tot, 1) if tot else 0} for r in rows if r['c'] != '?']
            return {'n': int(tot), 'items': items[:top] if top else items}

        opens = s.fetchall("SELECT a.other_text t, COUNT(*) n FROM survey_answer a JOIN survey_response r ON r.response_id=a.response_id "
                           "WHERE a.question_id='open' AND a.other_text IS NOT NULL AND a.other_text<>'' AND r.status_code<>'sat' "
                           "GROUP BY a.other_text ORDER BY n DESC, a.other_text LIMIT 8")
        n_text = s.scalar("SELECT COUNT(*) FROM survey_answer WHERE question_id='open' AND other_text IS NOT NULL AND other_text<>''")
        iss_other = s.fetchall("SELECT other_text t, COUNT(*) n FROM survey_answer WHERE question_id='issues' AND answer_code='other' "
                               "AND other_text IS NOT NULL AND other_text<>'' GROUP BY other_text ORDER BY n DESC, other_text LIMIT 6")
        pend = s.scalar("SELECT COUNT(*) FROM ai_proposal WHERE status='검토중'")
        return {'ready': True, 'sent': int(n_send), 'responded': int(n_resp), 'completed': int(n_done),
                'response_rate': round(100.0 * n_resp / n_send, 1), 'completion_rate': round(100.0 * n_done / n_resp, 1) if n_resp else 0,
                'by_type': [{'label': TYPE_L.get(r['survey_type'], r['survey_type']), 'n': int(r['n']), 'resp': int(r['r'] or 0),
                             'pct': round(100.0 * (r['r'] or 0) / r['n'], 1)} for r in by_type],
                'status': dist('status', STATUS_L), 'issues': dist('issues', ISSUE_L), 'form': dist('form', FORM_L),
                'wanted': dist('wanted', SOL_L, 8), 'newsol': dist('newsol', NEWSOL_L), 'helped': dist('helped', HELPED_L),
                'open_texts': [{'text': r['t'], 'n': int(r['n'])} for r in opens], 'open_total': int(n_text or 0),
                'issues_other': [{'text': r['t'], 'n': int(r['n'])} for r in iss_other], 'pending_proposals': int(pend or 0)}
