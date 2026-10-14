"""MODIS Terra daily LST loader (MODIS/061/MOD11A1, 1 km).

Bands used: LST_Day_1km, LST_Night_1km (scale 0.02 -> Kelvin), QC_Day, QC_Night.
QC mask: bits 0-1 are the "mandatory QA flags"; value 0 = "LST produced, good quality,
not necessary to examine more detailed QA". Only pixels with (QC & 0b11) == 0 are kept;
all others (other quality, cloud, no-retrieval) are masked.

Output bands: LST_K (day), LST_Night_K (night), both in Kelvin.
"""
from __future__ import annotations

import pandas as pd

from .utils import collection_to_dataframe, get_logger, log_fetch

ASSET = "MODIS/061/MOD11A1"
SRC_BANDS = ["LST_Day_1km", "LST_Night_1km"]
OUT_BANDS = ["LST_K", "LST_Night_K"]
QC_BANDS = ["QC_Day", "QC_Night"]
SCALE = 0.02
QC_MASK_BITS = 0b11   # bits 0-1 mandatory QA
QC_GOOD = 0           # 0 = good quality

log = get_logger("data_loaders.modis_lst")


def load(aoi, start_date: str, end_date: str, seed: int = 0, **kwargs):
    """Return an ee.ImageCollection with bands LST_K and LST_Night_K (Kelvin).

    kwargs: log_count (default True).
    """
    import ee  # lazy import

    col = ee.ImageCollection(ASSET).filterBounds(aoi).filterDate(start_date, end_date)

    def _prep(img):
        out = None
        for src, qc, dst in zip(SRC_BANDS, QC_BANDS, OUT_BANDS):
            good = img.select(qc).bitwiseAnd(QC_MASK_BITS).eq(QC_GOOD)
            band = img.select(src).multiply(SCALE).updateMask(good).rename(dst)
            out = band if out is None else out.addBands(band)
        return out.copyProperties(img, ["system:time_start", "system:index"])

    col = col.map(_prep).sort("system:time_start").sort("system:index")
    log_fetch(log, "modis_lst", col, ASSET, aoi, start_date, end_date, seed,
              kwargs.get("log_count", True))
    return col


def to_dataframe(aoi, start_date: str, end_date: str, seed: int = 0,
                 scale: int = 1000, use_cache: bool = True, **kwargs) -> pd.DataFrame:
    """Per-image AOI mean LST (K) as a cached DataFrame: time, LST_K, LST_Night_K."""
    return collection_to_dataframe(load, ASSET, OUT_BANDS, aoi, start_date, end_date,
                                   seed, scale, use_cache, **kwargs)


load_modis_lst = load
