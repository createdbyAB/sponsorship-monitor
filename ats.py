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
                out.append({"id": path, "title": j.get("title", ""),
                            "location": j.get("locationsText", ""),
                            "url": "https://%s%s" % (host, path),
                            "posted": "", "description": ""})
        return out
    return _safe(go, "workday/" + tenant)


_ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
             "smartrecruiters": smartrecruiters, "workable": workable, "recruitee": recruitee}


def fetch_board(board):
    """board: {ats, slug, ...}. Dispatches to the adapter. Workday takes the
    board dict (host/tenant/site); the rest take the slug."""
    ats = board.get("ats")
    if ats == "workday":
        return workday(board)
    fn = _ADAPTERS.get(ats)
    return fn(board.get("slug", "")) if fn else []
