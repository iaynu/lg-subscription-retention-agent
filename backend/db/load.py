"""CSV -> DB 적재.  사용:  python -m db.load --data data_in --reset
- 여러 번 실행해도 같은 결과가 나오도록, 기본은 대상 테이블을 비우고 다시 넣는다(--reset 은 테이블까지 새로 만든다).
- agent_run / action_log 는 건드리지 않는다(--reset 일 때만 삭제)."""
import argparse
import csv
import io
import json
import sys
from pathlib import Path

from config import ROOT, settings
from db import schema
from db.conn import connect

CATALOG = ROOT / 'data' / 'catalog' / 'solution_catalog.json'
LOAD_ORDER = ['care_manager', 'customer', 'subscription', 'usage_thinq', 'care_visit', 'voc_contact', 'payment',
              'notice_log', 'churn_prediction', 'churn_prediction_type', 'synth_churn_label', 'synth_truth_type']


def _conv(v, ty):
    if v is None or v == '':
        return None
    if ty == 'I':
        return int(float(v))
    if ty == 'R':
        return float(v)
    return v


def _rows(path, table):
    cols = schema.columns(table)
    types = [schema.coltype(table, c) for c in cols]
    with open(path, newline='', encoding='utf-8-sig') as f:
        rd = csv.DictReader(f)
        missing = [c for c in cols if c not in rd.fieldnames]
        if missing:
            raise SystemExit(f'{path.name}: 컬럼 없음 {missing}')
        for r in rd:
            yield tuple(_conv(r[c], t) for c, t in zip(cols, types))


def load_csv(s, table, path):
    cols = schema.columns(table)
    s.execute(f'DELETE FROM {table}')
    if s.kind == 'postgres':
        # COPY 는 INSERT 보다 훨씬 빠르다(수십만 행)
        with s.conn.cursor().copy(f'COPY {table} ({",".join(cols)}) FROM STDIN') as cp:
            for row in _rows(path, table):
                cp.write_row(row)
    else:
        ph = ','.join('?' * len(cols))
        batch = []
        for row in _rows(path, table):
            batch.append(row)
            if len(batch) >= 20000:
                s.executemany(f'INSERT INTO {table} ({",".join(cols)}) VALUES ({ph})', batch)
                batch = []
        if batch:
            s.executemany(f'INSERT INTO {table} ({",".join(cols)}) VALUES ({ph})', batch)
    return s.scalar(f'SELECT COUNT(*) FROM {table}')


TEAM_CATALOG = ROOT.parent / 'model' / 'data' / 'solution_catalog.json'
_TARGET = {'고객 메시지': '사용자', '상담·앱 안내': '사용자', '내부 전달': 'LG 내부 전달', '전사 개선': 'LG 내부 전달'}


def load_master(s, catalog_path=CATALOG):
    """유형·솔루션 마스터. 팀원 카탈로그(model/data/solution_catalog.json, 39개)를 DB 표로도 넣어 /catalog 에서 보이게 한다.
    (Agent 엔진은 이 JSON 을 직접 읽는다. DB 표는 조회용.)"""
    old = json.loads(Path(catalog_path).read_text(encoding='utf-8')) if Path(catalog_path).exists() else {'types': []}
    group = {t['type_id']: t.get('group_name') for t in old['types']}
    cat = json.loads(TEAM_CATALOG.read_text(encoding='utf-8'))
    s.execute('DELETE FROM solution_catalog')
    s.execute('DELETE FROM churn_type')
    tcols = schema.columns('churn_type')
    trows = [dict(type_id=t['type_id'], type_name=t['name'], group_name=group.get(t['type_id']), description=t.get('desc'))
             for t in cat['types']]
    if any(x['type_id'] == '유형없음' for x in cat['solutions']):
        trows.append(dict(type_id='유형없음', type_name='유형 없음(만족도 조사)', group_name=None,
                          description='이탈 유형이 없는 고객에게 만족도 조사를 안내하는 솔루션 묶음'))
    s.executemany(f'INSERT INTO churn_type ({",".join(tcols)}) VALUES ({",".join("?" * len(tcols))})',
                  [tuple(t.get(c) for c in tcols) for t in trows])
    scols = schema.columns('solution_catalog')
    srows = []
    for x in cat['solutions']:
        srows.append(dict(solution_id=x['solution_id'], type_id=x['type_id'], solution_name=x.get('name'),
                          solution_category=x.get('category'), solution_detail=x.get('detail'),
                          target=_TARGET.get(x.get('delivery_kind'), '사용자'),
                          message_kind='ad' if x.get('ad_yn') == 'Y' else 'info', offer_cost=int(x.get('offer_cost') or 0),
                          sample_message=x.get('message_hint'), priority=x.get('priority'), active_yn=x.get('active_yn')))
    s.executemany(f'INSERT INTO solution_catalog ({",".join(scols)}) VALUES ({",".join("?" * len(scols))})',
                  [tuple(r.get(c) for c in scols) for r in srows])
    return len(trows), len(srows)


def validate(s):
    """적재 후 정합성 점검. 문제가 있으면 문자열 목록으로 돌려준다."""
    probs = []
    q = {
        'subscription 의 customer 없음': 'SELECT COUNT(*) FROM subscription s LEFT JOIN customer c ON c.customer_id=s.customer_id WHERE c.customer_id IS NULL',
        'prediction 의 subscription 없음': 'SELECT COUNT(*) FROM churn_prediction p LEFT JOIN subscription s ON s.subscription_id=p.subscription_id WHERE s.subscription_id IS NULL',
        'prediction_type 의 type 없음': 'SELECT COUNT(*) FROM churn_prediction_type p LEFT JOIN churn_type t ON t.type_id=p.type_id WHERE t.type_id IS NULL',
        'solution 의 type 없음': 'SELECT COUNT(*) FROM solution_catalog x LEFT JOIN churn_type t ON t.type_id=x.type_id WHERE t.type_id IS NULL',
        'care_visit 의 subscription 없음': 'SELECT COUNT(*) FROM care_visit v LEFT JOIN subscription s ON s.subscription_id=v.subscription_id WHERE s.subscription_id IS NULL',
    }
    for msg, sql in q.items():
        n = s.scalar(sql)
        if n:
            probs.append(f'{msg}: {n}건')
    return probs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data_in', help='CSV 가 있는 폴더')
    ap.add_argument('--reset', action='store_true', help='테이블을 지우고 새로 만든다(agent_run/action_log 포함)')
    ap.add_argument('--url', default=None, help='DATABASE_URL 덮어쓰기')
    a = ap.parse_args(argv)
    data = Path(a.data)
    if not data.is_absolute():
        data = (ROOT / data) if (ROOT / data).exists() else Path.cwd() / data
    if not data.exists():
        raise SystemExit(f'데이터 폴더가 없어요: {data}')
    s = connect(a.url)
    try:
        if a.reset:
            schema.drop_all(s)
        schema.create_all(s)
        s.commit()
        nt, ns = load_master(s)
        print(f'[master] churn_type {nt}, solution_catalog {ns}')
        for t in LOAD_ORDER:
            p = data / schema.CSV_TABLES[t]
            if not p.exists() and t in schema.CSV_FALLBACK and (data / schema.CSV_FALLBACK[t]).exists():
                p = data / schema.CSV_FALLBACK[t]
                print(f'[주의] {schema.CSV_TABLES[t]} 가 없어 예전 스텁 파일({p.name})을 씁니다')
            if not p.exists():
                raise SystemExit(f'파일 없음: {p}')
            n = load_csv(s, t, p)
            print(f'[load] {t:<24} {n:>9,}')
            s.commit()
        for t, fn in schema.OPTIONAL_CSV.items():
            p = data / fn
            if p.exists():
                n = load_csv(s, t, p)
                print(f'[load] {t:<24} {n:>9,}')
                s.commit()
            else:
                print(f'[건너뜀] {fn} 없음 (설문 분석 화면은 비어 있어요)')
        probs = validate(s)
        if probs:
            print('[경고]', *probs, sep='\n  ')
        else:
            print('[검증] 정합성 OK')
        s.commit()
    finally:
        s.conn.close()


if __name__ == '__main__':
    sys.exit(main())
