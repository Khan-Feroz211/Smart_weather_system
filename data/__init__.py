"""
data package
============
Project datasets and dataset loaders.

Assets
------
- ``data/agriculture_dataset.csv`` 75 MB / ~212k-row remote-sensing + weather +
  soil crop-health dataset (Wheat / Maize / Rice). Target column is
  ``Crop_Health_Label`` (binary). Used as the research dataset for the
  crop-health / disease-detection tier of the AgriAdvisor pipeline.

The files themselves are tracked with Git LFS (see ``.gitattributes``), so a
plain ``git clone`` followed by ``git lfs pull`` (or the smudge hook) will
materialise them on disk.
"""

from data.dataset_loader import (
    DATA_DIR,
    AGRICULTURE_DATASET_PATH,
    TARGET_COLUMN,
    CROP_TYPES,
    FEATURE_COLUMNS,
    load_agriculture_dataset,
    dataset_info,
)

__all__ = [
    "DATA_DIR",
    "AGRICULTURE_DATASET_PATH",
    "TARGET_COLUMN",
    "CROP_TYPES",
    "FEATURE_COLUMNS",
    "load_agriculture_dataset",
    "dataset_info",
]
