"""Ashby public job board scraper (no auth).

    https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true

Many AI and fintech startups host their boards on Ashby. Response:
{"jobs": [{id, title, location, secondaryLocations[{location, address}],
isRemote, workplaceType, publishedAt, jobUrl, isListed, descriptionPlain,
descriptionHtml, compensation{compensationTierSummary, ...}}, ...]}

The primary `location` is often an HQ city while the Canada signal lives in
`secondaryLocations` (e.g. "Remote (Canada)"), so the location string we store
joins all of them; that is what jobfinder/classify.py reads.
"""
from __future__ import annotations

from typing import Iterable

import httpx

from ..db import connect
from .common import USER_AGENT, strip_html, upsert_jobs

API_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def _location(j: dict) -> str | None:
    parts: list[str] = []
    for loc in [j.get("location")] + [s.get("location") for s in (j.get("secondaryLocations") or [])]:
        loc = (loc or "").strip()
        if loc and loc not in parts:
            parts.append(loc)
    text = "; ".join(parts)
    workplace = (j.get("workplaceType") or "").lower()
    if j.get("isRemote") or workplace == "remote":
        if "remote" not in text.lower():
            text = f"{text}; Remote" if text else "Remote"
    elif workplace == "hybrid" and "hybrid" not in text.lower():
        text = f"{text} (Hybrid)" if text else "Hybrid"
    return text or None


def parse_board(payload: dict, slug: str, display_name: str | None = None) -> list[dict]:
    """Normalize an Ashby job-board payload into jobfinder job dicts."""
    company = display_name or slug.replace("-", " ").replace("_", " ").title()
    jobs = []
    for j in payload.get("jobs", []):
        if j.get("isListed") is False:
            continue
        jd = (j.get("descriptionPlain") or "").strip() or strip_html(j.get("descriptionHtml", ""))
        pay = ((j.get("compensation") or {}).get("compensationTierSummary") or "").strip()
        if pay:
            jd = f"Compensation: {pay}\n\n{jd}"
        jobs.append({
            "source": "ashby",
            "source_id": str(j["id"]),
            "company": company,
            "title": (j.get("title") or "").strip(),
            "location": _location(j),
            "url": j.get("jobUrl") or j.get("applyUrl"),
            "jd_text": jd,
            "posted_at": j.get("publishedAt"),
        })
    return jobs


def fetch_board(slug: str, client: httpx.Client, display_name: str | None = None) -> list[dict]:
    """Fetch all listed jobs for one Ashby job-board name."""
    r = client.get(API_URL.format(slug=slug), params={"includeCompensation": "true"}, timeout=30.0)
    r.raise_for_status()
    return parse_board(r.json(), slug, display_name)


def scrape(slugs: Iterable[str | tuple[str, str]]) -> dict:
    """Fetch boards for each slug, upsert into DB. Returns {slug: count}."""
    conn = connect()
    counts: dict[str, int] = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        for item in slugs:
            slug, display = item if isinstance(item, tuple) else (item, None)
            slug = (slug or "").strip()
            if not slug:
                continue
            try:
                jobs = fetch_board(slug, client, display_name=display)
            except httpx.HTTPStatusError as e:
                print(f"  [skip] ashby:{slug}: HTTP {e.response.status_code}")
                counts[slug] = 0
                continue
            except httpx.HTTPError as e:
                print(f"  [skip] ashby:{slug}: {e}")
                counts[slug] = 0
                continue
            upsert_jobs(conn, jobs)
            counts[slug] = len(jobs)
            print(f"  ashby:{slug}: {len(jobs)} jobs")
    conn.close()
    return counts
