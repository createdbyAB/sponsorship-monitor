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
import os, re, csv, io, json, sys, datetime, difflib, urllib.parse, urllib.request

ADZUNA_ID  = os.environ.get("ADZUNA_ID", "")
ADZUNA_KEY = os.environ.get("ADZUNA_KEY", "")

# (search term, field) -- edit this list to change what gets monitored
KEYWORDS = [
    ("interaction designer", "Design"), ("product designer", "Design"),
    ("ux designer", "Design"), ("ui designer", "Design"),
    ("service designer", "Design"), ("graphic designer", "Design"),
    ("data analyst", "Data"), ("operations manager", "Operations"),
    ("area manager", "Operations"), ("health and safety", "Operations"),
    ("process engineer", "Engineering"),
]
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
    req = urllib.request.Request(url, headers={"User-Agent": "sponsorship-monitor"})
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

def build_today():
    sponsors = load_sponsors()
    print("Licensed Skilled Worker sponsors loaded:", len(sponsors), file=sys.stderr)
    seen, jobs, hs = set(), [], []
    for keyword, field in KEYWORDS:
        for job in adzuna(keyword):
            company = (job.get("company") or {}).get("display_name", "")
            title = re.sub("<.*?>", "", job.get("title") or "")
            key = norm(title) + "|" + norm(company)
            if key in seen or not is_sponsor(company, sponsors):
                continue
            pay = job.get("salary_min") or 0
            if pay and pay < NEW_ENTRANT_FLOOR:
                continue
            seen.add(key)
            status, note = classify(pay)
            section = "hs" if _HS.search(title) else "jobs"
            (hs if section == "hs" else jobs).append({
                "score": score(job, field, keyword), "title": title, "field": field,
                "employer": company, "location": (job.get("location") or {}).get("display_name", ""),
                "salary": int(pay) if pay else None,
                "belowGeneral": bool(pay and pay < GENERAL_FLOOR),
                "posted": (job.get("created") or "")[:10], "url": job.get("redirect_url", ""),
                "section": section, "status": status, "note": note,
            })
    for bucket in (jobs, hs):
        bucket.sort(key=lambda m: m["score"], reverse=True)
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
    mk = lambda **kw: dict({"field": "Design", "posted": datetime.date.today().isoformat(),
                            "url": "https://example.com", "note": "", "belowGeneral": False}, **kw)
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
        mk(score=78, title="Health and Safety Advisor", employer="Balfour Beatty", location="Manchester",
           salary=42000, section="hs", status="caution", field="Operations",
           note="Licence held. The pay clears the floor, so confirm on the advert that this role "
                "is offered with sponsorship."),
    ]
    return {"jobs": jobs, "hs": hs, "phd": []}

if __name__ == "__main__":
    write(demo() if "--demo" in sys.argv else build_today())
