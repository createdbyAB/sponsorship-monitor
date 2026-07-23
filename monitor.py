#!/usr/bin/env python3
"""Daily UK opportunity monitor.

Cross-references recent Adzuna job postings against the GOV.UK licensed-sponsor
register (Skilled Worker route), filters by the salary floor, scores fit, and
writes a dated JSON archive that docs/index.html renders as a dashboard.

Output is split into the three sections the dashboard shows:
    jobs  sponsored roles across the monitored fields
    hs    health and safety roles, which get the salary vs visa floor check
    phd   funded PhD openings, ranked against the research interests below

Each opportunity carries a status of strong / caution / weak, which drives the
colour, icon and word on its card. Standard library only.

Run:  python monitor.py         (live)
      python monitor.py --demo  (writes sample data)
"""
import os, re, csv, io, json, sys, time, html, datetime, difflib, http.cookiejar
import urllib.parse, urllib.request, urllib.error

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

# --- funded PhDs ------------------------------------------------------------
# jobs.ac.uk searches, run against its PhD facet. The first few are the field,
# the rest are the research areas worth chasing.
PHD_QUERIES = [
    "chemical engineering",
    "carbon capture",
    "waste valorisation",
    "circular economy",
    "sustainable process engineering",
    "hydrogen decarbonisation",
]
PHD_PAGES     = 2       # 25 results per page
PHD_MAX_DAYS  = 120     # studentships are advertised months ahead of the deadline
PHD_ENRICH    = 50      # detail pages fetched per run, best scoring first

# EURAXESS covers doctoral posts across Europe and several partner portals, so
# this is what reaches Germany, France, Spain, the Nordics and beyond. Its search
# is a POST, but the redirect shows keywords are really a facet, so a plain GET
# works once the query is built that way. 447 is First Stage Researcher (R1),
# which is how EURAXESS labels PhD-level positions.
EURAXESS_QUERIES = [
    "carbon capture", "waste valorisation", "circular economy",
    "chemical engineering", "sustainable process", "biomass conversion",
]
EURAXESS_R1      = "447"
EURAXESS_PAGES   = 2    # 10 results per page
EURAXESS_ENRICH  = 25   # detail pages fetched per run, for the deadline

# Research interests, most wanted first. Drives the fit score, so reorder these
# rather than the queries above if the ranking feels wrong.
PHD_INTERESTS = [
    (r"waste valoris|valoris|circular econom|resource recovery|waste to (?:energy|value)", 18),
    (r"carbon captur|\bccus?\b|co2 (?:utilis|convers|capture)|direct air capture", 18),
    (r"sustainab|decarbonis|net zero|green (?:chemistry|hydrogen|process)|renewable", 14),
    (r"biomass|biorefin|bioenergy|biofuel|anaerobic digestion", 12),
    (r"catalys|process intensif|reactor|separation|membrane", 10),
    (r"hydrogen|electrolys|energy storage|fuel cell", 10),
    (r"life cycle assess|\blca\b|techno-?economic", 8),
    (r"wastewater|water treatment|effluent|pollution", 8),
]
# "\bchem" is deliberately broad: it takes chemical, chemistry, electrochemical
# and project names like e-ChemIn, all of which are on topic for a chemical
# engineer, while leaving generic academic posts out.
PHD_FIELD = re.compile(r"chemical engineer|process engineer|chemical (?:and|&) biological|"
                       r"chem(?:ical)? eng\b|energy engineer|environmental engineer|\bchem", re.I)

# Countries worth watching beyond the UK. Used to label a row and to fill the
# country filter on the dashboard.
COUNTRY_HINTS = [
    ("USA", r"\b(usa|united states|u\.s\.a?\.)\b|\b(boston|cambridge, ma|new york|california|"
            r"berkeley|stanford|texas|chicago|michigan|seattle|atlanta|pittsburgh)\b"),
    ("Canada", r"\bcanada\b|\b(ontario|quebec|british columbia|alberta|toronto|montreal|"
               r"vancouver|ottawa|waterloo|calgary|edmonton)\b"),
    ("Australia", r"\baustralia\b|\b(sydney|melbourne|brisbane|canberra|adelaide|queensland)\b"),
    ("New Zealand", r"\bnew zealand\b|\b(auckland|wellington|christchurch)\b"),
    ("Netherlands", r"\bnetherlands\b|\b(amsterdam|delft|eindhoven|utrecht|wageningen|"
                    r"groningen|twente|rotterdam|leiden)\b"),
    ("Germany", r"\bgermany\b|\b(berlin|munich|münchen|aachen|karlsruhe|dresden|heidelberg|"
                r"stuttgart|hamburg|leipzig|bonn|jülich|julich|darmstadt|freiburg)\b"),
    ("Switzerland", r"\bswitzerland\b|\b(zurich|zürich|lausanne|geneva|basel|epfl|eth)\b"),
    ("Belgium", r"\bbelgium\b|\b(ghent|gent|leuven|brussels|antwerp|liege)\b"),
    ("Sweden", r"\bsweden\b|\b(stockholm|lund|gothenburg|uppsala|chalmers|linköping)\b"),
    ("Denmark", r"\bdenmark\b|\b(copenhagen|aarhus|lyngby)\b"),
    ("Norway", r"\bnorway\b|\b(oslo|trondheim|bergen|ntnu)\b"),
    ("Finland", r"\bfinland\b|\b(helsinki|espoo|aalto|tampere)\b"),
    ("Ireland", r"\bireland\b|\b(dublin|cork|galway|limerick|maynooth)\b"),
    ("France", r"\bfrance\b|\b(paris|lyon|grenoble|toulouse|marseille|nantes)\b"),
    ("Spain", r"\bspain\b|\b(madrid|barcelona|valencia|seville)\b"),
    ("Italy", r"\bitaly\b|\b(rome|milan|turin|bologna|padua)\b"),
    ("Austria", r"\baustria\b|\b(vienna|graz|innsbruck|linz)\b"),
    ("Singapore", r"\bsingapore\b|\bnanyang\b|\bnus\b"),
    ("Hong Kong", r"\bhong kong\b"),
    ("Saudi Arabia", r"\bsaudi\b|\bkaust\b|\briyadh\b|\bdhahran\b"),
    ("UAE", r"\b(uae|united arab emirates|abu dhabi|dubai|khalifa)\b"),
]

# Optional. Set GOOGLE_API_KEY and GOOGLE_CSE_ID to also sweep a Google
# Programmable Search engine. See the README for why this is the only sanctioned
# way to search the open web here, and why its rows are treated as unconfirmed.
# This is what reaches past the UK for PhDs, so it matters more here than for H&S.
# Stripped, because a stray newline or space pasted into a secret is invisible
# in the GitHub UI and makes every call come back 400.
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()
GOOGLE_CSE_ID  = os.environ.get("GOOGLE_CSE_ID", "").strip()

# The engine id is easy to copy wrong, and Google answers a wrong one with a
# flat "Request contains an invalid argument" that names nothing. Recover the id
# from a pasted public URL or a "cx=..." fragment rather than failing all day.
if "cx=" in GOOGLE_CSE_ID:
    GOOGLE_CSE_ID = GOOGLE_CSE_ID.split("cx=", 1)[1].split("&", 1)[0].strip()
GOOGLE_CSE_ID = GOOGLE_CSE_ID.strip("/?& ")
GOOGLE_QUERIES = ["health and safety manager visa sponsorship UK",
                  "health and safety advisor jobs UK sponsorship"]
# These carry the countries the scraped sources cannot reach. Point the search
# engine at the PhD boards listed in the README and these stay on topic; each
# query costs one of the 100 free calls a day, so there is plenty of headroom.
GOOGLE_PHD_QUERIES = [
    "fully funded PhD chemical engineering carbon capture USA international students",
    "fully funded PhD waste valorisation circular economy Canada international",
    "PhD scholarship chemical engineering sustainability Australia international",
    "PhD scholarship carbon capture New Zealand funded international",
    "funded PhD position CO2 utilisation chemical engineering international",
    "PhD studentship biomass valorisation fully funded international students",
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
_J_DEPT   = re.compile(r'j-search-result__department">\s*(.*?)\s*</div>', re.S)
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

def jobs_ac_uk(keyword, pages=JACUK_PAGES, phds=False):
    today, out = datetime.date.today(), []
    for p in range(pages):
        params = {"keywords": keyword, "sort": "re", "s": 1,
                  "pageSize": 25, "startIndex": 1 + p * 25}
        if phds:
            # The PhD facet on the ordinary search. Without it the same URL
            # returns lectureships and admin posts rather than studentships.
            params["jobTypeFacet[]"] = "phds"
        q = urllib.parse.urlencode(params)
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
                "department": _grab(_J_DEPT, b),
                "location": _grab(_J_LOC, b),
                "salary": _first_pounds(salary_text),
                "salary_text": salary_text,
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

# ---------------------------------------------------------------- funded PhDs
_J_FUNDFOR = re.compile(r"Funding for:\s*([^\n]{0,120}?)\s*(?:Funding amount|Hours|Placed On|Closes)", re.I)
_J_FUNDAMT = re.compile(r"Funding amount:\s*([^\n]{0,120}?)\s*(?:Hours|Placed On|Closes)", re.I)

# Country-code top level domains, for rows whose only clue is a link. Deliberately
# excludes .edu, which is mostly but not only American.
_TLD = {"ca": "Canada", "au": "Australia", "nz": "New Zealand", "de": "Germany",
        "fr": "France", "es": "Spain", "it": "Italy", "nl": "Netherlands",
        "se": "Sweden", "ch": "Switzerland", "be": "Belgium", "dk": "Denmark",
        "no": "Norway", "fi": "Finland", "ie": "Ireland", "at": "Austria",
        "pt": "Portugal", "pl": "Poland", "sg": "Singapore", "jp": "Japan",
        "uk": "UK", "us": "USA"}

def country_of(text, url="", default="UK"):
    """Best guess at the country. Place names first, then the domain of the link
    if there is one. jobs.ac.uk is a UK site listing mostly UK institutions, so
    UK is a sensible fallback there, but web search results get no such default."""
    for name, pattern in COUNTRY_HINTS:
        if re.search(pattern, text or "", re.I):
            return name
    host = urllib.parse.urlparse(url or "").hostname or ""
    bits = host.lower().rsplit(".", 2)
    if len(bits) >= 2 and bits[-1] in _TLD:
        return _TLD[bits[-1]]
    if host.endswith(".edu"):
        return "USA"
    return default

def phd_relevant(text):
    """Is this actually in the field, or near one of the research interests?

    A source whose keyword search quietly stops working returns its generic
    listing instead of an error, which is how "Assistant professor in
    humanistic sciences" ended up in a chemical engineering tab. Judging every
    row on its own text means a broken search yields nothing rather than junk.
    """
    text = text or ""
    if PHD_FIELD.search(text):
        return True
    return any(re.search(p, text, re.I) for p, _ in PHD_INTERESTS)

def phd_interest_score(text):
    """Fit against the research interests, plus a bonus for the field itself."""
    s = 45
    if PHD_FIELD.search(text or ""):
        s += 15
    hits = 0
    for pattern, weight in PHD_INTERESTS:
        if re.search(pattern, text or "", re.I):
            s += weight
            hits += 1
            if hits == 3:          # three matching themes is already a strong fit
                break
    return s

def phd_detail(url):
    """Funding and eligibility from the advert page. The search results do not
    carry either, and for an international applicant eligibility is the whole
    question, so it is worth one extra request per shortlisted studentship."""
    try:
        page = fetch(url)
    except Exception as e:
        print("jobs.ac.uk detail error:", url, e, file=sys.stderr)
        return {}
    text = re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", page)))
    mf, ma = _J_FUNDFOR.search(text), _J_FUNDAMT.search(text)
    funding_for = mf.group(1).strip() if mf else ""
    amount = ma.group(1).strip() if ma else ""
    low = (funding_for + " " + amount).lower()

    intl = None
    if re.search(r"worldwide|international|overseas|non-?uk|\beu\b.*\bstudents\b", low):
        intl = True
    elif re.search(r"uk students|home students|uk only|home only", low):
        intl = False

    funding = ""
    if re.search(r"fully funded|full funding|covers? (?:full )?(?:tuition|fees)", text, re.I):
        funding = "full"
    elif _first_pounds(amount) or _first_pounds(funding_for):
        funding = "full"
    elif re.search(r"self-?funded|no funding|fees only", low):
        funding = ""
    elif re.search(r"part(?:ial|ly) fund|fees only", low):
        funding = "partial"
    # Adverts quote monthly figures, part-time rates and fee-only amounts in the
    # same field. Anything that is not plausibly a yearly UK stipend is dropped
    # rather than shown as one, since the card labels it "per year".
    stipend = _first_pounds(amount)
    if stipend and not (8000 <= stipend <= 80000):
        stipend = None
    return {"funding": funding, "intlEligible": intl, "stipend": stipend,
            "funding_for": funding_for}

# ------------------------------------------------------------------ EURAXESS
_E_ITEM  = re.compile(r'<article class="ecl-content-item"(.*?)</article>', re.S)
_E_TITLE = re.compile(r'ecl-content-block__title"><a\s+href="(/jobs/\d+)"\s+class="[^"]*"\s*><span>(.*?)</span>', re.S)
_E_ORG   = re.compile(r'primary-meta-item"><a href="[^"]*"[^>]*>(.*?)</a>', re.S)
_E_POST  = re.compile(r'Posted on:\s*(\d{1,2} [A-Za-z]+ \d{4})')
_E_LOC   = re.compile(r'Number of offers:[^,]*,\s*([^,<]+)')
_E_DEAD  = re.compile(r'Application Deadline\s*:?\s*(\d{1,2} [A-Za-z]{3,} \d{4})', re.I)

def _euraxess_session():
    """A cookie-carrying opener, warmed up on the plain search page.

    Without this the site answered every query with the same 187kB default
    page from a data centre IP, ignoring both the keyword facet and the page
    number, so six searches collapsed into one set of ten results. It behaved
    from a laptop, which is what made it easy to miss."""
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(_Redirect, urllib.request.HTTPCookieProcessor(jar))
    try:
        _euraxess_get(op, "https://euraxess.ec.europa.eu/jobs/search")
        time.sleep(0.5)
    except Exception as e:
        print("euraxess warm-up failed:", e, file=sys.stderr)
    return op

def _euraxess_get(opener, url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    with opener.open(req, timeout=90) as r:
        return r.read().decode("utf-8", "replace")

def _longdate(s):
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date().isoformat()
        except (ValueError, AttributeError):
            continue
    return ""

def euraxess(keyword, pages=EURAXESS_PAGES):
    out = []
    warm = _euraxess_session()
    for p in range(pages):
        q = urllib.parse.urlencode({"f[0]": "keywords:" + keyword,
                                    "f[1]": "job_research_profile:" + EURAXESS_R1,
                                    "page": p})
        try:
            page = _euraxess_get(warm, "https://euraxess.ec.europa.eu/jobs/search?" + q)
        except Exception as e:
            print("euraxess error:", keyword, e, file=sys.stderr)
            break
        items = _E_ITEM.findall(page)
        # This source has come back thin from a data centre IP while being fine
        # from a laptop, with no error to show for it. Log what actually arrived
        # so a short page is visible rather than silently becoming a small tab.
        print("  euraxess %-22s p%d: %6d bytes, %2d items"
              % (keyword[:22], p, len(page), len(items)), file=sys.stderr)
        if not items:
            break
        for b in items:
            t = _E_TITLE.search(b)
            if not t:
                continue
            out.append({
                "title": _text(t.group(2)),
                "employer": _grab(_E_ORG, b),
                "department": "",
                "country": _grab(_E_LOC, b),
                "location": _grab(_E_LOC, b),
                "salary": None,
                "posted": _longdate(_grab(_E_POST, b)),
                "deadline": "",
                "url": "https://euraxess.ec.europa.eu" + t.group(1),
            })
        if len(items) < 10:
            break
        time.sleep(1.0)
    return out

def euraxess_detail(url):
    """The deadline, plus whatever the advert says about funding. EURAXESS posts
    are usually salaried research contracts rather than student stipends, and a
    Marie Sklodowska-Curie action is open to any nationality by design, so that
    is the one case where eligibility can be called without guessing."""
    try:
        page = fetch(url)
    except Exception as e:
        print("euraxess detail error:", url, e, file=sys.stderr)
        return {}
    text = re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", page)))
    msca = bool(re.search(r"marie s[kc]|msca|horizon europe", text, re.I))
    funded = msca or bool(re.search(r"\b(fully funded|funded|salary|stipend|scholarship|"
                                    r"gross|remuneration)\b", text, re.I))
    intl = True if msca or re.search(r"any nationality|all nationalities|regardless of nationality|"
                                     r"international (?:candidates|applicants) (?:are )?welcome",
                                     text, re.I) else None
    return {"deadline": _longdate(_grab(_E_DEAD, text)),
            "funding": "full" if funded else "", "intlEligible": intl, "msca": msca}

def classify_phd(funding, intl):
    """For a self-funding international applicant, eligibility is the question
    that decides everything, so it drives the ramp the way sponsorship does on
    the jobs side."""
    if intl is False:
        return "weak", ("Funded for home students only, so this one is not open to you on "
                        "the funding as advertised. Worth a look only if you find another source.")
    if funding in ("full", "partial") and intl is True:
        return "strong", ""
    if funding in ("full", "partial"):
        return "caution", ("Funded, but the advert does not say whether international students "
                           "can apply. Check the eligibility before you spend time on it.")
    return "caution", ("Funding is not stated on the advert. Confirm there is a stipend and fees "
                       "cover, and that international students can apply.")

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
    except urllib.error.HTTPError as e:
        # The status alone says nothing useful. Google puts the actual complaint
        # in the body, so surface it, minus anything that could echo the key.
        reason, extra = "", []
        try:
            err = json.loads(e.read().decode("utf-8", "replace")).get("error", {})
            reason = err.get("message", "")
            # A 403 for a disabled service names the project it was refused for,
            # which is the only way to tell "not enabled" apart from "enabled on
            # a different project to the one the key belongs to".
            for d in err.get("details", []):
                consumer = (d.get("metadata") or {}).get("consumer")
                if consumer:
                    extra.append(consumer)
                for link in d.get("links", []):
                    if link.get("url"):
                        extra.append(link["url"])
            for sub in err.get("errors", []):
                if sub.get("reason"):
                    extra.append("reason=" + sub["reason"])
        except Exception:
            pass
        mask = lambda s: s.replace(GOOGLE_API_KEY, "***").replace(GOOGLE_CSE_ID, "***")
        print("google error %s: %s | %s" % (e.code, query[:34], mask(reason)[:180]), file=sys.stderr)
        for line in dict.fromkeys(extra):
            print("    %s" % mask(line)[:200], file=sys.stderr)
        return []
    except Exception as e:
        print("google error:", query[:40], e, file=sys.stderr)
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

    phd = build_phds()

    for bucket in (jobs, hs, phd):
        bucket.sort(key=lambda m: m["score"], reverse=True)
    print("Adzuna calls:", calls, file=sys.stderr)
    return {"jobs": jobs, "hs": hs, "phd": phd}

def build_phds():
    """Funded PhD openings, ranked against the research interests.

    The sponsor register does not apply here: a studentship is not a Skilled
    Worker vacancy. What matters instead is whether it is funded and whether an
    international student can hold the funding.
    """
    found, seen = [], set()
    for keyword in PHD_QUERIES:
        for row in jobs_ac_uk(keyword, pages=PHD_PAGES, phds=True):
            key = norm(row["title"]) + "|" + norm(row["employer"])
            if key in seen:
                continue
            if not within_days(row["posted"], PHD_MAX_DAYS):
                continue
            seen.add(key)
            blurb = " ".join([row["title"], row.get("department", ""), row["employer"]])
            row["score"] = min(100, phd_interest_score(blurb))
            found.append(row)
        time.sleep(1.0)

    # Only the best scoring ones earn a detail fetch, which is where funding and
    # eligibility actually live. The rest keep what the search page gave us.
    found.sort(key=lambda r: r["score"], reverse=True)
    out = []
    for i, row in enumerate(found):
        extra = {}
        if i < PHD_ENRICH:
            extra = phd_detail(row["url"])
            time.sleep(0.6)
        funding = extra.get("funding", "")
        intl = extra.get("intlEligible")
        stipend = extra.get("stipend") or row.get("salary")
        if stipend and not (8000 <= stipend <= 80000):
            stipend = None
        if not funding and stipend:
            funding = "full"
        status, note = classify_phd(funding, intl)
        # A perfect topic you cannot hold the funding for is not a good match,
        # so eligibility moves the score far more than any research theme does.
        score = row["score"] + (8 if funding else 0)
        score += 10 if intl is True else (-35 if intl is False else 0)
        country = country_of(row["location"] + " " + row["employer"])
        out.append({
            "score": min(100, score), "title": row["title"], "field": "Research",
            "employer": row["employer"],
            "location": ", ".join(v for v in (row["location"], country) if v and v != row["location"]),
            "country": country, "salary": None, "belowGeneral": False,
            "posted": row["posted"], "deadline": row["deadline"], "url": row["url"],
            "section": "phd", "status": status, "note": note, "source": "jobs.ac.uk",
            "funding": funding, "intlEligible": intl, "stipend": stipend,
        })
    print("%-12s %d queries -> %d kept (%d enriched)"
          % ("phd/jobs.ac", len(PHD_QUERIES), len(out), min(len(out), PHD_ENRICH)), file=sys.stderr)

    # --- EURAXESS: doctoral posts across Europe and its partner portals ------
    euro, offtopic = [], 0
    for keyword in EURAXESS_QUERIES:
        for row in euraxess(keyword):
            key = norm(row["title"]) + "|" + norm(row["employer"])
            if key in seen or not row["title"]:
                continue
            blurb = row["title"] + " " + row["employer"]
            if not phd_relevant(blurb):
                offtopic += 1
                continue
            if not within_days(row["posted"], PHD_MAX_DAYS):
                continue
            seen.add(key)
            row["score"] = phd_interest_score(blurb)
            euro.append(row)
        time.sleep(1.0)
    if offtopic:
        print("  euraxess dropped %d off-topic rows%s" % (offtopic,
              " (search looks broken, it is returning its generic listing)"
              if not euro else ""), file=sys.stderr)
    euro.sort(key=lambda r: r["score"], reverse=True)
    for i, row in enumerate(euro):
        extra = {}
        if i < EURAXESS_ENRICH:
            extra = euraxess_detail(row["url"])
            time.sleep(0.6)
        funding = extra.get("funding", "")
        intl = extra.get("intlEligible")
        status, note = classify_phd(funding, intl)
        if not note:
            note = ""
        if extra.get("msca"):
            note = ("Marie Sklodowska-Curie funded, which is open to any nationality and carries a "
                    "mobility rule. Check you meet the rule for this host country.")
        elif funding:
            note = ("A European doctoral post, usually a salaried contract rather than a student "
                    "stipend. The advert does not state nationality rules, so confirm them.")
        score = row["score"] + (8 if funding else 0) + (10 if intl is True else 0)
        out.append({
            "score": min(100, score), "title": row["title"], "field": "Research",
            "employer": row["employer"], "location": row["country"], "country": row["country"] or "Europe",
            "salary": None, "belowGeneral": False, "posted": row["posted"],
            "deadline": extra.get("deadline", ""), "url": row["url"], "section": "phd",
            "status": status, "note": note, "source": "euraxess",
            "funding": funding, "intlEligible": intl, "stipend": None,
        })
    print("%-12s %d queries -> %d kept (%d enriched)"
          % ("phd/euraxess", len(EURAXESS_QUERIES), len(euro), min(len(euro), EURAXESS_ENRICH)),
          file=sys.stderr)

    # Optional web sweep, the only thing here that reaches past the UK.
    if GOOGLE_API_KEY and GOOGLE_CSE_ID:
        n = 0
        for query in GOOGLE_PHD_QUERIES:
            for hit in google_search(query):
                if not hit["url"] or not re.search(r"phd|doctoral|studentship", hit["title"], re.I):
                    continue
                key = norm(hit["title"]) + "|web"
                if key in seen:
                    continue
                seen.add(key)
                country = country_of(hit["title"] + " " + hit["employer"],
                                     hit["url"], default="")
                out.append({
                    "score": min(100, phd_interest_score(hit["title"])), "title": hit["title"],
                    "field": "Research", "employer": hit["employer"] or "institution not named",
                    "location": country, "country": country, "salary": None, "belowGeneral": False,
                    "posted": "", "deadline": "", "url": hit["url"], "section": "phd",
                    "status": "weak", "source": "google",
                    "note": ("Found by web search, so none of the funding or eligibility has been "
                             "checked. Open it and confirm before you count on it."),
                    "funding": "", "intlEligible": None, "stipend": None,
                })
                n += 1
            time.sleep(1.0)
        print("%-12s %d queries -> %d kept" % ("phd/google", len(GOOGLE_PHD_QUERIES), n), file=sys.stderr)
    return out

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
    d = lambda n: (datetime.date.today() + datetime.timedelta(days=n)).isoformat()
    phd = [
        mk(score=81, title="PhD in Lasers and the Circular Economy",
           employer="University of Nottingham", location="Nottingham", country="UK",
           section="phd", status="strong", field="Research", source="jobs.ac.uk",
           funding="full", intlEligible=True, stipend=21805, salary=None, deadline=d(25)),
        mk(score=78, title="5 PhD Vacancies in Mechanics of Materials",
           employer="Ghent University", location="Ghent, Belgium", country="Belgium",
           section="phd", status="strong", field="Research", source="jobs.ac.uk",
           funding="full", intlEligible=True, stipend=None, salary=None, deadline=d(99)),
        mk(score=91, title="MSCA-DN e-ChemIn: Doctorate Candidate, Polymer Electrolytes",
           employer="INM Leibniz Institute", location="Sweden", country="Sweden",
           section="phd", status="strong", field="Research", source="euraxess",
           funding="full", intlEligible=True, stipend=None, salary=None, deadline=d(54),
           note="Marie Sklodowska-Curie funded, which is open to any nationality and carries a "
                "mobility rule. Check you meet the rule for this host country."),
        mk(score=85, title="PhD student: Lignin-sourced carbons for biorefinery",
           employer="Hasselt University", location="Belgium", country="Belgium",
           section="phd", status="caution", field="Research", source="euraxess",
           funding="full", intlEligible=None, stipend=None, salary=None, deadline=d(57),
           note="A European doctoral post, usually a salaried contract rather than a student "
                "stipend. The advert does not state nationality rules, so confirm them."),
        mk(score=71, title="PhD Studentship in Life Cycle Assessment: Evaluating Technologies",
           employer="Teagasc", location="Dublin, Ireland", country="Ireland",
           section="phd", status="strong", field="Research", source="jobs.ac.uk",
           funding="full", intlEligible=True, stipend=21744, salary=None, deadline=d(1)),
        mk(score=55, title="PhD Studentship: CO2 Utilisation for Subsurface Energy Storage",
           employer="The University of Manchester", location="Manchester", country="UK",
           section="phd", status="caution", field="Research", source="jobs.ac.uk",
           funding="full", intlEligible=None, stipend=21805, salary=None, deadline=d(42),
           note=classify_phd("full", None)[1]),
        mk(score=46, title="PhD Studentships: Process Industries Net Zero CDT",
           employer="Newcastle University", location="Newcastle upon Tyne", country="UK",
           section="phd", status="weak", field="Research", source="jobs.ac.uk",
           funding="full", intlEligible=False, stipend=21805, salary=None, deadline=d(3),
           note=classify_phd("full", False)[1]),
    ]
    return {"jobs": jobs, "hs": hs, "phd": phd}

if __name__ == "__main__":
    write(demo() if "--demo" in sys.argv else build_today())
