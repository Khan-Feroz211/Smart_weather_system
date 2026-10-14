"""Data-source citations and attribution headers for exports."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

_HIMAWARI = ("Research product of {product} (produced from Himawari-8/9) was supplied by the "
             "P-Tree System, Japan Aerospace Exploration Agency (JAXA).")

CITATIONS: Dict[str, str] = {
    "hls": "Harmonized Landsat Sentinel-2 (HLS): NASA HLS project, https://hls.gsfc.nasa.gov/",
    "sentinel1": "Contains modified Copernicus Sentinel data",
    "modis": "MODIS data courtesy of NASA LP DAAC at USGS EROS Center",
    "smap": "SMAP data courtesy of NASA NSIDC DAAC",
    "viirs": "VIIRS data courtesy of NOAA/CIMSS",
    "era5_land": "ERA5-Land: Copernicus Climate Change Service (C3S)",
    "himawari": _HIMAWARI,
    "openmeteo": "Weather data by Open-Meteo.com (CC-BY 4.0), https://open-meteo.com/",
}

_ALIASES = {
    "sentinel-1": "sentinel1", "s1": "sentinel1",
    "modis_lst": "modis", "modis_vi": "modis",
    "viirs_vi": "viirs",
    "era5": "era5_land", "era5-land": "era5_land",
    "himawari_ptree": "himawari", "ptree": "himawari", "p-tree": "himawari",
    "openmeteo_radiation": "openmeteo", "open-meteo": "openmeteo", "open_meteo": "openmeteo",
}


def _canon(name: str) -> str:
    key = str(name).strip().lower()
    key = _ALIASES.get(key, key)
    if key not in CITATIONS:
        raise ValueError(f"Unknown citation source {name!r}; known: {sorted(CITATIONS)}")
    return key


def get_citations(sources: Iterable[str], himawari_product: str = "[product name]") -> str:
    """Return one citation line per distinct source (input order, de-duplicated)."""
    if isinstance(sources, str):
        sources = [sources]
    seen: List[str] = []
    for s in sources:
        k = _canon(s)
        if k not in seen:
            seen.append(k)
    lines = []
    for k in seen:
        text = CITATIONS[k]
        if k == "himawari":
            text = _HIMAWARI.format(product=himawari_product)
        lines.append(text)
    return "\n".join(lines)


def attribution_header(sources: Iterable[str], himawari_product: Optional[str] = None) -> str:
    """Comment-prefixed ('# ') citation block for CSV/text exports."""
    text = get_citations(sources, himawari_product=himawari_product or "[product name]")
    return "\n".join(f"# {line}" for line in text.splitlines()) + "\n"
