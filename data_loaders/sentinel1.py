"""Sentinel-1 GRD loader (COPERNICUS/S1_GRD).

Units: GEE's COPERNICUS/S1_GRD is already converted to decibels (10*log10) after
thermal-noise removal, calibration and terrain correction (see the dataset catalog
page). Values are therefore NOT converted again here; bands are only renamed to the
shared schema: VV->VV_dB, VH->VH_dB, angle->ANGLE.

Speckle: a focal median (circle kernel, ``speckle_radius_m`` metres, default 30 m,
i.e. ~3 px at 10 m) is applied per image. Median on dB values is order-preserving
so it is valid in the log domain.

Determinism: the collection is sorted by system:time_start then system:index; the
seed is accepted for interface parity and recorded in logs (no randomness is used).
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from . import cache
from .utils import get_logger

ASSET = "COPERNICUS/S1_GRD"
SRC_BANDS = ["VV", "VH", "angle"]
OUT_BANDS = ["VV_dB", "VH_dB", "ANGLE"]
DEFAULT_SPECKLE_RADIUS_M = 30

log = get_logger("data_loaders.sentinel1")


def load(aoi, start_date: str, end_date: str, seed: int = 0, **kwargs):
    """Return an ee.ImageCollection with bands VV_dB, VH_dB, ANGLE.

    kwargs: speckle_radius_m (default 30), orbit_pass ('ASCENDING'|'DESCENDING'|None),
    log_count (default True; one getInfo() call for the record count).
    """
    import ee  # lazy import so the package works without earthengine-api

    radius = kwargs.get("speckle_radius_m", DEFAULT_SPECKLE_RADIUS_M)
    orbit_pass = kwargs.get("orbit_pass")

    col = (
        ee.ImageCollection(ASSET)
        .filterBounds(aoi)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
    )
    if orbit_pass:
        col = col.filter(ee.Filter.eq("orbitProperties_pass", orbit_pass))

    def _prep(img):
        smoothed = img.select(["VV", "VH"]).focalMedian(radius, "circle", "meters")
        out = smoothed.addBands(img.select("angle")).select(SRC_BANDS, OUT_BANDS)
        return out.copyProperties(img, ["system:time_start", "system:index"])

    col = col.map(_prep).sort("system:time_start").sort("system:index")

    n: Optional[int] = None
    if kwargs.get("log_count", True):
        try:
            n = int(col.size().getInfo())
        except Exception as exc:  # offline / not initialised: log, keep lazy collection
            log.warning(f"sentinel1 record count unavailable: {exc}")
    log.info(f"fetch asset={ASSET} aoi_hash={_safe_hash(aoi)} range={start_date}..{end_date} "
             f"seed={seed} records={n}")
    return col


def _safe_hash(aoi: Any) -> str:
    try:
        return cache.aoi_hash(aoi)
    except Exception:
        return "unhashable"


def to_dataframe(aoi, start_date: str, end_date: str, seed: int = 0,
                 scale: int = 20, use_cache: bool = True, **kwargs) -> pd.DataFrame:
    """Per-image AOI mean of VV_dB/VH_dB/ANGLE as a cached DataFrame.

    Means are taken in dB (simple mean of dB values; not a linear-power mean).
    Columns: time (UTC ISO str), VV_dB, VH_dB, ANGLE.
    """
    import ee

    if use_cache:
        hit = cache.read_cached(ASSET, aoi, start_date, end_date)
        if hit is not None:
            log.info(f"cache hit asset={ASSET} aoi_hash={_safe_hash(aoi)} "
                     f"range={start_date}..{end_date} records={len(hit)}")
            return hit

    col = load(aoi, start_date, end_date, seed=seed, **kwargs)

    def _stat(img):
        vals = img.reduceRegion(ee.Reducer.mean(), aoi, scale, maxPixels=1e9)
        return ee.Feature(None, vals).set("time", img.date().format("YYYY-MM-dd'T'HH:mm:ss"))

    feats = col.map(_stat).getInfo()["features"]
    rows = [{"time": f["properties"].get("time"),
             **{b: f["properties"].get(b) for b in OUT_BANDS}} for f in feats]
    df = pd.DataFrame(rows, columns=["time"] + OUT_BANDS)
    df = df.sort_values("time", kind="mergesort").reset_index(drop=True)
    log.info(f"fetch asset={ASSET} aoi_hash={_safe_hash(aoi)} range={start_date}..{end_date} "
             f"seed={seed} records={len(df)} (dataframe)")
    if use_cache:
        cache.write_cached(df, ASSET, aoi, start_date, end_date)
    return df
