"""jobfinder command line.

    python -m jobfinder.cli scrape --curated      # Greenhouse + Lever + Ashby from the company list
    python -m jobfinder.cli scrape --apis         # Remotive + RemoteOK (profile + CA-filtered)
    python -m jobfinder.cli scrape --source greenhouse --companies stripe,gitlab
    python -m jobfinder.cli verify                # check curated slugs are live
    python -m jobfinder.cli classify              # tag country / remote / role
    python -m jobfinder.cli score                 # résumé-fit score (profile's fit: block)
    python -m jobfinder.cli report --since 30     # write shortlist.md
    python -m jobfinder.cli serve                 # browse at http://127.0.0.1:5050
    python -m jobfinder.cli list --view surfaced --limit 40
    python -m jobfinder.cli show 123
    python -m jobfinder.cli status 123 applied

Pick a non-default profile with `--profile PATH` (before the command) or the
JOBFINDER_PROFILE environment variable, e.g.

    python -m jobfinder.cli --profile profiles/fintech-ai-eng.yaml scrape --curated --apis
"""
from __future__ import annotations

import argparse
import sys

import httpx

from . import classify as classify_mod
from . import companies as companies_mod
from . import profile as profile_mod
from . import report as report_mod
from . import score as score_mod
from .db import connect
from .profile import is_surfaced
from .scrapers import ashby, greenhouse, lever, remoteok, remotive
from .scrapers.common import USER_AGENT

VALID_STATUSES = ("seen", "shortlist", "applied", "interviewing", "closed", "rejected")
CURATED_PLATFORMS = ("greenhouse", "lever", "ashby")


def cmd_scrape(args) -> None:
    did = False
    if args.curated or args.source in CURATED_PLATFORMS:
        groups = companies_mod.grouped(tag=args.tag)
        if args.source in CURATED_PLATFORMS:
            groups = {args.source: groups.get(args.source, [])}
        if args.companies:
            wanted = {c.strip().lower() for c in args.companies.split(",")}
            groups = {p: [(s, d) for (s, d) in lst if s.lower() in wanted]
                      for p, lst in groups.items()}
        if groups.get("greenhouse"):
            print("Greenhouse:")
            greenhouse.scrape(groups["greenhouse"])
            did = True
        if groups.get("lever"):
            print("Lever:")
            lever.scrape(groups["lever"])
            did = True
        if groups.get("ashby"):
            print("Ashby:")
            ashby.scrape(groups["ashby"])
            did = True
    if args.apis or args.source == "remotive":
        print("Remotive:")
        remotive.scrape()
        did = True
    if args.apis or args.source == "remoteok":
        print("RemoteOK:")
        remoteok.scrape()
        did = True
    if not did:
        print("Nothing scraped. Use --curated, --apis, or --source <name>.")
        return
    conn = connect()
    total = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    conn.close()
    print(f"\nDB now holds {total} jobs. Next: python -m jobfinder.cli classify")


def cmd_verify(args) -> None:
    """Probe curated Greenhouse/Lever/Ashby slugs to confirm the boards exist."""
    groups = companies_mod.grouped(enabled_only=False, tag=args.tag)
    urls = {
        "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        "lever": "https://api.lever.co/v0/postings/{slug}",
        "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    }
    params = {"greenhouse": {"content": "true"}, "lever": {"mode": "json"}, "ashby": {}}
    dead = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        for platform, items in groups.items():
            if platform not in urls:
                continue
            for slug, _display in items:
                try:
                    r = client.get(urls[platform].format(slug=slug),
                                   params=params[platform], timeout=20.0)
                    n = (len(r.json().get("jobs", [])) if platform in ("greenhouse", "ashby")
                         else len(r.json())) if r.status_code == 200 else 0
                    flag = "ok " if r.status_code == 200 else "DEAD"
                    print(f"  [{flag}] {platform}:{slug}  HTTP {r.status_code}  {n} jobs")
                    if r.status_code != 200:
                        dead.append(f"{platform}:{slug}")
                except httpx.HTTPError as e:
                    print(f"  [DEAD] {platform}:{slug}  {e}")
                    dead.append(f"{platform}:{slug}")
    if dead:
        print(f"\n{len(dead)} dead slug(s): {', '.join(dead)}")
        print(f"Fix or set enabled:false in {companies_mod.config_path().name} before committing.")
    else:
        print("\nAll curated slugs are live.")


def cmd_classify(args) -> None:
    out = classify_mod.classify_all(force=args.force)
    print(f"Classified {out['total']} jobs · {out['ca_eligible']} Canada-eligible")
    print(f"  by role:    {out['by_role']}")
    print(f"  by remote:  {out['by_remote']}")
    print(f"  by country: {out['by_country']}")


def cmd_score(args) -> None:
    out = score_mod.score_all(resume=args.resume, eligible_only=not args.all)
    print(f"Scored {out['scored']} jobs against {out['resume']}")
    print(f"  resume shows:  {', '.join(out['have']) or '(none)'}")
    print(f"  not shown:     {', '.join(out['missing']) or '(none)'}")
    print(f"  fit buckets:   {out['buckets']}   dealbreakers: {out['dealbreakers']}")


def cmd_report(args) -> None:
    report_mod.main(since_days=args.since)


def cmd_serve(args) -> None:
    from . import web
    web.main(host=args.host, port=args.port, debug=args.debug)


def _view_clause(view: str) -> tuple[str, list]:
    if view == "surfaced":
        return ("ca_eligible = 1 AND role_tag != 'other'", [])
    if view == "remote-ca":
        return ("ca_eligible = 1 AND remote_policy = 'remote-ca'", [])
    if view == "remote-global":
        return ("ca_eligible = 1 AND remote_policy IN ('remote-global','remote-multi')", [])
    if view == "ca-onsite":
        return ("ca_eligible = 1 AND country = 'CA' AND remote_policy IN ('onsite','hybrid')", [])
    return ("1=1", [])


def cmd_list(args) -> None:
    conn = connect()
    where, params = [], []
    clause, p = _view_clause(args.view)
    where.append(clause); params += p
    if args.status:
        where.append("status = ?"); params.append(args.status)
    if args.company:
        where.append("company LIKE ?"); params.append(f"%{args.company}%")
    order = ("fit_score IS NULL, fit_score DESC, company, title" if args.sort == "fit"
             else "(role_tag != 'other') DESC, company, title")
    sql = ("SELECT id, company, title, location, remote_policy, role_tag, status, fit_score "
           "FROM jobs WHERE " + " AND ".join(where) + f" ORDER BY {order} LIMIT ?")
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    if not rows:
        print("(no matching jobs — run scrape + classify first?)")
        return
    for r in rows:
        mark = "⭐ " if is_surfaced(r["role_tag"]) else "  "
        fit = "" if r["fit_score"] is None else f"{r['fit_score']:>3} "
        print(f"{mark}{fit}#{r['id']:<5} [{r['status']:<7}] {(r['company'] or '')[:22]:<22} "
              f"{(r['title'] or '')[:46]:<46} {(r['remote_policy'] or ''):<13} {r['location'] or ''}")
    print(f"\n{len(rows)} shown.")


def cmd_show(args) -> None:
    conn = connect()
    j = conn.execute("SELECT * FROM jobs WHERE id = ?", (args.id,)).fetchone()
    conn.close()
    if not j:
        print(f"No job #{args.id}"); sys.exit(1)
    print(f"#{j['id']}  {j['title']}")
    print(f"  company:  {j['company']}")
    print(f"  location: {j['location']}   ({j['source']})")
    print(f"  remote:   {j['remote_policy']}   country={j['country']}   "
          f"role={j['role_tag']}   ca_eligible={'yes' if j['ca_eligible'] else 'no'}")
    print(f"  status:   {j['status']}   posted={j['posted_at'] or '?'}")
    if j["fit_score"] is not None:
        print(f"  fit:      {j['fit_score']}   flags: {j['fit_flags'] or '-'}")
        print(f"  hits:     {j['fit_hits'] or '-'}")
        print(f"  gaps:     {j['fit_gaps'] or '-'}")
    print(f"  url:      {j['url']}")
    print("\n" + (j["jd_text"] or "(no JD text)")[:3000])


def cmd_status(args) -> None:
    if args.new not in VALID_STATUSES:
        print(f"Invalid status. Choose: {', '.join(VALID_STATUSES)}"); sys.exit(1)
    conn = connect()
    cur = conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (args.new, args.id))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        print(f"No job #{args.id}"); sys.exit(1)
    print(f"#{args.id} → {args.new}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobfinder", description="Profile-driven remote-Canada job finder")
    p.add_argument("--profile", help="profile YAML (default: $JOBFINDER_PROFILE or profile.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape", help="fetch postings into the DB")
    s.add_argument("--curated", action="store_true",
                   help="scrape Greenhouse+Lever+Ashby from the profile's company list")
    s.add_argument("--apis", action="store_true", help="scrape Remotive + RemoteOK (CA-filtered)")
    s.add_argument("--source", choices=[*CURATED_PLATFORMS, "remotive", "remoteok"],
                   help="scrape a single source")
    s.add_argument("--tag", help="limit curated companies to a tag")
    s.add_argument("--companies", help="comma-separated slugs (with --curated/--source)")
    s.set_defaults(func=cmd_scrape)

    v = sub.add_parser("verify", help="check curated slugs are live")
    v.add_argument("--tag", help="limit to a tag")
    v.set_defaults(func=cmd_verify)

    c = sub.add_parser("classify", help="tag country/remote/role")
    c.add_argument("--force", action="store_true", help="re-classify all (not just new)")
    c.set_defaults(func=cmd_classify)

    sc = sub.add_parser("score", help="resume-fit score Canada-eligible jobs")
    sc.add_argument("--resume", help="resume file (default: $JOBFINDER_RESUME or the profile's fit.resume)")
    sc.add_argument("--all", action="store_true", help="also score jobs that aren't Canada-eligible")
    sc.set_defaults(func=cmd_score)

    rp = sub.add_parser("report", help="write shortlist.md")
    rp.add_argument("--since", type=int, default=30,
                    help="Top-by-fit section: only jobs posted within N days (0 = no limit)")
    rp.set_defaults(func=cmd_report)

    sv = sub.add_parser("serve", help="browse in a web UI")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=5050)
    sv.add_argument("--debug", action="store_true")
    sv.set_defaults(func=cmd_serve)

    ls = sub.add_parser("list", help="list jobs in the terminal")
    ls.add_argument("--view", choices=["surfaced", "remote-ca", "remote-global", "ca-onsite", "all"],
                    default="surfaced")
    ls.add_argument("--status", choices=VALID_STATUSES)
    ls.add_argument("--company")
    ls.add_argument("--limit", type=int, default=50)
    ls.add_argument("--sort", choices=["role", "fit"], default="role")
    ls.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="show one job")
    sh.add_argument("id", type=int)
    sh.set_defaults(func=cmd_show)

    st = sub.add_parser("status", help="set a job's status")
    st.add_argument("id", type=int)
    st.add_argument("new", help=f"one of: {', '.join(VALID_STATUSES)}")
    st.set_defaults(func=cmd_status)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    if args.profile:
        profile_mod.set_active(args.profile)
    args.func(args)


if __name__ == "__main__":
    main()
