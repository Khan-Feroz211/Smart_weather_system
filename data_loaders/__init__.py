"""Satellite data loaders for the VPM pipeline (GEE loaders, feature stack, shared infra)."""
from .utils import BAND_NAMES, BAND_SCHEMA, harmonize_bands
from .era5_land import load_era5_land
from .feature_stack import (build_feature_frame, build_feature_stack, register_extra_source,
                            stack_to_dataframe)
from .hls import load_hls
from .modis_lst import load_modis_lst
from .modis_vi import load_modis_vi
from .smap import load_smap
from .viirs_vi import load_viirs_vi

__all__ = [
    "BAND_NAMES", "BAND_SCHEMA", "harmonize_bands",
    "load_modis_lst", "load_modis_vi", "load_smap", "load_viirs_vi", "load_hls",
    "load_era5_land", "build_feature_stack", "build_feature_frame",
    "register_extra_source", "stack_to_dataframe",
]
