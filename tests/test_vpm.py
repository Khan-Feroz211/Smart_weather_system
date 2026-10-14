import numpy as np
import pandas as pd
import pytest

import vpm
from data_loaders.config import load_config


def test_t_scalar_edges():
    assert vpm.t_scalar(25, 0, 25, 40) == pytest.approx(1.0)
    assert vpm.t_scalar(-5, 0, 25, 40) == 0.0
    assert vpm.t_scalar(45, 0, 25, 40) == 0.0
    assert vpm.t_scalar(0, 0, 25, 40) == 0.0 and vpm.t_scalar(40, 0, 25, 40) == 0.0
    arr = vpm.t_scalar(np.array([-1.0, 12.0, 25.0, np.nan]))
    assert arr[0] == 0 and 0 < arr[1] < 1 and arr[2] == pytest.approx(1) and np.isnan(arr[3])
    assert vpm.t_scalar(0.0, 0, 0, 40) == 0.0  # zero-denominator guard


def test_select_temperature():
    assert vpm.select_temperature("era5", t_air_c=20.0) == 20.0
    assert vpm.select_temperature("lst", lst_k=300.15) == pytest.approx(27.0)
    assert vpm.select_temperature("lst", lst_k=np.array([273.15]))[0] == pytest.approx(0.0)
    for kw in ({"temp_source": "era5"}, {"temp_source": "lst"}, {"temp_source": "x", "t_air_c": 1}):
        with pytest.raises(ValueError):
            vpm.select_temperature(**kw)


def test_w_scalars_and_bounds():
    assert vpm.w_scalar_lswi(0.5, 0.5) == 1.0
    assert vpm.w_scalar_lswi(-1.0, 0.5) == 0.0
    assert vpm.w_scalar_lswi(0.0, 1.0) == pytest.approx(0.5)
    assert vpm.soil_moisture_stress(0.05) == 0.0 and vpm.soil_moisture_stress(0.5) == 1.0
    assert vpm.soil_moisture_stress(0.225) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        vpm.soil_moisture_stress(0.2, 0.3, 0.3)
    for a in (0, 0.3, 1):
        w = vpm.w_scalar_blended(np.array([-0.5, 0.3, 0.9]), 0.9, np.array([0.0, 0.2, 0.9]), alpha=a)
        assert ((w >= 0) & (w <= 1)).all()
    assert vpm.w_scalar_blended(0.3, 0.9, 0.2, alpha=1.0) == pytest.approx(vpm.w_scalar_lswi(0.3, 0.9))
    with pytest.raises(ValueError):
        vpm.w_scalar_blended(0.3, 0.9, 0.2, alpha=1.5)


def test_par_fpar_gpp():
    assert vpm.par_from_sw(100.0) == pytest.approx(45.0)
    assert vpm.fpar_from_evi(0.0) == 0.0 and vpm.fpar_from_evi(2.0) == 1.0
    assert vpm.fpar_from_evi(0.5) == pytest.approx(0.5)
    assert vpm.gpp(100.0, 0.5, 1.0, 0.8, eps_0=0.1) == pytest.approx(4.0)
    assert vpm.gpp(100.0, 0.5, 1.0, 0.8, p_scalar=0.5, eps_0=0.1) == pytest.approx(2.0)


def test_config_defaults_from_yaml():
    cfg = vpm.VPMConfig.from_yaml()
    assert (cfg.temp_source, cfg.w_scalar_mode, cfg.alpha) == ("era5", "lswi", 0.5)
    with pytest.raises(ValueError):
        vpm.VPMConfig(temp_source="bad")
    with pytest.raises(ValueError):
        vpm.VPMConfig(w_scalar_mode="bad")


def test_compute_gpp_default_is_lswi_only_era5():
    df = pd.DataFrame({"T_air_C": [20.0, 25.0], "LST_K": [310.0, 310.0], "LSWI": [0.2, 0.4],
                       "SM_rootzone": [0.1, 0.35], "EVI": [0.5, 0.6], "PAR": [100.0, 200.0]})
    default = vpm.compute_gpp(df)
    manual = vpm.gpp(df["PAR"].to_numpy(), vpm.fpar_from_evi(df["EVI"].to_numpy()),
                     vpm.t_scalar(df["T_air_C"].to_numpy()), vpm.w_scalar_lswi(df["LSWI"].to_numpy(), 0.4))
    assert np.allclose(default, manual)
    lst = vpm.compute_gpp(df, vpm.VPMConfig(temp_source="lst"))
    assert not np.allclose(lst, default)
    blended = vpm.compute_gpp(df, vpm.VPMConfig(w_scalar_mode="blended"))
    assert blended.shape == (2,) and (blended >= 0).all()


def test_datasets_yaml_himawari_env_interpolation(monkeypatch, tmp_path):
    monkeypatch.setenv("JAXA_USER", "alice")
    monkeypatch.setenv("JAXA_PASS", "s3cret")
    cfg = load_config()
    assert cfg["datasets"]["himawari"]["username"] == "alice"
    assert cfg["datasets"]["himawari"]["password"] == "s3cret"
    assert cfg["live"]["window_days"] == 30 and cfg["vpm"]["temp_source"] == "era5"
