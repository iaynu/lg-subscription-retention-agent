import os, sys, json
from pathlib import Path
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datagen.config import load_config
from datagen.generate import generate_all, OUTPUT_TABLES
from datagen.export import export_tables, clean
from datagen.stub_predictions import build_stub_predictions, MODEL_VERSION


@pytest.fixture(scope="module")
def env():
    cfg = load_config(run={"n_subscriptions": 3000})
    return cfg, generate_all(cfg)


def test_clean_casts_integer_floats():
    df = pd.DataFrame({"a": [1.0, np.nan, 3.0], "b": [1.5, 2.0, 3.0]})
    o = clean(df); assert str(o.a.dtype) == "Int64" and o.b.dtype == float


def test_export_csv_parquet_roundtrip(env, tmp_path):
    _, t = env; res = export_tables(t, tmp_path)
    assert len(res) == 2 * len(OUTPUT_TABLES) and not (tmp_path / "label_detail.csv").exists()
    raw = (tmp_path / "payment.csv").read_bytes(); assert not raw.startswith(b"\xef\xbb\xbf") and b".0," not in raw.split(b"\n")[1]
    assert len(pd.read_parquet(tmp_path / "subscription.parquet")) == len(t["subscription"])


def test_stub_predictions(env):
    cfg, t = env; pred, pt = build_stub_predictions(cfg, t)
    assert len(pred) == len(t["subscription"]) and (pred.model_version == MODEL_VERSION).all()
    assert pred.risk_rank_pct.between(0, 0.99999).all() and pred.churn_probability.between(0, 1).all()
    assert set(pred.risk_level) == {"HIGH", "MID", "LOW"} and abs((pred.risk_level == "HIGH").mean() - 0.10) < 0.01
    assert pt.groupby("subscription_id").size().max() <= 3 and set(pt.type_rank) <= {1, 2, 3}
    s = pt.groupby("subscription_id").contribution_share.sum(); assert np.allclose(s, 1.0, atol=1e-3)
    f = [x for js in pt.top_features_json for x in json.loads(js)]; assert f and all(isinstance(x["value"], (int, float)) for x in f)
    act = set(t["synth_truth_type"].subscription_id); assert set(pt.subscription_id) == act      # 정상군은 유형 행이 없음


URL = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL 없음")
def test_load_pg_and_integrity(env):
    from datagen.load_pg import load_all
    cfg, t = env; r = load_all(URL, cfg, t)
    assert r["integrity"]["checks"] == 29 and r["integrity"]["violations"] == []
    assert r["rows"]["subscription"] == len(t["subscription"]) and r["rows"]["churn_prediction_type"] > 0
