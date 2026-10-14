"""Harmonized Landsat Sentinel-2 loader (NASA/HLS/HLSS30/v2, optional HLSL30/v2). NEW code.

Band names (verified on the GEE catalog):
  HLSS30: B2 blue, B4 red, B8 NIR (broad), B11 SWIR1
  HLSL30: B2 blue, B4 red, B5 NIR, B6 SWIR1
Reflectance scale 0.0001. Indices:
  EVI  = 2.5*(NIR-RED)/(NIR+6*RED-7.5*BLUE+1)
  LSWI = (NIR-SWIR1)/(NIR+SWIR1)
Fmask bits masked: 1 cloud, 2 adjacent to cloud/shadow, 3 cloud shadow, 4 snow/ice.
Bit 0 (cirrus) is reserved/unused and bit 5 (water) is kept.

Output bands: EVI, LSWI. Pass ``include_landsat=True`` to merge HLSL30.
"""
from __future__ import annotations

import pandas as pd

from .utils import collection_to_dataframe, get_logger, log_fetch

ASSET = "NASA/HLS/HLSS30/v2"
ASSET_L30 = "NASA/HLS/HLSL30/v2"
BAND_MAP = {
    "S30": {"blue": "B2", "red": "B4", "nir": "B8", "swir1": "B11"},
    "L30": {"blue": "B2", "red": "B4", "nir": "B5", "swir1": "B6"},
}
OUT_BANDS = ["EVI", "LSWI"]
SCALE = 0.0001
FMASK = "Fmask"
FMASK_BAD_BITS = (1, 2, 3, 4)
FMASK_BAD_MASK = sum(1 << b for b in FMASK_BAD_BITS)

log = get_logger("data_loaders.hls")


def _indices(ee_unused, img, bands):
    b = img.select([bands["blue"], bands["red"], bands["nir"], bands["swir1"]]).multiply(SCALE)
    blue, red, nir, swir1 = (b.select(bands[k]) for k in ("blue", "red", "nir", "swir1"))
    evi = nir.subtract(red).multiply(2.5).divide(
        nir.add(red.multiply(6)).subtract(blue.multiply(7.5)).add(1)).rename("EVI")
    lswi = nir.subtract(swir1).divide(nir.add(swir1)).rename("LSWI")
    clear = img.select(FMASK).bitwiseAnd(FMASK_BAD_MASK).eq(0)
    return evi.addBands(lswi).updateMask(clear).copyProperties(
        img, ["system:time_start", "system:index"])


def load(aoi, start_date: str, end_date: str, seed: int = 0, **kwargs):
    """Return an ee.ImageCollection with bands EVI, LSWI.

    kwargs: include_landsat (default False; merge HLSL30), max_cloud (percent, optional,
    filters on CLOUD_COVERAGE), log_count (default True).
    """
    import ee  # lazy import

    def _source(asset, key):
        c = ee.ImageCollection(asset).filterBounds(aoi).filterDate(start_date, end_date)
        if kwargs.get("max_cloud") is not None:
            c = c.filter(ee.Filter.lt("CLOUD_COVERAGE", kwargs["max_cloud"]))
        return c.map(lambda img: _indices(ee, img, BAND_MAP[key]))

    col = _source(ASSET, "S30")
    if kwargs.get("include_landsat", False):
        col = col.merge(_source(ASSET_L30, "L30"))
    col = col.sort("system:time_start").sort("system:index")
    log_fetch(log, "hls", col, ASSET, aoi, start_date, end_date, seed, kwargs.get("log_count", True))
    return col


def to_dataframe(aoi, start_date: str, end_date: str, seed: int = 0,
                 scale: int = 30, use_cache: bool = True, **kwargs) -> pd.DataFrame:
    """Per-image AOI mean EVI/LSWI as a cached DataFrame."""
    return collection_to_dataframe(load, ASSET, OUT_BANDS, aoi, start_date, end_date,
                                   seed, scale, use_cache, **kwargs)


load_hls = load
