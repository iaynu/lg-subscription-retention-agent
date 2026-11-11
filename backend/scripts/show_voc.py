"""VOC 데이터 보기.  사용: python -m scripts.show_voc [개수] [유형]
예) python -m scripts.show_voc            → 최근 20건
    python -m scripts.show_voc 50         → 최근 50건
    python -m scripts.show_voc 30 해지문의 → 해지문의 유형 최근 30건
"""
import sys
from db.conn import session


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    cat = sys.argv[2] if len(sys.argv) > 2 else None
    with session() as s:
        total = s.scalar('SELECT COUNT(*) FROM voc_contact')
        print('VOC 전체 %s건' % format(total, ','))
        print('--- 유형별 건수 ---')
        for r in s.fetchall('SELECT category, COUNT(*) n FROM voc_contact GROUP BY category ORDER BY n DESC'):
            print('  %-10s %s' % (r['category'], format(r['n'], ',')))
        print('--- 최근 %d건%s ---' % (n, (' · ' + cat) if cat else ''))
        sql = 'SELECT contact_date, subscription_id, channel, category, summary_text FROM voc_contact '
        p = []
        if cat:
            sql += 'WHERE category=? '
            p.append(cat)
        sql += 'ORDER BY contact_date DESC, subscription_id LIMIT ?'
        p.append(n)
        for r in s.fetchall(sql, p):
            print('%s | %s | %s | %s | %s' % (str(r['contact_date'])[:10], r['subscription_id'], r['channel'], r['category'], r['summary_text']))


main()
