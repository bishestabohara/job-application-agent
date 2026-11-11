"""Application forms: read the fields (read-only), store prepared answers, then fill + submit in a visible browser.

Flow:
  extract_fields(job)       headless, read-only → list of fields {key, label, kind, required, options}
  prepare(job, answers)     validates answers against the fields; stores them (status=prepared)
  run(...)                  opens a *visible* browser, fills each prepared form, uploads the resume, clicks submit.
                            If a CAPTCHA challenge appears, it waits for Bishesta to solve it — it never bypasses one.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from .config import OUT_DIR, ROOT, resume_pdf

PROFILE_DIR = ROOT / "secrets" / "browser-profile"      # persistent, so it behaves like her normal browser
SHOTS = OUT_DIR / "applications"

# JS: collect form fields grouped by question. Radios/checkboxes sharing a name become one field with options.
_EXTRACT_JS = r"""
() => {
  const text = el => (el ? el.innerText || el.textContent || '' : '').replace(/\s+/g, ' ').trim();
  const entryOf = el => el.closest('[class*="field-entry"], [data-field-path]');
  const labelFor = el => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && text(l)) return text(l); }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const lb = el.getAttribute('aria-labelledby'); if (lb) { const l = document.getElementById(lb); if (l) return text(l); }
    const entry = entryOf(el);
    // nearest label inside the same field entry (e.g. "School*" inside "Education History")
    let p = el.parentElement;
    for (let i = 0; i < 4 && p && p !== entry?.parentElement; i++, p = p.parentElement) {
      const l = [...p.querySelectorAll('label, legend, [class*="label"], [class*="question-title"]')].find(x => text(x) && !x.contains(el));
      if (l) return text(l);
    }
    if (entry) { const l = entry.querySelector('label, [class*="question-title"]'); if (l && text(l)) return text(l); }
    const fs = el.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg) return text(lg); }
    return el.getAttribute('placeholder') || '';
  };
  const groups = new Map();
  const els = document.querySelectorAll('form input, form textarea, form select, input, textarea, select');
  for (const el of els) {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'search'].includes(type)) continue;
    if (el.name && el.name.startsWith('g-recaptcha') || (el.id || '').startsWith('g-recaptcha') || (el.id || '').startsWith('h-captcha')) continue;
    if (el.closest('[aria-hidden="true"]') && type !== 'file') continue;
    const yesno = el.closest('[class*="input-yesno"]');
    if (yesno) {  // Ashby yes/no: styled buttons over a hidden checkbox
      const k = `yesno=${el.name || el.id}`;
      const entry = entryOf(el), l = entry && entry.querySelector('label');
      groups.set(k, {key: k, kind: 'yesno', label: labelFor(el), options: ['Yes', 'No'],
                     required: !!(l && /required/i.test(l.className))});
      continue;
    }
    const isGroup = type === 'radio' || type === 'checkbox';
    const ph = el.getAttribute('placeholder');
    // unnamed dropdowns (e.g. Ashby's education Month/Year pickers) are addressed by position among <select>s
    const selIdx = el.tagName === 'SELECT' && !el.id && !el.name ? [...document.querySelectorAll('select')].indexOf(el) : -1;
    const key = isGroup ? (el.name ? `name=${el.name}` : `#${el.id}`) : (el.id ? `#${el.id}` : (el.name ? `name=${el.name}` : (ph ? `ph=${ph}` : (selIdx >= 0 ? `sel=${selIdx}` : null))));
    if (!key || groups.has(key) && !isGroup) continue;
    let kind = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : type === 'file' ? 'file'
             : isGroup ? type : (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') || /^search/i.test(ph || '') ? 'combobox' : 'text');
    const entryLabel = entryOf(el) && entryOf(el).querySelector('label');
    const required = el.required || el.getAttribute('aria-required') === 'true' || /[*✱]\s*$/.test(labelFor(el))
                     || !!(entryLabel && /required/i.test(entryLabel.className));
    if (isGroup) {
      const optLabel = (el.id && text(document.querySelector(`label[for="${CSS.escape(el.id)}"]`))) || text(el.closest('label')) || el.value;
      if (!groups.has(key)) {
        const fs = el.closest('fieldset, [role=radiogroup], [role=group], .application-question, [class*="question"]');
        const q = fs ? (text(fs.querySelector('legend, label, [class*="label"], [class*="question-title"]')) || '') : '';
        groups.set(key, {key, kind, label: q || optLabel || el.name, required, options: []});
      }
      const g = groups.get(key); g.options.push(optLabel); g.required = g.required || required;
      continue;
    }
    const f = {key, kind, label: labelFor(el), required};
    if (kind === 'select') {
      const first = (el.options[0] && el.options[0].text.trim()) || '';
      f.options = [...el.options].map(o => o.text.trim()).filter(t => t && !/^select/i.test(t) && !/\.\.\.$/.test(t));
      if (selIdx >= 0 && /\.\.\.$/.test(first)) f.label = `${f.label} (${first.replace(/\.+$/, '')})`;  // "Start Date (Month)"
    }
    groups.set(key, f);
  }
  return [...groups.values()];
}
"""

_CAPTCHA_CHALLENGE = ('iframe[src*="recaptcha/api2/bframe"], iframe[src*="recaptcha/enterprise/bframe"], '
                      'iframe[src*="hcaptcha.com"][title*="challenge"], iframe[title*="hCaptcha challenge"]')
_SUCCESS_TEXT = ("thank you for applying", "application submitted", "application has been received", "thanks for applying",
                 "we've received your application", "we have received your application", "your application was submitted",
                 "application received")
_SUBMIT = ('button[type=submit], input[type=submit], button:has-text("Submit application"), '
           'button:has-text("Submit Application"), button:has-text("Submit")')


_PM = re.compile(r"\b(product manag\w*|product management|associate product|apm|\bpm\b|product intern|product analyst|product owner)\b", re.I)
_INTERN = re.compile(r"\b(intern|internship|co-?op|summer 20\d\d|fall 20\d\d|spring 20\d\d|winter 20\d\d)\b", re.I)


def pick_resume(title: str) -> str:
    """Which of her 4 resumes fits: PM vs SWE by function, intern (grad 2028) vs full-time/new grad (grad 2027)."""
    track = "pm" if _PM.search(title or "") else "swe"
    level = "intern" if _INTERN.search(title or "") else "fulltime"
    return f"{track}_{level}"


def application_url(ats: str, slug: str, ext_id: str, posting_url: str) -> str:
    return {
        "greenhouse": f"https://job-boards.greenhouse.io/{slug}/jobs/{ext_id}",
        "lever": f"https://jobs.lever.co/{slug}/{ext_id}/apply",
        "ashby": f"https://jobs.ashbyhq.com/{slug}/{ext_id}/application",
    }.get(ats, posting_url)


def _greenhouse_options(url: str) -> dict[str, list[str]]:
    import re

    import requests

    m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", url)
    if not m:
        return {}
    d = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}", params={"questions": "true"}, timeout=20).json()
    opts = {f"#{f['name']}": [v["label"] for v in f.get("values") or []] for q in d.get("questions", []) for f in q["fields"]}
    for q in (d.get("demographic_questions") or {}).get("questions", []):
        opts[f"#{q['id']}"] = [a["label"] for a in q.get("answer_options", [])]
    return {k: v for k, v in opts.items() if v}


def extract_fields(url: str) -> list[dict]:
    """Read-only: load the application page headlessly and list its fields."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        fields = page.evaluate(_EXTRACT_JS)
        browser.close()
    fields = [f for f in fields if f["label"] or f["kind"] == "file"]
    if "greenhouse.io" in url:
        extra = _greenhouse_options(url)
        for f in fields:
            if f["key"] in extra:
                f["options"] = extra[f["key"]]
    return fields


def validate(fields: list[dict], answers: dict) -> list[str]:
    """Problems that must be fixed before this can be submitted."""
    by_key = {f["key"]: f for f in fields}
    problems = [f"unknown field {k}" for k in answers if k not in by_key]
    # a ticked "Still student?" box disables the end-date pickers, so they're not required then
    still_student = any(re.search(r"still (a )?student|currently (attending|enrolled)", f["label"], re.I)
                        and str(answers.get(f["key"], "")).lower() in ("yes", "true", "1", "on") for f in fields)
    answered_groups = {(f["label"], f["kind"]) for f in fields
                       if f["kind"] in ("checkbox", "radio") and str(answers.get(f["key"], "")).strip()}
    for f in fields:
        if still_student and re.match(r"(end date|graduation date)", f["label"], re.I):
            continue
        if (f["label"], f["kind"]) in answered_groups and not str(answers.get(f["key"], "")).strip():
            continue  # another box of the same question is ticked
        if f["required"] and f["kind"] != "file" and not str(answers.get(f["key"], "")).strip():
            problems.append(f"required field unanswered: {f['label'][:60]} ({f['key']})")
        v = answers.get(f["key"])
        if v and f.get("options") and f["kind"] in ("select", "radio", "yesno", "combobox"):
            if best_option(f["options"], v) is None:
                problems.append(f"'{v}' is not an option for: {f['label'][:60]}")
        if isinstance(v, str) and "TODO" in v:
            problems.append(f"placeholder left in: {f['label'][:60]}")
    return problems


RESUME_FILENAMES = {
    "swe_intern": "Bishesta_Bohara_Resume_SWE_Intern.pdf",
    "swe_fulltime": "Bishesta_Bohara_Resume_SWE_NewGrad.pdf",
    "pm_intern": "Bishesta_Bohara_Resume_PM_Intern.pdf",
    "pm_fulltime": "Bishesta_Bohara_Resume_PM_NewGrad.pdf",
}


def _named_resume(version: str) -> Path:
    """Copy the chosen resume to a name that says which version it is (visible on the form)."""
    dst = Path(tempfile.gettempdir()) / "jobagent_resumes" / RESUME_FILENAMES[version]
    dst.parent.mkdir(exist_ok=True)
    shutil.copyfile(resume_pdf(version), dst)
    return dst


def _loc(page, key: str):
    kind, _, val = key.partition("=") if not key.startswith("#") else ("id", "", key[1:])
    if kind == "sel":
        return page.locator("select").nth(int(val))
    attr = {"id": "id", "name": "name", "ph": "placeholder", "yesno": "name"}[kind]
    return page.locator(f'[{attr}="{val}"]')


_STOP = {"the", "of", "a", "an", "and", "at", "in", "university", "college", "school", "united", "states", "usa", "us"}


def _key_words(text) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if w not in _STOP}


def best_option(options: list[str], value) -> str | None:
    """Exact match, then starts-with, then contains (shortest) — so "No" never picks "Not a veteran"
    and "Male" never picks "Female"."""
    v = str(value).strip().lower()
    opts = [o for o in options if o]
    for test in (lambda o: o.lower() == v, lambda o: o.lower().startswith(v), lambda o: v in o.lower()):
        hits = [o for o in opts if test(o.strip())]
        if hits:
            return min(hits, key=len)
    return None


_COVER = re.compile(r"cover\s*letter", re.I)


def _fill(page, f: dict, value, resume: Path) -> None:
    if _COVER.search(f.get("label") or "") or _COVER.search((f.get("key") or "").replace("_", " ")):
        return  # Bishesta's rule: never submit a cover letter (no upload, no typed letter)
    kind, loc = f["kind"], _loc(page, f["key"])
    if kind == "file":
        # Only the real resume slot gets a file — not cover letters, not "autofill from resume" helpers
        # (those re-parse the PDF and can overwrite answers already typed).
        if not re.search(r"cover|autofill|auto-fill|parse", f["label"] + " " + f["key"], re.I):
            loc.first.set_input_files(str(resume))
        return
    if value in (None, ""):
        return
    if kind in ("text", "textarea"):
        loc.first.fill(str(value))
        loc.first.press("Tab")  # blur so React forms commit the value
    elif kind == "select":
        pick = best_option(f["options"], value)
        if pick is not None:  # never fall back to some other option (e.g. a wrong graduation year)
            loc.first.select_option(label=pick)
    elif kind == "checkbox" and loc.count() == 1:  # a single tick box ("Still Student?", consent to be contacted)
        if str(value).strip().lower() in ("yes", "true", "1", "on", "checked"):
            loc.first.check(force=True)
    elif kind in ("radio", "checkbox"):
        values = value if isinstance(value, list) else [value]
        labels = [loc.nth(i).evaluate("e => ((e.labels && e.labels[0] ? e.labels[0].innerText : e.closest('label')?.innerText) || e.value || '').trim()")
                  for i in range(loc.count())]  # every input sharing the group's name
        for v in values:
            pick = best_option(labels, v)
            if pick is None:
                continue
            el = loc.nth(labels.index(pick))
            el.check(force=True)
            if not el.is_checked():
                el.evaluate("e => { const l = (e.labels && e.labels[0]) || e.closest('label'); if (l) l.click() }")
    elif kind == "yesno":
        name = f["key"].split("=", 1)[1]
        page.locator(f'[data-field-path="{name}"] button[data-option="{str(value).strip().lower()}"]').first.click()
    elif kind == "combobox":
        el = loc.first
        want = _key_words(value)
        words = [w.strip(",") for w in str(value).split()]
        distinctive = [w for w in words if w.lower() not in _STOP]
        queries = [str(value), " ".join(distinctive[-2:]), distinctive[0] if distinctive else ""]
        for query in dict.fromkeys(q for q in queries if q):
            el.click()
            el.fill("")
            el.press_sequentially(query, delay=40)  # suggestion lists only react to real keystrokes
            page.wait_for_timeout(1800)
            # only this box's suggestions (not e.g. the phone field's hidden country list)
            lb = el.get_attribute("aria-controls") or el.get_attribute("aria-owns")
            opts = page.locator(f'[id="{lb}"] [role="option"]') if lb else page.locator('[role="option"]:visible')
            if lb and not opts.count():
                opts = page.locator('[role="option"]:visible')
            texts = [opts.nth(i).inner_text().strip() for i in range(min(opts.count(), 40))]
            # strict: every key word of the answer must appear in the option — never "closest junk"
            ok = [t for t in texts if want <= _key_words(t)]
            if ok:
                pick = best_option(ok, value) or min(ok, key=len)
                opts.nth(texts.index(pick)).click()
                return
        el.fill("")  # no confident match → leave it empty so the form flags it for Bishesta


def _succeeded(page, start_url: str) -> bool:
    body = page.inner_text("body").lower()
    url_moved = page.url != start_url and ("confirmation" in page.url or "thank" in page.url or "success" in page.url)
    return url_moved or any(s in body for s in _SUCCESS_TEXT)


def _remap(saved_fields: list[dict], live_fields: list[dict], answers: dict) -> tuple[list[dict], dict]:
    """Match each saved field to the live page's field with the same kind + label (in order), and move its answer."""
    def norm(t: str) -> str:
        return re.sub(r"[\s*✱]+", " ", (t or "")).strip().lower()

    pool: dict[tuple, list[dict]] = {}
    for lf in live_fields:
        pool.setdefault((lf["kind"], norm(lf["label"])), []).append(lf)
    fields, remapped = [], {}
    for sf in saved_fields:
        cands = pool.get((sf["kind"], norm(sf["label"])))
        lf = cands.pop(0) if cands else sf  # same page structure → keep the saved key
        fields.append(lf)
        if sf["key"] in answers:
            remapped[lf["key"]] = answers[sf["key"]]
    return fields, remapped


def fill_all(page, live: list[dict], answers: dict, resume: Path) -> list[str]:
    """Fill every field, then read them all back and refill any answered field that came back empty
    (some forms redraw a section and drop earlier typing). Returns labels that still failed."""
    for f in live:
        try:
            _fill(page, f, answers.get(f["key"]), resume)
        except Exception:
            pass
    page.wait_for_timeout(800)
    failed = []
    for f in live:
        want = answers.get(f["key"])
        if want in (None, "") or f["kind"] == "file" or _COVER.search(f["label"] + " " + f["key"].replace("_", " ")):
            continue
        try:
            got = page.evaluate(_READ_JS, f)
        except Exception:
            continue
        if got.get("disabled") or got.get("state") == "gone" or got.get("value"):
            continue
        try:
            _fill(page, f, want, resume)
            page.wait_for_timeout(300)
            if not page.evaluate(_READ_JS, f).get("value"):
                failed.append(f["label"][:50])
        except Exception:
            failed.append(f["label"][:50])
    return failed


def run(apps: list[dict], wait_for_human_s: int = 180, dry_run: bool = False, unattended: bool = False) -> list[dict]:
    """Fill + submit each prepared application in a visible browser. Returns per-app results.

    unattended: nobody is at the keyboard — don't wait on CAPTCHA challenges; mark those needs_you and move on."""
    if unattended:
        wait_for_human_s = 0
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    results, waiting = [], []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False, viewport={"width": 1280, "height": 900})
        for app in apps:
            page = ctx.new_page()
            res = {"job_id": app["job_id"], "status": "failed", "note": ""}
            try:
                page.goto(app["url"], wait_until="networkidle", timeout=60000)
                resume = _named_resume(app["resume_version"])
                live, answers = _remap(app["fields"], page.evaluate(_EXTRACT_JS), app["answers"])
                for label in fill_all(page, live, answers, resume):  # the form's own validation flags the rest
                    res["note"] += f"could not fill {label}; "
                shot = SHOTS / f"job{app['job_id']}_filled.png"
                page.screenshot(path=str(shot), full_page=True)
                if dry_run:
                    res.update(status="filled", note=res["note"] + f"dry run — screenshot {shot.name}")
                    results.append(res)
                    continue
                start = page.url
                page.locator(_SUBMIT).last.click()
                deadline = time.time() + 20
                while time.time() < deadline and not _succeeded(page, start):
                    if page.locator(_CAPTCHA_CHALLENGE).count() and page.locator(_CAPTCHA_CHALLENGE).first.is_visible():
                        res["note"] += "captcha shown — waiting for you; "
                        deadline = time.time() + wait_for_human_s
                    page.wait_for_timeout(1000)
                done = _succeeded(page, start)
                page.screenshot(path=str(SHOTS / f"job{app['job_id']}_after.png"), full_page=True)
                res["status"] = "submitted" if done else "needs_you"
                if not done:
                    res["note"] += ("no confirmation seen (form error or captcha) — "
                                    + ("run `./ja apply-run --retry` when you're at the laptop" if unattended else "tab left open for you"))
                    waiting.append((res, page, start))
            except Exception as e:
                res["note"] += f"{e.__class__.__name__}: {str(e)[:120]}"
            results.append(res)
            if res["status"] == "submitted":
                page.close()
        if waiting and not unattended:
            input(f"\n{len(waiting)} application(s) need you — finish them in the open tabs, then press Enter here… ")
            for res, page, start in waiting:
                try:
                    if _succeeded(page, start):
                        res.update(status="submitted", note=res["note"] + " → finished by you")
                except Exception:
                    pass  # tab was closed; leave as needs_you
        ctx.close()
    return results


def dumps(obj) -> str:
    return json.dumps(obj, indent=1)


# ---------------------------------------------------------------- pre-submit check (fills, never submits)

_READ_JS = r"""
(f) => {
  const txt = el => (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const [kind, ...rest] = f.key.startsWith('#') ? ['id', f.key.slice(1)] : f.key.split('=');
  const val = rest.join('=');
  let els = [];
  if (kind === 'id') els = [document.getElementById(val)];
  else if (kind === 'sel') els = [document.querySelectorAll('select')[+val]];
  else if (kind === 'ph') els = [document.querySelector(`[placeholder="${val}"]`)];
  else els = [...document.querySelectorAll(`[name="${val}"]`)];
  els = els.filter(Boolean);
  if (!els.length) return {state: 'gone'};
  const el = els[0];
  if (f.kind === 'yesno') {
    const btns = document.querySelectorAll(`[data-field-path="${val}"] button[aria-pressed]`);
    if (!btns.length) return {state: 'gone'};  // this form doesn't expose which button is pressed
    const b = document.querySelector(`[data-field-path="${val}"] button[aria-pressed="true"]`); return {value: b ? txt(b) : ''};
  }
  if (f.kind === 'radio' || f.kind === 'checkbox') {
    const on = els.filter(e => e.checked).map(e => txt((e.labels && e.labels[0]) || e.closest('label')) || e.value);
    return {value: on.join(', ')};
  }
  if (el.disabled) return {value: '', disabled: true};
  if (f.kind === 'select') return {value: el.selectedIndex > 0 || !/\.\.\.$|select/i.test(el.options[0]?.text || '') ? (el.options[el.selectedIndex] || {}).text : ''};
  if (f.kind === 'file') return {value: el.files && el.files.length ? el.files[0].name : ''};
  if (f.kind === 'combobox') {
    if (el.value) return {value: el.value};
    let c = el; for (let i = 0; i < 3 && c.parentElement; i++) c = c.parentElement;
    const single = c.querySelector('[class*="single-value"], [class*="singleValue"]'); return {value: single ? txt(single) : ''};
  }
  return {value: el.value || ''};
}
"""


def check(apps: list[dict], shots_dir: Path) -> list[dict]:
    """For each app: open the real form headless, fill everything, read every field back, screenshot. Never submits."""
    from playwright.sync_api import sync_playwright

    from .config import resume_pdf

    shots_dir.mkdir(parents=True, exist_ok=True)
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for app in apps:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            rep = {"job_id": app["job_id"], "fields": [], "resume_ok": False, "shot": None, "error": None}
            try:
                page.goto(app["url"], wait_until="networkidle", timeout=60000)
                live, answers = _remap(app["fields"], page.evaluate(_EXTRACT_JS), app["answers"])
                resume = _named_resume(app["resume_version"])
                fill_all(page, live, answers, resume)
                page.wait_for_timeout(1000)
                body = page.inner_text("body")
                size = resume_pdf(app["resume_version"]).stat().st_size
                sizes = page.evaluate("() => [...document.querySelectorAll('input[type=file]')].filter(i => i.files.length).map(i => i.files[0].size)")
                rep["resume_ok"] = size in sizes or RESUME_FILENAMES[app["resume_version"]] in body
                rep["resume_file"] = RESUME_FILENAMES[app["resume_version"]]
                for f in live:
                    r = page.evaluate(_READ_JS, f)
                    value = r.get("value") or ""
                    if f["kind"] == "file" and not value and re.search(r"resume|cv|attach", f["label"], re.I) and not _COVER.search(f["label"] + f["key"].replace("_", " ")):
                        value = RESUME_FILENAMES[app["resume_version"]] if rep["resume_ok"] else ""
                    rep["fields"].append({"label": f["label"], "kind": f["kind"], "required": f["required"], "value": value,
                                          "disabled": r.get("disabled", False) or r.get("state") == "gone",
                                          "cover_letter": bool(_COVER.search(f["label"] + " " + f["key"].replace("_", " ")))})
                shot = shots_dir / f"check_job{app['job_id']}.png"
                page.screenshot(path=str(shot), full_page=True)
                rep["shot"] = str(shot)
            except Exception as e:
                rep["error"] = f"{e.__class__.__name__}: {str(e)[:120]}"
            out.append(rep)
            page.close()
        browser.close()
    return out
