"""Direct ATS adapters for the Company watch section.

Most of the watched employers run their careers site on a third-party Applicant
Tracking System (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee,
Workday). Each ATS exposes a public, unauthenticated JSON endpoint keyed by a
board slug -- the same data the front end renders, without scraping the rendered
page. Hitting it returns exactly one employer's postings, so there is no
company-name guesswork: an ATS-sourced row is definitionally that employer's.

This is the open-jobs (github.com/elliottdehn/open-jobs, CC0) approach, ported to
Python: one adapter per ATS, each `fetch(board) -> [vacancy]`, every vacancy
normalised to the same shape the Adzuna adapter produces:

    {id, title, location, url, posted, description}

`description` is the advert body where the listing endpoint carries it (used to
read the real advertised salary); "" when it does not. A board that 404s is gone
and yields []; a transient error yields [] too, so one dead board never breaks
the run. Standard library only.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

UA = ("sponsorship-monitor (personal daily job alert; "
      "+https://github.com/createdbyAB/sponsorship-monitor)")
TIMEOUT = 30
_TAGS = re.compile(r"<[^>]+>")


def _text(s):
    return re.sub(r"\s+", " ", (_TAGS.sub(" ", s or ""))).strip()


def _req(url, data=None, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _text_fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _safe(fn, label):
    """Every adapter returns [] rather than raising, so a dead or changed board
    thins the section instead of failing the whole run."""
    try:
        return fn()
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print("ats %s: HTTP %s" % (label, e.code), file=__import__("sys").stderr)
        return []
    except Exception as e:
        print("ats %s: %s" % (label, e), file=__import__("sys").stderr)
        return []


# --- adapters (one per ATS) ------------------------------------------------
def greenhouse(slug):
    def go():
        body = _req("https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true"
                    % urllib.parse.quote(slug))
        out = []
        for j in body.get("jobs", []):
            out.append({"id": str(j.get("id", "")), "title": j.get("title", ""),
                        "location": (j.get("location") or {}).get("name", ""),
                        "url": j.get("absolute_url", ""), "posted": (j.get("updated_at") or "")[:10],
                        "description": _text(j.get("content", ""))})
        return out
    return _safe(go, "greenhouse/" + slug)


def lever(slug):
    def go():
        body = _req("https://api.lever.co/v0/postings/%s?mode=json" % urllib.parse.quote(slug))
        out = []
        for j in body:
            out.append({"id": str(j.get("id", "")), "title": j.get("text", ""),
                        "location": (j.get("categories") or {}).get("location", ""),
                        "url": j.get("hostedUrl", ""), "posted": "",
                        "description": _text(j.get("descriptionPlain") or j.get("description", ""))})
        return out
    return _safe(go, "lever/" + slug)


def ashby(slug):
    def go():
        body = _req("https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=true"
                    % urllib.parse.quote(slug))
        out = []
        for j in body.get("jobs", []):
            out.append({"id": str(j.get("id", "")), "title": j.get("title", ""),
                        "location": j.get("location", ""), "url": j.get("jobUrl", ""),
                        "posted": (j.get("publishedAt") or "")[:10],
                        "description": _text(j.get("descriptionPlain") or "")})
        return out
    return _safe(go, "ashby/" + slug)


def smartrecruiters(slug):
    def go():
        body = _req("https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100"
                    % urllib.parse.quote(slug))
        out = []
        for j in body.get("content", []):
            loc = j.get("location") or {}
            jid = str(j.get("id", ""))
            out.append({"id": jid, "title": j.get("name", ""),
                        "location": ", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
                        "url": "https://jobs.smartrecruiters.com/%s/%s" % (urllib.parse.quote(slug), jid),
                        "posted": (j.get("releasedDate") or "")[:10], "description": ""})
        return out
    return _safe(go, "smartrecruiters/" + slug)


def workable(slug):
    def go():
        body = _req("https://apply.workable.com/api/v3/accounts/%s/jobs" % urllib.parse.quote(slug),
                    data={"query": "", "location": [], "department": [], "worktype": [],
                          "remote": [], "workplace": []})
        out = []
        for j in body.get("results", []):
            loc = j.get("location") or {}
            out.append({"id": str(j.get("shortcode") or j.get("id", "")), "title": j.get("title", ""),
                        "location": loc.get("location_str") or ", ".join(
                            x for x in (loc.get("city"), loc.get("country")) if x),
                        "url": j.get("url") or "https://apply.workable.com/%s/j/%s/" % (slug, j.get("shortcode", "")),
                        "posted": (j.get("published_on") or j.get("created_at") or "")[:10],
                        "description": _text(j.get("description", ""))})
        return out
    return _safe(go, "workable/" + slug)


def recruitee(slug):
    def go():
        body = _req("https://%s.recruitee.com/api/offers/" % urllib.parse.quote(slug))
        out = []
        for j in body.get("offers", []):
            out.append({"id": str(j.get("id", "")), "title": j.get("title", ""),
                        "location": j.get("location") or j.get("city", ""),
                        "url": j.get("careers_url") or j.get("careers_apply_url", ""),
                        "posted": (j.get("published_at") or "")[:10],
                        "description": _text(j.get("description", ""))})
        return out
    return _safe(go, "recruitee/" + slug)


# A Workday board can hold thousands of global jobs, so blind paging rarely
# reaches the UK entry/associate roles company watch wants. Instead search for
# the relevant terms (Workday's searchText) and let the UK/role filters downstream
# do the rest. Kept broad and few, since each is one request per board.
WORKDAY_SEARCHES = ("data analyst", "business analyst", "data engineer",
                    "analyst", "designer", "project")

def workday(board):
    """Workday is per-tenant: board carries {host, tenant, site}. The listing is a
    POST search; descriptions need a per-job detail GET (not fetched), so salary
    is usually unstated for Workday rows and the verdict is 'unverified' until the
    advert is opened -- honest, not a miss."""
    host, tenant, site = board.get("host"), board.get("tenant"), board.get("site")
    if not (host and tenant and site):
        return []
    def go():
        base = "https://%s/wday/cxs/%s/%s" % (host, tenant, site)
        out, seen = [], set()
        for term in WORKDAY_SEARCHES:
            body = _req(base + "/jobs", data={"appliedFacets": {}, "limit": 20,
                                              "offset": 0, "searchText": term})
            for j in body.get("jobPostings", []):
                path = j.get("externalPath", "")
                if not path or path in seen:
                    continue
                seen.add(path)
                # Some tenants (e.g. Accenture) return locationsText null and put
                # the location in the externalPath: /job/{location}/{slug}_{id}.
                loc = j.get("locationsText") or ""
                if not loc:
                    seg = path.split("/")
                    if len(seg) >= 3 and seg[1] == "job":
                        loc = urllib.parse.unquote(seg[2]).replace("-", " ")
                out.append({"id": path, "title": j.get("title", ""), "location": loc,
                            "url": "https://%s%s" % (host, path),
                            "posted": "", "description": ""})
        return out
    return _safe(go, "workday/" + tenant)


# Enterprise ATS the big corporates use. Each is heavier than the startup ATS:
# Oracle Cloud must discover its site; Eightfold and Phenom page small and carry
# no salary in the listing (so their rows read "unverified" until opened).
_ENTERPRISE_SEARCHES = ("data analyst", "business analyst", "data engineer",
                        "analyst", "designer", "project")

def oraclecloud(board):
    """Oracle Cloud HCM (Candidate Experience). board carries {host}; the site is
    discovered. The listing carries the description, so real salaries are read."""
    host = board.get("host")
    if not host:
        return []
    base = "https://%s/hcmRestApi/resources/latest" % host
    def sites():
        if board.get("site"):
            return [board["site"]]                # confirmed site, skip discovery
        try:
            body = _req(base + "/recruitingCESites?onlyData=true")
        except Exception:
            return ["CX_1"]
        items = body.get("items", [])
        active = [s["SiteNumber"] for s in items
                  if s.get("SiteNumber") and s.get("StatusCode", "ORA_ACTIVE") == "ORA_ACTIVE"]
        return active or [s["SiteNumber"] for s in items if s.get("SiteNumber")] or ["CX_1"]
    def go():
        out, seen = [], set()
        for site in sites()[:3]:                  # first few active sites
            finder = ("findReqs;siteNumber=%s,limit=200,offset=0,sortBy=POSTING_DATES_DESC" % site)
            url = (base + "/recruitingCEJobRequisitions?onlyData=true"
                   "&expand=requisitionList.secondaryLocations&finder=" + urllib.parse.quote(finder, safe=";,="))
            body = _req(url)
            for item in body.get("items", []):
                for r in item.get("requisitionList", []):
                    rid = str(r.get("Id", ""))
                    if rid in seen:
                        continue
                    seen.add(rid)
                    locs = [r.get("PrimaryLocation")] + [l.get("Name") for l in r.get("secondaryLocations", [])]
                    desc = " ".join(x for x in (r.get("ShortDescriptionStr"),
                                    r.get("ExternalResponsibilitiesStr"), r.get("ExternalQualificationsStr")) if x)
                    out.append({"id": rid, "title": r.get("Title", ""),
                                "location": "; ".join(x for x in locs if x),
                                "url": "https://%s/hcmUI/CandidateExperience/en/sites/%s/job/%s" % (host, site, urllib.parse.quote(rid)),
                                "posted": (r.get("PostedDate") or "")[:10], "description": _text(desc)})
        return out
    return _safe(go, "oraclecloud/" + host)

def eightfold(board):
    """Eightfold. board carries {host, domain}: the request host (e.g.
    hsbc.eightfold.ai or jobs.vodafone.com) and the `domain` param (hsbc.com,
    vodafone.com) -- both are instance-specific. Two API generations exist, PCS
    (/api/apply/v2/jobs, positions at top level) and PCSX (/api/pcsx/search,
    positions under data); we try both. No salary in the listing, so unverified."""
    host, domain = board.get("host"), board.get("domain")
    if not host:
        return []
    domain = domain or host
    def go():
        out, seen = [], set()
        for term in _ENTERPRISE_SEARCHES:
            q = urllib.parse.urlencode({"domain": domain, "query": term, "start": 0, "num": 10})
            positions = []
            for path, dig in (("/api/apply/v2/jobs", lambda b: b.get("positions", [])),
                              ("/api/pcsx/search", lambda b: (b.get("data") or {}).get("positions", []))):
                try:
                    positions = dig(_req("https://%s%s?%s" % (host, path, q)))
                    if positions:
                        break
                except Exception:
                    continue
            for p in positions:
                pid = str(p.get("id", ""))
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                loc = p.get("location") or (p.get("locations") or [""])[0]
                out.append({"id": pid, "title": p.get("name", ""), "location": loc or "",
                            "url": p.get("canonicalPositionUrl") or "https://%s/careers/job/%s" % (host, pid),
                            "posted": "", "description": ""})
        return out
    return _safe(go, "eightfold/" + host)

def phenom(board):
    """Phenom People. board carries {host} (the careers domain). Listing is a POST
    to /widgets; no salary in the teaser, so rows read 'unverified' until opened."""
    host = board.get("host")
    if not host:
        return []
    def go():
        out, seen = [], set()
        for term in _ENTERPRISE_SEARCHES:
            payload = {"ddoKey": "refineSearch", "sortBy": "", "subsearch": "", "from": 0,
                       "jobs": True, "counts": False, "all_fields": [], "size": 50,
                       "clearAll": False, "jdsource": "facets", "isSliderEnable": False,
                       "keywords": term, "global": True}
            try:
                body = _req("https://%s/widgets" % host, data=payload)
            except Exception:
                continue
            jobs = ((body.get("refineSearch") or {}).get("data") or {}).get("jobs", [])
            for p in jobs:
                jid = p.get("jobSeqNo") or p.get("jobId")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                out.append({"id": str(jid), "title": p.get("title", ""),
                            "location": p.get("cityStateCountry") or p.get("location", ""),
                            "url": "https://%s/us/en/job/%s" % (host, urllib.parse.quote(str(jid))),
                            "posted": (p.get("postedDate") or "")[:10], "description": ""})
        return out
    return _safe(go, "phenom/" + host)


_SF_TILE = re.compile(r'<li class="job-tile job-id-(\d+)[^"]*"[^>]*data-url="([^"]+)"(.*?)</li>', re.S)
_SF_TITLE = re.compile(r'<a class="jobTitle-link[^"]*"[^>]*>(.*?)</a>', re.S)

def successfactors(board):
    """SAP SuccessFactors "Recruiting Marketing" (RMK / Jobs2Web) career sites.
    No JSON API -- the search endpoint returns server-rendered HTML job tiles,
    which are stable and structured (job-id, data-url, jobTitle-link), so we parse
    those (like the NHS scraper) rather than the JS-rendered page. board carries
    {host, prefix?}: the RMK host and an optional company path segment. The tile's
    data-url ends in the location + postcode, from which the UK filter reads the
    location; no salary in the tile, so rows read 'unverified'."""
    host, prefix = board.get("host"), (board.get("prefix") or "").strip("/")
    if not host:
        return []
    base = "https://%s/%s/tile-search-results/" % (host, prefix) if prefix else \
           "https://%s/tile-search-results/" % host
    def go():
        out, seen = [], set()
        for term in _ENTERPRISE_SEARCHES:
            q = urllib.parse.urlencode({"data": json.dumps({"keywords": term})})
            try:
                page = _text_fetch(base + "?" + q)
            except Exception:
                continue
            for jid, data_url, body in _SF_TILE.findall(page):
                if jid in seen:
                    continue
                seen.add(jid)
                tm = _SF_TITLE.search(body)
                title = _text(tm.group(1)) if tm else ""
                # data-url: /job/{Location-Title-words-POSTCODE}/{id}/ -- the slug
                # carries the location and postcode the UK filter needs.
                slug = data_url.split("/job/")[-1].rsplit("/", 2)[0] if "/job/" in data_url else ""
                loc = urllib.parse.unquote(slug).replace("-", " ")
                out.append({"id": jid, "title": title, "location": loc,
                            "url": "https://%s%s" % (host, data_url), "posted": "", "description": ""})
        return out
    return _safe(go, "successfactors/" + host)


_ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
             "smartrecruiters": smartrecruiters, "workable": workable, "recruitee": recruitee}
# Adapters whose board carries a host/coordinates rather than a bare slug.
_HOST_ADAPTERS = {"workday": workday, "oraclecloud": oraclecloud, "phenom": phenom,
                  "eightfold": eightfold, "successfactors": successfactors}


def fetch_board(board):
    """board: {ats, slug|host, ...}. Dispatches to the adapter. Workday, Oracle
    Cloud and Phenom take the board dict (host etc.); the rest take the slug."""
    ats = board.get("ats")
    if ats in _HOST_ADAPTERS:
        return _HOST_ADAPTERS[ats](board)
    fn = _ADAPTERS.get(ats)
    return fn(board.get("slug", "")) if fn else []
