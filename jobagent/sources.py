"""Public job-board fetchers (Greenhouse, Lever, Ashby) + a cheap title pre-filter."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

import requests

TIMEOUT = 20
_session = requests.Session()
_session.headers["User-Agent"] = "jobagent/0.1 (personal job search)"


@dataclass
class Job:
    ext_id: str
    title: str
    location: str
    url: str
    description: str
    posted_at: str | None


def _strip_html(s: str | None) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def greenhouse(slug: str) -> list[Job]:
    r = _session.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}, timeout=TIMEOUT)
    r.raise_for_status()
    return [
        Job(str(j["id"]), j["title"], (j.get("location") or {}).get("name", ""), j["absolute_url"],
            _strip_html(j.get("content")), j.get("first_published") or j.get("updated_at"))
        for j in r.json().get("jobs", [])
    ]


def lever(slug: str) -> list[Job]:
    r = _session.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        cats = j.get("categories") or {}
        loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        desc = "\n\n".join(filter(None, [j.get("descriptionPlain"), *(l.get("text", "") + "\n" + _strip_html(l.get("content")) for l in j.get("lists") or []), j.get("additionalPlain")]))
        out.append(Job(j["id"], j["text"], loc, j["hostedUrl"], desc, str(j.get("createdAt") or "")))
    return out


def ashby(slug: str) -> list[Job]:
    r = _session.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [l.get("location", "") for l in j.get("secondaryLocations") or []]
        if j.get("isRemote"):
            locs.append("Remote")
        out.append(Job(j["id"], j["title"], ", ".join(filter(None, locs)), j["jobUrl"],
                       j.get("descriptionPlain") or _strip_html(j.get("descriptionHtml")), j.get("publishedAt")))
    return out


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def probe(name: str) -> tuple[str, str] | None:
    """Guess a company's ATS board from its name. Returns (ats, slug) or None."""
    base = re.sub(r"[^a-z0-9 ]", "", name.lower()).split()
    candidates = dict.fromkeys(["".join(base), "-".join(base), base[0] if base else "", "".join(base) + "health", "".join(base) + "inc"])
    for slug in filter(None, candidates):
        for ats, url in (
            ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"),
            ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}"),
            ("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1"),
        ):
            try:
                r = _session.get(url, timeout=TIMEOUT)
            except requests.RequestException:
                continue
            if r.status_code == 200 and (ats != "lever" or r.text.startswith("[")):
                return ats, slug
    return None


# ---- pre-filter: only obviously-irrelevant titles are dropped; Claude judges the rest ----

_ROLE = re.compile(r"\b(software|engineer|engineering|developer|swe|full[- ]?stack|front[- ]?end|back[- ]?end|"
                   r"machine learning|\bml\b|\bai\b|data engineer|product manager|product management|\bapm\b|\bpm\b)", re.I)
_STRONG_EARLY = re.compile(r"\b(intern|internship|co-?op|new grad|new graduate|university grad|early[- ]career|"
                           r"entry[- ]level|apprentice|2027|2028)\b", re.I)
_EARLY = re.compile(r"\b(junior|associate|graduate|fellow|\bI)\b", re.I)
_SENIOR = re.compile(r"\b(senior|sr\.?|staff|principal|lead|leader|director|head of|vp|vice president|group product|"
                     r"(?<!product )manager|architect|mid[- ]level|\bII\b|\bIII\b|\bIV\b|distinguished)\b", re.I)
_NOT_SOFTWARE = re.compile(r"\b(mechanical|electrical|manufacturing|npi|technician|sales engineer|network engineer|it engineer|"
                           r"counsel|marketing|support engineer|corporate engineer|clinical product specialist)\b", re.I)


def relevant_title(title: str) -> bool:
    if not _ROLE.search(title) or _NOT_SOFTWARE.search(title):
        return False
    if _STRONG_EARLY.search(title):
        return True
    if _SENIOR.search(title):
        return False
    # Junior/associate/"Engineer I" and unleveled titles ("Software Engineer", "Product Manager") pass —
    # unleveled roles are often open to new grads; Claude reads the posting and decides.
    return True


# ---- SimplifyJobs community lists (free, updated daily): Summer 2027 internships + new grad ----

SIMPLIFY_LISTS = {
    "intern": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "newgrad": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
}
_SIMPLIFY_CATEGORIES = {"Software", "Software Engineering", "Product", "AI/ML/Data"}

_ATS_URL = [
    ("greenhouse", re.compile(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)")),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_app\?for=([\w-]+)&token=(\d+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)/([0-9a-f-]{36})")),
]


def parse_ats_url(url: str) -> tuple[str, str, str] | None:
    """(ats, slug, job id) for Greenhouse/Lever/Ashby links — lets Simplify postings dedupe with board fetches and auto-apply."""
    for ats, rx in _ATS_URL:
        if m := rx.search(url):
            return ats, m[1], m[2]
    return None


def simplify(lookback_days: int) -> list[dict]:
    """Active SWE/PM/AI postings from the last `lookback_days`, minus citizenship-only and PhD/Masters-only roles."""
    import time

    cutoff = time.time() - lookback_days * 86400
    out = []
    for kind, url in SIMPLIFY_LISTS.items():
        r = _session.get(url, timeout=60)
        r.raise_for_status()
        for x in r.json():
            degrees = x.get("degrees") or []
            if not (x.get("active") and x.get("is_visible", True) and x.get("category") in _SIMPLIFY_CATEGORIES
                    and x.get("sponsorship") != "U.S. Citizenship is Required" and x.get("date_posted", 0) >= cutoff
                    and (not degrees or "Bachelor's" in degrees)):
                continue
            out.append({**x, "kind": kind})
    return out
