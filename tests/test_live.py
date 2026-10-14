import builtins
import runpy
import sys
import time

import pandas as pd
import pytest

from data_loaders import live


def _fake_sources(monkeypatch, h=True, o=True):
    t = pd.date_range("2025-06-10", periods=2, freq="h")
    monkeypatch.setattr(live.himawari, "fetch", lambda *a, **k: pd.DataFrame({"time": t, "PAR": [45.0, 90.0]}) if h
                        else (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(live.openmeteo_radiation, "fetch",
                        lambda *a, **k: pd.DataFrame({"time": t, "SW_down": [100.0, 200.0],
                                                      "PAR_proxy": [45.0, 90.0]}) if o
                        else (_ for _ in ()).throw(RuntimeError("down")))


def test_run_once_with_mocks(monkeypatch):
    _fake_sources(monkeypatch)
    res = live.LiveMonitor(1.0, 2.0).run_once()
    assert len(res["himawari"]) == 2 and len(res["openmeteo"]) == 2
    assert res["sanity"]["ok"] is True
    # window is last 30 days
    assert (pd.Timestamp(res["end"]) - pd.Timestamp(res["start"])).days == 30


def test_run_once_degrades(monkeypatch):
    _fake_sources(monkeypatch, h=False, o=False)
    res = live.LiveMonitor(1.0, 2.0).run_once()
    assert res["sanity"] is None
    assert any("himawari failed" in e for e in res["errors"]) and any("openmeteo failed" in e for e in res["errors"])


def test_feature_stack_used_when_available(monkeypatch):
    _fake_sources(monkeypatch)
    import types
    fake_ee = types.SimpleNamespace(Geometry=types.SimpleNamespace(Point=lambda c: ("pt", c)))
    fs = types.ModuleType("data_loaders.feature_stack")
    fs.build_feature_stack = lambda aoi, s, e, mode="historical": ("stack", aoi, mode)
    monkeypatch.setitem(sys.modules, "ee", fake_ee)
    monkeypatch.setitem(sys.modules, "data_loaders.feature_stack", fs)
    monkeypatch.setattr(live.__package__ and sys.modules["data_loaders"], "feature_stack", fs, raising=False)
    res = live.LiveMonitor(1.0, 2.0).run_once()
    assert res["feature_stack"] == ("stack", ("pt", [2.0, 1.0]), "live")


def test_callback_exception_is_not_fatal(monkeypatch):
    mon = live.LiveMonitor(1.0, 2.0, callback=lambda r: 1 / 0)
    monkeypatch.setattr(mon, "run_once", lambda: {"ok": 1})
    mon._tick()  # must not raise
    monkeypatch.setattr(mon, "run_once", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    mon._tick()


def test_invalid_interval():
    with pytest.raises(ValueError):
        live.LiveMonitor(0, 0, interval_hours=0)


def test_start_stop_apscheduler(monkeypatch):
    got = []
    mon = live.LiveMonitor(1.0, 2.0, interval_hours=1, callback=got.append)
    monkeypatch.setattr(mon, "run_once", lambda: {"x": 1})
    mon.start()
    mon.start()  # idempotent
    assert mon.running
    for _ in range(50):
        if got:
            break
        time.sleep(0.1)
    mon.stop()
    assert not mon.running and got == [{"x": 1}]


def test_timer_fallback(monkeypatch):
    real_import = builtins.__import__

    def no_aps(name, *a, **k):
        if name.startswith("apscheduler"):
            raise ImportError("no aps")
        return real_import(name, *a, **k)

    got = []
    mon = live.LiveMonitor(1.0, 2.0, callback=got.append)
    monkeypatch.setattr(mon, "run_once", lambda: {"x": 2})
    monkeypatch.setattr(builtins, "__import__", no_aps)
    mon.start()
    monkeypatch.setattr(builtins, "__import__", real_import)
    for _ in range(50):
        if got:
            break
        time.sleep(0.1)
    mon.stop()
    assert got == [{"x": 2}] and mon._timer is None


def test_cli_once(monkeypatch, capsys):
    _fake_sources(monkeypatch)
    monkeypatch.setenv("JAXA_PASS", "topsecret")
    assert live.main(["--lat", "1", "--lon", "2", "--once"]) == 0
    out = capsys.readouterr().out
    assert "himawari=2" in out and "topsecret" not in out
