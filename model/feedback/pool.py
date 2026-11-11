"""주관식 문장 풀 로더·검사·표집. 시연·합성 중에는 LLM을 호출하지 않고 이 풀에서만 뽑는다."""
from __future__ import annotations
import re
from functools import lru_cache
from pathlib import Path
import yaml
from .mapping import load_mapping

HERE = Path(__file__).resolve().parent
POOL_FILE, VARIANTS_FILE = HERE / "sentence_pool.yaml", HERE / "sentence_pool_variants.yaml"
EXTRA_KEYS = ("sat", "other", "issue_other")
_PII = [re.compile(p) for p in (r"\d{2,4}[-\s]?\d{3,4}[-\s]?\d{4}", r"[\w.+-]+@[\w-]+\.[\w.]+", r"https?://", r"www\.", r"\d{6}[-\s]?\d{7}")]
MIN_LEN, MAX_LEN = 4, 120


def required_keys() -> list[str]:
    m = load_mapping()
    return list(m["type_to_issue"].values()) + list(m["hidden_topics"]) + list(EXTRA_KEYS)


def check_sentence(s: str) -> str | None:
    """문장 하나가 풀에 들어가도 되는지. 문제가 있으면 이유, 없으면 None. 개인정보 형태(전화·이메일·주민번호·URL)와 길이를 막는다."""
    if not isinstance(s, str) or not s.strip(): return "빈 문장"
    if not (MIN_LEN <= len(s.strip()) <= MAX_LEN): return f"길이 {len(s.strip())}자"
    for p in _PII:
        if p.search(s): return "개인정보 형태"
    if re.search(r"\b[A-Za-z]+(?:_[A-Za-z0-9]+)+\b", s): return "내부 필드명 형태"
    return None


def _load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


@lru_cache(maxsize=1)
def load_pool() -> dict:
    """{키: [문장...]} = 시드 + 검토 확정된 변형 문장. 순서를 고정해 같은 시드가 항상 같은 문장을 고르게 한다."""
    seeds = (_load_yaml(POOL_FILE) or {}).get("seeds", {})
    var = (_load_yaml(VARIANTS_FILE) or {}).get("variants", {})
    pool = {}
    for k in dict.fromkeys(list(seeds) + list(var)):
        pool[k] = list(dict.fromkeys(list(seeds.get(k, [])) + list(var.get(k, []))))
    problems = check_pool(pool)
    if problems: raise ValueError("문장 풀 오류: " + "; ".join(problems[:5]))
    return pool


def check_pool(pool: dict) -> list[str]:
    errs = [f"키 {k} 없음" for k in required_keys() if not pool.get(k)]
    for k, v in pool.items():
        if k not in required_keys(): errs.append(f"알 수 없는 키 {k}")
        if k != "issue_other" and len(v) < 3: errs.append(f"{k}: 문장 3개 미만")
        for s in v:
            why = check_sentence(s)
            if why: errs.append(f"{k}: {s[:20]!r} {why}")
    return errs


def sample(rng, key: str) -> str:
    v = load_pool()[key]
    return v[int(rng.random() * len(v))]
