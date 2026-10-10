"""DB(SqlSource)에서 AgentInput JSON을 내보낸다 → DB 없이 측정·시연에 쓰는 입력 파일.
python scripts/export_inputs.py --cases ../datagen/out/demo_cases.csv --out data/demo_inputs_12.json   (DATABASE_URL 환경변수 필요)"""
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from sqlalchemy import create_engine
from agent.config import load_config
from agent.sources import SqlSource

ap = argparse.ArgumentParser(); ap.add_argument("--cases", required=True); ap.add_argument("--out", required=True); a = ap.parse_args()
cfg = load_config(); src = SqlSource(engine=create_engine(os.environ["DATABASE_URL"], future=True), facts_cfg=cfg["facts"])
cases = pd.read_csv(a.cases, encoding="utf-8-sig"); out = []
for r in cases.itertuples():
    ai = src.get_input(r.subscription_id); ai.pop("request_id", None); out.append(dict(case_id=r.case_id, scenario=r.scenario, expected=r.expected, input=ai))
Path(a.out).write_text(json.dumps(dict(note="가설 기반 synthetic data(가상 고객). 모델 버전: " + str(cases.shape[0]) + "건 시연", cases=out), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print(f"{len(out)}건 저장: {a.out}")
