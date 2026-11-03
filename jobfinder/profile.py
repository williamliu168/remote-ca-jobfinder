"""Load the active search profile.

The profile is the business definition — what roles to surface, under what label,
for which location lens, which companies to scrape and (optionally) how to score
fit against a résumé. Everything else in the package is generic infrastructure
that consumes a Profile.

Which profile is active, highest precedence first:
  1. `--profile PATH` on the CLI (calls `set_active()`)
  2. the JOBFINDER_PROFILE environment variable
  3. `profile.yaml` at the repo root (the default)

Relative paths inside a profile (`companies`, `fit.resume`) resolve against the
profile file's own directory.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .roles import Family, compile_families

REPO_ROOT = Path(__file__).parent.parent
PROFILE_PATH = REPO_ROOT / "profile.yaml"          # the default profile
DEFAULT_REMOTIVE_CATEGORIES = ("legal",)


@dataclass
class Profile:
    name: str
    label: str
    location: str
    match: dict
    families: list[Family]
    surface_tags: tuple[str, ...] = field(default_factory=tuple)
    path: Path = PROFILE_PATH
    companies_path: Path = REPO_ROOT / "companies.yaml"
    remotive_categories: tuple[str, ...] = DEFAULT_REMOTIVE_CATEGORIES
    fit: dict = field(default_factory=dict)

    @property
    def is_default(self) -> bool:
        return self.path.resolve() == PROFILE_PATH.resolve()


_cache: Profile | None = None
_active_path: Path | None = None


def set_active(path: str | os.PathLike | None) -> None:
    """Select the profile file for this process (used by the CLI's --profile)."""
    global _active_path, _cache
    _active_path = Path(path).expanduser() if path else None
    _cache = None


def active_path() -> Path:
    if _active_path is not None:
        return _active_path
    env = os.environ.get("JOBFINDER_PROFILE")
    return Path(env).expanduser() if env else PROFILE_PATH


def _resolve(base: Path, value: str | None, default: Path) -> Path:
    if not value:
        return default
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p)


def load(path: Path | None = None) -> Profile:
    """Read and compile the active profile (cached), or `path` if given (uncached)."""
    global _cache
    if _cache is not None and path is None:
        return _cache
    p = Path(path) if path else active_path()
    if not p.exists():
        raise FileNotFoundError(f"Missing profile {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    base = p.resolve().parent
    fam_specs = data.get("families") or []
    families = compile_families(fam_specs, data.get("match"))
    sources = data.get("sources") or {}
    cats = sources.get("remotive_categories")
    prof = Profile(
        name=data.get("name", "default"),
        label=data.get("label", "Surfaced roles"),
        location=(data.get("location") or "canada").lower(),
        match=data.get("match") or {},
        families=families,
        surface_tags=tuple(f.tag for f in families),
        path=p.resolve(),
        companies_path=_resolve(base, data.get("companies"), REPO_ROOT / "companies.yaml"),
        remotive_categories=tuple(cats) if cats is not None else DEFAULT_REMOTIVE_CATEGORIES,
        fit=data.get("fit") or {},
    )
    if path is None:
        _cache = prof
    return prof


def is_surfaced(role_tag: str | None) -> bool:
    """A job is 'surfaced' if its title matched any profile family (tag != 'other')."""
    return bool(role_tag) and role_tag != "other"
