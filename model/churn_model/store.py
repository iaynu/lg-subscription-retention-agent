"""페이지 M4(b): 예측을 PostgreSQL에 반영한다(임시 예측 stub-gen 등 기존 예측을 교체).

replace_predictions(url, pred, ptype, as_of) — 같은 기준일의 기존 churn_prediction(+type)을 지우고 새로 적재한다.
agent_result가 기존 예측을 참조 중이면 거부한다(감사 이력 보호). 접속 문자열은 호출자가 넘긴다(환경변수 등).
"""
from __future__ import annotations
import io
import pandas as pd


def _conn(url):
    import psycopg2
    from sqlalchemy.engine import make_url
    u = make_url(url); return psycopg2.connect(host=u.query.get("host", u.host), port=u.query.get("port", u.port), dbname=u.database, user=u.username, password=u.password)


def _copy(cur, table, df):
    buf = io.StringIO(); df.to_csv(buf, index=False, header=False, na_rep="\\N"); buf.seek(0)
    cur.copy_expert(f"COPY {table} ({', '.join(df.columns)}) FROM STDIN WITH (FORMAT csv, NULL '\\N')", buf)


def replace_predictions(url, pred: pd.DataFrame, ptype: pd.DataFrame, as_of) -> dict:
    conn = _conn(url); cur = conn.cursor()
    try:
        cur.execute("SELECT count(*) FROM agent_result r JOIN churn_prediction p ON p.prediction_id = r.prediction_id WHERE p.as_of_date = %s", (as_of,))
        if cur.fetchone()[0]: raise RuntimeError("agent_result가 기존 예측을 참조 중이라 교체하지 않습니다")
        cur.execute("DELETE FROM churn_prediction_type WHERE prediction_id IN (SELECT prediction_id FROM churn_prediction WHERE as_of_date = %s)", (as_of,))
        cur.execute("DELETE FROM churn_prediction WHERE as_of_date = %s", (as_of,))
        _copy(cur, "churn_prediction", pred)
        cur.execute("SELECT subscription_id, prediction_id FROM churn_prediction WHERE as_of_date = %s AND model_version = %s", (as_of, pred.model_version.iloc[0]))
        pid = dict(cur.fetchall()); t = ptype.copy(); t["prediction_id"] = t.subscription_id.map(pid)
        _copy(cur, "churn_prediction_type", t[["prediction_id", "type_rank", "type_id", "contribution_share", "top_features_json"]])
        conn.commit()
        cur.execute("SELECT count(*) FROM churn_prediction"); n1 = cur.fetchone()[0]; cur.execute("SELECT count(*) FROM churn_prediction_type"); n2 = cur.fetchone()[0]
        cur.execute("SELECT model_version, count(*) FROM churn_prediction GROUP BY 1"); mv = dict(cur.fetchall())
        return dict(churn_prediction=n1, churn_prediction_type=n2, model_versions=mv)
    finally:
        conn.close()
