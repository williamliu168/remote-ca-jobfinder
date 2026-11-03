"""Deterministic résumé-fit scoring (no LLM).

The active profile's `fit:` block defines a weighted skills VOCABULARY for the
field — deliberately including skills you may not have, so gaps are visible.
The résumé (a local text/Markdown file, never committed) decides which terms
you HAVE. For each job:

    demand = Σ weight(t)  for vocab terms t the posting asks for
             (a term in the TITLE counts `title_weight` times)
    match  = Σ weight(t)  for those terms that the résumé also shows
    fit    = 100 × (match / demand) × min(1, match / saturation)
             + seniority adjustment, clamped to 0..100

`match / demand` is coverage (how much of what they ask for you have);
`min(1, match / saturation)` keeps a posting that names one matching term from
scoring 100. A `dealbreaker` pattern (e.g. "security clearance") forces fit to 0
and records a flag. Results land in fit_score / fit_hits / fit_gaps / fit_flags.

Profile shape:

    fit:
      resume: ../path/to/resume.md      # relative to the profile file
      saturation: 15
      title_weight: 2
      skills:
        - {term: python, weight: 3}
        - {term: "c++", weight: 2, patterns: ['c\\+\\+']}
        - {term: gcp, weight: 2, keywords: [gcp, google cloud, bigquery]}
      seniority:
        - {pattern: '\\b(senior|staff|principal)\\b', points: 5}
        - {pattern: '\\b(junior|intern)\\b', points: -30}
      dealbreakers:
        - {label: clearance, pattern: 'security clearance'}
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .db import connect
from .profile import Profile
from .profile import load as load_profile

DEFAULT_SATURATION = 15.0
DEFAULT_TITLE_WEIGHT = 2.0


@dataclass
class Skill:
    term: str
    weight: float
    # One (anchor, regex) per keyword/pattern. The anchor is a lowercase literal
    # that must appear in the text before its (lookaround-heavy, slower) regex
    # runs; None = always run (raw patterns). ~10x faster on long postings.
    parts: list[tuple[str | None, re.Pattern]]

    def search(self, text: str, lowered: str) -> bool:
        return any((a is None or a in lowered) and rx.search(text) for a, rx in self.parts)


@dataclass
class Rule:
    label: str
    regex: re.Pattern
    points: float = 0.0


@dataclass
class FitModel:
    skills: list[Skill]
    have: set[str]                 # terms the résumé shows
    seniority: list[Rule]
    dealbreakers: list[Rule]
    saturation: float = DEFAULT_SATURATION
    title_weight: float = DEFAULT_TITLE_WEIGHT


def keyword_pattern(kw: str) -> str:
    """Whole-word keyword regex where spaces and hyphens are interchangeable,
    so 'market data' also matches 'market-data' and 'real-time' matches 'real time'."""
    # hyphen first: the space replacement itself contains '\-'
    esc = re.escape(str(kw)).replace(r"\-", r"[\s\-]?").replace(r"\ ", r"[\s\-]+")
    return rf"(?<!\w){esc}(?!\w)"


def _anchor(kw: str) -> str:
    """Longest space/hyphen-free chunk of a keyword, lowercased (always a substring
    of any text the keyword's regex matches)."""
    return max(re.split(r"[\s\-]+", str(kw).lower()), key=len)


def compile_skills(specs: list[dict]) -> list[Skill]:
    out = []
    for s in specs or []:
        term = str(s["term"])
        kws = s.get("keywords")
        pats = list(s.get("patterns") or [])
        if not kws and not pats:
            kws = [term]
        parts = [(None, re.compile(p, re.I)) for p in pats]
        parts += [(_anchor(k), re.compile(keyword_pattern(k), re.I)) for k in kws or []]
        if parts:
            out.append(Skill(term=term, weight=float(s.get("weight", 1)), parts=parts))
    return out


def _rules(specs: list[dict]) -> list[Rule]:
    return [Rule(label=str(r.get("label") or r["pattern"]),
                 regex=re.compile(r["pattern"], re.I),
                 points=float(r.get("points", 0)))
            for r in specs or []]


def resume_path(profile: Profile, override: str | None = None) -> Path | None:
    """--resume beats JOBFINDER_RESUME beats the profile's fit.resume.

    --resume and JOBFINDER_RESUME resolve against the working directory; the
    profile's fit.resume resolves against the profile file's directory.
    """
    raw = override or os.environ.get("JOBFINDER_RESUME")
    if raw:
        return Path(raw).expanduser().resolve()
    raw = (profile.fit or {}).get("resume")
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (profile.path.parent / p)


def build_model(profile: Profile, resume_text: str) -> FitModel:
    fit = profile.fit or {}
    skills = compile_skills(fit.get("skills"))
    if not skills:
        raise ValueError(f"profile '{profile.name}' has no fit.skills vocabulary")
    text = resume_text or ""
    have = {s.term for s in skills if s.search(text, text.lower())}
    return FitModel(
        skills=skills,
        have=have,
        seniority=_rules(fit.get("seniority")),
        dealbreakers=_rules(fit.get("dealbreakers")),
        saturation=float(fit.get("saturation", DEFAULT_SATURATION)),
        title_weight=float(fit.get("title_weight", DEFAULT_TITLE_WEIGHT)),
    )


def score_job(title: str, jd_text: str, model: FitModel) -> dict:
    """Score one posting. Pure function: no DB, no I/O."""
    title = title or ""
    body = jd_text or ""
    demand = match = 0.0
    hits: list[tuple[float, str]] = []
    gaps: list[tuple[float, str]] = []
    title_lc, body_lc = title.lower(), body.lower()
    for s in model.skills:
        in_title = s.search(title, title_lc)
        if not (in_title or s.search(body, body_lc)):
            continue
        w = s.weight * (model.title_weight if in_title else 1.0)
        demand += w
        if s.term in model.have:
            match += w
            hits.append((w, s.term))
        else:
            gaps.append((w, s.term))

    flags: list[str] = []
    if demand > 0 and model.saturation > 0:
        fit = 100.0 * (match / demand) * min(1.0, match / model.saturation)
    else:
        fit = 0.0
    for r in model.seniority:
        if r.regex.search(title):
            fit += r.points
            flags.append(f"{r.label} {r.points:+g}")
    text = f"{title}\n{body}"
    for r in model.dealbreakers:
        if r.regex.search(text):
            flags.append(f"DEALBREAKER: {r.label}")
            fit = 0.0
    fit = max(0.0, min(100.0, fit))

    def _fmt(items):
        return ", ".join(t for _, t in sorted(items, key=lambda x: (-x[0], x[1])))

    return {
        "fit_score": int(round(fit)),
        "fit_hits": _fmt(hits) or None,
        "fit_gaps": _fmt(gaps) or None,
        "fit_flags": "; ".join(flags) or None,
    }


def score_all(resume: str | None = None, eligible_only: bool = True) -> dict:
    """Score jobs in the active profile's DB against the résumé. Returns a summary."""
    profile = load_profile()
    rpath = resume_path(profile, resume)
    if rpath is None:
        raise ValueError("no resume configured: set fit.resume in the profile, "
                         "JOBFINDER_RESUME, or pass --resume")
    if not rpath.exists():
        raise FileNotFoundError(f"resume not found: {rpath}")
    model = build_model(profile, rpath.read_text(encoding="utf-8", errors="replace"))

    conn = connect()
    where = "WHERE ca_eligible = 1" if eligible_only else ""
    rows = conn.execute(f"SELECT id, title, jd_text FROM jobs {where}").fetchall()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    buckets = {"80+": 0, "60-79": 0, "40-59": 0, "<40": 0}
    breakers = 0
    for r in rows:
        out = score_job(r["title"], r["jd_text"], model)
        conn.execute(
            "UPDATE jobs SET fit_score=:fit_score, fit_hits=:fit_hits, fit_gaps=:fit_gaps, "
            "fit_flags=:fit_flags, scored_at=:ts WHERE id=:id",
            {**out, "ts": now, "id": r["id"]},
        )
        f = out["fit_score"]
        buckets["80+" if f >= 80 else "60-79" if f >= 60 else "40-59" if f >= 40 else "<40"] += 1
        breakers += "DEALBREAKER" in (out["fit_flags"] or "")
    conn.commit()
    conn.close()
    return {
        "scored": len(rows),
        "resume": str(rpath),
        "have": sorted(model.have),
        "missing": sorted(s.term for s in model.skills if s.term not in model.have),
        "buckets": buckets,
        "dealbreakers": breakers,
    }
