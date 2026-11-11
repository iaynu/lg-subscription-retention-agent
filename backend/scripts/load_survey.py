"""설문 CSV 3개만 다시 적재한다(다른 테이블은 건드리지 않음).
사용:  python -m scripts.load_survey --data data_in
(survey_send → survey_response → survey_answer 순서로 비우고 넣는다. ai_proposal 은 그대로 둔다.)"""
import argparse
import sys
from pathlib import Path

from config import ROOT
from db import schema
from db.conn import connect
from db.load import load_csv


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data_in')
    ap.add_argument('--url', default=None)
    a = ap.parse_args(argv)
    data = Path(a.data)
    if not data.is_absolute():
        data = (ROOT / data) if (ROOT / data).exists() else Path.cwd() / data
    s = connect(a.url)
    try:
        schema.create_all(s)
        s.commit()
        for t in ('survey_send', 'survey_response', 'survey_answer'):
            p = data / schema.OPTIONAL_CSV[t]
            if not p.exists():
                raise SystemExit(f'파일 없음: {p}')
        # 자식 → 부모 순서로 비운 뒤 부모 → 자식 순서로 넣는다
        for t in ('survey_answer', 'survey_response', 'survey_send'):
            s.execute(f'DELETE FROM {t}')
        s.commit()
        for t in ('survey_send', 'survey_response', 'survey_answer'):
            n = load_csv(s, t, data / schema.OPTIONAL_CSV[t])
            print(f'[load] {t:<18} {n:>9,}')
            s.commit()
    finally:
        s.conn.close()


if __name__ == '__main__':
    sys.exit(main())
