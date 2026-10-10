"""평가 지표(M2). 정확도는 쓰지 않는다(이탈률 약 8%라 전부 '정상'이어도 92%)."""
from __future__ import annotations
import numpy as np, pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss


def at_top(y, p, pct: float) -> dict:
    """확률 상위 pct%를 '위험'으로 본 Recall/Precision/Lift."""
    y = np.asarray(y); n = max(int(round(len(y) * pct / 100)), 1); top = np.argsort(-np.asarray(p), kind="stable")[:n]
    tp = y[top].sum(); base = y.mean()
    return dict(recall=tp / max(y.sum(), 1), precision=tp / n, lift=(tp / n) / base if base > 0 else np.nan)


def summarize(y, p, tops=(5, 10, 20)) -> dict:
    y = np.asarray(y); out = dict(n=len(y), base_rate=float(y.mean()))
    if y.sum() == 0 or y.sum() == len(y): return out
    out.update(pr_auc=float(average_precision_score(y, p)), roc_auc=float(roc_auc_score(y, p)), brier=float(brier_score_loss(y, np.clip(p, 0, 1))), mean_p=float(np.mean(p)))
    for k in tops:
        r = at_top(y, p, k); out.update({f"recall@{k}": r["recall"], f"precision@{k}": r["precision"], f"lift@{k}": r["lift"]})
    return out


def by_group(y, p, groups: pd.Series, tops=(10,)) -> pd.DataFrame:
    rows = []
    for g, idx in groups.groupby(groups).groups.items():
        m = summarize(np.asarray(y)[groups.index.get_indexer(idx)], np.asarray(p)[groups.index.get_indexer(idx)], tops); m["group"] = g; rows.append(m)
    return pd.DataFrame(rows).set_index("group")
