from __future__ import annotations
import json
from pathlib import Path

def load_catalog(path) -> list[dict]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    sols = d["solutions"] if isinstance(d, dict) else d
    out = []
    for s in sols:
        s = dict(s)
        s.setdefault("priority", 50); s.setdefault("eligibility", None)
        if isinstance(s["eligibility"], str): s["eligibility"] = json.loads(s["eligibility"])
        out.append(s)
    return out
