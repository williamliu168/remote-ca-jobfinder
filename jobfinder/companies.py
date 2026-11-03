"""Load the active profile's company list and group slugs per platform.

Platforms: greenhouse | lever | ashby. The file defaults to companies.yaml; a
profile can point elsewhere with its `companies:` key.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .profile import load as load_profile

CONFIG_PATH = Path(__file__).parent.parent / "companies.yaml"   # default list


def config_path() -> Path:
    """The company list for the active profile."""
    return load_profile().companies_path


def load(enabled_only: bool = True, tag: str | None = None) -> list[dict]:
    """Return the list of company records (optionally filtered by enabled/tag)."""
    path = config_path()
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rows = data.get("companies") or []
    if enabled_only:
        rows = [r for r in rows if r.get("enabled", True)]
    if tag:
        rows = [r for r in rows if tag in (r.get("tags") or [])]
    return rows


def grouped(enabled_only: bool = True, tag: str | None = None) -> dict[str, list[tuple[str, str | None]]]:
    """Return {platform: [(slug, display_name_or_None), ...]}."""
    out: dict[str, list[tuple[str, str | None]]] = {}
    for r in load(enabled_only=enabled_only, tag=tag):
        platform = (r.get("platform") or "").lower()
        slug = r.get("slug")
        if not platform or not slug:
            continue
        out.setdefault(platform, []).append((slug, r.get("display_name")))
    return out
