"""Markdown shortlist generator (re-runnable, no LLM).

Writes shortlist.md (default profile) or shortlist.<profile-name>.md at the repo
root, organized so the most relevant roles are on top. The "surfaced" section is
whatever the active profile matches (see profile.yaml); nothing is silently
dropped — non-Canada-eligible jobs are relegated to a compact count table at the
bottom.

Sections:
  0. 🎯  Top by fit — best résumé fit among surfaced roles, recent only
         (only when `score` has run; see jobfinder/score.py)
  1. ⭐  Surfaced — profile matches (Canada-eligible)        [the primary list]
  2. 🏠  Remote — Canada (remote-ca, all roles)
  3. 🌍  Remote — global / worldwide (CA-eligible)
  4. 🏙️  Canada — onsite / hybrid
  5. 🗂️  Everything else (not Canada-eligible)  — relegated count-by-location
"""
from __future__ import annotations

import datetime
from pathlib import Path

from .db import connect
from .profile import is_surfaced
from .profile import load as load_profile

REPO_ROOT = Path(__file__).parent.parent
OUT = REPO_ROOT / "shortlist.md"
TOP_N = 25

_REMOTE_RANK = {"remote-ca": 0, "remote-global": 1, "remote-multi": 1, "hybrid": 2, "onsite": 3}


def out_path(profile) -> Path:
    return OUT if profile.is_default else REPO_ROOT / f"shortlist.{profile.name}.md"


def _cell(s, n=44):
    return (s or "").replace("|", "/").replace("\n", " ")[:n]


def _mark(role_tag: str) -> str:
    return "⭐ " if is_surfaced(role_tag) else ""


def posted_date(value: str | None) -> datetime.date | None:
    """Best-effort parse of the sources' posted_at strings (ISO-8601 variants)."""
    if not value:
        return None
    v = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(v).date()
    except ValueError:
        try:
            return datetime.date.fromisoformat(v[:10])
        except ValueError:
            return None


def _fit(r) -> str:
    return "" if r["fit_score"] is None else str(r["fit_score"])


def basic(items, with_fit: bool = False) -> list[str]:
    if with_fit:
        out = ["| Fit | Company | Title | Location | Remote | Role | Link |",
               "|---|---|---|---|---|---|---|"]
    else:
        out = ["| Company | Title | Location | Remote | Role | Link |",
               "|---|---|---|---|---|---|"]
    for r in items:
        row = (f"| {_cell(r['company'], 28)} | {_mark(r['role_tag'])}{_cell(r['title'])} | "
               f"{_cell(r['location'], 26)} | {r['remote_policy'] or '?'} | {r['role_tag'] or '?'} | "
               f"[link]({r['url']}) |")
        out.append(f"| {_fit(r)} {row}" if with_fit else row)
    if not items:
        out.append("| _(none)_ |  |  |  |  |  |" + (" |" if with_fit else ""))
    return out


def top_by_fit(items, since_days: int, today: datetime.date) -> list:
    """Surfaced, scored, no dealbreaker, posted within `since_days` (unknown date kept)."""
    keep = []
    for r in items:
        if r["fit_score"] is None or "DEALBREAKER" in (r["fit_flags"] or ""):
            continue
        d = posted_date(r["posted_at"])
        if since_days and d is not None and (today - d).days > since_days:
            continue
        keep.append(r)
    keep.sort(key=lambda r: (-(r["fit_score"] or 0), -(posted_date(r["posted_at"]) or datetime.date.min).toordinal(),
                             r["company"] or ""))
    return keep[:TOP_N]


def top_table(items) -> list[str]:
    out = ["| Fit | # | Company | Title | Remote | Posted | Hits | Gaps |",
           "|---|---|---|---|---|---|---|---|"]
    for r in items:
        d = posted_date(r["posted_at"])
        out.append(
            f"| **{r['fit_score']}** | {r['id']} | {_cell(r['company'], 22)} | "
            f"[{_cell(r['title'], 50)}]({r['url']}) | {r['remote_policy'] or '?'} | "
            f"{d or '?'} | {_cell(r['fit_hits'], 60)} | {_cell(r['fit_gaps'], 40)} |"
        )
    return out if items else ["| _(none)_ |  |  |  |  |  |  |  |"]


def main(since_days: int = 30) -> None:
    profile = load_profile()
    con = connect()
    rows = con.execute(
        "SELECT id, company, title, location, url, country, remote_policy, role_tag, ca_eligible, "
        "posted_at, fit_score, fit_hits, fit_gaps, fit_flags FROM jobs"
    ).fetchall()
    con.close()

    surfaced, remote_ca, remote_global, ca_onsite, relegated = [], [], [], [], []
    for r in rows:
        if not r["ca_eligible"]:
            relegated.append(r)
            continue
        if is_surfaced(r["role_tag"]):
            surfaced.append(r)
        rp = r["remote_policy"]
        if rp == "remote-ca":
            remote_ca.append(r)
        elif rp in ("remote-global", "remote-multi"):
            remote_global.append(r)
        elif r["country"] == "CA" and rp in ("onsite", "hybrid"):
            ca_onsite.append(r)

    scored = any(r["fit_score"] is not None for r in surfaced)
    if scored:
        surfaced.sort(key=lambda r: (-(r["fit_score"] or 0), _REMOTE_RANK.get(r["remote_policy"], 9),
                                     r["company"] or "", r["title"] or ""))
    else:
        surfaced.sort(key=lambda r: (_REMOTE_RANK.get(r["remote_policy"], 9),
                                     r["company"] or "", r["title"] or ""))
    for lst in (remote_ca, remote_global, ca_onsite):
        lst.sort(key=lambda r: (r["company"] or "", r["title"] or ""))

    tags = ", ".join(profile.surface_tags) or "—"
    today = datetime.date.today()
    L = [
        "# Remote-Canada job shortlist", "",
        f"_Generated {today} from {len(rows)} scraped postings (rule-based; no LLM). "
        f"Profile **{profile.name}** surfaces **{profile.label}** roles on top; "
        f"nothing is dropped. Browse/triage with `python -m jobfinder.cli serve`._", "",
        f"**Counts:** ⭐ {profile.label} {len(surfaced)} · 🏠 remote-CA {len(remote_ca)} · "
        f"🌍 remote-global {len(remote_global)} · 🏙️ CA onsite/hybrid {len(ca_onsite)} · "
        f"🗂️ relegated (not CA-eligible) {len(relegated)}", "",
    ]
    if scored:
        window = f"posted in the last {since_days} days (undated kept)" if since_days else "any date"
        L += [f"## 🎯 Top {TOP_N} by résumé fit", "",
              f"_Surfaced, Canada-eligible, no dealbreaker, {window}. Fit is deterministic "
              f"(jobfinder/score.py): hits = asked-for skills the résumé shows; gaps = asked-for "
              f"skills it doesn't. `show <#>` for the full posting._", ""]
        L += top_table(top_by_fit(surfaced, since_days, today)) + [""]

    sort_note = ("sorted by résumé fit" if scored
                 else "sorted remote-CA → remote-global → hybrid/onsite")
    L += [
        f"## ⭐ {profile.label} — surfaced (Canada-eligible)", "",
        f"_The primary list: titles matching the profile ({tags}), {sort_note}._", "",
    ] + basic(surfaced, with_fit=scored)

    L += ["", "## 🏠 Remote — Canada (all roles)", ""] + basic(remote_ca)
    L += ["", "## 🌍 Remote — global / worldwide (Canada-eligible)", ""] + basic(remote_global)
    L += ["", "## 🏙️ Canada — onsite / hybrid", ""] + basic(ca_onsite)

    L += ["", f"## 🗂️ Everything else — not Canada-eligible ({len(relegated)})", ""]
    if relegated:
        by_loc: dict[str, int] = {}
        for r in relegated:
            key = (r["location"] or "?").strip()[:40] or "?"
            by_loc[key] = by_loc.get(key, 0) + 1
        L += ["_Relegated, not dropped — browse with `serve` (filter view=all). "
              "Counts by location:_", "", "| Location | Count |", "|---|---|"]
        L += [f"| {k.replace('|', '/')} | {n} |"
              for k, n in sorted(by_loc.items(), key=lambda kv: -kv[1])[:50]]
    else:
        L.append("_(none)_")

    dest = out_path(profile)
    dest.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"surfaced={len(surfaced)} remote-ca={len(remote_ca)} remote-global={len(remote_global)} "
          f"ca-onsite={len(ca_onsite)} relegated={len(relegated)} -> {dest}")


if __name__ == "__main__":
    main()
