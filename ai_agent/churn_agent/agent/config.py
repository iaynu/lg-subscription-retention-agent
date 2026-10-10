from __future__ import annotations
import os
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "rules": dict(cooldown_same_solution_days=14, cooldown_count_statuses=["조치완료"], cost_cap_ratio=1.0,
                  cost_cap_cumulative_ratio=2.0, cost_cap_cumulative_days=90, max_actions=2,
                  survey_recommend_rank_range=[0.20, 0.30], fallback_survey_when_empty=True, unknown_required_fact="fail"),
    "llm": dict(provider="mock", model="gpt-4o-mini", api_key_env="OPENAI_API_KEY", temperature=None,
                first_timeout_s=10, retry_timeout_s=8, deadline_s=15, max_message_chars=400, banned_phrases_extra=[]),
    "facts": dict(trial_days=7, short_care_minutes=20, season_peak_mmdd={}),
    "clock": "simulated",
    "source": "demo",
    "storage": dict(backend="memory", database_url_env="DATABASE_URL"),
    "catalog_path": "data/solution_catalog.json",
}


def load_config(path: str | None = None) -> dict:
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    p = Path(path or os.environ.get("AGENT_CONFIG") or ROOT / "config.yaml")
    if p.exists():
        y = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k, v in y.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict): cfg[k].update(v)
            else: cfg[k] = v
    return cfg
