"""SMAP L4 soil moisture loader (NASA/SMAP/SPL4SMGP/008, 3-hourly, ~11 km).

The 3-hourly images are averaged to one image per UTC day (mean of all available
3-hourly images of that day; days without data are dropped). Values are already in
m3/m3 (no scale factor). Output bands: SM_surface (0-5 cm), SM_rootzone (0-100 cm).
"""
from __future__ import annotations

import pandas as pd

from .utils import collection_to_dataframe, daily_composites, get_logger, log_fetch

ASSET = "NASA/SMAP/SPL4SMGP/008"
SRC_BANDS = ["sm_surface", "sm_rootzone"]
OUT_BANDS = ["SM_surface", "SM_rootzone"]

log = get_logger("data_loaders.smap")


def load(aoi, start_date: str, end_date: str, seed: int = 0, **kwargs):
    """Return a daily-mean ee.ImageCollection with bands SM_surface, SM_rootzone."""
    import ee  # lazy import

    raw = (ee.ImageCollection(ASSET).filterBounds(aoi).filterDate(start_date, end_date)
           .select(SRC_BANDS))

    def _reduce(sub):
        return sub.mean().select(SRC_BANDS, OUT_BANDS)

    col = daily_composites(ee, raw, start_date, end_date, _reduce)
    log_fetch(log, "smap", col, ASSET, aoi, start_date, end_date, seed,
              kwargs.get("log_count", True))
    return col


def to_dataframe(aoi, start_date: str, end_date: str, seed: int = 0,
                 scale: int = 11000, use_cache: bool = True, **kwargs) -> pd.DataFrame:
    """Per-day AOI mean soil moisture as a cached DataFrame."""
    return collection_to_dataframe(load, ASSET, OUT_BANDS, aoi, start_date, end_date,
                                   seed, scale, use_cache, **kwargs)


load_smap = load
