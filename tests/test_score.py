"""Résumé-fit scoring: coverage math, gaps, seniority, dealbreakers, hyphen tolerance."""
from pathlib import Path

import pytest

from jobfinder import profile as profile_mod
from jobfinder.score import FitModel, build_model, keyword_pattern, resume_path, score_job

ROOT = Path(__file__).parent.parent
FINTECH = ROOT / "profiles" / "fintech-ai-eng.yaml"


def _profile(tmp_path, fit_yaml: str):
    p = tmp_path / "p.yaml"
    p.write_text("name: t\nlocation: canada\nfamilies: []\nfit:\n" + fit_yaml, encoding="utf-8")
    return profile_mod.load(p)


FIT = """
  saturation: 6
  title_weight: 2
  skills:
    - {term: python, weight: 3}
    - {term: java, weight: 2}
    - {term: market data, weight: 2}
  seniority:
    - {label: senior+, pattern: '\\bsenior\\b', points: 5}
  dealbreakers:
    - {label: clearance, pattern: 'security\\s+clearance'}
"""


@pytest.fixture
def model(tmp_path) -> FitModel:
    return build_model(_profile(tmp_path, FIT), "I write Python against market-data feeds.")


def test_have_uses_resume_and_tolerates_hyphens(model):
    assert model.have == {"python", "market data"}


def test_full_coverage_saturated(model):
    out = score_job("Backend Engineer", "Python and market data.", model)
    # match = demand = 5 -> coverage 1, saturation 5/6
    assert out["fit_score"] == round(100 * 5 / 6)
    assert out["fit_hits"] == "python, market data"
    assert out["fit_gaps"] is None


def test_gap_lowers_coverage_and_is_reported(model):
    out = score_job("Engineer", "Python, Java, market data", model)
    # demand 7, match 5 -> 100 * 5/7 * 5/6
    assert out["fit_score"] == round(100 * (5 / 7) * (5 / 6))
    assert out["fit_gaps"] == "java"


def test_title_hit_counts_double(model):
    out = score_job("Python Engineer", "market data", model)
    # python in title: 3*2 = 6; market data 2 -> match 8 >= saturation
    assert out["fit_score"] == 100


def test_seniority_adjusts_and_clamps(model):
    out = score_job("Senior Python Engineer", "market data", model)
    assert out["fit_score"] == 100            # clamped
    assert "senior+ +5" in out["fit_flags"]


def test_dealbreaker_zeroes(model):
    out = score_job("Python Engineer", "Requires an active security clearance.", model)
    assert out["fit_score"] == 0
    assert "DEALBREAKER: clearance" in out["fit_flags"]


def test_no_vocab_signal_is_zero(model):
    assert score_job("Chef", "Cook food.", model)["fit_score"] == 0


def test_keyword_pattern_word_boundaries():
    import re
    rx = re.compile(keyword_pattern("go"), re.I)
    assert rx.search("Python, go, Rust")
    assert not rx.search("google")
    rx = re.compile(keyword_pattern("real-time"), re.I)
    assert rx.search("real time analytics") and rx.search("real-time") and rx.search("realtime")


def test_resume_path_precedence(tmp_path, monkeypatch):
    prof = _profile(tmp_path, FIT + "  resume: mine.md\n")
    monkeypatch.delenv("JOBFINDER_RESUME", raising=False)
    assert resume_path(prof) == tmp_path / "mine.md"
    monkeypatch.setenv("JOBFINDER_RESUME", str(tmp_path / "env.md"))
    assert resume_path(prof) == (tmp_path / "env.md").resolve()
    assert resume_path(prof, str(tmp_path / "cli.md")) == (tmp_path / "cli.md").resolve()


def test_fintech_profile_dealbreakers_keep_canada_ok():
    prof = profile_mod.load(FINTECH)
    m = build_model(prof, "python")
    us_only = score_job("Senior Engineer", "Python. You must reside in the United States.", m)
    us_or_ca = score_job("Senior Engineer", "Python. You must reside in the US or Canada.", m)
    assert "US residents only" in (us_only["fit_flags"] or "")
    assert "DEALBREAKER" not in (us_or_ca["fit_flags"] or "")
