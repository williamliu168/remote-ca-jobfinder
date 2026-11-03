# remote-ca-jobfinder

A tiny, **profile-driven**, rule-based job aggregator. *What* it looks for lives
in a single config file, so the same engine can be pointed at **any role/field**
or company list. As shipped it surfaces **remote, Canada-eligible legal &
law-adjacent roles** from five free public job sources; a second profile
([`profiles/fintech-ai-eng.yaml`](profiles/fintech-ai-eng.yaml)) targets senior
fintech / financial-data / AI-agent engineering.

It pulls postings, tags each by location and role, optionally scores each one
against your résumé, and puts the roles you care about on top — while keeping
everything else browsable. No LLM, no API keys, no account.

## Why it's built this way

- **Deterministic & cheap.** Plain regex classification — no model calls, no
  cost, reproducible. You can read exactly why a job was tagged the way it was.
- **Business logic is separated from infrastructure.** The package (`jobfinder/`)
  is generic plumbing; the search definition lives in [`profile.yaml`](profile.yaml)
  and the company list in [`companies.yaml`](companies.yaml). Swap those two files
  to repurpose the tool — no code changes.
- **Nothing is hidden.** Roles outside the profile, and jobs that aren't
  Canada-eligible, are relegated to lower sections — never silently dropped.

## Architecture

```
profile.yaml   companies.yaml          <-  business: what & where to look
      │              │
      ▼              ▼
   scrape  ──►  classify  ──►  score  ──►  report  (shortlist.md)    <-  jobfinder/: infra
                                       └─►  serve   (web UI at localhost:5050)
                                  ▲
                          résumé (local file, never committed)
```

1. **scrape** — fetch postings into a local SQLite DB.
2. **classify** — tag each job: country (CA / remote / other), remote policy
   (remote-ca / remote-global / hybrid / onsite), Canada-eligibility, and a
   role tag from the active profile's families.
3. **score** *(optional)* — deterministic résumé fit (0–100) for each
   Canada-eligible job, plus the skills it asks for that the résumé shows
   (*hits*) and doesn't (*gaps*). See [Résumé-fit scoring](#résumé-fit-scoring).
4. **report / serve** — read a Markdown shortlist, or browse and mark statuses
   in a small web UI. The "surfaced" view = any job that matched a profile family.

The infrastructure in `jobfinder/` contains no role-specific knowledge; it reads
families from the profile via a generic matching engine (`jobfinder/roles.py`).

## Sources (all free, no auth)

| Source | What | Endpoint |
|---|---|---|
| Greenhouse | Curated companies' public boards | `boards-api.greenhouse.io` |
| Lever | Curated companies' public boards | `api.lever.co` |
| Ashby | Curated companies' public boards (many AI/fintech startups) | `api.ashbyhq.com/posting-api` |
| Remotive | Remote jobs incl. a real **legal** category | `remotive.com/api/remote-jobs` |
| RemoteOK | Remote jobs (tech-skewed) | `remoteok.com/api` |

The two remote boards are firehoses, so they're filtered to Canada-eligible
postings at scrape time.

## Quickstart

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate     macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

python -m jobfinder.cli verify              # check curated slugs are live
python -m jobfinder.cli scrape --curated --apis
python -m jobfinder.cli classify
python -m jobfinder.cli report              # writes shortlist.md
python -m jobfinder.cli serve               # browse at http://127.0.0.1:5050
```

## Configuring the search

Two business-config files, no code required:

- **[`profile.yaml`](profile.yaml) — what to look for.** Defines the surfaced
  section `label`, the `location` lens (`canada` is implemented), and ordered
  role `families`. Each family has `keywords` (matched whole-word) and/or
  `patterns` (raw regex for precision), plus an optional `exclude` veto. The
  first family a title matches becomes its role tag; a title matching none is
  `other` (kept, not surfaced). Edit this to change *what* gets surfaced.
- **[`companies.yaml`](companies.yaml) — where to look.** Greenhouse/Lever/Ashby
  company slugs. Add a `slug` + `platform`, then run
  `python -m jobfinder.cli verify` to confirm the board exists.

### Profiles

`profile.yaml` is the default. Point at another profile with `--profile PATH`
(before the command) or `JOBFINDER_PROFILE`:

```bash
python -m jobfinder.cli --profile profiles/fintech-ai-eng.yaml scrape --curated --apis
```

A profile may also set (paths are relative to the profile file):

| Key | Default | Meaning |
|---|---|---|
| `companies` | `companies.yaml` | company list to scrape |
| `sources.remotive_categories` | `[legal]` | Remotive category slugs ([list](https://remotive.com/api/remote-jobs/categories)) |
| `fit` | — | résumé-fit scoring config (below) |

Each non-default profile gets its own DB (`jobfinder/db.<name>.sqlite`) and
shortlist (`shortlist.<name>.md`), so statuses never mix between searches.

### Résumé-fit scoring

`fit.skills` is a weighted **vocabulary for the field**, including skills a given
résumé may lack. The résumé decides which terms you *have*; each posting is then
scored on the terms it asks for:

```
demand = Σ weight of vocab terms in the posting (title hits count title_weight×)
match  = Σ weight of those terms the résumé also shows
fit    = 100 × (match / demand) × min(1, match / saturation)  ± seniority, clamped 0–100
```

Coverage (`match / demand`) rewards postings that ask for what you have;
saturation stops a posting that names one matching term from scoring 100. Any
`dealbreakers` regex (e.g. security clearance, "must reside in the US" without
"or Canada") forces 0. Keywords match whole words with spaces and hyphens
interchangeable (`market data` = `market-data`).

**Privacy:** the résumé is read from a local file: `--resume PATH`,
`JOBFINDER_RESUME`, or the profile's `fit.resume`. `resume.local.*` and
`*.local.md` are git-ignored. Nothing from the résumé is stored except which
vocabulary terms matched.

## Commands

| Command | Does |
|---|---|
| `scrape --curated` | Greenhouse + Lever from `companies.yaml` |
| `scrape --apis` | Remotive + RemoteOK (CA-filtered) |
| `scrape --source <name> [--companies a,b]` | one source / specific slugs |
| `verify [--tag X]` | confirm curated slugs are live |
| `classify [--force]` | tag country / remote / role |
| `score [--resume PATH] [--all]` | résumé-fit score Canada-eligible jobs |
| `report [--since DAYS]` | write `shortlist.md`; with scores, adds a "Top by fit" section limited to recent postings (default 30 days) |
| `serve [--host --port]` | web UI |
| `list [--view surfaced] [--status S] [--sort fit] [--limit N]` | list in the terminal |
| `show <id>` · `status <id> <new>` | inspect / set a job's status |

## Privacy

Everything stored is a public job posting. The SQLite DB and generated
`shortlist.md` are git-ignored build artifacts; only code and config are
committed. All data sources are public and keyless — there are no credentials
in this repo.

## Not yet (possible later)

Light/part-time intensity scoring (the data is captured at scrape time as a
hook), additional location lenses beyond Canada, LLM enrichment, résumé
tailoring, full application tracking, and more ATS integrations (Workday,
SmartRecruiters).

Known classifier gaps: region locks that appear only in the title
("… / US / Remote") or only in the posting body ("you must reside in one of the
following cities") can still pass the location lens. Read the posting before
applying.

## Tests

```bash
pip install -r requirements.txt
python -m pytest
```

## License

[MIT](LICENSE)
