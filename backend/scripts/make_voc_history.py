"""2023-01 ~ 2026-03 의 VOC 이력을 '더미로 생성'해서 voc_history 테이블에 넣는다.
- 원본 voc_contact(2026-04 이후)는 건드리지 않는다. AI Agent·기존 집계는 voc_contact 만 읽는다.
- 고객마다 '구독 시작 이후 매달 평균 LAMBDA 건' 정도로 만들고, 문의 유형·요약 문구는
  같은 1순위 이탈 유형 고객들의 (더미) VOC 전체에서 뽑아 와서 유형 특성이 이어진다. 시드 고정 → 매번 같은 결과.
- 지우려면: DROP TABLE voc_history;   사용: python -m scripts.make_voc_history
"""
import math
import random
from collections import defaultdict
from datetime import date

from db.conn import session

LAMBDA = 0.12            # 구독 1건당 월평균 VOC 건수(생성용 가정값)
FIRST, LAST = (2023, 1), (2026, 3)
COLS = ['subscription_id', 'contact_date', 'channel', 'category', 'repeat_contact_yn', 'resolved_yn',
        'first_response_unresolved_yn', 'cancel_delay_days', 'response_hours', 'sentiment_score', 'summary_text']


def poisson(rng, lam):
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def month_days(y, m):
    return (date(y + (m == 12), m % 12 + 1, 1) - date(y, m, 1)).days


def main():
    rng = random.Random(42)
    with session() as s:
        subs = s.fetchall('SELECT s.subscription_id, s.start_date, t.type_id FROM subscription s '
                          'LEFT JOIN churn_prediction_type t ON t.subscription_id=s.subscription_id AND t.type_rank=1')
        top = {r['subscription_id']: r['type_id'] for r in subs}
        pool = defaultdict(list)
        for r in s.fetchall('SELECT ' + ', '.join(COLS) + ' FROM voc_contact'):
            pool[top.get(r['subscription_id'])].append(r)
        allp = [r for v in pool.values() for r in v]
        if not allp:
            raise SystemExit('voc_contact 가 비어 있어요. 먼저 데이터를 적재하세요.')
        s.execute('DROP TABLE IF EXISTS voc_history')
        s.execute('CREATE TABLE voc_history (subscription_id TEXT, contact_date TEXT, channel TEXT, category TEXT, '
                  'repeat_contact_yn TEXT, resolved_yn TEXT, first_response_unresolved_yn TEXT, cancel_delay_days INTEGER, '
                  'response_hours REAL, sentiment_score REAL, summary_text TEXT)')
        rows, n = [], 0
        ins = 'INSERT INTO voc_history (' + ', '.join(COLS) + ') VALUES (' + ','.join('?' * len(COLS)) + ')'
        for sub in subs:
            st = str(sub['start_date'])[:10]
            src = pool.get(sub['type_id']) if len(pool.get(sub['type_id'], ())) >= 30 else allp
            y, m = FIRST
            while (y, m) <= LAST:
                last_day = date(y, m, month_days(y, m)).isoformat()
                if st <= last_day:
                    for _ in range(poisson(rng, LAMBDA)):
                        lo = int(st[8:10]) if st[:7] == last_day[:7] else 1
                        d = date(y, m, rng.randint(lo, month_days(y, m))).isoformat()
                        b = rng.choice(src)
                        rows.append((sub['subscription_id'], d) + tuple(b[c] for c in COLS[2:]))
                m += 1
                if m == 13:
                    y, m = y + 1, 1
            if len(rows) >= 5000:
                s.executemany(ins, rows); n += len(rows); rows = []
        if rows:
            s.executemany(ins, rows); n += len(rows)
        s.commit()
        print('voc_history 생성 완료: %s건 (%d-%02d ~ %d-%02d)' % (format(n, ','), FIRST[0], FIRST[1], LAST[0], LAST[1]))


main()
