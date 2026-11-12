import sys
from pathlib import Path
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datagen.config import load_config
from datagen.generate import generate_all
from churn_model.io import from_generated
from churn_model.features import build_features
from churn_model.metrics import at_top, summarize
from churn_model.train import load_model_cfg, split_ids, fit, evaluate


@pytest.fixture(scope="module")
def env():
    cfg = load_config(run={"n_subscriptions": 4000}); g = generate_all(cfg); F = build_features(from_generated(g))
    y = g["synth_churn_label"].set_index("subscription_id").churn_30d.reindex(F.index); return g, F, y, load_model_cfg()


def test_at_top_hand_case():
    y = np.array([1, 0, 1, 0, 0, 0, 0, 0, 0, 0]); p = np.array([.9, .8, .7, .1, .1, .1, .1, .1, .1, .1])
    r = at_top(y, p, 20); assert r["recall"] == 0.5 and r["precision"] == 0.5 and r["lift"] == pytest.approx(2.5)
    assert summarize(y, p)["pr_auc"] > summarize(y, 1 - p)["pr_auc"]


def test_split_is_disjoint_complete_stratified(env):
    _, F, y, mc = env; tr, va, te = split_ids(F, y, mc)
    assert not (set(tr) & set(va)) and not (set(tr) & set(te)) and not (set(va) & set(te)) and len(tr) + len(va) + len(te) == len(F)
    assert abs(len(te) / len(F) - 0.2) < 0.01 and abs(y.loc[te].mean() - y.mean()) < 0.01
    assert set(F.loc[te, "product_category"]) == set(F.product_category)


def test_fit_and_calibration(env):
    g, F, y, mc = env; cm, info = fit(F, y, mc); r = evaluate(cm, F, y, info["test"], mc)
    assert r["test"]["pr_auc"] > 2 * r["test"]["base_rate"] and r["test"]["roc_auc"] > 0.75
    assert r["test"]["pr_auc"] > r["baseline_timing"]["pr_auc"]
    assert abs(r["test"]["mean_p"] - r["test"]["base_rate"]) < 0.03                      # 보정 후 평균 확률이 실제 이탈률과 가까움
    assert r["raw"]["mean_p"] > r["test"]["mean_p"]                                      # 클래스 가중으로 부풀었던 확률을 낮춤
    p = cm.predict(__import__("churn_model.features", fromlist=["encode"]).encode(F)); assert ((p >= 0) & (p <= 1)).all()


def test_labels_not_used_as_features(env):
    _, F, _, _ = env; assert not [c for c in F if "churn" in c or "truth" in c or "cancel_requested" in c]
