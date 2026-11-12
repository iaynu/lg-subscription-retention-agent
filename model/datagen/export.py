"""페이지 5-2(a): CSV·Parquet 출력.

export_tables(tables, out_dir, fmt=("csv","parquet")) -> {파일 경로: 행 수}
- CSV는 UTF-8(BOM 없음), 결측은 빈 칸. 정수처럼 쓰는 실수 컬럼은 Int64로 바꿔 '3.0'이 아니라 '3'으로 쓴다(COPY 적재 오류 방지).
- label_detail(생성 확률)은 검증용이라 출력하지 않는다(OUTPUT_TABLES만 출력).
"""
from __future__ import annotations
from pathlib import Path
import numpy as np, pandas as pd
from .generate import OUTPUT_TABLES


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """정수값만 가진 float 컬럼을 Int64로 바꾼다."""
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_float_dtype(df[c]):
            s = df[c].dropna()
            if len(s) and np.all(np.isclose(s, np.round(s))): df[c] = df[c].round().astype("Int64")
    return df


def export_tables(t: dict, out_dir, fmt=("csv", "parquet")) -> dict:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True); res = {}
    for name in OUTPUT_TABLES:
        df = clean(t[name])
        if "csv" in fmt: p = out / f"{name}.csv"; df.to_csv(p, index=False, encoding="utf-8"); res[str(p)] = len(df)
        if "parquet" in fmt: p = out / f"{name}.parquet"; df.to_parquet(p, index=False); res[str(p)] = len(df)
    return res
