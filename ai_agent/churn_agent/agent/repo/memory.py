"""메모리 저장소(데모·테스트용). SqlRepo와 같은 인터페이스."""
from __future__ import annotations
import datetime as dt, itertools


class MemoryRepo:
    def __init__(self): self.actions: list[dict] = []; self.results: dict[str, dict] = {}; self._id = itertools.count(1)

    def recent_actions(self, subscription_id, as_of_date, days=90):
        lim = dt.date.fromisoformat(as_of_date) - dt.timedelta(days=days)
        return [dict(solution_id=a["solution_id"], action_status=a["action_status"], offer_cost=a["offer_cost"], created_at=a["created_at"][:10])
                for a in self.actions if a["subscription_id"] == subscription_id and dt.date.fromisoformat(a["created_at"][:10]) > lim]

    def log_action(self, row):
        row = dict(row, action_id=next(self._id)); self.actions.append(row); return row

    def list_actions(self, subscription_id): return [a for a in self.actions if a["subscription_id"] == subscription_id]

    def put_result(self, request_id, result):
        if request_id in self.results: raise ValueError("이미 있는 request_id")
        self.results[request_id] = result

    def get_result(self, request_id): return self.results.get(request_id)
