"""Deterministic cache keys and Parquet/NetCDF cache under cache/data_loaders."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("DATA_LOADERS_CACHE_DIR", _ROOT / "cache" / "data_loaders"))


def aoi_hash(aoi: Any) -> str:
    """Stable 12-char hash of an AOI.

    Accepts a GeoJSON-like dict, a coordinates list, a string, or an object with
    ``getInfo()`` (ee.Geometry; this triggers one server call). Floats are
    rounded to 6 decimals so equivalent geometries hash identically.
    """
    if hasattr(aoi, "getInfo"):
        aoi = aoi.getInfo()

    def norm(x):
        if isinstance(x, float):
            return round(x, 6)
        if isinstance(x, (list, tuple)):
            return [norm(i) for i in x]
        if isinstance(x, dict):
            return {k: norm(v) for k, v in x.items()}
        return x

    payload = json.dumps(norm(aoi), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def cache_key(asset: str, aoi: Any, start_date: str, end_date: str) -> str:
    safe = asset.replace("/", "_")
    return f"{safe}__{aoi_hash(aoi)}__{start_date}_{end_date}"


def cache_path(asset: str, aoi: Any, start_date: str, end_date: str, ext: str = "parquet") -> Path:
    return CACHE_DIR / f"{cache_key(asset, aoi, start_date, end_date)}.{ext}"


def read_cached(asset: str, aoi: Any, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    p = cache_path(asset, aoi, start_date, end_date)
    if p.exists():
        return pd.read_parquet(p)
    csv = p.with_suffix(".csv")
    return pd.read_csv(csv) if csv.exists() else None


def write_cached(df: pd.DataFrame, asset: str, aoi: Any, start_date: str, end_date: str) -> Path:
    """Write Parquet if pyarrow/fastparquet is available, else CSV fallback."""
    p = cache_path(asset, aoi, start_date, end_date)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(p, index=False)
        return p
    except ImportError:
        csv = p.with_suffix(".csv")
        df.to_csv(csv, index=False)
        return csv
