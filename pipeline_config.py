"""
Pipeline configuration for the White Volta Basin FEWS research pipeline.

All scripts in preprocessing/, models/, and validation/ previously hard-coded
the project directory as F:\\white_volta_fews.  This module centralises
that path so the pipeline is portable.

Configuration
-------------
Set the PIPELINE_DATA_DIR environment variable (in .env or your shell)
to point at the root data directory for the White Volta FEWS project.

Example
-------
PIPELINE_DATA_DIR=F:\\white_volta_fews   # Windows
PIPELINE_DATA_DIR=/data/white_volta_fews # Linux/macOS

If the variable is not set, the module falls back to F:\\white_volta_fews
for backward compatibility, and logs a warning.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

_ENV_VAR = "PIPELINE_DATA_DIR"
_DEFAULT = r"F:\white_volta_fews"

PROJECT: str = os.environ.get(_ENV_VAR, _DEFAULT)

if not os.environ.get(_ENV_VAR):
    warnings.warn(
        f"Environment variable '{_ENV_VAR}' is not set. "
        f"Falling back to {_DEFAULT!r}. "
        f"Set {(_ENV_VAR)} in your .env or environment for portability.",
        stacklevel=2,
    )

# Derived paths
RAW = os.path.join(PROJECT, "data", "raw")
PROC = os.path.join(PROJECT, "data", "processed")
MODELS = os.path.join(PROJECT, "models")
OUTPUTS = os.path.join(PROJECT, "outputs")
TMPDIR = os.path.join(PROJECT, "data", "tmp_unzipped")

THRESHOLDS_DIR = os.path.join(OUTPUTS, "thresholds")
SENTINEL1_DIR = os.path.join(OUTPUTS, "sentinel1")
ALERTS_DIR = os.path.join(OUTPUTS, "alerts")
DOCS_DIR = os.path.join(PROJECT, "docs")

START_YEAR = 1985
END_YEAR = 2023
CHIRPS_BASE = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05"
MAX_RETRIES = 3
RETRY_WAIT = 10


def ensure_dirs() -> None:
    """Create all expected output directories if they don't exist."""
    for d in (PROC, MODELS, OUTPUTS, TMPDIR, THRESHOLDS_DIR,
              SENTINEL1_DIR, ALERTS_DIR, DOCS_DIR):
        os.makedirs(d, exist_ok=True)


def get_project_path() -> Path:
    """Return the project root as a pathlib.Path."""
    return Path(PROJECT)
