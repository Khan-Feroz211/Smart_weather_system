"""Multi-source feature stack: joins enabled GEE loaders onto one reference timeline.

Config (config/datasets.yaml via data_loaders.config, or a dict/path):
  datasets.<name>.enabled   - disabled sources are skipped
  datasets.<name>.priority  - optional int, LOWER = higher priority (explicit, ranks first)
  priority: [names...]      - optional top-level list, earlier = higher priority
  live.window_days          - optional, default 30
Order of precedence: explicit per-dataset priority, top-level list, DEFAULT_PRIORITY,
then the rest. Ties are broken by source name.

Reference timeline: the highest-priority enabled source that is optical/daily
(everything in SOURCES except ``sentinel1``, which is SAR). All other enabled sources
are joined to it by ``system:time_start`` with ee.Join.saveBest and a maxDifference
filter of +/- ``tolerance_days``. This is an INNER join: reference images with no
match for some source within tolerance are dropped. Composite products (MODIS/VIIRS
16-day) carry the composite start date, so a daily reference needs a larger
``tolerance_days`` to match them.

Band names are kept as-is; only when a joined band collides with an existing one is it
prefixed ``<source>_<band>`` (e.g. ``modis_vi_EVI`` when hls already provides EVI).

mode='live' ignores start/end and uses the last ``window_days`` (default 30) UTC days
ending today (inclusive). Non-GEE sources (himawari, openmeteo) are plugged in by
``register_extra_source(name, fn)``; ``build_feature_frame`` merges them by date.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from . import era5_land, hls, modis_lst, modis_vi, sentinel1, smap, viirs_vi
from .config import load_config
from .utils import get_logger

log = get_logger("data_loaders.feature_stack")

MODES = ("historical", "live")
DEFAULT_LIVE_WINDOW_DAYS = 30
DEFAULT_PRIORITY = ["hls", "modis_vi", "viirs_vi", "era5_land", "modis_lst", "smap", "sentinel1"]
SAR_SOURCES = {"sentinel1"}  # not eligible as the reference timeline
SOURCES = {
    "hls": hls, "modis_vi": modis_vi, "viirs_vi": viirs_vi, "era5_land": era5_land,
    "modis_lst": modis_lst, "smap": smap, "sentinel1": sentinel1,
}
_DAY_MS = 86400 * 1000

_EXTRA_SOURCES: Dict[str, Callable[..., pd.DataFrame]] = {}


def register_extra_source(name: str, fn: Callable[..., pd.DataFrame]) -> None:
    """Register a non-GEE source for live mode.

    ``fn(aoi, start_date, end_date, **kwargs)`` must return a DataFrame with a ``date``
    (or ``time``) column and feature columns. Used by ``build_feature_frame``.
    """
    if not name or not callable(fn):
        raise ValueError("register_extra_source needs a name and a callable")
    _EXTRA_SOURCES[name] = fn


def unregister_extra_source(name: str) -> None:
    _EXTRA_SOURCES.pop(name, None)


def extra_sources() -> List[str]:
    return sorted(_EXTRA_SOURCES)


# ----------------------------------------------------------------------------- config
def _resolve_config(config) -> Dict[str, Any]:
    if config is None or isinstance(config, (str, Path)):
        cfg = load_config(Path(config) if config else None)
    elif isinstance(config, dict):
        cfg = config
    else:
        raise TypeError("config must be a dict, a path, or None")
    return cfg


def _datasets(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg["datasets"] if isinstance(cfg.get("datasets"), dict) else cfg


def _rank(name: str, ds: Dict[str, Any], top: List[str]) -> Tuple[int, int, str]:
    """Sort key (tier, rank, name). Tiers: explicit per-dataset priority, then the
    top-level list, then DEFAULT_PRIORITY, then everything else by name."""
    entry = ds.get(name) or {}
    if isinstance(entry, dict) and entry.get("priority") is not None:
        return (0, int(entry["priority"]), name)
    if name in top:
        return (1, top.index(name), name)
    if name in DEFAULT_PRIORITY:
        return (2, DEFAULT_PRIORITY.index(name), name)
    return (3, 0, name)


def enabled_sources(config=None) -> List[str]:
    """Enabled GEE sources (known loaders only), highest priority first."""
    cfg = _resolve_config(config)
    ds = _datasets(cfg)
    top = list(cfg.get("priority") or []) if isinstance(cfg.get("priority"), list) else []
    names = []
    for name, entry in ds.items():
        if not isinstance(entry, dict):
            continue
        if not entry.get("enabled", False):
            log.info(f"feature_stack: source {name} disabled, skipped")
            continue
        if name not in SOURCES:
            log.info(f"feature_stack: source {name} has no GEE loader (extra-source registry), skipped")
            continue
        names.append(name)
    return sorted(names, key=lambda n: _rank(n, ds, top))


def _plan(config=None) -> List[Tuple[str, List[str], List[str]]]:
    """[(source, source_bands, output_bands)] with the reference first."""
    names = enabled_sources(config)
    ref_candidates = [n for n in names if n not in SAR_SOURCES]
    if not ref_candidates:
        raise ValueError("No enabled optical/daily source to use as the reference timeline")
    ordered = [ref_candidates[0]] + [n for n in names if n != ref_candidates[0]]
    plan, seen = [], set()
    for i, name in enumerate(ordered):
        bands = list(SOURCES[name].OUT_BANDS)
        out = [b if (i == 0 or b not in seen) else f"{name}_{b}" for b in bands]
        seen.update(out)
        plan.append((name, bands, out))
    return plan


def stack_band_names(config=None) -> List[str]:
    """Output band names of build_feature_stack for this config (no ee needed)."""
    return [b for _, _, out in _plan(config) for b in out]


# ----------------------------------------------------------------------------- inputs
def _parse_date(value: str, label: str) -> _dt.date:
    try:
        return _dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be an ISO date (YYYY-MM-DD), got {value!r}") from None


def live_window(config=None, now: Optional[_dt.datetime] = None) -> Tuple[str, str]:
    """(start, end_exclusive) covering the last window_days UTC days including today."""
    cfg = _resolve_config(config) if config is not None else {}
    days = int((cfg.get("live") or {}).get("window_days", DEFAULT_LIVE_WINDOW_DAYS))
    if days < 1:
        raise ValueError("live.window_days must be >= 1")
    today = (now or _dt.datetime.now(_dt.timezone.utc)).date()
    end = today + _dt.timedelta(days=1)
    return (end - _dt.timedelta(days=days)).isoformat(), end.isoformat()


def _validate(mode, start_date, end_date, tolerance_days, config):
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if not isinstance(tolerance_days, (int, float)) or tolerance_days < 0:
        raise ValueError("tolerance_days must be a non-negative number")
    if mode == "live":
        start_date, end_date = live_window(config)
    s, e = _parse_date(start_date, "start_date"), _parse_date(end_date, "end_date")
    if s >= e:
        raise ValueError(f"start_date {start_date} must be before end_date {end_date}")
    return start_date, end_date


# ------------------------------------------------------------------------------ stack
def build_feature_stack(aoi, start_date: str, end_date: str, mode: str = "historical",
                        config=None, tolerance_days: int = 1, seed: int = 0):
    """Return one multi-band ee.ImageCollection on the reference source's timeline."""
    cfg = _resolve_config(config)
    start_date, end_date = _validate(mode, start_date, end_date, tolerance_days, cfg)
    plan = _plan(cfg)
    import ee  # lazy import

    ref_name = plan[0][0]
    cols = {name: SOURCES[name].load(aoi, start_date, end_date, seed=seed, log_count=False)
            for name, _, _ in plan}
    stack = cols[ref_name]
    tol_ms = int(tolerance_days * _DAY_MS)
    time_filter = ee.Filter.maxDifference(difference=tol_ms, leftField="system:time_start",
                                          rightField="system:time_start")
    join = ee.Join.saveBest("match", "time_diff")

    for name, src_bands, out_bands in plan[1:]:
        joined = join.apply(stack, cols[name], time_filter)

        def _merge(img, _src=src_bands, _out=out_bands):
            match = ee.Image(img.get("match")).select(_src, _out)
            return ee.Image(img).addBands(match)

        stack = joined.map(_merge)

    stack = stack.sort("system:time_start").sort("system:index")
    log.info(f"feature_stack mode={mode} range={start_date}..{end_date} reference={ref_name} "
             f"used={[p[0] for p in plan]} tolerance_days={tolerance_days} seed={seed} "
             f"bands={[b for p in plan for b in p[2]]}")
    return stack


def stack_to_dataframe(stack, aoi, scale: int = 1000, bands: Optional[List[str]] = None) -> pd.DataFrame:
    """Per-image AOI mean of every band: columns ``date`` (UTC YYYY-MM-DD) + bands."""
    import ee

    def _stat(img):
        vals = img.reduceRegion(ee.Reducer.mean(), aoi, scale, maxPixels=1e9)
        return ee.Feature(None, vals).set("date", img.date().format("YYYY-MM-dd"))

    feats = stack.map(_stat).getInfo()["features"]
    rows = [{"date": f["properties"].get("date"),
             **{k: v for k, v in f["properties"].items() if k != "date"}} for f in feats]
    df = pd.DataFrame(rows)
    if bands is not None:
        df = df.reindex(columns=["date"] + list(bands))
    if "date" not in df.columns:
        df["date"] = pd.Series(dtype=str)
    return df.sort_values("date", kind="mergesort").reset_index(drop=True)


def _date_key(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    col = "date" if "date" in out.columns else "time"
    out["date"] = pd.to_datetime(out[col], utc=True).dt.strftime("%Y-%m-%d")
    if col != "date":
        out = out.drop(columns=[col])
    return out


def build_feature_frame(aoi, start_date: str, end_date: str, mode: str = "historical",
                        config=None, tolerance_days: int = 1, seed: int = 0,
                        scale: int = 1000, **extra_kwargs) -> pd.DataFrame:
    """DataFrame of the stack's AOI means plus (live mode) registered extra sources.

    Extra-source columns that collide with existing ones are prefixed ``<name>_``.
    """
    cfg = _resolve_config(config)
    stack = build_feature_stack(aoi, start_date, end_date, mode, cfg, tolerance_days, seed)
    df = stack_to_dataframe(stack, aoi, scale=scale, bands=stack_band_names(cfg))
    if mode == "live":
        s, e = live_window(cfg)
        for name in extra_sources():
            extra = _date_key(_EXTRA_SOURCES[name](aoi, s, e, **extra_kwargs))
            extra = extra.rename(columns={c: f"{name}_{c}" for c in extra.columns
                                          if c != "date" and c in df.columns})
            df = df.merge(extra, on="date", how="outer")
            log.info(f"feature_frame merged extra source {name} ({len(extra)} rows)")
        df = df.sort_values("date", kind="mergesort").reset_index(drop=True)
    return df
