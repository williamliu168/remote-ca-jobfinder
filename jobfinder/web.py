"""Lightweight web UI for browsing scraped jobs.

    python -m jobfinder.cli serve     # then open http://127.0.0.1:5050

Single file, no JS framework. Browse, filter by view/status/source/search, open
a posting, and mark its status. The "surfaced" view and role badges come from the
active profile (profile.yaml) — this module has no hardcoded role knowledge.
"""
from __future__ import annotations

from flask import Flask, abort, redirect, request, url_for
from markupsafe import Markup, escape

from .db import connect
from .profile import is_surfaced
from .profile import load as load_profile

app = Flask(__name__)
_PROFILE = load_profile()

VALID_STATUSES = ("seen", "shortlist", "applied", "interviewing", "closed", "rejected")
STATUS_COLORS = {
    "seen": "#8a94a6", "shortlist": "#f39c12", "applied": "#3498db",
    "interviewing": "#9b59b6", "closed": "#95a5a6", "rejected": "#e74c3c",
}
VIEWS = {
    "surfaced": f"⭐ {_PROFILE.label}",
    "remote-ca": "🏠 Remote — Canada",
    "remote-global": "🌍 Remote — global",
    "ca-onsite": "🏙️ Canada onsite/hybrid",
    "all": "All (raw)",
}
_PALETTE = ["#6a1b9a", "#00796b", "#b8860b", "#2c6cb3", "#c0392b", "#8e44ad"]
ROLE_COLORS = {tag: _PALETTE[i % len(_PALETTE)] for i, tag in enumerate(_PROFILE.surface_tags)}
ROLE_COLORS["other"] = "#95a5a6"
REGION_BADGE = {
    "remote-ca": ("#27ae60", "remote-CA"), "remote-global": ("#2c6cb3", "remote-global"),
    "remote-multi": ("#2c6cb3", "remote-multi"), "hybrid": ("#16a085", "hybrid"),
    "remote-other": ("#b08", "remote (non-CA)"),
    "onsite": ("#7f8c8d", "onsite"), "unknown": ("#95a5a6", "?"),
}

BASE_CSS = """
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.5 -apple-system, Segoe UI, system-ui, sans-serif;
         max-width: 1100px; margin: 1rem auto; padding: 0 1rem; }
  header { display: flex; justify-content: space-between; align-items: center;
           border-bottom: 1px solid #e0e0e0; padding-bottom: .5rem; margin-bottom: 1rem; }
  h1 { margin: 0; font-size: 1.3rem; }
  nav a { margin-left: 1rem; text-decoration: none; color: #3498db; }
  form.filters { display: flex; gap: .5rem; margin-bottom: 1rem; flex-wrap: wrap; }
  form.filters input, form.filters select, form.filters button {
    padding: .35rem .6rem; border: 1px solid #ccc; border-radius: 4px; font: inherit; }
  form.filters button { background: #3498db; color: #fff; border-color: #3498db; cursor: pointer; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #eee; vertical-align: top; }
  th { font-size: .8rem; text-transform: uppercase; color: #666; letter-spacing: .05em; }
  tr:hover { background: #f7f9fc; }
  .status { display: inline-block; padding: .1rem .5rem; border-radius: 999px;
            color: #fff; font-size: .72rem; text-transform: uppercase; letter-spacing: .05em; }
  .jd { white-space: pre-wrap; background: #f7f9fc; padding: 1rem; border-radius: 6px;
        border: 1px solid #e0e0e0; max-height: 60vh; overflow: auto; }
  .actions { display: flex; gap: .4rem; flex-wrap: wrap; margin: 1rem 0; }
  .actions form { display: inline; }
  .actions button { padding: .35rem .8rem; border: 1px solid #ccc; background: #fff;
                    border-radius: 4px; cursor: pointer; font: inherit; }
  .actions button:hover { background: #eef3f8; }
  .summary { color: #555; margin-bottom: .5rem; }
  a { color: #3498db; }
  a.title { color: inherit; text-decoration: none; }
  a.title:hover { text-decoration: underline; }
  @media (prefers-color-scheme: dark) {
    body { background: #1a1d21; color: #e6e6e6; }
    header, th, td { border-color: #2e333a; }
    .jd, tr:hover { background: #242830; border-color: #2e333a; }
    form.filters input, form.filters select, .actions button { background: #242830; color: #e6e6e6; border-color: #2e333a; }
  }
</style>
"""


def status_pill(status: str) -> Markup:
    color = STATUS_COLORS.get(status, "#888")
    return Markup(f'<span class="status" style="background:{color}">{escape(status)}</span>')


def layout(title: str, body: str) -> str:
    nav = "".join(
        f'<a href="{url_for("index", status=s)}">{escape(s)}</a>'
        for s in ("seen", "shortlist", "applied", "interviewing")
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{escape(title)} · jobfinder</title>{BASE_CSS}</head>
<body>
  <header>
    <h1><a href="{url_for('index')}" style="color:inherit;text-decoration:none">jobfinder</a></h1>
    <nav>{nav}</nav>
  </header>
  {body}
</body></html>"""


def _matches_view(view: str, r) -> bool:
    if view == "all":
        return True
    if not r["ca_eligible"]:
        return False
    if view == "surfaced":
        return is_surfaced(r["role_tag"])
    if view == "remote-ca":
        return r["remote_policy"] == "remote-ca"
    if view == "remote-global":
        return r["remote_policy"] in ("remote-global", "remote-multi")
    if view == "ca-onsite":
        return r["country"] == "CA" and r["remote_policy"] in ("onsite", "hybrid")
    return True


@app.get("/")
def index():
    view = request.args.get("view") or "surfaced"
    if view not in VIEWS:
        view = "surfaced"
    status = (request.args.get("status") or "").strip()
    source = (request.args.get("source") or "").strip()
    q = (request.args.get("q") or "").strip()

    conn = connect()
    sql = ["SELECT id, source, company, title, location, status, url, "
           "country, remote_policy, role_tag, ca_eligible, fit_score FROM jobs"]
    where, params = [], []
    if status:
        where.append("status = ?"); params.append(status)
    if source:
        where.append("source = ?"); params.append(source)
    if q:
        where.append("(title LIKE ? OR company LIKE ? OR jd_text LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY (role_tag != 'other') DESC, fit_score IS NULL, fit_score DESC, "
               "scraped_at DESC LIMIT 5000")
    raw = conn.execute(" ".join(sql), params).fetchall()
    sources = [r["source"] for r in conn.execute(
        "SELECT DISTINCT source FROM jobs ORDER BY source").fetchall()]
    totals = {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall()}
    conn.close()

    rows = [r for r in raw if _matches_view(view, r)]

    body_rows = []
    for r in rows:
        rc = ROLE_COLORS.get(r["role_tag"], "#888")
        role_badge = (f'<span style="background:{rc};color:#fff;padding:.05rem .4rem;'
                      f'border-radius:3px;font-size:.7rem;margin-right:.25rem">'
                      f'{escape(r["role_tag"] or "?")}</span>')
        rcol, rlab = REGION_BADGE.get(r["remote_policy"], ("#888", r["remote_policy"] or "?"))
        region_badge = f'<span class="status" style="background:{rcol}">{escape(rlab)}</span>'
        mark = "⭐ " if is_surfaced(r["role_tag"]) else ""
        fit = "" if r["fit_score"] is None else str(r["fit_score"])
        body_rows.append(f"""
          <tr>
            <td style="text-align:right;font-weight:600">{fit}</td>
            <td>{region_badge}</td>
            <td>{status_pill(r['status'])}</td>
            <td style="color:#888;font-size:.72rem">{escape(r['source'])}</td>
            <td>{escape(r['company'] or '')}</td>
            <td>{role_badge}{mark}<a class="title" href="{url_for('show_job', job_id=r['id'])}">
                {escape(r['title'] or '')}</a></td>
            <td style="color:#888">{escape(r['location'] or '')}</td>
          </tr>""")
    table = ("<p style='color:#888'>(no jobs in this view)</p>" if not rows else
             "<table><thead><tr><th>Fit</th><th>Region</th><th>Status</th><th>Source</th><th>Company</th>"
             "<th>Title</th><th>Location</th></tr></thead><tbody>" + "".join(body_rows) + "</tbody></table>")

    view_opts = "".join(f'<option value="{k}"{" selected" if k == view else ""}>{escape(v)}</option>'
                        for k, v in VIEWS.items())
    status_opts = '<option value="">(any status)</option>' + "".join(
        f'<option value="{s}"{" selected" if s == status else ""}>{s}</option>' for s in VALID_STATUSES)
    source_opts = '<option value="">(any source)</option>' + "".join(
        f'<option value="{s}"{" selected" if s == source else ""}>{s}</option>' for s in sources)
    status_summary = "  ·  ".join(f"{s}: {totals.get(s, 0)}" for s in VALID_STATUSES)

    body = f"""
      <p class="summary"><strong>{len(rows)}</strong> in <em>{escape(VIEWS.get(view, view))}</em>
        &nbsp;|&nbsp; {escape(status_summary)}</p>
      <form class="filters" method="get">
        <label>view <select name="view">{view_opts}</select></label>
        <label>status <select name="status">{status_opts}</select></label>
        <label>source <select name="source">{source_opts}</select></label>
        <input type="search" name="q" value="{escape(q)}" placeholder="search title / company / JD">
        <button type="submit">Filter</button>
        <a href="{url_for('index')}" style="align-self:center">reset</a>
      </form>
      {table}
    """
    return layout("Jobs", body)


@app.get("/job/<int:job_id>")
def show_job(job_id: int):
    conn = connect()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    if job is None:
        abort(404)

    action_buttons = "".join(
        f'<form method="post" action="{url_for("update_status", job_id=job_id)}">'
        f'<input type="hidden" name="status" value="{s}">'
        f'<button type="submit">→ {s}</button></form>'
        for s in VALID_STATUSES if s != job["status"]
    )
    external = (f'<a href="{escape(job["url"])}" target="_blank" rel="noopener">'
                f'Open job posting ↗</a>' if job["url"] else "")
    classify_line = ""
    if job["classified_at"]:
        classify_line = (
            f'<p class="summary" style="font-size:.85rem">'
            f'country <code>{escape(job["country"] or "?")}</code> · '
            f'remote <code>{escape(job["remote_policy"] or "?")}</code> · '
            f'role <code>{escape(job["role_tag"] or "?")}</code> · '
            f'CA-eligible <code>{"yes" if job["ca_eligible"] else "no"}</code>'
            f'{" · <em>" + escape(job["role_notes"]) + "</em>" if job["role_notes"] else ""}'
            f'</p>'
        )
    if job["fit_score"] is not None:
        classify_line += (
            f'<p class="summary" style="font-size:.85rem">'
            f'fit <strong>{job["fit_score"]}</strong>'
            f'{" · " + escape(job["fit_flags"]) if job["fit_flags"] else ""}<br>'
            f'hits: {escape(job["fit_hits"] or "-")}<br>'
            f'gaps: {escape(job["fit_gaps"] or "-")}</p>'
        )
    body = f"""
      <p><a href="{url_for('index')}">← back to list</a></p>
      <h2>{escape(job['title'] or '')}</h2>
      <p class="summary">
        <strong>{escape(job['company'] or '')}</strong>
        · {escape(job['location'] or '')}
        · {escape(job['source'])}
        · posted {escape((job['posted_at'] or '')[:10])}
        · {status_pill(job['status'])}
        {'· ' + external if external else ''}
      </p>
      {classify_line}
      <div class="actions">{action_buttons}</div>
      <h3>Job description</h3>
      <div class="jd">{escape(job['jd_text'] or '(no JD text)')}</div>
    """
    return layout(job["title"] or "Job", body)


@app.post("/job/<int:job_id>/status")
def update_status(job_id: int):
    new_status = request.form.get("status", "")
    if new_status not in VALID_STATUSES:
        abort(400)
    conn = connect()
    cur = conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (new_status, job_id))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        abort(404)
    return redirect(url_for("show_job", job_id=job_id))


def main(host: str = "127.0.0.1", port: int = 5050, debug: bool = False) -> None:
    print(f"Open http://{host}:{port} in your browser")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
