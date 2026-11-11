"""The four Claude-powered steps: score jobs, discover companies, find contacts, draft outreach."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .config import criteria
from .llm import ask

Lane = Literal["healthcare", "education", "caregiving_aging_mental_health", "other_human_impact", "non_mission"]
Sponsorship = Literal["likely", "unlikely", "confirmed_no", "unknown"]
ResumeVersion = Literal["swe_intern", "swe_fulltime", "pm_intern", "pm_fulltime"]


# ---------------------------------------------------------------- job scoring

class JobFit(BaseModel):
    fit_score: int = Field(description="0-100 overall fit for Bishesta")
    role_type: Literal["swe_intern", "swe_fulltime", "pm_intern", "pm_fulltime", "not_target"]
    level_ok: bool = Field(description="True if an intern or new grad (graduating 2027/2028) can realistically get this")
    resume_version: ResumeVersion
    nyc: bool
    sponsorship: Sponsorship
    stepping_stone: bool = Field(description="True only if mission is weak but ownership/scope/growth is strong")
    why: str = Field(description="Two sentences max: the specific reason this fits (or doesn't)")
    red_flags: list[str]


SCORE_ROLE = """You screen job postings for one candidate, Bishesta. Score each posting 0-100 against her context below.
Weigh, in order: (1) can an intern/new grad graduating 2027 or 2028 realistically get it; (2) mission fit by lane priority
(healthcare first); (3) technical/product fit to her resume; (4) NYC; (5) sponsorship openness (boost, never exclude).
Company size does not matter. Ads/trading/engagement-optimized missions score low unless it's a genuine stepping-stone role.
Senior roles, roles needing 3+ years of experience, or non-SWE/PM roles score under 30.
Sponsorship: use 'confirmed_no' only if the posting or context says so; 'unknown' if the posting is silent."""


def score_job(company: dict, title: str, location: str, description: str) -> JobFit:
    known = f"Known about company: lane={company['lane']}, sponsorship={company['sponsorship']}, note={company['note'] or '-'}"
    task = f"Company: {company['name']}\n{known}\nTitle: {title}\nLocation: {location}\n\nPosting:\n{description[:12000]}"
    return ask(task, JobFit, role=SCORE_ROLE, effort="low", max_tokens=4000)


# ---------------------------------------------------------- company discovery

class DiscoveredCompany(BaseModel):
    name: str
    website: str
    lane: Lane
    size: Literal["startup", "mid", "large"]
    nyc: bool = Field(description="Has an NYC office or NYC-based roles")
    sponsorship: Sponsorship
    why: str = Field(description="One specific sentence on the everyday human problem it solves")
    evidence_url: str


class Discovery(BaseModel):
    companies: list[DiscoveredCompany]


DISCOVER_ROLE = """You source companies for Bishesta's job search and networking. Use web search to find real, currently operating
companies that match her mission criteria — healthcare access/quality/navigation first, then education access, then caregiving/aging/
mental health. Any size is fine (startup, mid-size, big) as long as the mission is genuine everyday human impact.
Favor companies with engineering/product teams that hire interns or new grads, NYC presence, and any sign of sponsoring visas
(H-1B filings, OPT/CPT-friendly language). Every company must be verifiable — include a real evidence URL you actually saw.
Never invent companies."""


def discover(exclude: list[str], n: int) -> list[DiscoveredCompany]:
    task = (f"Find {n} new companies that fit. Mix of sizes. Lean toward healthcare (at least half).\n"
            f"Do NOT return any of these (already tracked): {', '.join(sorted(exclude))}")
    return ask(task, Discovery, role=DISCOVER_ROLE, web_search_uses=12, effort="high", max_tokens=32000).companies


# ------------------------------------------------------------- contact finding

class Contact(BaseModel):
    name: str
    title: str
    linkedin_url: str = Field(description="Exact LinkedIn profile URL if found in search results, else empty string")
    hook: str = Field(description="One specific, recent, public thing they did/said/built to open the message with")
    evidence_url: str = Field(description="URL where you saw the hook / their role")
    why_this_person: str


class Contacts(BaseModel):
    contacts: list[Contact]


CONTACTS_ROLE = """You find the right people for Bishesta to reach out to at a company, to start a conversation that could lead to an
internship/new-grad role or a referral. Use web search. Pick people in this order of preference:
- Startup (<200 people): founder, CTO, or head of engineering/product.
- Mid/large: an engineering manager or engineer on a team she'd want to join; a PM on a patient/learner-facing product;
  a university/early-career recruiter last.
- Big bonus: University of Southern Mississippi alumni, women-in-tech org members (Grace Hopper, SWE, WiCyS, Rewriting the Code),
  or people who've written/talked publicly about the company's mission.
Only return people you actually found in search results with a current role at the company — never guess names or URLs.
The hook must be specific and public (a blog post, talk, launch, podcast, paper, repo) — not generic praise."""


def find_contacts(company: dict, n: int, already: list[str]) -> list[Contact]:
    task = (f"Company: {company['name']} ({company['website'] or 'website unknown'}). Lane: {company['lane']}. "
            f"Why it fits: {company['why'] or company['note'] or '-'}\n"
            f"Find up to {n} people. Skip: {', '.join(already) or 'none'}.")
    return ask(task, Contacts, role=CONTACTS_ROLE, web_search_uses=8, effort="medium", max_tokens=16000).contacts


# ------------------------------------------------------------ outreach drafting

class Drafts(BaseModel):
    resume_version: ResumeVersion
    email_subject: str
    email_body: str
    linkedin_note: str = Field(description="Connection request note, hard limit 300 characters")
    follow_up_1: str = Field(description="Short nudge for ~5 days later, same thread, 2-3 sentences")
    follow_up_2: str = Field(description="Final gracious note for ~12 days later, 2 sentences, no guilt")


def outreach_role() -> str:
    o = criteria()["outreach"]
    return f"""You write outreach from Bishesta to one specific person. Goal: {o['ask']}, not a job ask. Rules:
- Sound like her: personal, passionate, simple. No corporate phrasing, no "I hope this finds you well", no flattery filler.
- Open with the person's specific hook (their work), then ONE line on why this company's mission matters to her,
  grounded in a real reason from her context, then ONE concrete proof point — lead with the human problem, then the tech
  (her healthcare EHR reconciliation project is the strongest hook for healthcare companies).
- Ask clearly for {o['ask']} to learn about their team/work. Mention internship/new-grad interest lightly at most once.
- Never dump her resume. Never invent facts about her or them. Only reference the hook provided.
- Email body: under {o['email_max_words']} words, sign off "Bishesta" with her LinkedIn URL. Subject: short, specific, lowercase-ok.
- LinkedIn note: under {o['linkedin_note_max_chars']} characters, no links.
- Choose resume_version: SWE vs PM by the person's function; intern vs fulltime by what the company offers (default intern)."""


def draft_outreach(company: dict, contact: dict, open_roles: list[str]) -> Drafts:
    task = (f"Company: {company['name']} — {company['why'] or company['note'] or company['lane']}\n"
            f"Person: {contact['name']}, {contact['title']}\nWhy them: {contact['why_this_person']}\n"
            f"Hook: {contact['hook']} (source: {contact['evidence_url']})\n"
            f"Open relevant roles right now: {'; '.join(open_roles) or 'none posted — this is a cold networking note'}")
    d = ask(task, Drafts, role=outreach_role(), effort="high", max_tokens=16000)
    limit = criteria()["outreach"]["linkedin_note_max_chars"]
    if len(d.linkedin_note) > limit:
        d.linkedin_note = ask(f"Shorten to under {limit} characters, same voice, keep the hook and the ask:\n\n{d.linkedin_note}",
                              _Note, role="You tighten short messages.", effort="low", max_tokens=2000).text[:limit]
    return d


class _Note(BaseModel):
    text: str
