"""VIIRS 16-day vegetation index loader (NOAA/VIIRS/001/VNP13A1, 500 m).

NDVI, EVI and EVI2 are scaled integers (x0.0001). VI_Quality bits 0-1 are MODLAND_QA:
0 = VI produced, good quality (kept); 1-3 = check QA / cloudy / not produced (masked).

Output bands: NDVI, EVI2, EVI (EVI is present in the catalog band list).
"""
from __future__ import annotations

import pandas as pd

from .utils import collection_to_dataframe, get_logger, log_fetch

ASSET = "NOAA/VIIRS/001/VNP13A1"
SRC_BANDS = ["NDVI", "EVI2", "EVI"]
OUT_BANDS = ["NDVI", "EVI2", "EVI"]
QA_BAND = "VI_Quality"
SCALE = 0.0001
QA_MASK_BITS = 0b11   # bits 0-1 MODLAND_QA
QA_GOOD = 0

log = get_logger("data_loaders.viirs_vi")


def load(aoi, start_date: str, end_date: str, seed: int = 0, **kwargs):
    """Return an ee.ImageCollection with bands NDVI, EVI2, EVI."""
    import ee  # lazy import

    col = ee.ImageCollection(ASSET).filterBounds(aoi).filterDate(start_date, end_date)

    def _prep(img):
        good = img.select(QA_BAND).bitwiseAnd(QA_MASK_BITS).eq(QA_GOOD)
        out = img.select(SRC_BANDS).multiply(SCALE).updateMask(good).select(SRC_BANDS, OUT_BANDS)
        return out.copyProperties(img, ["system:time_start", "system:index"])

    col = col.map(_prep).sort("system:time_start").sort("system:index")
    log_fetch(log, "viirs_vi", col, ASSET, aoi, start_date, end_date, seed,
              kwargs.get("log_count", True))
    return col


def to_dataframe(aoi, start_date: str, end_date: str, seed: int = 0,
                 scale: int = 500, use_cache: bool = True, **kwargs) -> pd.DataFrame:
    """Per-image AOI mean NDVI/EVI2/EVI as a cached DataFrame."""
    return collection_to_dataframe(load, ASSET, OUT_BANDS, aoi, start_date, end_date,
                                   seed, scale, use_cache, **kwargs)


load_viirs_vi = load
