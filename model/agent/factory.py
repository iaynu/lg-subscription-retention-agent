from __future__ import annotations
import os
from pathlib import Path
from .config import ROOT, load_config
from .catalog import load_catalog
from .llm.factory import make_llm
from .repo.memory import MemoryRepo
from .service import AgentService
from .sources import DemoSource


def _engine(cfg):
    from sqlalchemy import create_engine
    env = cfg["storage"]["database_url_env"]
    if not os.environ.get(env): raise RuntimeError(f"환경변수 {env}가 없습니다(storage.backend=sql 또는 source=sql).")
    return create_engine(os.environ[env], future=True)


def build(cfg_path=None, llm=None, repo=None, source=None):
    """설정대로 AgentService와 예측 소스를 만든다. storage(memory|sql), source(demo|sql)."""
    cfg = load_config(cfg_path)
    p = Path(cfg["catalog_path"]); p = p if p.is_absolute() else ROOT / p
    engine = None
    if repo is None:
        if cfg["storage"]["backend"] == "sql":
            from .repo.sql import SqlRepo
            engine = _engine(cfg); repo = SqlRepo(None, engine)
        else:
            repo = MemoryRepo()
    if source is None:
        if cfg.get("source", "demo") == "sql":
            from .sources import SqlSource
            source = SqlSource(engine=engine or _engine(cfg), facts_cfg=cfg.get("facts"))
        else:
            source = DemoSource()
    svc = AgentService(cfg, load_catalog(p), llm or make_llm(cfg), repo)
    return svc, source
