"""Loads config/datasets.yaml with ${VAR} environment expansion."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "datasets.yaml"
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _VAR.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    with open(path or DEFAULT_PATH, "r", encoding="utf-8") as fh:
        return _expand(yaml.safe_load(fh) or {})
