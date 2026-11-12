"""페이지 5-2(c): PostgreSQL 적재(COPY) + 무결성 검사 + 임시 예측 적재.

load_all(url_or_conn, cfg, tables, reset=True, stub=True) -> dict(rows, integrity, ...)
- DDL·시드는 churn_agent/sql/ 것을 쓴다(reset=True면 public 스키마를 비우고 새로 만든다).
- product_profile은 config의 products에서 넣는다. 나머지는 OUTPUT_TABLES 순서(부모→자식)로 COPY.
- GENERATED ALWAYS 식별자 컬럼은 DataFrame에 없으므로 COPY 대상에서 자연히 빠진다.
- 접속 문자열은 호출자가 넘긴다(환경변수 TEST_DATABASE_URL 등). 비밀번호를 코드·설정 파일에 쓰지 않는다.
CLI: python -m datagen.load_pg --url "$DATABASE_URL" [--n 5000] [--no-stub]
"""
from __future__ import annotations
import io, argparse, json
from pathlib import Path
import pandas as pd
from .generate import OUTPUT_TABLES
from .export import clean
from .stub_predictions import build_stub_predictions

SQL_DIR = Path(__file__).resolve().parents[1] / "churn_agent" / "sql"


def _conn(url):
    import psycopg2
    if not isinstance(url, str): return url
    from sqlalchemy.engine import make_url
    u = make_url(url); return psycopg2.connect(host=u.query.get("host", u.host), port=u.query.get("port", u.port), dbname=u.database, user=u.username, password=u.password)


def copy_df(cur, table: str, df: pd.DataFrame):
    df = clean(df); buf = io.StringIO(); df.to_csv(buf, index=False, header=False, na_rep="\\N"); buf.seek(0)
    cols = ", ".join(df.columns)
    cur.copy_expert(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '\\N')", buf)


def load_all(url, cfg: dict, t: dict, reset: bool = True, stub: bool = True) -> dict:
    conn = _conn(url); cur = conn.cursor(); res = {}
    try:
        if reset:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            cur.execute((SQL_DIR / "schema_postgres.sql").read_text(encoding="utf-8")); cur.execute((SQL_DIR / "seed_solution_catalog.sql").read_text(encoding="utf-8"))
        for name, p in cfg["population"]["products"].items():
            cur.execute("INSERT INTO product_profile VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (name, p["structure"], p["fee_min"], p["fee_max"], p["contract_months"], p["care_included_default"], p["visit_interval_months"],
                         p["base_fault_rate_monthly"], p["base_usage_per_week"], p["weight"]))
        for name in OUTPUT_TABLES: copy_df(cur, name, t[name])
        if stub:
            pred, ptype = build_stub_predictions(cfg, t); copy_df(cur, "churn_prediction", pred)
            cur.execute("SELECT subscription_id, prediction_id FROM churn_prediction WHERE model_version = %s", (pred.model_version.iloc[0],))
            pid = dict(cur.fetchall()); ptype["prediction_id"] = ptype.subscription_id.map(pid)
            copy_df(cur, "churn_prediction_type", ptype[["prediction_id", "type_rank", "type_id", "contribution_share", "top_features_json"]])
        conn.commit()
        res["rows"] = {}
        for name in ["product_profile"] + OUTPUT_TABLES + (["churn_prediction", "churn_prediction_type"] if stub else []):
            cur.execute(f"SELECT count(*) FROM {name}"); res["rows"][name] = cur.fetchone()[0]
        res["integrity"] = run_integrity(cur)
    finally:
        conn.close()
    return res


def run_integrity(cur) -> dict:
    """check_integrity.sql(관계별 고아 행 수 29행) 실행 → 검사 수와 0이 아닌 위반 목록."""
    cur.execute((SQL_DIR / "check_integrity.sql").read_text(encoding="utf-8"))
    rows = cur.fetchall() if cur.description else []
    return dict(checks=len(rows), violations=[r for r in rows if r[-1] not in (0, None)])


if __name__ == "__main__":
    from .config import load_config
    from .generate import generate_all
    ap = argparse.ArgumentParser(); ap.add_argument("--url", required=True); ap.add_argument("--n", type=int); ap.add_argument("--no-stub", action="store_true")
    a = ap.parse_args(); cfg = load_config(**({"run": {"n_subscriptions": a.n}} if a.n else {}))
    r = load_all(a.url, cfg, generate_all(cfg), stub=not a.no_stub); print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
