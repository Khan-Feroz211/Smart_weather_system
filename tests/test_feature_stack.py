"""Mocked-ee tests for data_loaders.feature_stack."""
import datetime as dt
import sys
from unittest import mock

import pandas as pd
import pytest

from data_loaders import feature_stack as fs


def cfg(enabled, **extra):
    names = ["hls", "modis_vi", "viirs_vi", "era5_land", "modis_lst", "smap", "sentinel1",
             "himawari", "openmeteo_radiation"]
    c = {"datasets": {n: {"enabled": n in enabled} for n in names}}
    c.update(extra)
    return c


@pytest.fixture
def fake_ee(monkeypatch):
    ee = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "ee", ee)
    return ee


@pytest.fixture
def fake_loads(monkeypatch):
    """Replace each source's load() with a recorder returning a labelled mock."""
    calls = []
    for name, mod in fs.SOURCES.items():
        def _load(aoi, s, e, seed=0, _n=name, **k):
            calls.append((_n, s, e, seed, k))
            return mock.MagicMock(name=_n)
        monkeypatch.setattr(mod, "load", _load)
    return calls


def test_priority_default_order_and_disabled_skipped():
    c = cfg({"hls", "modis_vi", "smap"})
    assert fs.enabled_sources(c) == ["hls", "modis_vi", "smap"]
    assert "himawari" not in fs.enabled_sources(cfg({"hls", "himawari"}))  # no GEE loader
    assert fs.enabled_sources(cfg(set())) == []


def test_priority_overrides():
    c = cfg({"hls", "modis_vi", "smap"})
    c["datasets"]["smap"]["priority"] = 0
    assert fs.enabled_sources(c)[0] == "smap"
    c2 = cfg({"hls", "modis_vi", "smap"}, priority=["modis_vi", "hls"])
    assert fs.enabled_sources(c2) == ["modis_vi", "hls", "smap"]


def test_priority_tie_broken_by_name():
    c = cfg({"viirs_vi", "modis_vi"})
    c["datasets"]["viirs_vi"]["priority"] = 5
    c["datasets"]["modis_vi"]["priority"] = 5
    assert fs.enabled_sources(c) == ["modis_vi", "viirs_vi"]


def test_expected_band_names_with_collision_prefix():
    names = fs.stack_band_names(cfg({"hls", "modis_vi", "viirs_vi"}))
    assert names == ["EVI", "LSWI", "NDVI", "modis_vi_EVI",
                     "viirs_vi_NDVI", "EVI2", "viirs_vi_EVI"]
    assert len(names) == len(set(names))


def test_reference_skips_sar_and_requires_optical():
    plan = fs._plan(cfg({"sentinel1", "modis_lst"}))
    assert [p[0] for p in plan] == ["modis_lst", "sentinel1"]
    with pytest.raises(ValueError):
        fs._plan(cfg({"sentinel1"}))


def test_build_stack_joins_in_priority_order(fake_ee, fake_loads):
    c = cfg({"hls", "modis_vi", "smap"})
    stack = fs.build_feature_stack(mock.sentinel.aoi, "2024-06-01", "2024-06-10", config=c,
                                   tolerance_days=2, seed=7)
    assert [x[0] for x in fake_loads] == ["hls", "modis_vi", "smap"]
    assert all(x[3] == 7 and x[4] == {"log_count": False} for x in fake_loads)
    fake_ee.Filter.maxDifference.assert_called_once_with(
        difference=2 * 86400000, leftField="system:time_start", rightField="system:time_start")
    join = fake_ee.Join.saveBest.return_value
    assert join.apply.call_count == 2
    assert stack is not None
    # merge closure selects source bands and renames only the colliding ones
    merge = join.apply.return_value.map.call_args[0][0]
    merge(mock.MagicMock())
    fake_ee.Image.return_value.select.assert_called_with(["SM_surface", "SM_rootzone"],
                                                         ["SM_surface", "SM_rootzone"])


def test_collision_rename_in_merge(fake_ee, fake_loads):
    fs.build_feature_stack({}, "2024-06-01", "2024-06-10", config=cfg({"hls", "modis_vi"}))
    merge = fake_ee.Join.saveBest.return_value.apply.return_value.map.call_args[0][0]
    merge(mock.MagicMock())
    fake_ee.Image.return_value.select.assert_called_with(["NDVI", "EVI"], ["NDVI", "modis_vi_EVI"])


def test_single_source_no_join(fake_ee, fake_loads):
    fs.build_feature_stack({}, "2024-06-01", "2024-06-10", config=cfg({"hls"}))
    fake_ee.Join.saveBest.return_value.apply.assert_not_called()


def test_disabled_sources_not_loaded(fake_ee, fake_loads):
    fs.build_feature_stack({}, "2024-06-01", "2024-06-10", config=cfg({"hls", "smap"}))
    assert {x[0] for x in fake_loads} == {"hls", "smap"}


def test_live_window_default_and_config():
    now = dt.datetime(2024, 6, 30, 12, tzinfo=dt.timezone.utc)
    s, e = fs.live_window(cfg({"hls"}), now=now)
    assert (s, e) == ("2024-06-01", "2024-07-01")
    assert (dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days == 30
    s2, e2 = fs.live_window(cfg({"hls"}, live={"window_days": 7}), now=now)
    assert (dt.date.fromisoformat(e2) - dt.date.fromisoformat(s2)).days == 7
    with pytest.raises(ValueError):
        fs.live_window(cfg({"hls"}, live={"window_days": 0}), now=now)


def test_live_mode_uses_30_day_window(fake_ee, fake_loads):
    fs.build_feature_stack({}, "1999-01-01", "1999-01-02", mode="live", config=cfg({"hls"}))
    _, s, e, _, _ = fake_loads[0]
    assert (dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days == 30
    assert dt.date.fromisoformat(e) > dt.datetime.now(dt.timezone.utc).date()


@pytest.mark.parametrize("kwargs", [
    {"mode": "bogus"}, {"start_date": "2024-13-01"}, {"start_date": "2024-06-10"},
    {"tolerance_days": -1}])
def test_invalid_inputs_raise(fake_ee, fake_loads, kwargs):
    args = {"start_date": "2024-06-01", "end_date": "2024-06-10", "mode": "historical",
            "tolerance_days": 1}
    args.update(kwargs)
    with pytest.raises(ValueError):
        fs.build_feature_stack({}, config=cfg({"hls"}), **args)


def test_bad_config_type():
    with pytest.raises(TypeError):
        fs.enabled_sources(42)


def test_config_from_path(tmp_path):
    p = tmp_path / "d.yaml"
    p.write_text("datasets:\n  smap: {enabled: true}\n  hls: {enabled: false}\n")
    assert fs.enabled_sources(str(p)) == ["smap"]


def test_default_config_file_loads():
    names = fs.enabled_sources(None)
    assert names[0] == "hls" and "himawari" not in names


def test_stack_to_dataframe(fake_ee):
    feats = [{"properties": {"date": "2024-06-02", "EVI": 0.5, "LSWI": 0.1}},
             {"properties": {"date": "2024-06-01", "EVI": 0.4, "LSWI": 0.2}}]
    result = mock.Mock(getInfo=lambda: {"features": feats})
    stack = mock.MagicMock()

    def fake_map(f):
        f(mock.MagicMock())
        return result
    stack.map = fake_map
    df = fs.stack_to_dataframe(stack, {}, bands=["EVI", "LSWI"])
    assert list(df["date"]) == ["2024-06-01", "2024-06-02"]
    assert list(df.columns) == ["date", "EVI", "LSWI"]
    stack.map = lambda f: mock.Mock(getInfo=lambda: {"features": []})
    assert fs.stack_to_dataframe(stack, {}).empty


def test_feature_frame_with_extra_source(monkeypatch):
    c = cfg({"hls"})
    monkeypatch.setattr(fs, "build_feature_stack", lambda *a, **k: "stack")
    monkeypatch.setattr(fs, "stack_to_dataframe", lambda *a, **k: pd.DataFrame(
        {"date": ["2024-06-01", "2024-06-02"], "EVI": [0.4, 0.5], "LSWI": [0.1, 0.2]}))
    seen = {}

    def extra(aoi, s, e, **k):
        seen["win"] = (s, e)
        return pd.DataFrame({"time": ["2024-06-01T00:00:00Z", "2024-06-02T00:00:00Z"],
                             "PAR": [100.0, 110.0], "EVI": [9, 9]})
    fs.register_extra_source("himawari", extra)
    try:
        assert fs.extra_sources() == ["himawari"]
        live = fs.build_feature_frame({}, "2024-06-01", "2024-06-10", mode="live", config=c)
        assert list(live.columns) == ["date", "EVI", "LSWI", "PAR", "himawari_EVI"]
        assert live["PAR"].tolist() == [100.0, 110.0]
        assert (dt.date.fromisoformat(seen["win"][1]) - dt.date.fromisoformat(seen["win"][0])).days == 30
        hist = fs.build_feature_frame({}, "2024-06-01", "2024-06-10", config=c)
        assert "PAR" not in hist.columns  # extras only in live mode
    finally:
        fs.unregister_extra_source("himawari")
    assert fs.extra_sources() == []


def test_register_extra_source_validates():
    with pytest.raises(ValueError):
        fs.register_extra_source("", lambda *a: None)
    with pytest.raises(ValueError):
        fs.register_extra_source("x", None)


def test_package_exports():
    import data_loaders
    for n in ("build_feature_stack", "build_feature_frame", "register_extra_source",
              "load_modis_lst", "load_modis_vi", "load_smap", "load_viirs_vi", "load_hls",
              "load_era5_land"):
        assert hasattr(data_loaders, n)
