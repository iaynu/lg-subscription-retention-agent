"""DB 연결 어댑터. PostgreSQL(psycopg 3)과 SQLite 를 같은 코드로 다룬다.
SQL 안의 자리표시자는 항상 ? 로 쓴다(Postgres 에서는 자동으로 %s 로 바뀜). SQL 안에 % 문자는 쓰지 않는다."""
import datetime as dt
import re
import sqlite3
from contextlib import contextmanager
from urllib.parse import urlparse

from config import settings


def _norm(v):
    # 날짜/시간은 항상 ISO 문자열로 돌려준다(두 DB 의 반환형 차이를 없애고 JSON 직렬화를 쉽게)
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=' ', timespec='seconds')
    if isinstance(v, dt.date):
        return v.isoformat()
    return v


class Session:
    def __init__(self, conn, kind):
        self.conn, self.kind = conn, kind
        self.ph = '%s' if kind == 'postgres' else '?'

    def _sql(self, sql):
        return sql.replace('?', '%s') if self.kind == 'postgres' else sql

    def _cur(self):
        return self.conn.cursor()

    def execute(self, sql, params=()):
        cur = self._cur()
        cur.execute(self._sql(sql), tuple(params))
        return cur

    def executemany(self, sql, rows):
        cur = self._cur()
        cur.executemany(self._sql(sql), rows)
        return cur

    def fetchall(self, sql, params=()):
        cur = self.execute(sql, params)
        rows = cur.fetchall()
        if self.kind == 'sqlite':
            return [{k: _norm(r[k]) for k in r.keys()} for r in rows]
        return [{k: _norm(v) for k, v in r.items()} for r in rows]

    def fetchone(self, sql, params=()):
        rows = self.fetchall(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql, params=()):
        cur = self.execute(sql, params)
        r = cur.fetchone()
        if r is None:
            return None
        return _norm(r[0] if self.kind == 'sqlite' else list(r.values())[0])

    def insert_returning_id(self, sql, params, id_col):
        """INSERT 한 뒤 자동 생성된 id 를 돌려준다."""
        if self.kind == 'postgres':
            cur = self.execute(sql + f' RETURNING {id_col}', params)
            return list(cur.fetchone().values())[0]
        cur = self.execute(sql, params)
        return cur.lastrowid

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()


def _norm_url(url):
    # SQLAlchemy 식 주소(postgresql+psycopg://...)도 받아 준다 -> psycopg 가 이해하는 postgresql:// 로 변환
    return re.sub(r'^(postgres|postgresql)\+\w+://', 'postgresql://', url.strip())


def kind_of(url):
    s = urlparse(_norm_url(url)).scheme
    if s in ('postgres', 'postgresql'):
        return 'postgres'
    if s == 'sqlite':
        return 'sqlite'
    raise ValueError(f'지원하지 않는 DATABASE_URL: {url}')


def connect(url=None):
    url = url or settings.DATABASE_URL
    kind = kind_of(url)
    if kind == 'sqlite':
        path = url.split('sqlite:///', 1)[1]
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        return Session(conn, kind)
    import psycopg
    from psycopg.rows import dict_row
    conn = psycopg.connect(_norm_url(url), row_factory=dict_row)
    return Session(conn, kind)


@contextmanager
def session(url=None):
    s = connect(url)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.conn.close()
