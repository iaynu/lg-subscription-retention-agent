"""유형별로 구독을 몇 개씩 뽑아 Agent 가 조치를 내는지 점검한다.  python -m scripts.scenario_check [--n 40]"""
import argparse
import collections

from agent_v1 import service
from db.conn import session


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=40)
    a = ap.parse_args()
    with session() as s:
        cat = service.load_catalog(s)
        print(f'{"유형":<6}{"표본":>5}{"조치있음":>8}{"평균조치수":>10}  자주 나온 솔루션')
        for t in [f'T{i:02d}' for i in range(1, 11)]:
            rows = s.fetchall(
                "SELECT p.subscription_id FROM churn_prediction_type t JOIN churn_prediction p ON p.subscription_id=t.subscription_id "
                "WHERE t.type_rank=1 AND t.type_id=? AND p.risk_level IN ('HIGH','MID') LIMIT ?", (t, a.n))
            hit, tot, cnt = 0, 0, collections.Counter()
            for r in rows:
                res = service.make_plan(s, r['subscription_id'], use_llm=False, catalog=cat)
                acts = res['plan']['actions']
                hit += 1 if acts else 0
                tot += len(acts)
                cnt.update(x['solution_id'] for x in acts)
            n = max(len(rows), 1)
            print(f'{t:<6}{len(rows):>5}{hit / n:>8.0%}{tot / n:>10.1f}  {dict(cnt.most_common(3))}')


if __name__ == '__main__':
    main()
