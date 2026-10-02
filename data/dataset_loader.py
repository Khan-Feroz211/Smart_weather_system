"""
dataset_loader.py
=================
Thin, offline-safe loader for the bundled research datasets.

Currently ships one dataset:
    * ``agriculture_dataset.csv`` - a 212,019-row remote-sensing / weather /
      soil crop-health dataset with columns spanning multispectral indices
      (NDVI, SAVI, Chlorophyll_Content, Leaf_Area_Index), weather
      (Temperature, Humidity, Rainfall, Wind_Speed), soil
      (Soil_Moisture, Soil_pH, Organic_Matter), pest/weed pressure and a
      binary ``Crop_Health_Label`` target for Wheat / Maize / Rice.

The loader makes no network calls; everything is read from the local
``data/`` directory relative to this module, so it is safe to use in the
offline / low-connectivity deployments the rest of the pipeline targets.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import pandas as pd

# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #
# Directory that contains this file -> ``<project_root>/data``
DATA_DIR: Path = Path(__file__).resolve().parent

#: Absolute path to the agriculture crop-health research dataset.
AGRICULTURE_DATASET_PATH: Path = DATA_DIR / "agriculture_dataset.csv"

# --------------------------------------------------------------------------- #
# Dataset schema (kept as module-level constants so other modules can import
# them without loading the 75 MB frame).
# --------------------------------------------------------------------------- #
#: Name of the column the models classify / regress against.
TARGET_COLUMN: str = "Crop_Health_Label"

#: Crops present in ``Crop_Type``.
CROP_TYPES: List[str] = ["Wheat", "Maize", "Rice"]

#: Feature columns (everything except the target).
FEATURE_COLUMNS: List[str] = [
    "High_Resolution_RGB", "Multispectral_Images", "Thermal_Images",
    "Temporal_Images", "Spatial_Resolution", "GPS_Coordinates",
    "Field_Boundaries", "Elevation_Data", "Canopy_Coverage", "NDVI",
    "SAVI", "Chlorophyll_Content", "Leaf_Area_Index",
    "Crop_Stress_Indicator", "Temperature", "Humidity", "Rainfall",
    "Wind_Speed", "Soil_Moisture", "Soil_pH", "Organic_Matter",
    "Pest_Hotspots", "Weed_Coverage", "Pest_Damage", "Crop_Growth_Stage",
    "Expected_Yield", "Crop_Type", "Ground_Truth_Segmentation",
    "Bounding_Boxes", "Water_Flow", "Drainage_Features",
]

# Image / segmentation flag columns are kept numeric (0/1) but listed
# separately so downstream feature engineering can drop them if needed.
IMAGE_FLAG_COLUMNS: List[str] = [
    "High_Resolution_RGB", "Multispectral_Images", "Thermal_Images",
    "Temporal_Images", "Ground_Truth_Segmentation", "Bounding_Boxes",
]


def agriculture_dataset_exists() -> bool:
    """Return ``True`` if the CSV is present on disk."""
    return AGRICULTURE_DATASET_PATH.is_file()


def load_agriculture_dataset(
    path: os.PathLike | str | None = None,
    *,
    columns: List[str] | None = None,
    crop_type: str | None = None,
    sample: int | None = None,
    random_state: int = 42,
) -> pd.DataFrame:
    """
    Load the agriculture crop-health research dataset.

    Parameters
    ----------
    path:
        Optional override for the CSV location. Defaults to the bundled
        ``data/agriculture_dataset.csv``.
    columns:
        Optional list of columns to read (enables a memory-efficient
        column subset via ``usecols``).
    crop_type:
        If given, filter to a single crop ("Wheat" | "Maize" | "Rice").
    sample:
        If given, return at most this many rows (useful for quick local
        iteration / tests without loading the full 75 MB frame).
    random_state:
        Seed for deterministic subsampling.

    Returns
    -------
    pandas.DataFrame
        The (optionally filtered / sampled) dataset.

    Raises
    ------
    FileNotFoundError
        If the dataset file is not present on disk.
    """
    csv_path = Path(path) if path else AGRICULTURE_DATASET_PATH
    if not csv_path.is_file():
        raise FileNotFoundError(
            f"Dataset not found at {csv_path}. Run `git lfs pull` to "
            "materialise the Git-LFS-backed data files."
        )

    read_kwargs: Dict = {"usecols": columns} if columns else {}
    df = pd.read_csv(csv_path, **read_kwargs)

    if crop_type is not None:
        if crop_type not in CROP_TYPES:
            raise ValueError(
                f"Unknown crop_type={crop_type!r}. "
                f"Expected one of {CROP_TYPES}."
            )
        df = df[df["Crop_Type"] == crop_type].reset_index(drop=True)

    if sample is not None and sample < len(df):
        df = df.sample(n=sample, random_state=random_state).reset_index(drop=True)

    return df


def dataset_info() -> Dict:
    """
    Return a lightweight summary of the agriculture dataset without
    necessarily reading the whole frame when only ``sample`` rows are needed.

    The summary is cached on the module level after the first full scan.
    """
    import structlog

    logger = structlog.get_logger()

    if not agriculture_dataset_exists():
        return {"available": False, "path": str(AGRICULTURE_DATASET_PATH)}

    df = pd.read_csv(AGRICULTURE_DATASET_PATH)
    summary = {
        "available": True,
        "path": str(AGRICULTURE_DATASET_PATH),
        "rows": len(df),
        "columns": df.shape[1],
        "target": TARGET_COLUMN,
        "crop_distribution": df["Crop_Type"].value_counts().to_dict(),
        "label_distribution": df[TARGET_COLUMN].value_counts().to_dict(),
    }
    logger.info("agriculture_dataset_loaded", **{
        k: v for k, v in summary.items() if k != "path"
    })
    return summary


if __name__ == "__main__":
    # Quick manual smoke check: `python -m data.dataset_loader`
    info = dataset_info()
    for k, v in info.items():
        print(f"{k}: {v}")
