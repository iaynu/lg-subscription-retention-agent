"""PostgreSQL에 샘플 데이터(8개 구독, 기준일 2026-09-30)를 넣는다.

  export DATABASE_URL=postgresql+psycopg2://user:pw@host:5432/dbname
  python scripts/load_sample_data.py --reset     # 스키마를 지우고 DDL·시드부터 다시 적재(주의: public 스키마 삭제)
  python scripts/load_sample_data.py             # DDL·시드가 이미 있는 DB에 샘플만 추가

그다음 config.yaml에서 source: sql, storage.backend: sql로 바꾸고 API를 띄우면 DB → Agent 전체 흐름을 볼 수 있다.
"""
import argparse, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine  # noqa: E402
from agent.sample_data import load_sample  # noqa: E402


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url: print("DATABASE_URL 환경변수가 필요합니다."); return 1
    e = create_engine(url, future=True)
    if a.reset:
        raw = e.raw_connection()
        try:
            cur = raw.cursor(); cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            cur.execute((ROOT / "sql" / "schema_postgres.sql").read_text(encoding="utf-8"))
            cur.execute((ROOT / "sql" / "seed_solution_catalog.sql").read_text(encoding="utf-8")); raw.commit()
        finally: raw.close()
    load_sample(e)
    print("샘플 적재 완료: S100001~S100008 (기준일 2026-09-30)")
    return 0


if __name__ == "__main__": raise SystemExit(main())
