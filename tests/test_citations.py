import pytest

from data_loaders.citations import attribution_header, get_citations


def test_required_texts():
    out = get_citations(["hls", "sentinel1", "modis_lst", "smap", "viirs_vi", "era5_land", "himawari",
                         "openmeteo"], himawari_product="PAR")
    for needle in ("https://hls.gsfc.nasa.gov/", "Contains modified Copernicus Sentinel data",
                   "NASA LP DAAC at USGS EROS Center", "NASA NSIDC DAAC", "NOAA/CIMSS",
                   "Copernicus Climate Change Service (C3S)", "Open-Meteo.com (CC-BY 4.0)",
                   "Research product of PAR (produced from Himawari-8/9) was supplied by the P-Tree "
                   "System, Japan Aerospace Exploration Agency (JAXA)."):
        assert needle in out


def test_dedup_aliases_and_string_input():
    assert get_citations(["modis_lst", "modis_vi", "MODIS"]).count("LP DAAC") == 1
    assert "Sentinel" in get_citations("sentinel-1")
    assert "[product name]" in get_citations(["himawari"])


def test_unknown_source():
    with pytest.raises(ValueError):
        get_citations(["nope"])


def test_attribution_header():
    h = attribution_header(["sentinel1", "smap"])
    assert all(line.startswith("# ") for line in h.splitlines()) and h.endswith("\n")
