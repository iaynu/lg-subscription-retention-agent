"""승인/추천 기록 확인.  실행:  python -m scripts.check_log"""
from db.conn import session

with session() as s:
    print('=== action_log (승인된 조치, 최근 10건) ===')
    rows = s.fetchall('SELECT action_id, subscription_id, solution_id, target, offer_cost, status, created_at '
                      'FROM action_log ORDER BY action_id DESC LIMIT 10')
    for r in rows:
        print(r)
    if not rows:
        print('(비어 있음 - 아직 승인한 기록이 없어요)')
    print()
    print('=== agent_run (AI 추천 생성 기록, 최근 10건) ===')
    runs = s.fetchall('SELECT run_id, subscription_id, llm_used, model, created_at FROM agent_run ORDER BY run_id DESC LIMIT 10')
    for r in runs:
        print(r)
    if not runs:
        print('(비어 있음 - 아직 추천을 생성한 기록이 없어요)')
