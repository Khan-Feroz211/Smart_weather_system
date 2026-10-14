import ftplib
from datetime import datetime, timezone

import pandas as pd
import pytest

from data_loaders import himawari

NOW = datetime(2025, 6, 15, tzinfo=timezone.utc)


class FakeFTP:
    instances = []

    def __init__(self, host, timeout=None):
        self.host, self.timeout = host, timeout
        self.quit_called = False
        FakeFTP.instances.append(self)

    def login(self, user, pwd):
        self.creds = (user, pwd)

    def nlst(self, d):
        if d.endswith("/10/"):
            return ["H09_20250610_0000_PAR_5km.nc", "H09_20250610_0010_PAR_5km.nc",
                    "H09_20250610_0000_AOT_5km.nc", "readme.txt"]
        raise ftplib.error_perm("550 no such dir")

    def retrbinary(self, cmd, cb):
        cb(b"data")

    def quit(self):
        self.quit_called = True


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    FakeFTP.instances = []
    monkeypatch.setattr(himawari.cache, "CACHE_DIR", tmp_path)
    for k in ("JAXA_USER", "JAXA_PASS", "PTREE_USER", "PTREE_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "dotenv", None)  # block .env loading


def test_missing_creds_returns_empty():
    df = himawari.fetch(1.0, 2.0, "2025-06-10", "2025-06-10", ftp_factory=FakeFTP, now=NOW)
    assert df.empty and list(df.columns) == ["time", "PAR"]
    assert FakeFTP.instances == []


def test_alias_creds_listing_and_download(monkeypatch):
    monkeypatch.setenv("PTREE_USER", "u")
    monkeypatch.setenv("PTREE_PASSWORD", "p")
    vals = iter([2.0, 1.0])
    df = himawari.fetch(1.0, 2.0, "2025-06-10", "2025-06-11", ftp_factory=FakeFTP,
                        reader=lambda *a: next(vals), now=NOW)
    assert list(df["PAR"]) == [2.0, 1.0]
    assert df["time"].is_monotonic_increasing and len(df) == 2  # AOT/readme filtered out
    assert FakeFTP.instances[0].quit_called and FakeFTP.instances[0].timeout == 30.0
    # second call is served from cache
    df2 = himawari.fetch(1.0, 2.0, "2025-06-10", "2025-06-11", ftp_factory=FakeFTP,
                         reader=lambda *a: 99.0, now=NOW)
    assert len(df2) == 2 and len(FakeFTP.instances) == 1


def test_clamp_30_days():
    s, e = himawari.clamp_window("2025-01-01", "2025-06-14", now=NOW)
    assert s == "2025-05-16" and e == "2025-06-14"


def test_parse_filename():
    assert himawari.parse_filename("x_20250610_0010_PAR.nc", "PAR") == datetime(2025, 6, 10, 0, 10)
    assert himawari.parse_filename("x_20250610_0010_AOT.nc", "PAR") is None
    assert himawari.parse_filename("nodate_PAR.nc", "PAR") is None
    assert himawari.parse_filename("x_20251310_0010_PAR.nc", "PAR") is None


def test_ftp_error_returns_empty_and_quits(monkeypatch):
    monkeypatch.setenv("JAXA_USER", "u")
    monkeypatch.setenv("JAXA_PASS", "p")

    class Boom(FakeFTP):
        def login(self, u, p):
            raise ftplib.error_perm("530 bad login")

    df = himawari.fetch(1.0, 2.0, "2025-06-10", "2025-06-10", ftp_factory=Boom, now=NOW)
    assert df.empty and Boom.instances[0].quit_called


def test_invalid_product():
    with pytest.raises(ValueError):
        himawari.fetch(0, 0, "2025-06-10", "2025-06-10", product="XYZ")


def test_export_has_attribution(tmp_path):
    p = himawari.export_csv(pd.DataFrame({"time": ["t"], "PAR": [1.0]}), str(tmp_path / "o.csv"), "PAR")
    text = open(p, encoding="utf-8").read()
    assert text.startswith("# Research product of PAR") and "JAXA" in text
