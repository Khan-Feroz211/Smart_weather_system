"""MODIS Terra 16-day vegetation index loader (MODIS/061/MOD13Q1, 250 m).

NDVI and EVI are stored as scaled integers (x0.0001). DetailedQA bits 0-1 are the
"VI quality (MODLAND QA)" flags: 0 = VI produced with good quality, 1 = produced but
check other QA, 2 = probably cloudy, 3 = not produced. Only value 0 is kept.

Output bands: NDVI, EVI.
"""
from __future__ import annotations

import pandas as pd

from .utils import collection_to_dataframe, get_logger, log_fetch

ASSET = "MODIS/061/MOD13Q1"
SRC_BANDS = ["NDVI", "EVI"]
OUT_BANDS = ["NDVI", "EVI"]
QA_BAND = "DetailedQA"
SCALE = 0.0001
QA_MASK_BITS = 0b11   # bits 0-1 MODLAND QA
QA_GOOD = 0

log = get_logger("data_loaders.modis_vi")


def load(aoi, start_date: str, end_date: str, seed: int = 0, **kwargs):
    """Return an ee.ImageCollection with bands NDVI and EVI (unitless, scaled)."""
    import ee  # lazy import

    col = ee.ImageCollection(ASSET).filterBounds(aoi).filterDate(start_date, end_date)

    def _prep(img):
        good = img.select(QA_BAND).bitwiseAnd(QA_MASK_BITS).eq(QA_GOOD)
        out = img.select(SRC_BANDS).multiply(SCALE).updateMask(good).select(SRC_BANDS, OUT_BANDS)
        return out.copyProperties(img, ["system:time_start", "system:index"])

    col = col.map(_prep).sort("system:time_start").sort("system:index")
    log_fetch(log, "modis_vi", col, ASSET, aoi, start_date, end_date, seed,
              kwargs.get("log_count", True))
    return col


def to_dataframe(aoi, start_date: str, end_date: str, seed: int = 0,
                 scale: int = 250, use_cache: bool = True, **kwargs) -> pd.DataFrame:
    """Per-image AOI mean NDVI/EVI as a cached DataFrame."""
    return collection_to_dataframe(load, ASSET, OUT_BANDS, aoi, start_date, end_date,
                                   seed, scale, use_cache, **kwargs)


load_modis_vi = load
