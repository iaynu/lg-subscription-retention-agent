"""페이지 5-2(d): 적재된 DB에서 시연 12건을 Agent(모의 LLM, SqlSource/SqlRepo)에 실제로 돌려 결과표를 만든다.

python -m datagen.run_demo_agent --url "$DATABASE_URL" [--cases datagen/out/demo_cases.csv] [--out datagen/out/demo_agent_results.md]
churn_agent 패키지 경로를 sys.path에 넣어 쓴다. 예측은 임시(stub-gen)이므로 '파이프라인이 끝까지 도는지'만 본다.
"""
from __future__ import annotations
import sys, argparse
from pathlib import Path
import pandas as pd

AGENT = Path(__file__).resolve().parents[1] / "churn_agent"


def run(url: str, cases: str, out: str) -> pd.DataFrame:
    sys.path.insert(0, str(AGENT))
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from agent.api import create_app
    from agent.config import ROOT, load_config
    from agent.catalog import load_catalog
    from agent.llm.mock import RuleBasedMock
    from agent.repo.sql import SqlRepo
    from agent.service import AgentService
    from agent.sources import SqlSource
    e = create_engine(url, future=True); cfg = load_config()
    c = TestClient(create_app(AgentService(cfg, load_catalog(ROOT / "data/solution_catalog.json"), RuleBasedMock(), SqlRepo(None, e)), SqlSource(engine=e, facts_cfg=cfg["facts"])))
    with e.connect() as cx:
        from sqlalchemy import text
        mv = ", ".join(r[0] for r in cx.execute(text("SELECT DISTINCT model_version FROM churn_prediction ORDER BY 1")))
    d = pd.read_csv(cases, encoding="utf-8-sig"); rows = []
    for r in d.itertuples():
        x = c.post("/agent/run", json={"subscription_id": r.subscription_id})
        j = x.json() if x.status_code == 200 else {}
        rows.append(dict(case_id=r.case_id, subscription_id=r.subscription_id, true_types=r.true_types, status=x.status_code, outcome=j.get("outcome"),
                         selected=",".join(s["solution_id"] for s in j.get("selected", [])), fallback=j.get("fallback") or "", attempts=j.get("attempts"),
                         failed_checks=",".join(map(str, j.get("failed_checks") or [])), expected=r.expected))
    res = pd.DataFrame(rows)
    L = [f"# 시연 12건 Agent 실행 결과 (모의 LLM, 예측 모델 버전: {mv})", "", "> 가설 기반 synthetic data. 이 표는 파이프라인이 DB에서 끝까지 도는지 확인한 것이지 모델 성능이 아닙니다." + (" `stub-gen`은 학습 모델이 아니라 생성 확률로 채운 임시 예측입니다." if "stub" in mv else " 시연 사례는 모델이 맞게 예측한 구독에서 골랐으므로 통과율이 정확도를 뜻하지 않습니다."), "",
         "| 사례 | 구독 | 정답 유형 | 결과 | 선택된 솔루션 | 폴백 | 기대 |", "|---|---|---|---|---|---|---|"]
    L += [f"| {r.case_id} | {r.subscription_id} | {r.true_types} | {r.outcome} | {r.selected} | {r.fallback or '-'} | {r.expected} |" for r in res.itertuples()]
    Path(out).write_text("\n".join(L) + "\n", encoding="utf-8"); return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--url", required=True); ap.add_argument("--cases", default="datagen/out/demo_cases.csv"); ap.add_argument("--out", default="datagen/out/demo_agent_results.md")
    print(run(**vars(ap.parse_args())).drop(columns=["expected"]).to_string())
