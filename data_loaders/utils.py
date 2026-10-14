"""Shared helpers for satellite data loaders: band schema, harmonisation,
logging and Earth Engine initialisation.

Nothing here imports or modifies existing project modules.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Iterable, Optional

import pandas as pd

# Single source of truth for band names used across all loaders.
BAND_SCHEMA: Dict[str, str] = {
    "EVI": "Enhanced Vegetation Index (unitless)",
    "NDVI": "Normalised Difference Vegetation Index (unitless)",
    "EVI2": "Two-band EVI (unitless, VIIRS)",
    "LST_K": "Land surface temperature (Kelvin)",
    "T_air_C": "Air temperature at 2 m (degrees Celsius)",
    "SM_surface": "Surface soil moisture, 0-5 cm (m3/m3)",
    "SM_rootzone": "Root-zone soil moisture, 0-100 cm (m3/m3)",
    "VV_dB": "Sentinel-1 VV backscatter (dB)",
    "VH_dB": "Sentinel-1 VH backscatter (dB)",
    "ANGLE": "Sentinel-1 incidence angle (degrees)",
    "PAR": "Photosynthetically active radiation (W/m2)",
    "SW_down": "Downwelling shortwave radiation (W/m2)",
    "LST_Night_K": "Night-time land surface temperature (Kelvin)",
    "LSWI": "Land Surface Water Index (NIR-SWIR1)/(NIR+SWIR1) (unitless)",
    "T_air_min_C": "Daily minimum 2 m air temperature (degrees Celsius)",
    "T_air_max_C": "Daily maximum 2 m air temperature (degrees Celsius)",
    "Precip_mm": "Daily total precipitation (mm)",
    "SW_down_J": "Daily accumulated downwelling shortwave radiation (J/m2/day)",
    "VPD_kPa": "Mean hourly vapour pressure deficit from T and dewpoint (kPa)",
    "SM_L1": "ERA5-Land volumetric soil water, layer 1, 0-7 cm (m3/m3)",
    "SM_L2": "ERA5-Land volumetric soil water, layer 2, 7-28 cm (m3/m3)",
    "SM_L3": "ERA5-Land volumetric soil water, layer 3, 28-100 cm (m3/m3)",
    "SM_L4": "ERA5-Land volumetric soil water, layer 4, 100-289 cm (m3/m3)",
}
BAND_NAMES = tuple(BAND_SCHEMA)


def get_logger(name: str = "data_loaders"):
    """Return a loguru logger if installed, else a stdlib logger."""
    try:
        from loguru import logger  # type: ignore

        return logger.bind(component=name)
    except ImportError:
        log = logging.getLogger(name)
        if not log.handlers and not logging.getLogger().handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            log.addHandler(handler)
            log.setLevel(logging.INFO)
        return log


def harmonize_bands(
    df: pd.DataFrame,
    rename: Optional[Dict[str, str]] = None,
    scale: Optional[Dict[str, float]] = None,
    keep: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Rename source columns to schema names, apply scale factors, order columns.

    ``rename`` maps source->schema name; ``scale`` maps schema name->multiplier
    (applied after renaming). Columns not in the schema are kept after the schema
    columns unless ``keep`` is given, in which case only those schema bands remain.
    Raises ValueError if a rename target is not in the schema or if renaming would
    create duplicate columns. Returns a new frame; the input is not modified.
    """
    out = df.copy()
    if rename:
        bad = [v for v in rename.values() if v not in BAND_SCHEMA]
        if bad:
            raise ValueError(f"Not in band schema: {bad}")
        out = out.rename(columns=rename)
        if out.columns.duplicated().any():
            raise ValueError("Renaming produced duplicate columns")
    for band, factor in (scale or {}).items():
        if band not in out.columns:
            raise KeyError(f"Cannot scale missing band {band!r}")
        out[band] = out[band] * factor
    if keep is not None:
        keep = list(keep)
        missing = [b for b in keep if b not in out.columns]
        if missing:
            raise KeyError(f"Missing bands: {missing}")
        return out[keep]
    schema_cols = [c for c in BAND_NAMES if c in out.columns]
    other = [c for c in out.columns if c not in BAND_SCHEMA]
    return out[other + schema_cols] if other else out[schema_cols]


def safe_aoi_hash(aoi) -> str:
    """aoi_hash that never raises (offline ee.Geometry etc.)."""
    from . import cache

    try:
        return cache.aoi_hash(aoi)
    except Exception:
        return "unhashable"


def log_fetch(log, name: str, col, asset: str, aoi, start_date: str, end_date: str,
              seed: int = 0, count: bool = True) -> Optional[int]:
    """Log AOI/date range/asset/record count for a collection; returns the count or None."""
    n: Optional[int] = None
    if count:
        try:
            n = int(col.size().getInfo())
        except Exception as exc:  # offline / not initialised: keep the lazy collection
            log.warning(f"{name} record count unavailable: {exc}")
    log.info(f"fetch asset={asset} aoi_hash={safe_aoi_hash(aoi)} range={start_date}..{end_date} "
             f"seed={seed} records={n}")
    return n


def daily_composites(ee, raw, start_date: str, end_date: str, reduce_fn):
    """One image per UTC day in [start_date, end_date): ``reduce_fn(day_collection)`` -> ee.Image.

    Days with no source images are dropped. system:time_start is the day start and
    system:index is YYYYMMdd, so the result is deterministic and sortable.
    """
    start = ee.Date(start_date)
    n_days = ee.Date(end_date).difference(start, "day").floor()
    days = ee.List.sequence(0, n_days.subtract(1))

    def _day(d):
        t0 = start.advance(d, "day")
        sub = raw.filterDate(t0, t0.advance(1, "day"))
        return (reduce_fn(sub).set("system:time_start", t0.millis())
                .set("system:index", t0.format("YYYYMMdd")).set("n_src", sub.size()))

    col = ee.ImageCollection.fromImages(days.map(_day))
    return col.filter(ee.Filter.gt("n_src", 0)).sort("system:time_start").sort("system:index")


def collection_to_dataframe(load_fn, asset: str, out_bands, aoi, start_date: str, end_date: str,
                            seed: int = 0, scale: int = 1000, use_cache: bool = True,
                            **kwargs) -> pd.DataFrame:
    """Per-image AOI mean of ``out_bands`` as a cached DataFrame (columns: time + bands)."""
    import ee

    from . import cache

    log = get_logger("data_loaders.export")
    if use_cache:
        hit = cache.read_cached(asset, aoi, start_date, end_date)
        if hit is not None:
            log.info(f"cache hit asset={asset} aoi_hash={safe_aoi_hash(aoi)} "
                     f"range={start_date}..{end_date} records={len(hit)}")
            return hit
    col = load_fn(aoi, start_date, end_date, seed=seed, **kwargs)

    def _stat(img):
        vals = img.reduceRegion(ee.Reducer.mean(), aoi, scale, maxPixels=1e9)
        return ee.Feature(None, vals).set("time", img.date().format("YYYY-MM-dd'T'HH:mm:ss"))

    feats = col.map(_stat).getInfo()["features"]
    rows = [{"time": f["properties"].get("time"),
             **{b: f["properties"].get(b) for b in out_bands}} for f in feats]
    df = pd.DataFrame(rows, columns=["time"] + list(out_bands))
    df = df.sort_values("time", kind="mergesort").reset_index(drop=True)
    log.info(f"fetch asset={asset} aoi_hash={safe_aoi_hash(aoi)} range={start_date}..{end_date} "
             f"seed={seed} records={len(df)} (dataframe)")
    if use_cache:
        cache.write_cached(df, asset, aoi, start_date, end_date)
    return df


def init_ee(project: Optional[str] = None) -> None:
    """Initialise Earth Engine.

    Uses a service account when EE_SERVICE_ACCOUNT and EE_PRIVATE_KEY_FILE are set,
    otherwise falls back to existing user credentials. Project comes from the
    argument or EE_PROJECT. Only key *names* are read here; no value is logged.
    """
    import ee  # lazy: earthengine-api is optional

    project = project or os.environ.get("EE_PROJECT")
    account = os.environ.get("EE_SERVICE_ACCOUNT")
    key_file = os.environ.get("EE_PRIVATE_KEY_FILE")
    if account and key_file:
        creds = ee.ServiceAccountCredentials(account, key_file)
        ee.Initialize(creds, project=project)
    else:
        ee.Initialize(project=project)
