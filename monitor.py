#!/usr/bin/env python3
"""Daily UK sponsorship job monitor.

Cross-references recent Adzuna job postings against the GOV.UK licensed-sponsor
register (Skilled Worker route), filters by the salary floor, scores fit, and
writes a dated JSON archive that docs/index.html renders as a dashboard.

Standard library only. Run:  python monitor.py         (live)
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

def build_today():
    sponsors = load_sponsors()
    print("Licensed Skilled Worker sponsors loaded:", len(sponsors), file=sys.stderr)
    seen, out = set(), []
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
            out.append({
                "score": score(job, field, keyword), "title": title, "field": field,
                "employer": company, "location": (job.get("location") or {}).get("display_name", ""),
                "salary": int(pay) if pay else None,
                "belowGeneral": bool(pay and pay < GENERAL_FLOOR),
                "posted": (job.get("created") or "")[:10], "url": job.get("redirect_url", ""),
            })
    out.sort(key=lambda m: m["score"], reverse=True)
    return out

def write(jobs):
    os.makedirs(DATA_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    with open(os.path.join(DATA_DIR, today + ".json"), "w") as f:
        json.dump({"date": today, "count": len(jobs), "jobs": jobs}, f, indent=2)
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
        json.dump({"updated": datetime.datetime.utcnow().isoformat() + "Z", "days": days}, f, indent=2)
    print("Wrote", len(jobs), "jobs for", today, file=sys.stderr)

DEMO = [
    {"score":82,"title":"Product Designer","field":"Design","employer":"Monzo Bank","location":"London / Remote","salary":55000,"belowGeneral":False,"posted":"","url":"https://example.com"},
    {"score":78,"title":"Interaction Designer","field":"Design","employer":"Sky","location":"Leeds","salary":48000,"belowGeneral":False,"posted":"","url":"https://example.com"},
    {"score":66,"title":"Service Designer","field":"Design","employer":"Capgemini","location":"Birmingham","salary":44000,"belowGeneral":False,"posted":"","url":"https://example.com"},
    {"score":54,"title":"Graphic Designer","field":"Design","employer":"ASOS","location":"London","salary":36000,"belowGeneral":True,"posted":"","url":"https://example.com"},
]

if __name__ == "__main__":
    write(DEMO if "--demo" in sys.argv else build_today())
