import pandas as pd
import pytest
import requests

from data_loaders import openmeteo_radiation as om


class Resp:
    def __init__(self, code=200, payload=None):
        self.status_code, self._p = code, payload

    def json(self):
        return self._p


GOOD = {"hourly": {"time": ["2025-06-10T01:00", "2025-06-10T00:00"], "shortwave_radiation": [200.0, 100.0]}}


class Session:
    def __init__(self, items):
        self.items, self.calls = list(items), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        it = self.items.pop(0)
        if isinstance(it, Exception):
            raise it
        return it


def test_success_params_and_columns():
    s = Session([Resp(200, GOOD)])
    df = om.fetch(10.5, 20.5, "2025-06-10", "2025-06-10", session=s)
    url, params, _ = s.calls[0]
    assert url == om.URL
    assert set(params) == {"latitude", "longitude", "start_date", "end_date", "hourly", "models",
                           "temporal_resolution"}
    assert params["hourly"] == "shortwave_radiation" and params["temporal_resolution"] == "native"
    assert list(df.columns) == ["time", "SW_down", "PAR_proxy"]
    assert list(df["SW_down"]) == [100.0, 200.0]  # sorted by time
    assert df["PAR_proxy"].tolist() == pytest.approx([45.0, 90.0])


def test_retry_then_success():
    sleeps = []
    s = Session([Resp(503), requests.ConnectionError("x"), Resp(200, GOOD)])
    df = om.fetch(0, 0, "a", "b", session=s, sleep=sleeps.append, backoff=2)
    assert len(df) == 2 and sleeps == [2, 4]


def test_exhausted_retries():
    s = Session([Resp(500)] * 3)
    with pytest.raises(om.OpenMeteoError):
        om.fetch(0, 0, "a", "b", session=s, sleep=lambda x: None)


def test_client_error_not_retried():
    s = Session([Resp(400, {"reason": "bad"})])
    with pytest.raises(om.OpenMeteoError):
        om.fetch(0, 0, "a", "b", session=s, sleep=lambda x: None)
    assert len(s.calls) == 1


def test_missing_hourly():
    with pytest.raises(om.OpenMeteoError):
        om.fetch(0, 0, "a", "b", session=Session([Resp(200, {"reason": "oops"})]))


def _df(vals, col="SW_down"):
    return pd.DataFrame({"time": pd.date_range("2025-06-10", periods=len(vals), freq="h"), col: vals})


def test_sanity_check():
    a, b = _df([100.0, 200.0]), _df([110.0, 190.0])
    r = om.radiation_sanity_check(a, b, tol=0.1)
    assert r["n"] == 2 and r["ok"] is True and r["mean_abs_diff"] == pytest.approx(10.0)
    assert om.radiation_sanity_check(a, _df([500.0, 600.0]), tol=0.1)["ok"] is False
    assert om.radiation_sanity_check(a, pd.DataFrame(columns=["time", "SW_down"]))["ok"] is None
    far = pd.DataFrame({"time": pd.date_range("2030-01-01", periods=2, freq="h"), "SW_down": [1.0, 2.0]})
    assert om.radiation_sanity_check(a, far)["n"] == 0


def test_export_csv(tmp_path):
    p = om.export_csv(_df([1.0]), str(tmp_path / "o.csv"))
    assert "CC-BY 4.0" in open(p, encoding="utf-8").read().splitlines()[0]
