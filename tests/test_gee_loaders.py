"""Mocked-ee tests for the GEE loaders (MODIS LST/VI, SMAP, VIIRS VI, HLS, ERA5-Land).

Real-GEE tests are marked ``gee`` and skip when Earth Engine cannot initialise.
"""
import sys
from unittest import mock

import pandas as pd
import pytest

from data_loaders import (cache, era5_land, hls, modis_lst, modis_vi, smap, utils,
                          viirs_vi)

AOI = {"type": "Point", "coordinates": [73.05, 31.45]}
START, END = "2024-06-01", "2024-06-10"

MODULES = [modis_lst, modis_vi, smap, viirs_vi, hls, era5_land]
ALIASES = {modis_lst: "load_modis_lst", modis_vi: "load_modis_vi", smap: "load_smap",
           viirs_vi: "load_viirs_vi", hls: "load_hls", era5_land: "load_era5_land"}


class FakeCollection:
    """Records chained ee.ImageCollection calls; every call returns self."""

    def __init__(self):
        self.calls = []
        self.mapped = None

    def __getattr__(self, name):
        def method(*a, **k):
            self.calls.append((name, a, k))
            if name == "map":
                self.mapped = a[0]
            if name == "size":
                return mock.Mock(getInfo=lambda: 5)
            return self
        return method

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def fake_ee(monkeypatch):
    ee = mock.MagicMock()
    col = FakeCollection()
    ee.ImageCollection.return_value = col
    monkeypatch.setitem(sys.modules, "ee", ee)
    return ee, col


@pytest.mark.parametrize("mod", MODULES)
def test_output_bands_in_schema_and_alias(mod):
    assert all(b in utils.BAND_SCHEMA for b in mod.OUT_BANDS)
    assert getattr(mod, ALIASES[mod]) is mod.load
    assert mod.ASSET


@pytest.mark.parametrize("mod", MODULES)
def test_load_nonempty_sorted_and_logged(mod, fake_ee):
    ee, col = fake_ee
    result = mod.load(AOI, START, END, seed=1)
    assert result is not None
    names = col.names()
    assert names[0] == "filterBounds"
    if mod in (smap, era5_land):
        ee.ImageCollection.fromImages.assert_called_once()
        ee.Filter.gt.assert_called_once_with("n_src", 0)
    else:
        assert names.count("filterDate") == 1
        sorts = [c for c in col.calls if c[0] == "sort"]
        assert [s[1][0] for s in sorts] == ["system:time_start", "system:index"]


def test_modis_lst_constants_and_mask(fake_ee):
    _, col = fake_ee
    assert modis_lst.ASSET == "MODIS/061/MOD11A1"
    assert modis_lst.SCALE == 0.02 and modis_lst.QC_MASK_BITS == 3 and modis_lst.QC_GOOD == 0
    assert modis_lst.OUT_BANDS == ["LST_K", "LST_Night_K"]
    modis_lst.load(AOI, START, END)
    img = mock.MagicMock()
    col.mapped(img)
    img.select.assert_any_call("QC_Day")
    img.select.assert_any_call("QC_Night")
    img.select.return_value.bitwiseAnd.assert_called_with(3)
    img.select.return_value.multiply.assert_any_call(0.02)


def test_modis_vi_constants_and_mask(fake_ee):
    _, col = fake_ee
    assert modis_vi.ASSET == "MODIS/061/MOD13Q1"
    assert modis_vi.SCALE == 0.0001 and modis_vi.QA_BAND == "DetailedQA"
    assert modis_vi.OUT_BANDS == ["NDVI", "EVI"]
    modis_vi.load(AOI, START, END)
    img = mock.MagicMock()
    col.mapped(img)
    img.select.assert_any_call("DetailedQA")
    img.select.return_value.bitwiseAnd.assert_called_with(3)
    img.select.return_value.multiply.assert_called_with(0.0001)


def test_viirs_constants_and_mask(fake_ee):
    _, col = fake_ee
    assert viirs_vi.ASSET == "NOAA/VIIRS/001/VNP13A1"
    assert viirs_vi.OUT_BANDS == ["NDVI", "EVI2", "EVI"] and viirs_vi.QA_BAND == "VI_Quality"
    viirs_vi.load(AOI, START, END)
    img = mock.MagicMock()
    col.mapped(img)
    img.select.assert_any_call("VI_Quality")
    img.select.return_value.multiply.assert_called_with(0.0001)


def test_hls_indices_mask_and_merge(fake_ee):
    ee, col = fake_ee
    assert hls.ASSET == "NASA/HLS/HLSS30/v2"
    assert hls.BAND_MAP["S30"] == {"blue": "B2", "red": "B4", "nir": "B8", "swir1": "B11"}
    assert hls.BAND_MAP["L30"]["nir"] == "B5" and hls.BAND_MAP["L30"]["swir1"] == "B6"
    assert hls.FMASK_BAD_MASK == 0b11110 and hls.SCALE == 0.0001
    assert hls.OUT_BANDS == ["EVI", "LSWI"]
    hls.load(AOI, START, END)
    assert "merge" not in col.names()
    img = mock.MagicMock()
    col.mapped(img)
    img.select.assert_any_call("Fmask")
    img.select.return_value.bitwiseAnd.assert_called_with(30)
    hls.load(AOI, START, END, include_landsat=True, max_cloud=40)
    assert "merge" in col.names()
    ee.ImageCollection.assert_any_call(hls.ASSET_L30)
    ee.Filter.lt.assert_called_with("CLOUD_COVERAGE", 40)


def test_hls_index_math_matches_formulas():
    """Numerically check EVI/LSWI using a small arithmetic stand-in for ee.Image."""
    class Num:
        def __init__(self, v, name=None):
            self.v, self.name = v, name

        def _w(self, o): return o.v if isinstance(o, Num) else o
        def subtract(self, o): return Num(self.v - self._w(o))
        def add(self, o): return Num(self.v + self._w(o))
        def multiply(self, o): return Num(self.v * self._w(o))
        def divide(self, o): return Num(self.v / self._w(o))
        def rename(self, n):
            renamed[n] = self.v
            return self
        def addBands(self, o): return self
        def updateMask(self, o): return self
        def copyProperties(self, *a): return self

    renamed = {}
    raw = {"B2": 500, "B4": 800, "B8": 4000, "B11": 2000}  # DN, scale 0.0001

    class Bands(Num):
        def select(self, name): return Num(raw[name] * hls.SCALE)

    class Src:
        def select(self, key):
            if isinstance(key, list):
                return Bands(0)
            return mock.MagicMock()  # Fmask

    class Scaled(Bands):
        pass

    # Bands.multiply(SCALE) is a no-op here because Bands.select applies the scale itself
    Bands.multiply = lambda self, o: self
    hls._indices(None, Src(), hls.BAND_MAP["S30"])
    nir, red, blue, swir = 0.4, 0.08, 0.05, 0.2
    assert renamed["EVI"] == pytest.approx(2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1))
    assert renamed["LSWI"] == pytest.approx((nir - swir) / (nir + swir))

def test_smap_daily_mean_reducer(fake_ee):
    ee, col = fake_ee
    smap.load(AOI, START, END)
    day_fn = ee.List.sequence.return_value.map.call_args[0][0]
    day_fn(0)
    assert ("select", (smap.SRC_BANDS, smap.OUT_BANDS), {}) in col.calls
    assert "mean" in col.names()
    assert smap.ASSET == "NASA/SMAP/SPL4SMGP/008"


def test_era5_daily_aggregation(fake_ee):
    ee, col = fake_ee
    assert era5_land.ASSET == "ECMWF/ERA5_LAND/HOURLY"
    assert {"T_air_C", "T_air_min_C", "T_air_max_C", "Precip_mm", "SW_down_J", "VPD_kPa",
            "SM_L1", "SM_L4"} <= set(era5_land.OUT_BANDS)
    era5_land.load(AOI, START, END)
    day_fn = ee.List.sequence.return_value.map.call_args[0][0]
    day_fn(0)
    names = col.names()
    for n in ("min", "max", "mean", "sum"):
        assert n in names
    # de-accumulated hourly bands are summed; precipitation converted m -> mm
    assert ("select", ("total_precipitation_hourly",), {}) in col.calls
    assert ("multiply", (1000,), {}) in col.calls
    assert ("subtract", (273.15,), {}) in col.calls
    # hourly VPD closure is mappable
    img = mock.MagicMock()
    col.mapped(img)
    img.select.assert_any_call("dewpoint_temperature_2m")


def test_era5_source_bands_use_hourly_deaccumulated():
    assert "total_precipitation_hourly" in era5_land.SRC_BANDS
    assert "surface_solar_radiation_downwards_hourly" in era5_land.SRC_BANDS
    assert "total_precipitation" not in era5_land.SRC_BANDS


def test_count_failure_nonfatal(fake_ee):
    _, col = fake_ee
    col.size = mock.Mock(side_effect=RuntimeError("offline"))
    assert modis_lst.load(AOI, START, END) is col


@pytest.mark.parametrize("mod", [modis_lst, modis_vi, smap, viirs_vi, hls, era5_land])
def test_to_dataframe_cache_hit(mod, tmp_path, monkeypatch, fake_ee):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    df = pd.DataFrame({"time": ["2024-06-02T00:00:00"], **{b: [1.0] for b in mod.OUT_BANDS}})
    cache.write_cached(df, mod.ASSET, AOI, START, END)
    pd.testing.assert_frame_equal(mod.to_dataframe(AOI, START, END), df)


def test_to_dataframe_fetch_sorted_and_cached(tmp_path, monkeypatch, fake_ee):
    ee, col = fake_ee
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    feats = [{"properties": {"time": "b", "LST_K": 301.0, "LST_Night_K": 290.0}},
             {"properties": {"time": "a", "LST_K": 300.0, "LST_Night_K": 289.0}}]
    result = mock.Mock(getInfo=lambda: {"features": feats})
    maps = iter([col, result])

    def fake_map(f):
        out = next(maps)
        if out is result:
            f(mock.MagicMock())
        return out
    col.map = fake_map
    col.sort = lambda *a: col
    col.size = lambda: mock.Mock(getInfo=lambda: 2)
    df = modis_lst.to_dataframe(AOI, START, END)
    assert list(df["time"]) == ["a", "b"]
    assert cache.read_cached(modis_lst.ASSET, AOI, START, END) is not None


def test_safe_aoi_hash_unhashable():
    class Bad:
        def getInfo(self):
            raise RuntimeError("offline")
    assert utils.safe_aoi_hash(Bad()) == "unhashable"


@pytest.fixture
def real_ee():
    try:
        import ee
        utils.init_ee()
        ee.Number(1).getInfo()
    except Exception as exc:
        pytest.skip(f"Earth Engine unavailable: {exc}")
    return ee


@pytest.mark.gee
@pytest.mark.parametrize("mod,start,end", [
    (modis_lst, "2024-06-01", "2024-06-10"), (modis_vi, "2024-06-01", "2024-07-15"),
    (smap, "2024-06-01", "2024-06-05"), (viirs_vi, "2024-06-01", "2024-07-15"),
    (hls, "2024-06-01", "2024-06-30"), (era5_land, "2024-06-01", "2024-06-05")])
def test_real_gee_loaders(real_ee, mod, start, end):
    aoi = real_ee.Geometry.Rectangle([73.0, 31.4, 73.1, 31.5])
    col = mod.load(aoi, start, end, seed=0)
    assert col.size().getInfo() > 0
    assert col.first().bandNames().getInfo() == mod.OUT_BANDS
