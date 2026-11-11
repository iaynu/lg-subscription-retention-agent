"""python -m unittest discover -s tests -v   (data_in/ 에 CSV 가 있어야 함. 임시 sqlite 에 적재해서 시험)"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{_tmp}/test.db'
os.environ['LLM_MODE'] = 'auto'
os.environ['OPENAI_API_KEY'] = ''

from agent_v1 import llm, rules, service, validator  # noqa: E402
from api import handlers as h  # noqa: E402
from db import load  # noqa: E402
from db.conn import session  # noqa: E402


def setUpModule():
    load.main(['--data', str(ROOT / 'data_in'), '--reset'])


def _first(risk, type_id=None):
    with session() as s:
        sql = ("SELECT p.subscription_id FROM churn_prediction p JOIN churn_prediction_type t ON t.subscription_id=p.subscription_id "
               "AND t.type_rank=1 WHERE p.risk_level=?" + (" AND t.type_id=?" if type_id else "") + " ORDER BY p.subscription_id LIMIT 1")
        return s.scalar(sql, (risk, type_id) if type_id else (risk,))


class LoadTest(unittest.TestCase):
    def test_counts(self):
        with session() as s:
            self.assertEqual(s.scalar('SELECT COUNT(*) FROM subscription'), 30000)
            self.assertEqual(s.scalar('SELECT COUNT(*) FROM solution_catalog'), 36)
            self.assertEqual(load.validate(s), [])

    def test_idempotent(self):
        with session() as s:
            load.load_csv(s, 'care_manager', ROOT / 'data_in' / 'care_manager.csv')
            self.assertEqual(s.scalar('SELECT COUNT(*) FROM care_manager'), 140)


class AgentTest(unittest.TestCase):
    def test_low_risk_gets_no_action(self):
        sid = _first('LOW')
        with session() as s:
            r = service.make_plan(s, sid, use_llm=False)
        self.assertEqual(r['plan']['actions'], [])

    def test_template_plan_respects_limits(self):
        with session() as s:
            for t in ('T01', 'T05', 'T08', 'T10'):
                r = service.make_plan(s, _first('HIGH', t), use_llm=False)
                acts = r['plan']['actions']
                self.assertTrue(0 < len(acts) <= 3, t)
                self.assertLessEqual(r['plan']['total_cost'], 50000)
                self.assertLessEqual(sum(1 for a in acts if a['customer_message']), 2)
                ok, errs = validator.validate(r['plan'], build_ctx(s, r), r['rule_trace'])
                self.assertTrue(ok, errs)

    def test_llm_hallucinated_id_falls_back(self):
        bad = lambda sy, us: {'summary': 'x', 'actions': [{'solution_id': 'S-T99-9', 'customer_message': 'hi', 'internal_note': None}]}
        with session() as s:
            r = service.make_plan(s, _first('HIGH', 'T05'), llm_fn=bad)
        self.assertEqual(r['meta']['llm_used'], 'N')
        self.assertIn('검증 실패', r['meta']['fallback_reason'])
        self.assertTrue(r['plan']['actions'])

    def test_llm_good_output_used(self):
        def good(sy, us):
            c = json.loads(us)['후보 목록'][0]
            name = json.loads(us)['고객 사실']['name']
            a = {'solution_id': c['solution_id'], 'customer_message': None, 'internal_note': None}
            if c['target'] in ('사용자', '둘 다'):
                a['customer_message'] = ('(광고) ' if c['message_kind'] == 'ad' else '') + f'{name}님, 안내드립니다.'
            if c['target'] in ('LG 내부 전달', '둘 다'):
                a['internal_note'] = '전달 메모'
            return {'summary': 'ok', 'actions': [a]}
        with session() as s:
            r = service.make_plan(s, _first('HIGH', 'T08'), llm_fn=good)
        self.assertEqual(r['meta']['llm_used'], 'Y')

    def test_ungrounded_number_and_banned_phrase_rejected(self):
        with session() as s:
            r = service.make_plan(s, _first('HIGH', 'T01'), use_llm=False)
            ctx = build_ctx(s, r)
        cand = r['rule_trace']
        cust = next(e for e in cand['eligible'] if e['target'] == '사용자' and e['message_kind'] == 'info')
        plan = {'actions': [{'solution_id': cust['solution_id'], 'customer_message': '무조건 987,654원 할인해 드려요', 'internal_note': None}]}
        ok, errs = validator.validate(plan, ctx, cand)
        self.assertFalse(ok)
        self.assertTrue(any('금지 표현' in e for e in errs) and any('숫자' in e for e in errs))

    def test_consent_blocks_customer_message(self):
        sid = _first('HIGH', 'T01')
        with session() as s:
            s.execute("UPDATE customer SET notify_consent_yn='N' WHERE customer_id=(SELECT customer_id FROM subscription WHERE subscription_id=?)", (sid,))
            r = service.make_plan(s, sid, use_llm=False)
            s.rollback()
        self.assertTrue(all(a['target'] == 'LG 내부 전달' for a in r['plan']['actions']))

    def test_cooldown_and_approve(self):
        sid = _first('HIGH', 'T05')
        res = h.make_plan(sid, use_llm=False)
        self.assertTrue(res['run_id'])
        out = h.approve(res['run_id'])
        self.assertTrue(out['logged_action_ids'])
        self.assertEqual(h.approve(res['run_id'])['logged_action_ids'], [])  # 중복 승인 방지
        again = service_plan(sid)
        used = {a['solution_id'] for a in res['plan']['actions']}
        self.assertFalse(used & {a['solution_id'] for a in again['plan']['actions']})  # 쿨다운


def service_plan(sid):
    with session() as s:
        return service.make_plan(s, sid, use_llm=False)


def build_ctx(s, r):
    from agent_v1.context import build_context
    return build_context(s, r['subscription_id'])


class ApiTest(unittest.TestCase):
    def test_list_and_detail(self):
        r = h.list_subscriptions(risk='HIGH', limit=5)
        self.assertEqual(r['total'], 3000)
        self.assertEqual(len(r['items']), 5)
        self.assertGreaterEqual(r['items'][0]['churn_probability'], r['items'][4]['churn_probability'])
        d = h.get_subscription(r['items'][0]['subscription_id'])
        self.assertIn('features', d)
        self.assertEqual(len(h.list_subscriptions(type_id='T08', risk='HIGH,MID', limit=10)['items']), 10)

    def test_errors(self):
        with self.assertRaises(h.NotFound):
            h.get_subscription('S999999')
        with self.assertRaises(h.BadRequest):
            h.list_subscriptions(risk='XX')

    def test_no_leakage(self):
        import inspect
        for mod in (service, rules, validator):
            self.assertNotIn('synth_', inspect.getsource(mod))
        from agent_v1 import context
        self.assertNotIn('synth_', inspect.getsource(context).replace('synth_* 테이블은', '').replace('synth_*', ''))

    def test_health_template_mode(self):
        self.assertEqual(h.health()['llm'], 'template')


if __name__ == '__main__':
    unittest.main()
