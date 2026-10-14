"""Himawari-8/9 PAR / shortwave radiation / AOT from the JAXA P-Tree FTP (non-GEE).

Returns a pandas DataFrame with an AOI-mean time series (10-minute, ~5 km NetCDF).
Columns: ``time`` (UTC) and the product column (PAR -> "PAR", SW -> "SW_down", AOT -> "AOT").

Credentials: ONLY from env JAXA_USER / JAXA_PASS (aliases PTREE_USER / PTREE_PASSWORD);
python-dotenv is used to load .env if installed. Missing credentials -> warning and an
empty DataFrame. P-Tree keeps roughly 30 days of data, so requested windows are clamped.

VERIFICATION STATUS: the JAXA user guide (https://www.eorc.jaxa.jp/ptree/userguide.html) was
fetched and confirms 10-minute / 5 km NetCDF full-disk products, but it does NOT document the
FTP directory layout or file naming. The default directory pattern ('/jma/netcdf/YYYYMM/DD/')
and the filename parser (looks for YYYYMMDD and HHMM tokens, optionally a product token) are
therefore UNVERIFIED and configurable via ``dir_pattern``, ``name_regex`` and ``PRODUCT_TOKENS``.

The AWS noaa-himawari9 fallback was deliberately omitted: the bucket layout could not be
verified from the registry page, and it holds raw L1b radiance (not PAR).
"""
from __future__ import annotations

import ftplib
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from . import cache
from .citations import attribution_header
from .utils import get_logger

HOST = "ftp.ptree.jaxa.jp"
RETENTION_DAYS = 30
DEFAULT_DIR_PATTERN = "/jma/netcdf/{yyyymm}/{dd}/"  # UNVERIFIED, configurable
DEFAULT_NAME_REGEX = r"(?P<date>\d{8})[_-]?(?P<hm>\d{4})"
PRODUCT_COLUMN = {"PAR": "PAR", "SW": "SW_down", "AOT": "AOT"}
PRODUCT_TOKENS: Dict[str, List[str]] = {  # filename substrings identifying a product
    "PAR": ["PAR"], "SW": ["SWR", "SW"], "AOT": ["AOT"],
}
PRODUCT_VARS: Dict[str, List[str]] = {  # candidate NetCDF variable names
    "PAR": ["PAR", "par"], "SW": ["SWR", "swr", "SW", "shortwave_radiation"], "AOT": ["AOT", "aot"],
}

log = get_logger("data_loaders.himawari")


def get_credentials() -> Optional[Tuple[str, str]]:
    try:
        from dotenv import load_dotenv  # optional
        load_dotenv()
    except ImportError:
        pass
    user = os.environ.get("JAXA_USER") or os.environ.get("PTREE_USER")
    pwd = os.environ.get("JAXA_PASS") or os.environ.get("PTREE_PASSWORD")
    return (user, pwd) if user and pwd else None


def _empty(product: str) -> pd.DataFrame:
    return pd.DataFrame(columns=["time", PRODUCT_COLUMN[product]])


def clamp_window(start_date: str, end_date: str, now: Optional[datetime] = None,
                 retention_days: int = RETENTION_DAYS) -> Tuple[str, str]:
    """Clamp [start, end] to the last ``retention_days`` days (warns when clamped)."""
    now = now or datetime.now(timezone.utc)
    earliest = (now - timedelta(days=retention_days)).date()
    s = datetime.strptime(start_date, "%Y-%m-%d").date()
    e = datetime.strptime(end_date, "%Y-%m-%d").date()
    if s < earliest:
        log.warning(f"himawari: start {s} is older than {retention_days}-day retention; using {earliest}")
        s = earliest
    if e < s:
        e = s
    return s.isoformat(), e.isoformat()


def parse_filename(name: str, product: Optional[str] = None,
                   name_regex: str = DEFAULT_NAME_REGEX) -> Optional[datetime]:
    """Return the UTC timestamp encoded in a file name, or None if it does not match.

    If ``product`` is given the name must contain one of that product's tokens
    (case-insensitive) and end in .nc.
    """
    base = name.rsplit("/", 1)[-1]
    if product is not None:
        if not base.lower().endswith(".nc"):
            return None
        if not any(t.lower() in base.lower() for t in PRODUCT_TOKENS.get(product, [product])):
            return None
    m = re.search(name_regex, base)
    if not m:
        return None
    try:
        return datetime.strptime(m.group("date") + m.group("hm"), "%Y%m%d%H%M")
    except ValueError:
        return None


def _days(start: str, end: str):
    d = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    while d <= e:
        yield d
        d += timedelta(days=1)


def list_files(ftp, start: str, end: str, product: str = "PAR",
               dir_pattern: str = DEFAULT_DIR_PATTERN,
               name_regex: str = DEFAULT_NAME_REGEX) -> List[Tuple[datetime, str]]:
    """Sorted (timestamp, remote_path) for matching files in the window."""
    found: List[Tuple[datetime, str]] = []
    for day in _days(start, end):
        d = dir_pattern.format(yyyymm=day.strftime("%Y%m"), dd=day.strftime("%d"),
                               yyyy=day.strftime("%Y"), mm=day.strftime("%m"))
        try:
            names = ftp.nlst(d)
        except ftplib.all_errors as exc:
            log.warning(f"himawari: cannot list {d}: {exc}")
            continue
        for n in names:
            ts = parse_filename(n, product, name_regex)
            if ts is not None and ts.date() == day:
                path = n if "/" in n else d.rstrip("/") + "/" + n
                found.append((ts, path))
    return sorted(found)


def _aoi_mean(path: str, product: str, lat: float, lon: float, half_deg: float) -> Optional[float]:
    import xarray as xr  # lazy
    with xr.open_dataset(path) as ds:
        var = next((v for v in PRODUCT_VARS[product] if v in ds.variables), None)
        if var is None:
            return None
        da = ds[var]
        latn = next((c for c in ("latitude", "lat") if c in da.coords or c in da.dims), None)
        lonn = next((c for c in ("longitude", "lon") if c in da.coords or c in da.dims), None)
        if latn is None or lonn is None:
            return None
        lats, lons = ds[latn], ds[lonn]
        sub = da.where((abs(lats - lat) <= half_deg) & (abs(lons - lon) <= half_deg))
        val = float(sub.mean(skipna=True))
        return None if val != val else val


def fetch(lat: float, lon: float, start_date: str, end_date: str, product: str = "PAR", *,
          host: str = HOST, dir_pattern: str = DEFAULT_DIR_PATTERN,
          name_regex: str = DEFAULT_NAME_REGEX, half_deg: float = 0.05,
          timeout: float = 30.0, use_cache: bool = True,
          ftp_factory: Callable[..., "ftplib.FTP"] = ftplib.FTP,
          reader: Optional[Callable[[str, str, float, float, float], Optional[float]]] = None,
          now: Optional[datetime] = None) -> pd.DataFrame:
    """AOI-mean time series for ``product`` (PAR, SW or AOT); empty frame if no credentials."""
    if product not in PRODUCT_COLUMN:
        raise ValueError(f"product must be one of {sorted(PRODUCT_COLUMN)}")
    creds = get_credentials()
    if creds is None:
        log.warning("himawari: JAXA_USER/JAXA_PASS not set; returning empty DataFrame")
        return _empty(product)

    start, end = clamp_window(start_date, end_date, now=now)
    aoi = {"lat": round(lat, 6), "lon": round(lon, 6), "half_deg": half_deg}
    asset = f"himawari_{product}"
    if use_cache:
        hit = cache.read_cached(asset, aoi, start, end)
        if hit is not None:
            log.info(f"himawari cache hit aoi={aoi} range={start}..{end} product={product} records={len(hit)}")
            return hit

    reader = reader or _aoi_mean
    col = PRODUCT_COLUMN[product]
    rows = []
    ftp = ftp_factory(host, timeout=timeout)
    try:
        ftp.login(*creds)
        files = list_files(ftp, start, end, product, dir_pattern, name_regex)
        with tempfile.TemporaryDirectory(prefix="himawari_") as tmp:
            for ts, remote in files:
                local = os.path.join(tmp, os.path.basename(remote))
                try:
                    with open(local, "wb") as fh:
                        ftp.retrbinary(f"RETR {remote}", fh.write)
                    val = reader(local, product, lat, lon, half_deg)
                except Exception as exc:  # one bad file must not abort the series
                    log.warning(f"himawari: skipped {os.path.basename(remote)}: {exc}")
                    continue
                if val is not None:
                    rows.append({"time": pd.Timestamp(ts), col: val})
                try:
                    os.remove(local)
                except OSError:
                    pass
    except ftplib.all_errors as exc:
        log.warning(f"himawari: FTP error: {exc}")
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass

    df = pd.DataFrame(rows, columns=["time", col])
    df = df.sort_values("time", kind="mergesort").reset_index(drop=True)
    log.info(f"himawari fetch aoi={aoi} range={start}..{end} product={product} records={len(df)}")
    if use_cache and len(df):
        cache.write_cached(df, asset, aoi, start, end)
    return df


def export_csv(df: pd.DataFrame, path: str, product: str = "PAR") -> str:
    """Write CSV with the required JAXA P-Tree attribution as a comment header."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(attribution_header(["himawari"], himawari_product=product))
        df.to_csv(fh, index=False)
    return path
