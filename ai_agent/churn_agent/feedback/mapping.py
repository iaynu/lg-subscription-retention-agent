"""survey_mapping.yaml / config.yaml 로더와 검증. 코드표(questionnaire)와 어긋난 매핑은 불러올 때 바로 오류로 알린다."""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
import yaml
from . import questionnaire as Q

HERE = Path(__file__).resolve().parent


class MappingError(ValueError):
    pass


@lru_cache(maxsize=1)
def load_mapping() -> dict:
    m = yaml.safe_load((HERE / "survey_mapping.yaml").read_text(encoding="utf-8"))
    problems = check_mapping(m)
    if problems: raise MappingError("매핑표 오류: " + "; ".join(problems))
    return m


@lru_cache(maxsize=1)
def load_config() -> dict:
    return yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))


def check_mapping(m: dict) -> list[str]:
    """매핑표의 모든 코드가 설문 코드표에 있는지, 유형 10개가 빠짐없이 있는지 점검한다."""
    errs = []
    types = [f"T{i:02d}" for i in range(1, 11)]
    if sorted(m["type_to_issue"]) != types: errs.append("type_to_issue가 T01~T10 전부가 아님")
    for t, c in m["type_to_issue"].items():
        if c not in Q.ISSUES: errs.append(f"type_to_issue {t}: 불편 코드 {c} 없음")
    if len(set(m["type_to_issue"].values())) != 10: errs.append("type_to_issue가 1:1이 아님")
    for a, b in m["confusable_pairs"]:
        if a not in types or b not in types: errs.append(f"헷갈리는 쌍 {a},{b}: 유형 ID 오류")
    for h, v in m["hidden_topics"].items():
        for c in v["issue_codes"]:
            if c not in Q.ISSUES: errs.append(f"hidden_topics {h}: 불편 코드 {c} 없음")
    iss = [i for i in Q.ISSUES if i != "other"]
    if sorted(m["issue_to_wanted"]) != sorted(iss): errs.append("issue_to_wanted가 불편 코드 10개 전부가 아님")
    for i, pair in m["issue_to_wanted"].items():
        if len(pair) != 2: errs.append(f"issue_to_wanted {i}: 주·부 2개여야 함")
        for c in pair:
            if c not in Q.SOL: errs.append(f"issue_to_wanted {i}: wanted 코드 {c} 없음")
    for n, issues in m["newsol_to_issues"].items():
        if n not in Q.NEWSOL: errs.append(f"newsol_to_issues: 보기 {n} 없음")
        for c in issues:
            if c not in iss: errs.append(f"newsol_to_issues {n}: 불편 코드 {c} 없음")
    for s, codes in m["solution_to_action"].items():
        if not codes: errs.append(f"solution_to_action {s}: 비어 있음")
        for c in codes:
            if c not in Q.SOL: errs.append(f"solution_to_action {s}: action 코드 {c} 없음")
    if m["unmapped_action_code"] not in Q.QUESTIONS["action"][1]: errs.append("unmapped_action_code가 action 보기에 없음")
    return errs


def action_codes(solution_id: str) -> list[str]:
    """솔루션이 설문 '받은 조치' 보기로 어떻게 기록되는지(앞이 주). 대응이 없으면 [other]."""
    m = load_mapping()
    return list(m["solution_to_action"].get(solution_id) or [m["unmapped_action_code"]])
