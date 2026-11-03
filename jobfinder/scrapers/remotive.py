"""Remotive public API scraper (no auth).

    https://remotive.com/api/remote-jobs            (all remote jobs)
    https://remotive.com/api/remote-jobs?category=legal

Response: {"jobs": [{id, title, company_name, candidate_required_location,
url, description, publication_date, job_type, category}, ...]}

Remotive is one of the few free remote boards with a real "legal" category, so
it's the main non-tech source here. We pull the profile's categories
(`sources.remotive_categories`, default [legal]; slugs are listed at
/api/remote-jobs/categories) plus a general sweep and keep only Canada-eligible
postings (this is a firehose — filtering at
scrape time keeps the DB small). `job_type` is a v2 light-intensity hook; not
stored in v1.
"""
from __future__ import annotations

import httpx

from ..classify import classify_job
from ..db import connect
from ..profile import load as load_profile
from .common import USER_AGENT, strip_html, upsert_jobs

API_URL = "https://remotive.com/api/remote-jobs"


def _fetch(client: httpx.Client, params: dict) -> list[dict]:
    r = client.get(API_URL, params=params, timeout=30.0)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        location = (j.get("candidate_required_location") or "").strip() or None
        jd = strip_html(j.get("description", ""))
        # firehose: keep only what someone in Canada could take
        if not classify_job(location or "", jd)["ca_eligible"]:
            continue
        out.append({
            "source": "remotive",
            "source_id": str(j["id"]),
            "company": (j.get("company_name") or "").strip() or "(unknown)",
            "title": (j.get("title") or "").strip(),
            "location": location,
            "url": j.get("url"),
            "jd_text": jd,
            "posted_at": j.get("publication_date"),
        })
    return out


def scrape() -> dict:
    """Pull the profile's categories + a general sweep; upsert CA-eligible jobs."""
    conn = connect()
    seen: dict[str, dict] = {}
    pulls = [{"category": c} for c in load_profile().remotive_categories] + [{}]
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        for params in pulls:
            try:
                for job in _fetch(client, params):
                    seen[job["source_id"]] = job        # dedup across the two pulls
            except httpx.HTTPError as e:
                print(f"  [skip] remotive {params or 'all'}: {e}")
    jobs = list(seen.values())
    upsert_jobs(conn, jobs)
    conn.close()
    print(f"  remotive: {len(jobs)} Canada-eligible jobs")
    return {"remotive": len(jobs)}
