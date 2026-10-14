"""ERA5-Land loader (ECMWF/ERA5_LAND/HOURLY, ~11 km). NEW code.

Hourly -> daily (UTC days). Accumulated fluxes: the raw ``total_precipitation`` and
``surface_solar_radiation_downwards`` bands are cumulative within the forecast window,
so summing them would over-count. The GEE catalog provides de-accumulated hourly
bands (``total_precipitation_hourly``, ``surface_solar_radiation_downwards_hourly``);
the daily total is the plain SUM of those 24 hourly values. ECMWF/ERA5_LAND/DAILY_AGGR
was not used because it lacks the hourly dewpoint/T pairing needed for the mean hourly
VPD, and the hourly route keeps all variables on one consistent day definition.

Daily outputs (schema names):
  T_air_C / T_air_min_C / T_air_max_C : temperature_2m mean/min/max, K -> degC
  Precip_mm   : sum(total_precipitation_hourly) * 1000   (m -> mm)
  SW_down_J   : sum(surface_solar_radiation_downwards_hourly)  (J/m2/day)
  SW_down     : SW_down_J / 86400                             (W/m2, daily mean)
  VPD_kPa     : mean of hourly es(T) - es(Td) (Tetens, es=0.6108*exp(17.27T/(T+237.3)))
  SM_L1..SM_L4: volumetric_soil_water_layer_1..4 daily mean (m3/m3)
"""
from __future__ import annotations

import pandas as pd

from .utils import collection_to_dataframe, daily_composites, get_logger, log_fetch

ASSET = "ECMWF/ERA5_LAND/HOURLY"
SRC_BANDS = [
    "temperature_2m", "dewpoint_temperature_2m",
    "total_precipitation_hourly", "surface_solar_radiation_downwards_hourly",
    "volumetric_soil_water_layer_1", "volumetric_soil_water_layer_2",
    "volumetric_soil_water_layer_3", "volumetric_soil_water_layer_4",
]
SOIL_BANDS = ["volumetric_soil_water_layer_%d" % i for i in range(1, 5)]
SOIL_OUT = ["SM_L1", "SM_L2", "SM_L3", "SM_L4"]
OUT_BANDS = ["T_air_C", "T_air_min_C", "T_air_max_C", "Precip_mm", "SW_down_J", "SW_down",
             "VPD_kPa"] + SOIL_OUT
KELVIN = 273.15
SECONDS_PER_DAY = 86400.0

log = get_logger("data_loaders.era5_land")


def _es(temp_c):
    """Saturation vapour pressure (kPa), Tetens."""
    return temp_c.multiply(17.27).divide(temp_c.add(237.3)).exp().multiply(0.6108)


def _hourly_vpd(img):
    t = img.select("temperature_2m").subtract(KELVIN)
    td = img.select("dewpoint_temperature_2m").subtract(KELVIN)
    return _es(t).subtract(_es(td)).max(0).rename("VPD_kPa")


def _reduce_day(sub):
    t = sub.select("temperature_2m")
    t_mean = t.mean().subtract(KELVIN).rename("T_air_C")
    t_min = t.min().subtract(KELVIN).rename("T_air_min_C")
    t_max = t.max().subtract(KELVIN).rename("T_air_max_C")
    precip = sub.select("total_precipitation_hourly").sum().multiply(1000).rename("Precip_mm")
    sw = sub.select("surface_solar_radiation_downwards_hourly").sum().rename("SW_down_J")
    sw_w = sw.divide(SECONDS_PER_DAY).rename("SW_down")
    vpd = sub.map(_hourly_vpd).mean().rename("VPD_kPa")
    soil = sub.select(SOIL_BANDS).mean().select(SOIL_BANDS, SOIL_OUT)
    return (t_mean.addBands(t_min).addBands(t_max).addBands(precip).addBands(sw)
            .addBands(sw_w).addBands(vpd).addBands(soil))


def load(aoi, start_date: str, end_date: str, seed: int = 0, **kwargs):
    """Return a daily ee.ImageCollection with the bands in OUT_BANDS."""
    import ee  # lazy import

    raw = (ee.ImageCollection(ASSET).filterBounds(aoi).filterDate(start_date, end_date)
           .select(SRC_BANDS))
    col = daily_composites(ee, raw, start_date, end_date, _reduce_day)
    log_fetch(log, "era5_land", col, ASSET, aoi, start_date, end_date, seed,
              kwargs.get("log_count", True))
    return col


def to_dataframe(aoi, start_date: str, end_date: str, seed: int = 0,
                 scale: int = 11132, use_cache: bool = True, **kwargs) -> pd.DataFrame:
    """Per-day AOI mean of the daily ERA5-Land variables as a cached DataFrame."""
    return collection_to_dataframe(load, ASSET, OUT_BANDS, aoi, start_date, end_date,
                                   seed, scale, use_cache, **kwargs)


load_era5_land = load
