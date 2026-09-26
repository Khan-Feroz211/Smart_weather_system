"""
openmeteo_integration.py
=========================
Free, open-source weather satellite, flood, climate, and seasonal forecast
data from Open-Meteo (https://open-meteo.com/) — no API key required.

Attribution: Data © Open-Meteo, licensed under CC BY 4.0.

Four sub-services are integrated:

1. **Satellite API** (``satellite-api.open-meteo.com``)
   - Archive shortwave radiation and clear-sky shortwave radiation.
   - Used to enhance the GeoVis satellite analyser with real radiation data.

2. **Flood API** (``flood-api.open-meteo.com``)
   - River discharge, mean/median/max discharge, ensemble forecasts.
   - Used by the MultiHazardClassifier for flood risk assessment.

3. **Climate API** (``climate-api.open-meteo.com``)
   - Multi-model climate projections (1950–2050) for temperature, humidity,
     precipitation, soil moisture, wind, and pressure.

4. **Seasonal API** (``seasonal-api.open-meteo.com``)
   - Seasonal outlooks for temperature, precipitation, soil, wave, wind, etc.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API endpoints — all hosted under the open-meteo.com domain, each is a
# separate sub-domain for a different data service.
# ---------------------------------------------------------------------------
SATELLITE_API_URL = "https://satellite-api.open-meteo.com/v1/archive"
FLOOD_API_URL = "https://flood-api.open-meteo.com/v1/flood"
CLIMATE_API_URL = "https://climate-api.open-meteo.com/v1/climate"
SEASONAL_API_URL = "https://seasonal-api.open-meteo.com/v1/seasonal"

# Default coordinates for common agricultural regions (lat, lon)
DEFAULT_LOCATIONS: Dict[str, Tuple[float, float]] = {
    "Lahore": (31.5497, 74.3437),
    "London": (51.5074, -0.1278),
    "New York": (40.7128, -74.0060),
    "Tokyo": (35.6762, 139.6503),
    "Paris": (48.8566, 2.3522),
    "Berlin": (52.5200, 13.4050),
    "Mumbai": (19.0760, 72.8777),
    "Sydney": (-33.8688, 151.2093),
    "Cairo": (30.0444, 31.2357),
    "Delhi": (28.6139, 77.1031),
}

REQUEST_TIMEOUT = 10  # seconds


def geocode_location(location: str) -> Optional[Dict[str, Any]]:
    """Geocode a city name via the Open-Meteo Geocoding API (no API key needed).

    Falls back to the built-in ``DEFAULT_LOCATIONS`` dict for known cities.
    """
    # Fast path: known city
    if location in DEFAULT_LOCATIONS:
        lat, lon = DEFAULT_LOCATIONS[location]
        return {
            "name": location,
            "latitude": lat,
            "longitude": lon,
            "country": "",
            "source": "openmeteo_default_locations",
        }

    # Live geocoding
    try:
        resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1, "format": "json", "language": "en"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                r = results[0]
                return {
                    "name": r.get("name", location),
                    "latitude": r["latitude"],
                    "longitude": r["longitude"],
                    "country": r.get("country", ""),
                    "source": "openmeteo_geocoding_api",
                }
    except Exception as exc:
        logger.warning("Open-Meteo geocoding failed for '%s': %s", location, exc)

    return None


def _coords(location: str) -> Tuple[float, float]:
    """Resolve a location name to (latitude, longitude)."""
    info = geocode_location(location)
    if info:
        return info["latitude"], info["longitude"]
    # Ultimate fallback: Lahore coordinates
    return DEFAULT_LOCATIONS["Lahore"]


# ===========================================================================
# 1. Satellite API — archive radiation data
# ===========================================================================
def fetch_satellite_data(
    location: str = "Lahore",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: int = 7,
) -> Dict[str, Any]:
    """Fetch archive satellite radiation data from Open-Meteo Satellite API.

    Returns shortwave radiation and clear-sky shortwave radiation, which
    are useful for solar / evapotranspiration modelling.
    """
    lat, lon = _coords(location)

    if start_date is None:
        start_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    if end_date is None:
        end_date = (datetime.utcnow() - timedelta(days=days - 1)).strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            SATELLITE_API_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start_date,
                "end_date": end_date,
                "hourly": "shortwave_radiation,shortwave_radiation_clear_sky",
                "models": "satellite_radiation_seamless",
                "temporal_resolution": "native",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            d = resp.json()
            hourly = d.get("hourly", {})
            swr = hourly.get("shortwave_radiation", [])
            swr_cs = hourly.get("shortwave_radiation_clear_sky", [])
            times = hourly.get("time", [])
            return {
                "available": True,
                "source": "openmeteo_satellite_api",
                "location": location,
                "latitude": lat,
                "longitude": lon,
                "start_date": start_date,
                "end_date": end_date,
                "model": d.get("model", "satellite_radiation_seamless"),
                "parameters": {
                    "times": times,
                    "shortwave_radiation": swr,
                    "shortwave_radiation_clear_sky": swr_cs,
                },
                "summary": {
                    "mean_shortwave_radiation": round(sum(swr) / len(swr), 2) if swr else 0.0,
                    "mean_clear_sky_radiation": round(sum(swr_cs) / len(swr_cs), 2) if swr_cs else 0.0,
                    "data_points": len(times),
                },
                "attribution": "Data © Open-Meteo (CC BY 4.0)",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
    except Exception as exc:
        logger.warning("Open-Meteo Satellite API failed: %s", exc)

    return {
        "available": False,
        "source": "unavailable",
        "error": "Failed to fetch satellite data from Open-Meteo.",
    }


# ===========================================================================
# 2. Flood API — river discharge and flood forecast
# ===========================================================================
def fetch_flood_data(
    location: str = "Lahore",
    days: int = 7,
) -> Dict[str, Any]:
    """Fetch river discharge and flood forecast from Open-Meteo Flood API.

    Returns current / forecast river discharge including ensemble statistics
    (mean, median, max, 25th percentile) which are used to assess flood risk.
    """
    lat, lon = _coords(location)
    end_date = datetime.utcnow().strftime("%Y-%m-%d")
    start_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            FLOOD_API_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "river_discharge,river_discharge_mean,river_discharge_median,"
                         "river_discharge_max,river_discharge_p25",
                "models": "consolidated_v4,seamless_v4",
                "ensemble": "true",
                "start_date": start_date,
                "end_date": end_date,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            d = resp.json()
            daily = d.get("daily", {})
            discharge = daily.get("river_discharge", [])
            discharge_mean = daily.get("river_discharge_mean", [])
            discharge_max = daily.get("river_discharge_max", [])
            times = daily.get("time", [])

            max_discharge = max(discharge_max) if discharge_max else 0.0
            return {
                "available": True,
                "source": "openmeteo_flood_api",
                "location": location,
                "latitude": lat,
                "longitude": lon,
                "model": d.get("model", "consolidated_v4,seamless_v4"),
                "ensemble": True,
                "parameters": {
                    "times": times,
                    "river_discharge": discharge,
                    "river_discharge_mean": discharge_mean,
                    "river_discharge_median": daily.get("river_discharge_median", []),
                    "river_discharge_max": discharge_max,
                    "river_discharge_p25": daily.get("river_discharge_p25", []),
                },
                "summary": {
                    "max_discharge_m3s": round(max_discharge, 2),
                    "mean_discharge_m3s": round(sum(discharge_mean) / len(discharge_mean), 2) if discharge_mean else 0.0,
                    "data_points": len(times),
                },
                "flood_risk": "high" if max_discharge > 150 else "moderate" if max_discharge > 50 else "low",
                "attribution": "Data © Open-Meteo (CC BY 4.0)",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
    except Exception as exc:
        logger.warning("Open-Meteo Flood API failed: %s", exc)

    return {
        "available": False,
        "source": "unavailable",
        "error": "Failed to fetch flood data from Open-Meteo.",
    }


# ===========================================================================
# 3. Climate API — multi-model climate projections
# ===========================================================================
def fetch_climate_data(
    location: str = "Lahore",
    start_date: str = "1950-01-01",
    end_date: str = "2050-12-31",
) -> Dict[str, Any]:
    """Fetch multi-model climate projections from Open-Meteo Climate API.

    Returns daily projections for temperature, humidity, precipitation, soil
    moisture, wind, and pressure from multiple global climate models.
    """
    lat, lon = _coords(location)

    # Ensure end_date doesn't exceed 2050-12-31 as per API limits
    end_date = min(end_date, "2050-12-31")

    try:
        resp = requests.get(
            CLIMATE_API_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start_date,
                "end_date": end_date,
                "models": "CMCC_CM2_VHR4,FGOALS_f3_H,HiRAM_SIT_HR,MRI_AGCM3_2_S,EC_Earth3P_HR,MPI_ESM1_2_XR,NICAM16_8S",
                "daily": "temperature_2m_max,cloud_cover_mean,wind_speed_10m_max,wind_speed_10m_mean,"
                         "relative_humidity_2m_mean,dew_point_2m_max,precipitation_sum,"
                         "soil_moisture_0_to_10cm_mean,pressure_msl_mean",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            d = resp.json()
            daily = d.get("daily", {})
            temp_max = daily.get("temperature_2m_max", [])
            precip = daily.get("precipitation_sum", [])
            return {
                "available": True,
                "source": "openmeteo_climate_api",
                "location": location,
                "latitude": lat,
                "longitude": lon,
                "start_date": start_date,
                "end_date": end_date,
                "models": "CMCC_CM2_VHR4, FGOALS_f3_H, HiRAM_SIT_HR, MRI_AGCM3_2_S, "
                          "EC_Earth3P_HR, MPI_ESM1_2_XR, NICAM16_8S",
                "parameters": {
                    "temperature_2m_max": temp_max,
                    "cloud_cover_mean": daily.get("cloud_cover_mean", []),
                    "wind_speed_10m_max": daily.get("wind_speed_10m_max", []),
                    "wind_speed_10m_mean": daily.get("wind_speed_10m_mean", []),
                    "relative_humidity_2m_mean": daily.get("relative_humidity_2m_mean", []),
                    "dew_point_2m_max": daily.get("dew_point_2m_max", []),
                    "precipitation_sum": precip,
                    "soil_moisture_0_to_10cm_mean": daily.get("soil_moisture_0_to_10cm_mean", []),
                    "pressure_msl_mean": daily.get("pressure_msl_mean", []),
                    "times": daily.get("time", []),
                },
                "summary": {
                    "mean_temp_max": round(sum(temp_max) / len(temp_max), 2) if temp_max else 0.0,
                    "total_precipitation": round(sum(precip), 2) if precip else 0.0,
                    "data_points": len(temp_max),
                },
                "attribution": "Data © Open-Meteo (CC BY 4.0)",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
    except Exception as exc:
        logger.warning("Open-Meteo Climate API failed: %s", exc)

    return {
        "available": False,
        "source": "unavailable",
        "error": "Failed to fetch climate data from Open-Meteo.",
    }


# ===========================================================================
# 4. Seasonal API — seasonal weather outlooks
# ===========================================================================
def fetch_seasonal_data(
    location: str = "Lahore",
) -> Dict[str, Any]:
    """Fetch seasonal forecast from Open-Meteo Seasonal API.

    Returns seasonal outlooks for temperature, precipitation, soil, wave, wind,
    cloud cover, sunshine duration, and more.
    """
    lat, lon = _coords(location)

    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
    }
    # Hourly parameters that provide detailed seasonal outlook
    hourly_params = [
        "temperature_2m", "temperature_2m_max", "relative_humidity_2m",
        "soil_temperature_0_to_7cm", "soil_temperature_7_to_28cm",
        "soil_temperature_28_to_100cm", "soil_moisture_0_to_7cm",
        "soil_moisture_7_to_28cm", "weather_code", "precipitation",
        "showers", "rain", "snowfall", "wave_height", "wave_direction",
        "wave_period", "cloud_cover", "sunshine_duration",
        "wind_speed_10m", "wind_direction_10m", "wind_speed_200m",
        "wind_direction_200m", "wind_gusts_10m",
    ]
    params["hourly"] = ",".join(hourly_params)

    try:
        resp = requests.get(
            SEASONAL_API_URL,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            d = resp.json()
            daily = d.get("daily", {})
            hourly = d.get("hourly", {})
            temp_max = daily.get("temperature_2m_max", [])
            return {
                "available": True,
                "source": "openmeteo_seasonal_api",
                "location": location,
                "latitude": lat,
                "longitude": lon,
                "model": d.get("model", "seasonal_ensemble"),
                "parameters": {
                    "daily": {
                        "temperature_2m_max": temp_max,
                        "times": daily.get("time", []),
                    },
                    "hourly": {
                        "temperature_2m": hourly.get("temperature_2m", []),
                        "relative_humidity_2m": hourly.get("relative_humidity_2m", []),
                        "soil_temperature_0_to_7cm": hourly.get("soil_temperature_0_to_7cm", []),
                        "soil_temperature_7_to_28cm": hourly.get("soil_temperature_7_to_28cm", []),
                        "soil_moisture_0_to_7cm": hourly.get("soil_moisture_0_to_7cm", []),
                        "precipitation": hourly.get("precipitation", []),
                        "rain": hourly.get("rain", []),
                        "wind_speed_10m": hourly.get("wind_speed_10m", []),
                        "wind_direction_10m": hourly.get("wind_direction_10m", []),
                        "wave_height": hourly.get("wave_height", []),
                        "cloud_cover": hourly.get("cloud_cover", []),
                        "sunshine_duration": hourly.get("sunshine_duration", []),
                        "times": hourly.get("time", []),
                    },
                },
                "summary": {
                    "mean_temp_max": round(sum(temp_max) / len(temp_max), 2) if temp_max else 0.0,
                    "total_precipitation": round(sum(hourly.get("precipitation", [])), 2) if hourly.get("precipitation") else 0.0,
                    "data_points_daily": len(temp_max),
                    "data_points_hourly": len(hourly.get("time", [])),
                },
                "attribution": "Data © Open-Meteo (CC BY 4.0)",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
    except Exception as exc:
        logger.warning("Open-Meteo Seasonal API failed: %s", exc)

    return {
        "available": False,
        "source": "unavailable",
        "error": "Failed to fetch seasonal data from Open-Meteo.",
    }


# ===========================================================================
# Aggregated convenience function
# ===========================================================================
def fetch_all_openmeteo_data(location: str = "Lahore") -> Dict[str, Any]:
    """Fetch all four Open-Meteo datasets for a given location.

    Returns a dict with keys: ``satellite``, ``flood``, ``climate``, ``seasonal``.
    Each sub-result has an ``available`` flag so callers can degrade gracefully.
    """
    loc = geocode_location(location)
    lat, lon = _coords(location)

    return {
        "location": location,
        "latitude": lat,
        "longitude": lon,
        "geocoding_source": loc.get("source", "fallback_default") if loc else "fallback_default",
        "satellite": fetch_satellite_data(location, days=7),
        "flood": fetch_flood_data(location, days=7),
        "climate": fetch_climate_data(location),
        "seasonal": fetch_seasonal_data(location),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


if __name__ == "__main__":
    import json as _json
    result = fetch_all_openmeteo_data("Lahore")
    print(_json.dumps(result, indent=2, default=str))
