#!/usr/bin/env python3
"""Daily UK opportunity monitor.

Cross-references recent Adzuna job postings against the GOV.UK licensed-sponsor
register (Skilled Worker route), filters by the salary floor, scores fit, and
writes a dated JSON archive that docs/index.html renders as a dashboard.

Output is split into the three sections the dashboard shows:
    jobs  sponsored roles across the monitored fields
    hs    health and safety roles, which get the salary vs visa floor check
    phd   funded PhD openings (no source wired up yet, so always empty)

Each opportunity carries a status of strong / caution / weak, which drives the
colour, icon and word on its card. Standard library only.

Run:  python monitor.py         (live)
      python monitor.py --demo  (writes sample data)
"""
import os, re, csv, io, json, sys, time, html, datetime, difflib, urllib.parse, urllib.request

ADZUNA_ID  = os.environ.get("ADZUNA_ID", "")
ADZUNA_KEY = os.environ.get("ADZUNA_KEY", "")

UA = ("sponsorship-monitor (personal daily job alert; "
      "+https://github.com/createdbyAB/sponsorship-monitor)")

# (search term, field) -- edit this list to change what gets monitored
KEYWORDS = [
    ("interaction designer", "Design"), ("product designer", "Design"),
    ("ux designer", "Design"), ("ui designer", "Design"),
    ("service designer", "Design"), ("graphic designer", "Design"),
    ("data analyst", "Data"), ("operations manager", "Operations"),
    ("area manager", "Operations"), ("health and safety", "Operations"),
    ("process engineer", "Engineering"),
]

# Extra Adzuna sweeps aimed squarely at the health and safety tab. Anything they
# return is still routed by title, so a loose term cannot pollute the section.
# Each entry costs one Adzuna call per day, so keep the list short.
HS_KEYWORDS = [
    "health and safety advisor", "health and safety manager",
    "hse manager", "safety officer",
]

# jobs.ac.uk searches. Universities are almost all licensed sponsors, and their
# adverts run for weeks rather than days, so this window is much wider than the
# Adzuna one. Two queries saturate the results; more just repeat them.
JACUK_QUERIES  = ["health and safety", "safety officer"]
JACUK_PAGES    = 2      # 25 results per page
JACUK_MAX_DAYS = 45

# reed.co.uk searches. Much the widest H&S source, covering the whole UK market
# rather than one sector. Adverts run for weeks, same as jobs.ac.uk.
REED_QUERIES  = ["health and safety", "hse manager", "safety officer"]
REED_PAGES    = 2       # 25 results per page
REED_MAX_DAYS = 30

# Optional. Set GOOGLE_API_KEY and GOOGLE_CSE_ID to also sweep a Google
# Programmable Search engine. See the README for why this is the only sanctioned
# way to search the open web here, and why its rows are treated as unconfirmed.
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID  = os.environ.get("GOOGLE_CSE_ID", "")
GOOGLE_QUERIES = ["health and safety manager visa sponsorship UK",
                  "health and safety advisor jobs UK sponsorship"]

NEW_ENTRANT_FLOOR = 33400   # early-career Skilled Worker rate (applies to AB)
GENERAL_FLOOR     = 41700   # roles between the two are flagged
COUNTRY, MAX_DAYS_OLD = "gb", 2
DATA_DIR = os.path.join("docs", "data")

# Routes a title into the health and safety section. Deliberately narrow at the
# edges: "Health and Social Care" and "Healthcare Architecture" must not match.
# Keep this in step with HS_RE in docs/index.html, which reads older archives.
_HS = re.compile(
    r"health\s*[,&]?\s*(?:and\s+)?safety"
    r"|\bhse\b|\bsheq\b|\behs\b|nebosh|iosh"
    r"|(?:process|fire|technical|food|construction|occupational|product)\s+safety"
    r"|\bsafety\s+(?:officer|advisor|adviser|manager|co-?ordinator|coordinator|lead|consultant"
    r"|inspector|engineer|specialist|practitioner|superintendent)", re.I)

_SUFFIX = re.compile(r"\b(ltd|limited|plc|llp|uk|group|holdings|services|solutions|technologies|technology|international|company|co|the)\b")
def norm(name):
    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    return re.sub(r"\s+", " ", _SUFFIX.sub(" ", n)).strip()

class _Redirect(urllib.request.HTTPRedirectHandler):
    """urllib does not follow 308 by itself, and reed.co.uk answers with one."""
    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_301(req, fp, 301, msg, headers)

_OPENER = urllib.request.build_opener(_Redirect)

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with _OPENER.open(req, timeout=90) as r:
        return r.read().decode("utf-8", "replace")

# Agencies advertise on behalf of an employer they do not name. The agency may
# well hold a licence, but that says nothing about who would actually sponsor
# you, so these rows get flagged rather than trusted.
_AGENCY = re.compile(r"\b(recruit\w*|resourc\w*|staffing|talent|personnel|placements?|"
                     r"headhunt\w*|manpower|employment agency|search & selection|"
                     r"search and selection)\b", re.I)

def looks_like_agency(name):
    return bool(_AGENCY.search(name or ""))

def sane_salary(lo, hi):
    """A trustworthy annual minimum, or None if the advert's figures look like
    a placeholder band. Boards often pad the range out to catch more searches,
    and a bogus low figure would wrongly sink a role under the visa floor."""
    lo = int(lo or 0)
    hi = int(hi or 0)
    if lo < 12000:
        return None                 # hourly, pro-rata, or a filler value
    if hi and hi >= lo * 2:
        return None                 # a band that wide is not a real minimum
    return lo

def load_sponsors():
    """Set of normalised names of Skilled Worker licensed sponsors."""
    page = fetch("https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers")
    m = re.search(r'href="([^"]+\.csv)"', page)
    if not m:
        raise RuntimeError("Could not find the sponsor register CSV link on GOV.UK")
    csv_url = m.group(1)
    if csv_url.startswith("/"):
        csv_url = "https://www.gov.uk" + csv_url
    reader = csv.reader(io.StringIO(fetch(csv_url)))
    next(reader, None)  # header
    return {norm(row[0]) for row in reader if row and "Skilled Worker" in ",".join(row)}

def is_sponsor(company, sponsors):
    c = norm(company)
    if not c:
        return False
    if c in sponsors:
        return True
    for s in sponsors:
        if s and (c.startswith(s) or s.startswith(c)) and abs(len(c) - len(s)) <= 6:
            return True
    return bool(difflib.get_close_matches(c, sponsors, n=1, cutoff=0.93))

def adzuna(keyword):
    q = urllib.parse.urlencode({
        "app_id": ADZUNA_ID, "app_key": ADZUNA_KEY, "results_per_page": 50,
        "what": keyword, "max_days_old": MAX_DAYS_OLD, "sort_by": "date",
    })
    try:
        return json.loads(fetch(f"https://api.adzuna.com/v1/api/jobs/{COUNTRY}/search/1?{q}")).get("results", [])
    except Exception as e:
        print("Adzuna error:", keyword, e, file=sys.stderr)
        return []

# ---------------------------------------------------------------- jobs.ac.uk
# HTML scrape of the public search. jobs.ac.uk/robots.txt allows /search/ (it
# only disallows /job/feedback/ and /enhanced/fp/). One polite request per page,
# a descriptive user agent, and a pause between queries.
_J_RESULT = re.compile(r'<div class="j-search-result__result[^"]*"\s+data-advert-id="(\d+)">'
                       r'(.*?)(?=<div class="j-search-result__result|<div id="job-listings-end|$)', re.S)
_J_LINK   = re.compile(r'<a href="(/job/[^"]+)"\s*>\s*(.*?)\s*</a>', re.S)
_J_EMP    = re.compile(r'j-search-result__employer">\s*<b>\s*(.*?)\s*</b>', re.S)
_J_LOC    = re.compile(r'<div>Location:\s*(.*?)\s*</div>', re.S)
_J_SAL    = re.compile(r'j-search-result__info">\s*<strong>Salary:\s*</strong>\s*(.*?)</div>', re.S)
_J_PLACED = re.compile(r'<strong>Date Placed:\s*</strong>\s*(\d{1,2}\s+[A-Za-z]{3})', re.S)
_J_CLOSES = re.compile(r'j-search-result__date--blue[^"]*">\s*(\d{1,2}\s+[A-Za-z]{3})\s*</span>', re.S)
_TAGS     = re.compile(r"<[^>]+>")

def _text(s):
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", s or ""))).strip()

def _first_pounds(s):
    """First £ figure in a salary blurb. Ignores hourly rates."""
    m = re.search(r"£\s?([\d,]+)", s or "")
    if not m:
        return None
    try:
        v = int(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v if v >= 1000 else None

def _daymon(s, today):
    """'10 Jul' -> ISO date, picking the year that lands nearest today."""
    if not s:
        return ""
    for year in (today.year, today.year - 1, today.year + 1):
        try:
            d = datetime.datetime.strptime(s + " " + str(year), "%d %b %Y").date()
        except ValueError:
            continue
        if abs((d - today).days) <= 200:
            return d.isoformat()
    return ""

def _grab(rx, block):
    m = rx.search(block)
    return _text(m.group(1)) if m else ""

def jobs_ac_uk(keyword, pages=JACUK_PAGES):
    today, out = datetime.date.today(), []
    for p in range(pages):
        q = urllib.parse.urlencode({"keywords": keyword, "sort": "re", "s": 1,
                                    "pageSize": 25, "startIndex": 1 + p * 25})
        try:
            page = fetch("https://www.jobs.ac.uk/search/?" + q)
        except Exception as e:
            print("jobs.ac.uk error:", keyword, e, file=sys.stderr)
            break
        blocks = _J_RESULT.findall(page)
        if not blocks:
            break
        for _id, b in blocks:
            link = _J_LINK.search(b)
            if not link:
                continue
            salary_text = _grab(_J_SAL, b)
            out.append({
                "title": _text(link.group(2)),
                "employer": _grab(_J_EMP, b),
                "location": _grab(_J_LOC, b),
                "salary": _first_pounds(salary_text),
                "posted": _daymon(_grab(_J_PLACED, b), today),
                "deadline": _daymon(_grab(_J_CLOSES, b), today),
                "url": "https://www.jobs.ac.uk" + link.group(1),
            })
        if len(blocks) < 25:
            break
        time.sleep(1.0)
    return out

# ------------------------------------------------------------------ reed.co.uk
# reed.co.uk/robots.txt allows /jobs for the generic agent. The page is a Next.js
# app, so the listings come from its own JSON payload rather than from parsing
# rendered markup, which is both cleaner and far less likely to break.
_NEXT = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)

def reed(keyword, pages=REED_PAGES):
    out = []
    for p in range(1, pages + 1):
        q = urllib.parse.urlencode({"keywords": keyword, "pageno": p})
        try:
            page = fetch("https://www.reed.co.uk/jobs?" + q)
        except Exception as e:
            print("reed error:", keyword, e, file=sys.stderr)
            break
        m = _NEXT.search(page)
        if not m:
            print("reed: no data payload for", keyword, file=sys.stderr)
            break
        try:
            jobs = json.loads(m.group(1))["props"]["pageProps"]["searchResults"]["jobs"]
        except Exception as e:
            print("reed: unexpected payload shape:", e, file=sys.stderr)
            break
        if not jobs:
            break
        for j in jobs:
            x = j.get("jobDetail") or {}
            if x.get("salaryType") not in (None, 5):        # 5 = annual
                continue
            out.append({
                "title": x.get("jobTitle") or "",
                "employer": x.get("ouName") or j.get("profileName") or "",
                "location": ", ".join(v for v in (x.get("displayLocationName"),
                                                  x.get("countyLocation")) if v),
                "salary": sane_salary(x.get("salaryFrom"), x.get("salaryTo")),
                "posted": (x.get("displayDate") or "")[:10],
                "deadline": (x.get("expiryDate") or "")[:10],
                "url": "https://www.reed.co.uk" + (j.get("url") or ""),
                # ouType 1 is an agency, 2 is the employer advertising directly.
                # Checked against reed's own agency filter, which returns only
                # type 1. Far more reliable than guessing from the trading name.
                "agency": x.get("ouType") == 1,
            })
        time.sleep(1.0)
    return out

# --------------------------------------------------------- google web search
# Google's robots.txt disallows /search, so the result pages are off limits.
# The Programmable Search JSON API is the supported way to run a web search, so
# that is what this uses, and only when both credentials are present.
def google_search(query, limit=10):
    if not (GOOGLE_API_KEY and GOOGLE_CSE_ID):
        return []
    q = urllib.parse.urlencode({"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID,
                                "q": query, "num": min(10, limit)})
    try:
        data = json.loads(fetch("https://www.googleapis.com/customsearch/v1?" + q))
    except Exception as e:
        print("google error:", query, e, file=sys.stderr)
        return []
    out = []
    for item in data.get("items", []):
        title = (item.get("title") or "").strip()
        # Page titles are usually "Role - Employer - Location | Board". Take the
        # second part as a guess at the employer, and never treat it as fact.
        parts = [p.strip() for p in re.split(r"\s+[-|–]\s+", title) if p.strip()]
        out.append({
            "title": parts[0] if parts else title,
            "employer": parts[1] if len(parts) > 1 else "",
            "location": "", "salary": None, "posted": "", "deadline": "",
            "url": item.get("link") or "",
        })
    return out

def score(job, field, keyword):
    title = (job.get("title") or "").lower()
    s = 55
    if keyword in title: s += 20
    elif any(w in title for w in keyword.split()): s += 8
    pay = job.get("salary_min") or 0
    s += 12 if pay >= GENERAL_FLOOR else (4 if pay >= NEW_ENTRANT_FLOOR else 0)
    if field == "Design" and "designer" in title: s += 8
    return max(0, min(100, s))

def classify(pay, agency=False, on_register=True):
    """Status plus the plain-language note the card shows underneath it.

    Copy is deliberately plain and never claims more certainty than the data
    supports. The three things that can pull a row down from strong are: the
    employer not being confirmed on the register, the advert coming from an
    agency that will not name the employer, and the pay not clearing the floor.
    """
    if not on_register:
        return "weak", (
            "Found by web search, not by a job board we can check. Nobody has confirmed "
            "this employer holds a licence, so treat it as a lead to look into yourself.")
    if agency:
        return "caution", (
            "Advertised by an agency. The agency holds a licence, but it does not name the "
            "employer who would actually sponsor you, so ask them before you apply.")
    if pay and pay >= GENERAL_FLOOR:
        return "strong", ""
    if pay:
        return "caution", (
            "Pay is under the general floor of £{:,}. It can still work on the new entrant "
            "rate, but confirm the salary and the sponsorship on the advert.".format(GENERAL_FLOOR))
    return "caution", (
        "No salary stated, or the advert gives too wide a band to trust. The employer holds "
        "a licence, so confirm the pay and the sponsorship on the advert before you apply.")

def make_row(title, employer, location, pay, posted, url, field, section,
             source, base_score, deadline="", on_register=True, agency=False):
    status, note = classify(pay, agency or looks_like_agency(employer), on_register)
    return {
        "score": base_score, "title": title, "field": field, "employer": employer,
        "location": location, "salary": int(pay) if pay else None,
        "belowGeneral": bool(pay and pay < GENERAL_FLOOR),
        "posted": posted, "deadline": deadline, "url": url,
        "section": section, "status": status, "note": note, "source": source,
    }

def within_days(posted, limit):
    if not posted:
        return True          # undated adverts are kept and judged on their own merits
    try:
        d = datetime.date.fromisoformat(posted)
    except ValueError:
        return True
    return 0 <= (datetime.date.today() - d).days <= limit

def build_today():
    sponsors = load_sponsors()
    print("Licensed Skilled Worker sponsors loaded:", len(sponsors), file=sys.stderr)
    seen, jobs, hs = set(), [], []
    calls = 0

    def take(title, employer, key_extra=""):
        """Dedupe and sponsor gate, shared by every source."""
        key = norm(title) + "|" + norm(employer) + key_extra
        if key in seen:
            return False
        if not is_sponsor(employer, sponsors):
            return False
        seen.add(key)
        return True

    # --- Adzuna: the monitored fields, routed into jobs or H&S by title -------
    for keyword, field in KEYWORDS + [(k, "Operations") for k in HS_KEYWORDS]:
        hs_only = keyword in HS_KEYWORDS
        calls += 1
        for job in adzuna(keyword):
            company = (job.get("company") or {}).get("display_name", "")
            title = re.sub("<.*?>", "", job.get("title") or "")
            is_hs = bool(_HS.search(title))
            # The H&S sweeps exist to fill one tab. If a term drifts, drop the
            # result rather than letting it land in the general jobs list.
            if hs_only and not is_hs:
                continue
            pay = job.get("salary_min") or 0
            if pay and pay < NEW_ENTRANT_FLOOR:
                continue
            if not take(title, company):
                continue
            section = "hs" if is_hs else "jobs"
            (hs if is_hs else jobs).append(make_row(
                title, company, (job.get("location") or {}).get("display_name", ""),
                pay, (job.get("created") or "")[:10], job.get("redirect_url", ""),
                field, section, "adzuna", score(job, field, keyword)))

    # --- scraped boards: same shape, same gate, different window -------------
    def board(name, getter, queries, max_days):
        found = 0
        for keyword in queries:
            for job in getter(keyword):
                title = job["title"]
                if not _HS.search(title):
                    continue
                if not within_days(job["posted"], max_days):
                    continue
                pay = job["salary"] or 0
                if pay and pay < NEW_ENTRANT_FLOOR:
                    continue
                if not take(title, job["employer"]):
                    continue
                hs.append(make_row(
                    title, job["employer"], job["location"], pay, job["posted"],
                    job["url"], "Operations", "hs", name,
                    score({"title": title, "salary_min": pay}, "Operations", keyword),
                    deadline=job["deadline"], agency=job.get("agency", False)))
                found += 1
            time.sleep(1.0)
        print("%-12s %d queries -> %d kept" % (name, len(queries), found), file=sys.stderr)

    board("jobs.ac.uk", jobs_ac_uk, JACUK_QUERIES, JACUK_MAX_DAYS)
    board("reed.co.uk", reed, REED_QUERIES, REED_MAX_DAYS)

    # --- optional web search: leads, not verified vacancies ------------------
    if GOOGLE_API_KEY and GOOGLE_CSE_ID:
        found = 0
        for query in GOOGLE_QUERIES:
            for hit in google_search(query):
                title = hit["title"]
                if not _HS.search(title) or not hit["url"]:
                    continue
                # No employer field to trust here, so the register decides the
                # status rather than gating entry, and the card says as much.
                on_register = bool(hit["employer"]) and is_sponsor(hit["employer"], sponsors)
                key = norm(title) + "|" + norm(hit["employer"]) + "|web"
                if key in seen:
                    continue
                seen.add(key)
                hs.append(make_row(
                    title, hit["employer"] or "employer not named", "", 0, "",
                    hit["url"], "Operations", "hs", "google", 55,
                    on_register=on_register))
                found += 1
            time.sleep(1.0)
        print("%-12s %d queries -> %d kept" % ("google", len(GOOGLE_QUERIES), found), file=sys.stderr)

    for bucket in (jobs, hs):
        bucket.sort(key=lambda m: m["score"], reverse=True)
    print("Adzuna calls:", calls, file=sys.stderr)
    # No PhD source is wired up yet. The dashboard renders its own empty state
    # for this section, so leave the list present but empty rather than absent.
    return {"jobs": jobs, "hs": hs, "phd": []}

def write(day):
    os.makedirs(DATA_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    floors = {"newEntrant": NEW_ENTRANT_FLOOR, "general": GENERAL_FLOOR}
    total = sum(len(day[k]) for k in ("jobs", "hs", "phd"))
    payload = {"date": today, "count": total, "floors": floors,
               "counts": {k: len(day[k]) for k in ("jobs", "hs", "phd")},
               "jobs": day["jobs"], "hs": day["hs"], "phd": day["phd"]}
    with open(os.path.join(DATA_DIR, today + ".json"), "w") as f:
        json.dump(payload, f, indent=2)
    days = []
    for fn in sorted(os.listdir(DATA_DIR)):
        if re.match(r"\d{4}-\d{2}-\d{2}\.json$", fn):
            try:
                with open(os.path.join(DATA_DIR, fn)) as f:
                    days.append({"date": fn[:-5], "count": json.load(f).get("count", 0)})
            except Exception:
                pass
    days.sort(key=lambda d: d["date"], reverse=True)
    with open(os.path.join(DATA_DIR, "index.json"), "w") as f:
        json.dump({"updated": datetime.datetime.now(datetime.timezone.utc)
                                       .replace(tzinfo=None).isoformat() + "Z",
                   "floors": floors, "days": days}, f, indent=2)
    print("Wrote", total, "opportunities for", today,
          "(jobs %d, hs %d, phd %d)" % (len(day["jobs"]), len(day["hs"]), len(day["phd"])),
          file=sys.stderr)

def demo():
    """Sample data covering every card state, for previewing without API keys."""
    n = [0]
    def mk(**kw):
        n[0] += 1
        return dict({"field": "Design", "posted": datetime.date.today().isoformat(),
                     "url": "https://example.com/advert/%d" % n[0], "note": "",
                     "belowGeneral": False, "deadline": "", "source": "adzuna"}, **kw)
    jobs = [
        mk(score=92, title="Senior Software Engineer", employer="Monzo Bank", location="London, UK",
           salary=65000, section="jobs", status="strong", field="Engineering"),
        mk(score=89, title="Platform Engineer", employer="Wise", location="London, UK",
           salary=72000, section="jobs", status="strong", field="Engineering"),
        mk(score=74, title="Backend Engineer, Payments", employer="Starling Bank", location="Cardiff",
           salary=38000, belowGeneral=True, section="jobs", status="caution", field="Engineering",
           note=classify(38000)[1]),
        mk(score=68, title="Service Designer", employer="Capgemini", location="Birmingham",
           salary=None, section="jobs", status="caution", note=classify(0)[1]),
    ]
    hs = [
        mk(score=88, title="Health and Safety Manager", employer="Skanska", location="Birmingham",
           salary=52000, section="hs", status="strong", field="Operations"),
        mk(score=87, title="Health and Safety Audit Manager", employer="University of Bath",
           location="Bath", salary=47389, section="hs", status="strong", field="Operations",
           source="jobs.ac.uk", posted="2026-07-17",
           deadline=(datetime.date.today() + datetime.timedelta(days=24)).isoformat()),
        mk(score=87, title="Corporate Health and Safety Manager", employer="The Hyde Group",
           location="London", salary=66000, section="hs", status="strong", field="Operations",
           source="reed.co.uk"),
        mk(score=87, title="Health and Safety Officer", employer="Hamilton Woods",
           location="Birmingham", salary=45000, section="hs", status="caution", field="Operations",
           source="reed.co.uk", note=classify(45000, agency=True)[1]),
        mk(score=78, title="Health and Safety Advisor", employer="Balfour Beatty", location="Manchester",
           salary=42000, section="hs", status="caution", field="Operations",
           note="Licence held. The pay clears the floor, so confirm on the advert that this role "
                "is offered with sponsorship."),
        mk(score=55, title="Health and Safety Manager", employer="employer not named",
           location="", salary=None, section="hs", status="weak", field="Operations",
           source="google", note=classify(0, on_register=False)[1]),
    ]
    return {"jobs": jobs, "hs": hs, "phd": []}

if __name__ == "__main__":
    write(demo() if "--demo" in sys.argv else build_today())
