"""Profile resolution, role families, and the Ashby parser."""
from pathlib import Path

import pytest

from jobfinder import companies as companies_mod
from jobfinder import db as db_mod
from jobfinder import profile as profile_mod
from jobfinder.classify import classify_job
from jobfinder.roles import classify_role
from jobfinder.scrapers.ashby import parse_board

ROOT = Path(__file__).parent.parent
FINTECH = ROOT / "profiles" / "fintech-ai-eng.yaml"


@pytest.fixture(autouse=True)
def _reset_active(monkeypatch):
    monkeypatch.delenv("JOBFINDER_PROFILE", raising=False)
    profile_mod.set_active(None)
    yield
    profile_mod.set_active(None)


def test_default_profile_unchanged():
    prof = profile_mod.load()
    assert prof.is_default and prof.name == "legal-ca"
    assert prof.companies_path == ROOT / "companies.yaml"
    assert prof.remotive_categories == ("legal",)
    assert db_mod.db_path() == db_mod.DB_PATH


def test_legal_titles_still_tag_the_same():
    fams = profile_mod.load().families
    cases = {
        "Senior Corporate Counsel": "legal",
        "Paralegal": "legal",
        "VP, Legal": "legal",
        # NOTE: "Head of Legal" / "Director of Legal" are 'other' on main too: the
        # profile.yaml pattern lacks \s+ before 'legal'. Left as-is (profile owner's call).
        "Privacy Program Manager": "law-adjacent",
        "Contracts Manager": "law-adjacent",
        "AI Governance Engineer": "other",
        "Contract Recruiter": "other",
        "Senior Software Engineer": "other",
    }
    for title, tag in cases.items():
        assert classify_role(title, fams)[0] == tag, title


def test_set_active_and_env(monkeypatch):
    profile_mod.set_active(FINTECH)
    prof = profile_mod.load()
    assert prof.name == "fintech-ai-eng" and not prof.is_default
    assert prof.companies_path.resolve() == (ROOT / "companies-fintech-ai.yaml").resolve()
    assert "artificial-intelligence" in prof.remotive_categories
    assert db_mod.db_path().name == "db.fintech-ai-eng.sqlite"
    assert any(r.get("platform") == "ashby" for r in companies_mod.load())

    profile_mod.set_active(None)
    monkeypatch.setenv("JOBFINDER_PROFILE", str(FINTECH))
    assert profile_mod.load().name == "fintech-ai-eng"


def test_fintech_families():
    fams = profile_mod.load(FINTECH).families
    cases = {
        "Staff AI Engineer, Agents": "ai-agents",
        "Forward Deployed Engineer": "ai-agents",
        "Quantitative Developer": "quant-fin-eng",
        "Senior Software Engineer, Payments": "quant-fin-eng",
        "Senior Data Engineer": "data-platform",
        "Staff Backend Engineer": "backend-systems",
        "Solutions Architect": "backend-systems",
        "Software Engineering Intern": "other",
        "Senior iOS Engineer": "other",
        "Account Executive, AI": "other",
    }
    for title, tag in cases.items():
        assert classify_role(title, fams)[0] == tag, title


ASHBY_PAYLOAD = {
    "apiVersion": "1",
    "jobs": [
        {
            "id": "abc-1", "title": " Senior Engineer, Ledger ", "isListed": True,
            "location": "New York, NY (HQ)",
            "secondaryLocations": [{"location": "Remote (Canada)"}, {"location": "Remote (US)"}],
            "isRemote": True, "workplaceType": "Hybrid",
            "publishedAt": "2026-10-30T17:12:35.753+00:00",
            "jobUrl": "https://jobs.ashbyhq.com/acme/abc-1",
            "descriptionPlain": "Build the ledger in Python.",
            "compensation": {"compensationTierSummary": "$200K – $250K"},
        },
        {"id": "hidden", "title": "Unlisted", "isListed": False},
        {
            "id": "abc-2", "title": "Platform Engineer", "location": "Toronto",
            "isRemote": False, "workplaceType": "OnSite", "descriptionHtml": "<p>Go &amp; Rust</p>",
            "applyUrl": "https://jobs.ashbyhq.com/acme/abc-2/application",
        },
    ],
}


def test_ashby_parse():
    jobs = parse_board(ASHBY_PAYLOAD, "acme", "Acme")
    assert [j["source_id"] for j in jobs] == ["abc-1", "abc-2"]
    a, b = jobs
    assert a["title"] == "Senior Engineer, Ledger" and a["company"] == "Acme"
    assert a["location"] == "New York, NY (HQ); Remote (Canada); Remote (US)"
    assert a["jd_text"].startswith("Compensation: $200K")
    assert classify_job(a["location"], a["jd_text"])["remote_policy"] == "remote-ca"
    assert b["jd_text"] == "Go & Rust" and b["url"].endswith("/application")
    assert classify_job(b["location"], "")["ca_eligible"] == 1
