"""Open-Meteo Satellite Radiation API loader (non-GEE, returns pandas DataFrame).

Endpoint: https://satellite-api.open-meteo.com/v1/archive
Verified on the Open-Meteo docs page: hourly variable ``shortwave_radiation`` (GHI, W/m2,
preceding-hour mean) and the "native" temporal resolution option (10/15/30 min). The exact
query-parameter spellings ``models`` and ``temporal_resolution`` were NOT visible on the
fetched page text and follow the task brief; they are module constants so they can be changed.
Only latitude, longitude and dates are sent.

PAR_proxy = 0.45 * SW_down (PAR is roughly 45% of shortwave; a common approximation).
Data licence: CC-BY 4.0, attribution required (see data_loaders.citations).
"""
from __future__ import annotations

import time as _time
from typing import Callable, Optional

import pandas as pd
import requests

from .citations import attribution_header
from .utils import get_logger

URL = "https://satellite-api.open-meteo.com/v1/archive"
MODELS = "satellite_radiation_seamless"
TEMPORAL_RESOLUTION = "native"
PAR_FRACTION = 0.45
COLUMNS = ["time", "SW_down", "PAR_proxy"]

log = get_logger("data_loaders.openmeteo_radiation")


class OpenMeteoError(RuntimeError):
    pass


def fetch(lat: float, lon: float, start_date: str, end_date: str, *,
          timeout: float = 30.0, retries: int = 3, backoff: float = 1.0,
          session=None, sleep: Callable[[float], None] = _time.sleep) -> pd.DataFrame:
    """Download hourly (or native) shortwave radiation; returns columns time, SW_down, PAR_proxy.

    Retries on network errors, HTTP 429 and 5xx with exponential backoff; other 4xx raise
    OpenMeteoError immediately.
    """
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": start_date, "end_date": end_date,
        "hourly": "shortwave_radiation",
        "models": MODELS, "temporal_resolution": TEMPORAL_RESOLUTION,
    }
    getter = (session or requests).get
    last: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        try:
            resp = getter(URL, params=params, timeout=timeout)
            code = resp.status_code
            if code == 429 or code >= 500:
                raise OpenMeteoError(f"HTTP {code}")
            if code >= 400:
                raise OpenMeteoError(f"HTTP {code}: request rejected")  # not retried below
            payload = resp.json()
            break
        except OpenMeteoError as exc:
            last = exc
            if "rejected" in str(exc):
                raise
        except (requests.RequestException, ValueError) as exc:
            last = exc
        if attempt < retries - 1:
            sleep(backoff * (2 ** attempt))
    else:
        raise OpenMeteoError(f"Open-Meteo request failed after {retries} attempts: {last}")

    hourly = payload.get("hourly") or {}
    times, sw = hourly.get("time"), hourly.get("shortwave_radiation")
    if times is None or sw is None:
        raise OpenMeteoError(f"Unexpected response: {payload.get('reason', 'missing hourly data')}")
    df = pd.DataFrame({"time": pd.to_datetime(times), "SW_down": pd.to_numeric(sw)})
    df["PAR_proxy"] = PAR_FRACTION * df["SW_down"]
    df = df.sort_values("time", kind="mergesort").reset_index(drop=True)[COLUMNS]
    log.info(f"openmeteo radiation lat={lat} lon={lon} range={start_date}..{end_date} records={len(df)}")
    return df


def radiation_sanity_check(df_a: pd.DataFrame, df_b: pd.DataFrame, tol: float = 0.25,
                           col_a: str = "SW_down", col_b: str = "SW_down") -> dict:
    """Compare two radiation series on hourly-mean aligned timestamps.

    Returns n (overlapping hours), mean_abs_diff (W/m2), rel_diff (mean abs diff divided by
    mean of series b) and ok (rel_diff <= tol). With no overlap: ok=None, n=0.
    """
    def _hourly(df, col):
        s = df.set_index(pd.to_datetime(df["time"]))[col].astype(float)
        return s.resample("1h").mean()

    if df_a is None or df_b is None or df_a.empty or df_b.empty:
        return {"n": 0, "mean_abs_diff": None, "rel_diff": None, "ok": None}
    both = pd.concat([_hourly(df_a, col_a), _hourly(df_b, col_b)], axis=1, keys=["a", "b"]).dropna()
    if both.empty:
        return {"n": 0, "mean_abs_diff": None, "rel_diff": None, "ok": None}
    mad = float((both["a"] - both["b"]).abs().mean())
    ref = float(both["b"].mean())
    rel = mad / ref if ref else float("inf")
    return {"n": int(len(both)), "mean_abs_diff": mad, "rel_diff": rel, "ok": bool(rel <= tol)}


def export_csv(df: pd.DataFrame, path: str) -> str:
    """Write CSV with a CC-BY attribution header (comment lines starting with '#')."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(attribution_header(["openmeteo"]))
        df.to_csv(fh, index=False)
    return path
