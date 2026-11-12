"""페이지 M4(a): 전 구독 점수 산출 — 교차 적합(out-of-fold)으로 예측 확률·위험 등급·유형을 만든다.

crossfit_predict(F, y, mc, tcfg, sc) -> dict(prediction, prediction_type, oof_prob, S, G, folds)
- 학습에 쓴 구독을 같은 모델로 점수화하면 확률이 과하게 극단적이라, 5-겹 교차 적합으로 모든 구독을 '자기를 보지 않은 모델'로 점수화한다.
- 위험 순위: 전체 OOF 확률 순위 백분위(0=가장 높음). 상위 10% HIGH, 다음 10% MID, 나머지 LOW.
- 유형: 그 구독을 점수화한 모델의 SHAP으로 select_types(M3와 같은 규칙).
산출 DataFrame은 DB 테이블 churn_prediction / churn_prediction_type 형식(prediction_id 제외)과 같다.
"""
from __future__ import annotations
import numpy as np, pandas as pd
from sklearn.model_selection import StratifiedKFold, train_test_split
from .features import encode
from .train import fit_ids
from .shap_types import group_shap, select_types


def crossfit_predict(F: pd.DataFrame, y: pd.Series, mc: dict, tcfg: dict, sc: dict, as_of) -> dict:
    key = (F.product_category.astype(str) + "_" + y.astype(str)).to_numpy(); ids = np.asarray(F.index, dtype=object)
    skf = StratifiedKFold(n_splits=sc["folds"], shuffle=True, random_state=mc["seed"]); prob = pd.Series(np.nan, index=F.index); Ss, Gs, info = [], [], []
    for k, (tr_i, te_i) in enumerate(skf.split(ids, key)):
        rest, te = ids[tr_i], ids[te_i]
        tr, va = train_test_split(rest, test_size=0.25, stratify=key[tr_i], random_state=mc["seed"] + k)
        cm = fit_ids(F, y, tr, va, mc); prob.loc[te] = cm.predict(encode(F).loc[te])
        S, G, _ = group_shap(cm, F.loc[te]); Ss.append(S); Gs.append(G); info.append(dict(fold=k, n_test=len(te), best_iter=int(cm.model.best_iteration)))
    S, G = pd.concat(Ss).loc[F.index], pd.concat(Gs).loc[F.index]
    rank = prob.rank(ascending=False, method="first") - 1; pct = (rank / len(F)).clip(upper=0.99999); hi, mid = sc["risk_split"]["high"], sc["risk_split"]["mid"]
    level = np.where(pct < hi, "HIGH", np.where(pct < hi + mid, "MID", "LOW"))
    pred = pd.DataFrame({"subscription_id": F.index, "as_of_date": as_of, "churn_probability": prob.clip(0, 1).round(4).to_numpy(), "risk_level": level,
                         "risk_rank_pct": pct.round(5).to_numpy(), "model_version": sc["model_version"]})
    pt = select_types(S, G, F, tcfg).drop(columns=["group_shap"])
    return dict(prediction=pred, prediction_type=pt, oof_prob=prob, S=S, G=G, folds=pd.DataFrame(info))
