import os, sys
from pathlib import Path
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datagen.config import load_config
from datagen.generate import generate_all
from datagen.demo_cases import select_demo_cases
from churn_model.io import from_generated
from churn_model.features import build_features, HERE
from churn_model.train import load_model_cfg
from churn_model.shap_types import load_type_cfg
from churn_model.score import crossfit_predict
import yaml


@pytest.fixture(scope="module")
def env():
    cfg = load_config(run={"n_subscriptions": 4000}); g = generate_all(cfg); F = build_features(from_generated(g))
    y = g["synth_churn_label"].set_index("subscription_id").churn_30d.reindex(F.index); sc = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))["scoring"]
    r = crossfit_predict(F, y, load_model_cfg(), load_type_cfg(), sc, "2026-09-30"); return cfg, g, F, y, r


def test_every_subscription_scored_once_out_of_fold(env):
    _, _, F, y, r = env; p = r["prediction"]
    assert len(p) == len(F) and p.subscription_id.is_unique and not r["oof_prob"].isna().any() and p.churn_probability.between(0, 1).all()
    assert len(r["folds"]) == 5 and r["folds"].n_test.sum() == len(F)


def test_risk_levels_follow_rank_split(env):
    p = env[4]["prediction"]; share = p.risk_level.value_counts(normalize=True)
    assert abs(share["HIGH"] - 0.10) < 0.002 and abs(share["MID"] - 0.10) < 0.002 and p.risk_rank_pct.between(0, 0.99999).all()
    hp = p[p.risk_level == "HIGH"].churn_probability.min(); lp = p[p.risk_level == "LOW"].churn_probability.max(); assert hp >= lp          # 등급은 확률 순위와 일치


def test_oof_quality_and_calibration(env):
    from churn_model.metrics import summarize
    _, _, F, y, r = env; m = summarize(y, r["oof_prob"].to_numpy()); assert m["pr_auc"] > 2.5 * m["base_rate"] and abs(m["mean_p"] - m["base_rate"]) < 0.03


def test_type_rows_valid_for_db(env):
    pt = env[4]["prediction_type"]
    assert list(pt.columns) == ["subscription_id", "type_rank", "type_id", "contribution_share", "top_features_json"]
    assert pt.type_rank.between(1, 3).all() and pt.contribution_share.between(0, 1).all() and not pt.duplicated(["subscription_id", "type_id"]).any() and pt.groupby("subscription_id").size().max() <= 3


DATA = Path(__file__).resolve().parents[2] / "data_out"


@pytest.mark.skipif(not (DATA / "churn_prediction.parquet").exists(), reason="data_out 예측 파일 없음")
def test_demo_selection_uses_model_predictions():
    cfg = load_config(); g = generate_all(cfg); pred = pd.read_parquet(DATA / "churn_prediction.parquet"); ptype = pd.read_parquet(DATA / "churn_prediction_type.parquet")
    d = select_demo_cases(cfg, g, pred, ptype); pr = pred.set_index("subscription_id"); pts = ptype.groupby("subscription_id").type_id.apply(set)
    assert len(d) == 12
    for c in d.itertuples():
        if c.case_id <= "D10": assert pr.risk_level[c.subscription_id] in ("HIGH", "MID") and set(c.true_types.split(",")) <= pts.get(c.subscription_id, set())


URL = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL 없음")
def test_replace_predictions_in_pg(env):
    from datagen.load_pg import load_all
    from churn_model.store import replace_predictions
    cfg, g, F, y, r = env; load_all(URL, cfg, g)            # 스키마 초기화 + 임시 예측(stub-gen) 적재
    out = replace_predictions(URL, r["prediction"], r["prediction_type"], r["prediction"].as_of_date.iloc[0])
    assert out["model_versions"] == {"xgb-oof-v1": len(F)} and out["churn_prediction_type"] == len(r["prediction_type"])
