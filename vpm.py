"""Vegetation Photosynthesis Model (VPM) scalars and GPP (numpy/pandas, scalar or array safe).

GPP = eps_0 * T_scalar * W_scalar * P_scalar * FPAR * PAR   (Xiao et al. 2004)

Written new for this repo (no earlier VPM code existed). Defaults reproduce the
"classic" behaviour: ERA5 air temperature and LSWI-only W_scalar.

ASSUMPTIONS (open questions for the user, see .agents/tasks/vpm-track-b-report.md):
  * T_MIN/T_OPT/T_MAX = 0/25/40 C. crop_database.py only has an optimal range
    (temp_min/temp_max) per crop, not cardinal temperatures, so it is NOT used.
  * SM_WILT/SM_FC = 0.10/0.35 m3/m3 (generic loam); site/crop specific values needed.
  * alpha = 0.5 blend weight; eps_0 = 0.05 (mol CO2 per mol photons; the GPP unit follows
    eps_0 x PAR units); FPAR = clip(1.25 * (EVI - 0.1), 0, 1).
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

import numpy as np

T_MIN, T_OPT, T_MAX = 0.0, 25.0, 40.0   # ASSUMPTION
SM_WILT, SM_FC = 0.10, 0.35             # ASSUMPTION (m3/m3)
EPS_0 = 0.05                            # ASSUMPTION
PAR_FRACTION = 0.45
DEFAULT_CONFIG = Path(__file__).resolve().parent / "config" / "datasets.yaml"


def _out(x):
    """Return a Python float for 0-d results, else the ndarray."""
    return float(x) if np.ndim(x) == 0 else x


def t_scalar(T, t_min: float = T_MIN, t_opt: float = T_OPT, t_max: float = T_MAX):
    """Temperature scalar: ((T-Tmin)(T-Tmax)) / (((T-Tmin)(T-Tmax)) - (T-Topt)^2).

    0 outside [Tmin, Tmax]; 1 at Topt; clipped to [0, 1]; NaN propagates.
    """
    T = np.asarray(T, dtype=float)
    num = (T - t_min) * (T - t_max)
    den = num - (T - t_opt) ** 2
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(den == 0, 0.0, num / den)
    inside = (T >= t_min) & (T <= t_max)
    res = np.clip(np.where(inside, ratio, 0.0), 0.0, 1.0)
    return _out(np.where(np.isnan(T), np.nan, res))


def select_temperature(temp_source: str = "era5", t_air_c=None, lst_k=None):
    """Temperature in Celsius from ERA5 air temp ('era5') or MODIS LST in Kelvin ('lst')."""
    src = str(temp_source).lower()
    if src == "era5":
        if t_air_c is None:
            raise ValueError("temp_source='era5' requires t_air_c")
        return t_air_c
    if src == "lst":
        if lst_k is None:
            raise ValueError("temp_source='lst' requires lst_k")
        return np.asarray(lst_k, dtype=float) - 273.15 if np.ndim(lst_k) else float(lst_k) - 273.15
    raise ValueError("temp_source must be 'era5' or 'lst'")


def w_scalar_lswi(lswi, lswi_max):
    """(1 + LSWI) / (1 + LSWI_max), clipped to [0, 1]."""
    res = np.clip((1.0 + np.asarray(lswi, dtype=float)) / (1.0 + np.asarray(lswi_max, dtype=float)), 0.0, 1.0)
    return _out(res)


def soil_moisture_stress(sm, sm_wilt: float = SM_WILT, sm_fc: float = SM_FC):
    """clip((sm - sm_wilt) / (sm_fc - sm_wilt), 0, 1)."""
    if sm_fc == sm_wilt:
        raise ValueError("sm_fc must differ from sm_wilt")
    return _out(np.clip((np.asarray(sm, dtype=float) - sm_wilt) / (sm_fc - sm_wilt), 0.0, 1.0))


def w_scalar_blended(lswi, lswi_max, sm_rootzone, alpha: float = 0.5,
                     sm_wilt: float = SM_WILT, sm_fc: float = SM_FC):
    """alpha * W_LSWI + (1 - alpha) * f(SM_rootzone), clipped to [0, 1]."""
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("alpha must be within [0, 1]")
    w = alpha * np.asarray(w_scalar_lswi(lswi, lswi_max)) \
        + (1.0 - alpha) * np.asarray(soil_moisture_stress(sm_rootzone, sm_wilt, sm_fc))
    return _out(np.clip(w, 0.0, 1.0))


def par_from_sw(sw):
    """PAR ~= 0.45 * shortwave (same units)."""
    return _out(PAR_FRACTION * np.asarray(sw, dtype=float))


def fpar_from_evi(evi, a: float = 1.25, evi_min: float = 0.1):
    """FPAR = clip(a * (EVI - evi_min), 0, 1). ASSUMPTION: a=1.25, evi_min=0.1."""
    return _out(np.clip(a * (np.asarray(evi, dtype=float) - evi_min), 0.0, 1.0))


def gpp(par, fpar, t_scalar_v, w_scalar_v, p_scalar=1.0, eps_0: float = EPS_0):
    """eps_0 * T_scalar * W_scalar * P_scalar * FPAR * PAR."""
    res = eps_0 * np.asarray(t_scalar_v, dtype=float) * np.asarray(w_scalar_v, dtype=float) \
        * np.asarray(p_scalar, dtype=float) * np.asarray(fpar, dtype=float) * np.asarray(par, dtype=float)
    return _out(res)


@dataclass
class VPMConfig:
    temp_source: str = "era5"      # 'era5' | 'lst'
    w_scalar_mode: str = "lswi"    # 'lswi' | 'blended'
    alpha: float = 0.5
    t_min: float = T_MIN
    t_opt: float = T_OPT
    t_max: float = T_MAX
    sm_wilt: float = SM_WILT
    sm_fc: float = SM_FC
    eps_0: float = EPS_0
    fpar_a: float = 1.25
    evi_min: float = 0.1

    def __post_init__(self):
        if self.temp_source not in ("era5", "lst"):
            raise ValueError("temp_source must be 'era5' or 'lst'")
        if self.w_scalar_mode not in ("lswi", "blended"):
            raise ValueError("w_scalar_mode must be 'lswi' or 'blended'")

    @classmethod
    def from_yaml(cls, path: Optional[Path] = None) -> "VPMConfig":
        """Load the ``vpm:`` section of config/datasets.yaml (missing keys use defaults)."""
        import yaml
        with open(path or DEFAULT_CONFIG, "r", encoding="utf-8") as fh:
            section = (yaml.safe_load(fh) or {}).get("vpm") or {}
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in section.items() if k in names})


def compute_gpp(df, cfg: Optional[VPMConfig] = None, lswi_max: Optional[float] = None):
    """GPP series from a DataFrame with EVI, LSWI, PAR and T_air_C (era5) or LST_K (lst);
    SM_rootzone is required when w_scalar_mode='blended'. lswi_max defaults to the column max."""
    cfg = cfg or VPMConfig()
    temp = select_temperature(cfg.temp_source,
                              t_air_c=df["T_air_C"].to_numpy() if cfg.temp_source == "era5" else None,
                              lst_k=df["LST_K"].to_numpy() if cfg.temp_source == "lst" else None)
    lswi = df["LSWI"].to_numpy(dtype=float)
    lmax = float(np.nanmax(lswi)) if lswi_max is None else lswi_max
    if cfg.w_scalar_mode == "blended":
        w = w_scalar_blended(lswi, lmax, df["SM_rootzone"].to_numpy(dtype=float),
                             cfg.alpha, cfg.sm_wilt, cfg.sm_fc)
    else:
        w = w_scalar_lswi(lswi, lmax)
    ts = t_scalar(temp, cfg.t_min, cfg.t_opt, cfg.t_max)
    fp = fpar_from_evi(df["EVI"].to_numpy(dtype=float), cfg.fpar_a, cfg.evi_min)
    return gpp(df["PAR"].to_numpy(dtype=float), fp, ts, w, 1.0, cfg.eps_0)


# ---- optional Earth Engine versions (lazy import) --------------------------------------
def t_scalar_ee(temp_img, t_min=T_MIN, t_opt=T_OPT, t_max=T_MAX):
    """ee.Image version of t_scalar (temp_img in Celsius)."""
    import ee  # noqa: F401  (lazy)
    num = temp_img.subtract(t_min).multiply(temp_img.subtract(t_max))
    den = num.subtract(temp_img.subtract(t_opt).pow(2))
    inside = temp_img.gte(t_min).And(temp_img.lte(t_max))
    return num.divide(den).clamp(0, 1).updateMask(inside).unmask(0)
