"""명령줄 실행.
  python -m agent.cli list                     # 구독 목록(설정의 source: demo|sql)
  python -m agent.cli run S000101 [--as-of 2026-09-30]
"""
import argparse, json
from .factory import build
from .schemas import AgentInput


def main(argv=None):
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list"); r = sp.add_parser("run"); r.add_argument("subscription_id"); r.add_argument("--as-of")
    ap.add_argument("--config")
    a = ap.parse_args(argv); svc, src = build(a.config)
    if a.cmd == "list": print(json.dumps(src.list_subscriptions(), ensure_ascii=False, indent=1)); return 0
    ai = src.get_input(a.subscription_id, a.as_of)
    if ai is None: print("not found"); return 1
    ai.pop("request_id", None)                      # 실행마다 새 ID(저장소가 SQL이어도 충돌 없음)
    print(json.dumps(svc.run(AgentInput(**ai).model_dump()), ensure_ascii=False, indent=1)); return 0


if __name__ == "__main__": raise SystemExit(main())
