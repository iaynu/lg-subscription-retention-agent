import json, pytest
from agent.config import ROOT, load_config
from agent.catalog import load_catalog

@pytest.fixture(scope="session")
def catalog(): return load_catalog(ROOT / "data/solution_catalog.json")

@pytest.fixture(scope="session")
def demos(): return json.loads((ROOT / "data/demo_subscribers.json").read_text(encoding="utf-8"))["demos"]

@pytest.fixture
def cfg(): return load_config()

def pg_reset(engine):
    """PostgreSQL 스키마를 비우고 패키지의 DDL·시드(sql/)를 적재한다."""
    raw = engine.raw_connection()
    try:
        cur = raw.cursor(); cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cur.execute((ROOT / "sql" / "schema_postgres.sql").read_text(encoding="utf-8"))
        cur.execute((ROOT / "sql" / "seed_solution_catalog.sql").read_text(encoding="utf-8")); raw.commit()
    finally: raw.close()

def base_input(types, level="HIGH", fee=50000, facts=None, recent=None, ad="Y", rank=0.05):
    return {"request_id": "T", "as_of_date": "2026-09-30",
            "subscription": {"subscription_id": "S1", "monthly_fee": fee, "ad_consent_yn": ad},
            "prediction": {"risk_level": level, "risk_rank_pct": rank, "churn_probability": 0.6},
            "types": [{"type_rank": i + 1, "type_id": t, "contribution_share": s, "top_features": []} for i, (t, s) in enumerate(types)],
            "facts": facts or {}, "recent_actions": recent or []}
