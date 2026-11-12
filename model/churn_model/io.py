"""테이블 읽기: 더미 데이터 폴더(Parquet) 또는 DB에서 DataFrame 딕셔너리로 불러온다."""
from __future__ import annotations
from pathlib import Path
import pandas as pd

TABLES = ["care_manager", "customer", "subscription", "usage_thinq", "care_visit", "voc_contact", "payment", "notice_log"]
LABEL_TABLES = ["synth_churn_label", "synth_truth_type"]
DATE_COLS = {"subscription": ["start_date", "end_date", "promo_end_date", "care_renewal_date"], "usage_thinq": ["week_start_date"],
             "care_visit": ["scheduled_date", "completed_date", "as_received_date", "expected_complete_date"], "voc_contact": ["contact_date"],
             "payment": ["billing_month", "due_date", "paid_date"], "notice_log": ["sent_at", "opened_at"], "customer": ["joined_at"],
             "synth_churn_label": ["as_of_date", "cancel_requested_date"]}


def load_parquet(folder, names=TABLES) -> dict:
    """Parquet 폴더에서 읽는다. 날짜 컬럼은 datetime64로 통일(날짜 비교 일관성)."""
    out = {}
    for n in names:
        df = pd.read_parquet(Path(folder) / f"{n}.parquet")
        for c in DATE_COLS.get(n, []):
            if c in df: df[c] = pd.to_datetime(df[c])
        out[n] = df
    return out


def from_generated(tables: dict, names=TABLES) -> dict:
    """datagen.generate_all 결과(날짜가 date 객체)를 같은 형식(datetime64)으로 맞춘다."""
    out = {}
    for n in names:
        df = tables[n].copy()
        for c in DATE_COLS.get(n, []):
            if c in df: df[c] = pd.to_datetime(df[c])
        out[n] = df
    return out
