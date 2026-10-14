"""Tests for data_loaders.utils/cache and data_loaders.sentinel1.

Run real-GEE tests with: pytest -m gee (auto-skipped when ee cannot initialise).
"""
import sys
from unittest import mock

import pandas as pd
import pytest

from data_loaders import cache, sentinel1, utils
from data_loaders.config import load_config


class FakeCollection:
    """Records chained ee.ImageCollection calls."""

    def __init__(self):
        self.calls = []
        self.mapped = None

    def __getattr__(self, name):
        def method(*a, **k):
            self.calls.append((name, a, k))
            if name == "map":
                self.mapped = a[0]
            if name == "size":
                return mock.Mock(getInfo=lambda: 7)
            return self
        return method


class FakeImage:
    def __init__(self):
        self.calls = []

    def select(self, *a):
        self.calls.append(("select", a))
        return self

    def focalMedian(self, *a):
        self.calls.append(("focalMedian", a))
        return self

    def addBands(self, *a):
        self.calls.append(("addBands", a))
        return self

    def copyProperties(self, *a):
        return self


@pytest.fixture
def fake_ee(monkeypatch):
    ee = mock.MagicMock()
    col = FakeCollection()
    ee.ImageCollection.return_value = col
    monkeypatch.setitem(sys.modules, "ee", ee)
    return ee, col


def test_schema_has_required_bands():
    for b in ["EVI", "NDVI", "EVI2", "LST_K", "T_air_C", "SM_surface",
              "SM_rootzone", "VV_dB", "VH_dB", "ANGLE", "PAR", "SW_down"]:
        assert b in utils.BAND_SCHEMA


def test_sentinel1_bands_in_schema():
    assert sentinel1.OUT_BANDS == ["VV_dB", "VH_dB", "ANGLE"]
    assert all(b in utils.BAND_SCHEMA for b in sentinel1.OUT_BANDS)


def test_sentinel1_load_mocked(fake_ee):
    ee, col = fake_ee
    result = sentinel1.load({"type": "Point", "coordinates": [1, 2]}, "2024-01-01", "2024-02-01", seed=3)
    assert result is col
    names = [c[0] for c in col.calls]
    assert names[:3] == ["filterBounds", "filterDate", "filter"]
    assert names.count("filter") == 3  # IW + VV + VH
    ee.Filter.eq.assert_any_call("instrumentMode", "IW")
    ee.Filter.listContains.assert_any_call("transmitterReceiverPolarisation", "VV")
    ee.Filter.listContains.assert_any_call("transmitterReceiverPolarisation", "VH")
    assert "sort" in names
    # per-image prep: median filter, rename to schema, and no dB conversion
    img = FakeImage()
    col.mapped(img)
    assert ("focalMedian", (30, "circle", "meters")) in img.calls
    assert ("select", (sentinel1.SRC_BANDS, sentinel1.OUT_BANDS)) in img.calls
    assert not any(c[0] in ("log10", "multiply") for c in img.calls)


def test_sentinel1_count_failure_is_nonfatal(fake_ee):
    _, col = fake_ee
    col.size = mock.Mock(side_effect=RuntimeError("offline"))
    assert sentinel1.load({"a": 1}, "2024-01-01", "2024-02-01") is col


def test_sentinel1_to_dataframe_uses_cache(tmp_path, monkeypatch, fake_ee):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    aoi = {"type": "Point", "coordinates": [1, 2]}
    df = pd.DataFrame({"time": ["2024-01-02T00:00:00"], "VV_dB": [-10.0],
                       "VH_dB": [-17.0], "ANGLE": [39.0]})
    cache.write_cached(df, sentinel1.ASSET, aoi, "2024-01-01", "2024-02-01")
    out = sentinel1.to_dataframe(aoi, "2024-01-01", "2024-02-01")
    pd.testing.assert_frame_equal(out, df)


def test_sentinel1_to_dataframe_fetch(tmp_path, monkeypatch, fake_ee):
    ee, col = fake_ee
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    result = mock.Mock(getInfo=lambda: {"features": [
        {"properties": {"time": "b", "VV_dB": -9, "VH_dB": -16, "ANGLE": 40}},
        {"properties": {"time": "a", "VV_dB": -10, "VH_dB": -17, "ANGLE": 39}}]})
    maps = iter([col, result])  # 1st map = _prep inside load(), 2nd = per-image stats
    def fake_map(f):
        out = next(maps)
        if out is result:
            f(mock.MagicMock())  # exercise the per-image stat closure
        return out
    col.map = fake_map
    col.sort = lambda *a: col
    col.size = lambda: mock.Mock(getInfo=lambda: 2)
    aoi = {"type": "Point", "coordinates": [3, 4]}
    df = sentinel1.to_dataframe(aoi, "2024-01-01", "2024-02-01")
    assert list(df["time"]) == ["a", "b"]
    assert cache.read_cached(sentinel1.ASSET, aoi, "2024-01-01", "2024-02-01") is not None


def test_cache_key_deterministic():
    a = {"type": "Polygon", "coordinates": [[[0.1234567891, 1], [2, 3]]]}
    b = {"coordinates": [[[0.1234567892, 1], [2, 3]]], "type": "Polygon"}
    assert cache.aoi_hash(a) == cache.aoi_hash(b)
    assert cache.aoi_hash(a) != cache.aoi_hash({"type": "Point", "coordinates": [0, 0]})
    k1 = cache.cache_key("COPERNICUS/S1_GRD", a, "2024-01-01", "2024-02-01")
    k2 = cache.cache_key("COPERNICUS/S1_GRD", a, "2024-01-01", "2024-02-01")
    assert k1 == k2 and "/" not in k1
    assert k1 != cache.cache_key("COPERNICUS/S1_GRD", a, "2024-01-01", "2024-02-02")


def test_cache_roundtrip_and_getinfo_aoi(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    geom = mock.Mock(getInfo=lambda: {"type": "Point", "coordinates": [1, 1]})
    assert cache.aoi_hash(geom) == cache.aoi_hash({"type": "Point", "coordinates": [1, 1]})
    assert cache.read_cached("x/y", {"a": 1}, "s", "e") is None
    df = pd.DataFrame({"a": [1, 2]})
    p = cache.write_cached(df, "x/y", {"a": 1}, "s", "e")
    assert p.exists()
    pd.testing.assert_frame_equal(cache.read_cached("x/y", {"a": 1}, "s", "e"), df)


def test_cache_csv_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    with mock.patch.object(pd.DataFrame, "to_parquet", side_effect=ImportError):
        p = cache.write_cached(pd.DataFrame({"a": [1]}), "x", {"a": 1}, "s", "e")
    assert p.suffix == ".csv"
    assert cache.read_cached("x", {"a": 1}, "s", "e")["a"].tolist() == [1]


def test_harmonize_bands_rename_scale_order():
    df = pd.DataFrame({"LST_Day_1km": [15000.0], "EVI": [0.4], "extra": [1]})
    out = utils.harmonize_bands(df, rename={"LST_Day_1km": "LST_K"}, scale={"LST_K": 0.02})
    assert list(out.columns) == ["extra", "EVI", "LST_K"]
    assert out["LST_K"].iloc[0] == pytest.approx(300.0)
    assert "LST_Day_1km" in df.columns  # input untouched


def test_harmonize_bands_errors_and_keep():
    df = pd.DataFrame({"a": [1], "b": [2]})
    with pytest.raises(ValueError):
        utils.harmonize_bands(df, rename={"a": "NOT_A_BAND"})
    with pytest.raises(ValueError):
        utils.harmonize_bands(df, rename={"a": "EVI", "b": "EVI"})
    with pytest.raises(KeyError):
        utils.harmonize_bands(df, scale={"EVI": 2})
    with pytest.raises(KeyError):
        utils.harmonize_bands(df, rename={"a": "EVI"}, keep=["NDVI"])
    out = utils.harmonize_bands(df, rename={"a": "EVI"}, keep=["EVI"])
    assert list(out.columns) == ["EVI"]


def test_get_logger_and_init_ee_service_account(monkeypatch):
    assert utils.get_logger("t") is not None
    ee = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "ee", ee)
    monkeypatch.setenv("EE_SERVICE_ACCOUNT", "svc@example.iam")
    monkeypatch.setenv("EE_PRIVATE_KEY_FILE", "key.json")
    monkeypatch.setenv("EE_PROJECT", "proj")
    utils.init_ee()
    ee.ServiceAccountCredentials.assert_called_once_with("svc@example.iam", "key.json")
    ee.Initialize.assert_called_once()
    monkeypatch.delenv("EE_SERVICE_ACCOUNT")
    ee.reset_mock()
    utils.init_ee()
    ee.Initialize.assert_called_once_with(project="proj")


def test_config_expands_env(monkeypatch):
    monkeypatch.setenv("JAXA_USER", "u")
    monkeypatch.setenv("JAXA_PASS", "p")
    cfg = load_config()["datasets"]
    assert cfg["himawari"]["username"] == "u" and cfg["himawari"]["password"] == "p"
    assert cfg["himawari"]["enabled"] is False and cfg["openmeteo_radiation"]["enabled"] is False
    assert cfg["sentinel1"]["asset"] == "COPERNICUS/S1_GRD"


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
def test_sentinel1_real_gee(real_ee):
    aoi = real_ee.Geometry.Rectangle([73.0, 31.4, 73.1, 31.5])
    col = sentinel1.load(aoi, "2024-01-01", "2024-01-31", seed=0)
    assert col.size().getInfo() > 0
    assert col.first().bandNames().getInfo() == sentinel1.OUT_BANDS
