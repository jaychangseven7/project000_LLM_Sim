from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_path(path: str | Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return project_root() / path


def load_yaml(path: str | Path) -> dict[str, Any]:
    full_path = resolve_path(path)
    with full_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data


def ensure_parent(path: str | Path) -> Path:
    full_path = resolve_path(path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    return full_path

