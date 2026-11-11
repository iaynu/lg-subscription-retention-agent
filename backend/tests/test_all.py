"""PostgreSQL 에서 시험한다(운영과 같은 DB). data_in/ 에 CSV 가 있어야 하고, 시험 전용 DB 주소를 TEST_DATABASE_URL 로 준다.
  예) TEST_DATABASE_URL=postgresql://postgres:비밀번호@localhost:5432/churn_test  python -m unittest discover -s tests -v
  (Linux/Mac 은 임시 DB 를 알아서 띄우는 scripts/pg_test.sh 도 있다)
주의: 시험은 테이블을 지우고 다시 만든다. 그래서 DB 이름에 'test' 가 없으면 실행을 거부한다.
시험 범위: 적재 · 계획 생성(템플릿/모의 LLM) · 승인/반려(수정 문구·부서 저장) · 쿨다운 · 설문 집계 · 신규 유형 채택/기각/활성화 · 컬럼 자동 추가."""
import json
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_url = os.environ.get('TEST_DATABASE_URL', '')
if not _url.startswith(('postgresql://', 'postgres://', 'postgresql+')):
    raise RuntimeError('TEST_DATABASE_URL(PostgreSQL 주소)을 지정하세요. 예) postgresql://postgres:비밀번호@localhost:5432/churn_test')
if 'test' not in urlparse(_url.replace('postgresql+psycopg://', 'postgresql://')).path.lower():
    raise RuntimeError('안전장치: 시험은 테이블을 지우고 다시 만듭니다. DB 이름에 test 가 들어간 전용 DB 를 쓰세요.')
os.environ['DATABASE_URL'] = _url
os.environ['OPENAI_API_KEY'] = ''
os.environ['OPENAI_MODEL'] = ''

from api import engine, handlers as h, proposals  # noqa: E402
from config import settings  # noqa: E402
from db import load  # noqa: E402
from db.conn import session  # noqa: E402


def setUpModule():
    load.main(['--data', str(ROOT / 'data_in'), '--reset'])


def _sid(risk):
    with session() as s:
        return s.scalar("SELECT p.subscription_id FROM churn_prediction p JOIN churn_prediction_type t ON t.subscription_id=p.subscription_id "
                        "AND t.type_rank=1 WHERE p.risk_level=? ORDER BY p.churn_probability DESC LIMIT 1", (risk,))


class DataTest(unittest.TestCase):
    def test_counts(self):
        with session() as s:
            self.assertEqual(s.scalar('SELECT COUNT(*) FROM subscription'), 30000)
            self.assertEqual(s.scalar("SELECT COUNT(*) FROM churn_prediction WHERE model_version='xgb-oof-v1'"), 30000)
            self.assertEqual(s.scalar('SELECT COUNT(*) FROM solution_catalog'), 39)
            self.assertEqual(s.scalar('SELECT COUNT(*) FROM survey_send'), 9000)


class PlanTest(unittest.TestCase):
    def _plan(self, mode, sid):
        settings.LLM_MODE = mode
        return h.make_plan(sid, True)

    def test_template(self):
        r = self._plan('template', _sid('HIGH'))
        self.assertEqual(r['meta']['llm_used'], 'N')
        self.assertTrue(0 < len(r['plan']['actions']) <= 2)
        self.assertLessEqual(r['plan']['total_cost'], 100000)
        self.assertIn('run_id', r)

    def test_mock_llm_passes_validator(self):
        r = self._plan('mock', _sid('MID'))
        self.assertEqual(r['meta']['llm_used'], 'Y')
        self.assertEqual(r['meta']['outcome'], 'pass')
        for a in r['plan']['actions']:
            self.assertTrue(a['customer_message'] or a['internal_note'])

    def test_rule_trace_explains(self):
        r = self._plan('template', _sid('HIGH'))
        self.assertTrue(r['rule_trace']['eligible'])
        for e in r['rule_trace']['excluded']:
            self.assertTrue(e['reason'])

    def test_cooldown_after_approve(self):
        sid = _sid('HIGH')
        r = self._plan('template', sid)
        ids = [a['solution_id'] for a in r['plan']['actions']]
        h.approve(r['run_id'], ids)
        r2 = self._plan('template', sid)
        again = {a['solution_id'] for a in r2['plan']['actions']}
        self.assertFalse(again & set(ids), '쿨다운: 방금 승인한 솔루션은 다시 나오면 안 됨')

    def test_low_risk_no_crash(self):
        with session() as s:
            sid = s.scalar("SELECT subscription_id FROM churn_prediction WHERE risk_level='LOW' LIMIT 1")
        self._plan('template', sid)


class SurveyTest(unittest.TestCase):
    def test_eda(self):
        e = proposals.survey_eda()
        self.assertTrue(e['ready'])
        self.assertEqual(e['sent'], 9000)
        self.assertGreater(e['issues']['n'], 0)


class ProposalTest(unittest.TestCase):
    def _add(self, title):
        ev = {'source': 'test', 'support': 12, 'closest_type_id': 'T01', 'difference': '시험', 'quotes': ['문장'],
              'solutions': [{'title': title + ' 안내', 'category': '정보제공', 'message_hint': '시험용 안내'}], 'response_ids': [1, 2]}
        proposals.load_proposal_rows([{'proposal_kind': 'new_type', 'title': title, 'description': '시험 설명',
                                       'evidence_json': json.dumps(ev, ensure_ascii=False)}], reset=False)
        with session() as s:
            return s.scalar('SELECT MAX(proposal_id) FROM ai_proposal')

    def test_approve_activate(self):
        pid = self._add('시험용 신규 유형 A')
        pre = proposals.approve(pid, '팀', None, True)
        self.assertEqual(pre['type_id'], 'T11')
        proposals.approve(pid, '팀')
        with self.assertRaises(proposals.BadInput):
            proposals.approve(pid, '팀')                    # 이중 채택 차단
        sols = proposals.list_proposals()['new_solutions']
        self.assertFalse(sols[-1]['active'])
        proposals.activate(sols[-1]['solution_id'])
        with session() as s:
            cat = engine.load_catalog_with_ext(s)
        self.assertEqual([c['active_yn'] for c in cat if c['solution_id'] == sols[-1]['solution_id']], ['Y'])

    def test_benefit_activation_needs_cost(self):
        ev = {'source': 'test', 'support': 12, 'closest_type_id': 'T01', 'difference': '시험', 'quotes': ['문장'],
              'solutions': [{'title': '혜택 시험', 'category': '혜택제안', 'message_hint': '혜택 안내'}], 'response_ids': [1, 2]}
        proposals.load_proposal_rows([{'proposal_kind': 'new_type', 'title': '시험용 혜택 유형', 'description': '시험 설명',
                                       'evidence_json': json.dumps(ev, ensure_ascii=False)}], reset=False)
        with session() as s:
            pid = s.scalar('SELECT MAX(proposal_id) FROM ai_proposal')
        proposals.approve(pid, '팀')
        sid = proposals.list_proposals()['new_solutions'][-1]['solution_id']
        with self.assertRaises(proposals.BadInput):
            proposals.activate(sid)                                  # 비용 없이 활성화 불가
        with self.assertRaises(proposals.BadInput):
            proposals.activate(sid, offer_cost=-1)
        with self.assertRaises(proposals.BadInput):
            proposals.activate(sid, offer_cost=3000, fee_impact='이상한값')
        proposals.activate(sid, offer_cost=3000, fee_impact='감소')
        with session() as s:
            c = [x for x in engine.load_catalog_with_ext(s) if x['solution_id'] == sid][0]
        self.assertEqual((c['offer_cost'], c['fee_impact'], c['active_yn']), (3000, '감소', 'Y'))

    def test_reject_needs_reason(self):
        pid = self._add('시험용 신규 유형 B')
        with self.assertRaises(proposals.BadInput):
            proposals.reject(pid, '팀', '')
        proposals.reject(pid, '팀', '근거 부족')

    def test_duplicate_name_blocked(self):
        pid = self._add('구독료 부담형')
        with self.assertRaises(proposals.BadInput):
            proposals.approve(pid, '팀', None, True)


class ApproveRejectTest(unittest.TestCase):
    def _plan(self, risk='HIGH', skip=0):
        settings.LLM_MODE = 'template'
        with session() as s:
            sid = s.fetchall("SELECT p.subscription_id FROM churn_prediction p JOIN churn_prediction_type t ON t.subscription_id=p.subscription_id "
                             "AND t.type_rank=1 WHERE p.risk_level=? ORDER BY p.churn_probability DESC LIMIT 1 OFFSET ?", (risk, skip))[0]['subscription_id']
        return h.make_plan(sid, True)

    def _rows(self, run_id):
        with session() as s:
            return s.fetchall('SELECT * FROM action_log WHERE run_id=? ORDER BY action_id', (run_id,))

    def test_plan_has_limits(self):
        r = self._plan('HIGH', 3)
        lim = r['plan']['limits']
        self.assertGreater(lim['cost_cap'], 0)
        self.assertGreaterEqual(lim['cost_limit'], r['plan']['total_cost'])
        self.assertEqual(lim['max_actions'], 2)

    def test_approve_saves_edited_text_and_depts(self):
        r = self._plan('HIGH', 1)
        cust = [a['solution_id'] for a in r['plan']['actions'] if a['target'] in ('사용자', '둘 다')]
        self.assertTrue(cust)
        h.approve(r['run_id'], cust, part='customer', message_text='상담자가 고친 문구', edited=True, decision_note='문구 수정')
        rows = self._rows(r['run_id'])
        self.assertTrue(rows)
        for x in rows:
            self.assertEqual((x['target'], x['message_text'], x['memo_edited_yn'], x['decision_note']), ('사용자', '상담자가 고친 문구', 'Y', '문구 수정'))
            self.assertTrue(x['created_at'].startswith(r['as_of']), '조치 시각은 예측 기준일(시뮬레이션 날짜)이어야 함')
            self.assertEqual(x['status'], 'simulated')

    def test_internal_part_saves_depts_and_both_target_logged_twice(self):
        # '둘 다' 조치: 고객 발송분과 협업 전달분을 각각 승인할 수 있어야 한다(예전엔 두 번째가 조용히 무시됨)
        plan = {'subscription_id': 'S000001', 'as_of': '2026-09-30', 'plan': {'actions': [
            {'solution_id': 'S-T05-A', 'type_id': 'T05', 'target': '둘 다', 'message_kind': 'info', 'offer_cost': 0,
             'customer_message': '고객 문구', 'internal_note': '내부 메모'}]}}
        with session() as s:
            run_id = s.insert_returning_id('INSERT INTO agent_run (subscription_id, created_at, llm_used, model, plan_json) VALUES (?,?,?,?,?)',
                                           ('S000001', '2026-09-30 10:00:00', 'N', None, json.dumps(plan, ensure_ascii=False)), 'run_id')
        h.approve(run_id, None, part='customer', message_text='고객 최종')
        h.approve(run_id, None, part='internal', internal_memo='부서 최종', forward_depts=['케어서비스팀', '고객센터(CS)'], edited=True)
        h.approve(run_id, None, part='internal', internal_memo='중복')            # 같은 분량 재승인은 무시
        rows = self._rows(run_id)
        self.assertEqual([(x['target'], x['message_text']) for x in rows], [('사용자', '고객 최종'), ('LG 내부 전달', '부서 최종')])
        self.assertEqual(rows[1]['forward_depts'], '케어서비스팀,고객센터(CS)')
        self.assertIsNone(rows[0]['forward_depts'])

    def test_bad_part_rejected(self):
        r = self._plan('HIGH', 2)
        with self.assertRaises(h.BadRequest):
            h.approve(r['run_id'], None, part='x')

    def test_reject_needs_reason_and_does_not_block_resuggest(self):
        r = self._plan('HIGH', 4)
        ids = [a['solution_id'] for a in r['plan']['actions']]
        with self.assertRaises(h.BadRequest):
            h.reject(r['run_id'], '  ')
        out = h.reject(r['run_id'], '고객 요청으로 보류')
        self.assertEqual(out['status'], '반려')
        rows = self._rows(r['run_id'])
        self.assertEqual({x['status'] for x in rows}, {'반려'})
        self.assertTrue(all(x['decision_note'] == '고객 요청으로 보류' for x in rows))
        r2 = self._plan('HIGH', 4)                                  # 같은 고객 다시 계획 → 반려한 솔루션이 쿨다운에 걸리면 안 됨
        self.assertEqual([a['solution_id'] for a in r2['plan']['actions']], ids)
        self.assertEqual(r2['plan']['limits']['spent_90d'], 0)

    def test_reject_unknown_run(self):
        with self.assertRaises(h.NotFound):
            h.reject(999999, '사유')


class ManualForwardAndDuplicateTest(unittest.TestCase):
    def _plan(self, skip):
        settings.LLM_MODE = 'template'
        with session() as s:
            sid = s.fetchall("SELECT p.subscription_id FROM churn_prediction p JOIN churn_prediction_type t ON t.subscription_id=p.subscription_id "
                             "AND t.type_rank=1 WHERE p.risk_level='HIGH' ORDER BY p.churn_probability DESC LIMIT 1 OFFSET ?", (skip,))[0]['subscription_id']
        return h.make_plan(sid, True)

    def test_same_action_not_recorded_twice_for_same_customer(self):
        r = self._plan(20)
        first = h.approve(r['run_id'], None, part='customer')
        self.assertTrue(first['logged_action_ids'])
        second = h.approve(r['run_id'], None, part='customer')              # 같은 플랜 다시 승인
        self.assertEqual(second['logged_action_ids'], [])
        with session() as s:                                                  # 새 플랜(run)으로 같은 조치를 승인해도 중복 기록 없음
            row = s.fetchone('SELECT plan_json FROM agent_run WHERE run_id=?', (r['run_id'],))
            r2_id = h.service.save_run(s, json.loads(row['plan_json']))
        self.assertEqual(h.approve(r2_id, None, part='customer')['logged_action_ids'], [])

    def test_manual_forward_is_saved_with_names_and_validates(self):
        r = self._plan(21)
        sid = r['subscription_id']
        out = h.manual_forward(sid, ['고객센터(CS)', '품질팀'], '', r['run_id'])
        self.assertEqual(out['status'], 'simulated')
        rows = h.list_actions(sid, 5)
        m = [x for x in rows if x['solution_id'] == 'MANUAL'][0]
        self.assertEqual(m['message_kind'], '직접 요청')
        self.assertEqual(m['forward_depts'], '고객센터(CS),품질팀')
        self.assertTrue(m['customer_name'])
        with self.assertRaises(h.BadRequest):
            h.manual_forward(sid, [])
        with self.assertRaises(h.NotFound):
            h.manual_forward('NOPE', ['x'])
        # 직접 요청은 쿨다운·중복 검사에 영향을 주지 않는다
        self.assertTrue(h.approve(r['run_id'], None, part='customer')['logged_action_ids'])


class HealthModelTest(unittest.TestCase):
    def test_health_reflects_engine_llm(self):
        settings.LLM_MODE = 'mock'
        self.assertEqual(h.health()['llm'], 'mock')
        settings.LLM_MODE = 'template'
        self.assertEqual(h.health()['llm'], 'template')

    def test_model_comes_from_core_config_unless_env_set(self):
        old = (settings.LLM_MODE, settings.OPENAI_API_KEY, settings.OPENAI_MODEL)
        try:
            settings.LLM_MODE, settings.OPENAI_API_KEY, settings.OPENAI_MODEL = 'auto', 'sk-test-not-real', ''
            self.assertEqual(engine.make_llm().model, engine.load_config()['llm']['model'])
            self.assertEqual(engine.load_config()['llm']['model'], 'gpt-5.4-nano')
            settings.OPENAI_MODEL = 'my-model'
            self.assertEqual(engine.make_llm().model, 'my-model')
        finally:
            settings.LLM_MODE, settings.OPENAI_API_KEY, settings.OPENAI_MODEL = old


class MigrateTest(unittest.TestCase):
    def test_migrate_adds_missing_columns_and_keeps_rows(self):
        from db import schema
        with session() as s:
            before = s.scalar('SELECT COUNT(*) FROM action_log')
            for c in ('decision_note', 'forward_depts', 'memo_edited_yn'):
                s.execute(f'ALTER TABLE action_log DROP COLUMN {c}')
        with session() as s:
            added = schema.migrate(s)
        self.assertEqual(sorted(added), ['action_log.decision_note', 'action_log.forward_depts', 'action_log.memo_edited_yn'])
        with session() as s:
            self.assertEqual(s.scalar('SELECT COUNT(*) FROM action_log'), before)
            self.assertEqual(schema.migrate(s), [])                 # 두 번째는 할 일이 없다


if __name__ == '__main__':
    unittest.main()
