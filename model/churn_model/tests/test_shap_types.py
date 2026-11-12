import sys
from pathlib import Path
import numpy as np, pandas as pd, pytest, xgboost as xgb
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datagen.config import load_config
from datagen.generate import generate_all
from churn_model.io import from_generated
from churn_model.features import build_features, encode, feature_groups
from churn_model.train import load_model_cfg, fit
from churn_model.shap_types import group_shap, select_types, recovery, load_type_cfg, TYPES


@pytest.fixture(scope="module")
def env():
    cfg = load_config(run={"n_subscriptions": 4000}); g = generate_all(cfg); F = build_features(from_generated(g))
    y = g["synth_churn_label"].set_index("subscription_id").churn_30d.reindex(F.index); cm, info = fit(F, y, load_model_cfg())
    S, G, base = group_shap(cm, F); return g, F, cm, S, G, base


def test_shap_is_additive(env):
    _, F, cm, S, G, base = env; m = cm.model.get_booster().predict(xgb.DMatrix(encode(F)[cm.cols]), output_margin=True)
    assert np.abs(S.sum(axis=1).to_numpy() + base - m).max() < 1e-4


def test_group_sum_excludes_context(env):
    _, F, cm, S, G, _ = env; g = feature_groups(); ctx = [c for c in S if g[c] == "컨텍스트"]
    assert ctx and list(G.columns) == TYPES and np.allclose(G.sum(axis=1), S.drop(columns=ctx).sum(axis=1), atol=1e-5)


def test_selection_rule_hand_case():
    cols = ["voc_fee_cnt_90d", "voc_totalcost_cnt_90d", "zero_use_weeks_12w", "visit_delay_cnt_365d", "sales_pitch_cnt_180d", "repeat_voc_cnt"]
    S = pd.DataFrame([[0.9, 0.3, 0.7, 0.0, 0.0, 0.0], [0.2, 0.0, 0.0, 0.0, 0.0, 0.0], [-0.5, 0.0, 0.0, 0.0, 0.0, 0.0], [0.6, 0.0, 0.5, 0.45, 0.45, 0.5]], columns=cols, index=["a", "b", "c", "d"])
    g = feature_groups(); G = pd.DataFrame({t: S[[c for c in cols if g[c] == t]].sum(axis=1) for t in TYPES}); F = pd.DataFrame(1, index=S.index, columns=cols)
    out = select_types(S, G, F, {"share_min": 0.2, "shap_min": 0.5, "max_types": 3, "top_features_n": 3})
    a = out[out.subscription_id == "a"]; assert list(a.type_id) == ["T01", "T04"] and a.contribution_share.iloc[0] == pytest.approx(1.2 / 1.9, abs=1e-3)
    assert "b" not in set(out.subscription_id) and "c" not in set(out.subscription_id)          # 절대 기준 미달 / 음수만 → 유형 없음
    d = out[out.subscription_id == "d"]; assert len(d) == 3 and list(d.type_rank) == [1, 2, 3]   # 최대 3개, 큰 순
    assert d.group_shap.is_monotonic_decreasing and '"feature": "voc_fee_cnt_90d"' in a.top_features_json.iloc[0]


def test_top_features_have_values(env):
    g, F, cm, S, G, _ = env; out = select_types(S, G, F, load_type_cfg())
    import json
    allf = [x for js in out.top_features_json for x in json.loads(js)]; assert allf and all(isinstance(x["value"], (int, float)) and x["shap"] > 0 for x in allf)
    gm = feature_groups(); assert all(gm[x["feature"]] == t for t, js in zip(out.type_id, out.top_features_json) for x in json.loads(js))     # 근거 피처는 그 유형 그룹 안에서만


def test_recovery_hand_case():
    pred = pd.DataFrame([("s1", 1, "T01"), ("s1", 2, "T02"), ("s2", 1, "T03")], columns=["subscription_id", "type_rank", "type_id"])
    truth = pd.DataFrame([("s1", "T01", "Y"), ("s2", "T04", "Y"), ("s3", "T05", "Y")], columns=["subscription_id", "type_id", "is_primary_yn"])
    r = recovery(pred, truth, ["s1", "s2", "s3", "s4"]); assert r["per"].precision["T01"] == 1 and r["per"].recall["T04"] == 0 and r["primary_top1_acc"] == pytest.approx(1 / 2)   # 채택 목록이 있는 주 유형 구독만 대상
    assert r["no_type_rate_normal"] == 1.0 and r["no_type_rate_active"] == pytest.approx(1 / 3) and r["any_hit_rate_active"] == pytest.approx(1 / 3)


def test_no_truth_used_in_selection(env):
    g, F, cm, S, G, _ = env; import inspect, churn_model.shap_types as m
    assert "truth" not in inspect.getsource(m.select_types) and "truth" not in inspect.getsource(m.group_shap)
