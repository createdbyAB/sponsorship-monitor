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

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8", "replace")

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

def score(job, field, keyword):
    title = (job.get("title") or "").lower()
    s = 55
    if keyword in title: s += 20
    elif any(w in title for w in keyword.split()): s += 8
    pay = job.get("salary_min") or 0
    s += 12 if pay >= GENERAL_FLOOR else (4 if pay >= NEW_ENTRANT_FLOOR else 0)
    if field == "Design" and "designer" in title: s += 8
    return max(0, min(100, s))

def classify(pay):
    """Status plus the plain-language note the card shows underneath it.

    Everything reaching this point is already at a licensed sponsor, so the
    only open question is whether the pay clears the general floor. Copy is
    deliberately plain and never claims more certainty than the data supports.
    """
    if pay and pay >= GENERAL_FLOOR:
        return "strong", ""
    if pay:
        return "caution", (
            "Pay is under the general floor of £{:,}. It can still work on the new entrant "
            "rate, but confirm the salary and the sponsorship on the advert.".format(GENERAL_FLOOR))
    return "caution", (
        "No salary on the listing. The employer holds a licence, so confirm the pay and "
        "the sponsorship on the advert before you apply.")

def make_row(title, employer, location, pay, posted, url, field, section,
             source, base_score, deadline=""):
    status, note = classify(pay)
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

    # --- jobs.ac.uk: universities, which are nearly all licensed sponsors -----
    for keyword in JACUK_QUERIES:
        for job in jobs_ac_uk(keyword):
            title = job["title"]
            if not _HS.search(title):
                continue
            if not within_days(job["posted"], JACUK_MAX_DAYS):
                continue
            pay = job["salary"] or 0
            if pay and pay < NEW_ENTRANT_FLOOR:
                continue
            if not take(title, job["employer"]):
                continue
            hs.append(make_row(
                title, job["employer"], job["location"], pay, job["posted"],
                job["url"], "Operations", "hs", "jobs.ac.uk",
                score({"title": title, "salary_min": pay}, "Operations", keyword),
                deadline=job["deadline"]))
        time.sleep(1.0)

    for bucket in (jobs, hs):
        bucket.sort(key=lambda m: m["score"], reverse=True)
    print("Adzuna calls:", calls, "| jobs.ac.uk queries:", len(JACUK_QUERIES), file=sys.stderr)
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
        mk(score=78, title="Health and Safety Advisor", employer="Balfour Beatty", location="Manchester",
           salary=42000, section="hs", status="caution", field="Operations",
           note="Licence held. The pay clears the floor, so confirm on the advert that this role "
                "is offered with sponsorship."),
    ]
    return {"jobs": jobs, "hs": hs, "phd": []}

if __name__ == "__main__":
    write(demo() if "--demo" in sys.argv else build_today())
