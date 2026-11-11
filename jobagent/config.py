"""Paths, model settings, and profile loading."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PROFILE_DIR = ROOT / "profile"
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"
DB_PATH = Path(os.getenv("JOBAGENT_DB", DATA_DIR / "jobagent.db"))

MODEL = os.getenv("JOBAGENT_MODEL", "claude-opus-5")
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
GMAIL_CREDENTIALS = ROOT / os.getenv("GMAIL_CREDENTIALS", "secrets/gmail_credentials.json")
GMAIL_TOKEN = ROOT / "secrets" / "gmail_token.json"

RESUME_VERSIONS = ("swe_intern", "swe_fulltime", "pm_intern", "pm_fulltime")


@lru_cache
def criteria() -> dict:
    return yaml.safe_load((PROFILE_DIR / "criteria.yaml").read_text())


@lru_cache
def context_md() -> str:
    return (PROFILE_DIR / "context.md").read_text()


@lru_cache
def resume_text(version: str) -> str:
    return (PROFILE_DIR / "resumes" / f"{version}.md").read_text()


def resume_pdf(version: str) -> Path:
    return PROFILE_DIR / "resumes" / "pdf" / f"{version}.pdf"


@lru_cache
def candidate_brief() -> str:
    """Stable system-prompt block: context + criteria + SWE and PM resumes. Cached by the API."""
    return (
        "# CANDIDATE CONTEXT\n" + context_md()
        + "\n\n# CRITERIA (machine-readable)\n```yaml\n" + (PROFILE_DIR / "criteria.yaml").read_text() + "```"
        + "\n\n# SWE RESUME\n" + resume_text("swe_intern")
        + "\n\n# PRODUCT RESUME\n" + resume_text("pm_intern")
        + "\n\n(Intern resumes list graduation May 2028; full-time/new-grad resumes list May 2027.)"
    )
