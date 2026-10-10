"""전체 생성 오케스트레이터: 페이지 2~5를 순서대로 돌려 테이블 딕셔너리를 만든다.

    from datagen.config import load_config
    from datagen.generate import generate_all
    t = generate_all(load_config())          # {'care_manager': DataFrame, 'customer': ..., 'subscription': ..., ...}

테이블 이름은 DDL 테이블 이름과 같다(product_profile·churn_type 등 참조 테이블은 DB 시드/설정에서 온다).
label_detail(생성 확률)은 검증용이라 DB·출력 파일 대상이 아니다(OUTPUT_TABLES에 없음).
"""
from __future__ import annotations
from .population import build_population
from .modulation import apply_type_modulation
from .events_basic import build_events_basic
from .events_visit import build_visits
from .events_voc import build_voc
from .labels import build_labels

OUTPUT_TABLES = ["care_manager", "customer", "subscription", "usage_thinq", "care_visit", "voc_contact", "payment", "notice_log",
                 "synth_churn_label", "synth_truth_type"]          # 적재·출력 순서(부모 → 자식)


def generate_all(cfg: dict) -> dict:
    pop = apply_type_modulation(cfg, build_population(cfg))
    ev = build_events_basic(cfg, pop)
    visits = build_visits(cfg, pop); voc = build_voc(cfg, pop, visits)
    lb = build_labels(cfg, pop)
    return dict(care_manager=pop["care_manager"], customer=pop["customer"], subscription=pop["subscription"],
                usage_thinq=ev["usage_thinq"], care_visit=visits, voc_contact=voc, payment=ev["payment"], notice_log=ev["notice_log"],
                synth_churn_label=lb["synth_churn_label"], synth_truth_type=lb["synth_truth_type"], label_detail=lb["label_detail"])
