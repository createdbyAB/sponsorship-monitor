"""Company-watch eligibility engine. Pure: no I/O, no network (see test_eligibility.py).

Three things must all hold for a vacancy to convert into a *first* Certificate of
Sponsorship, and most job boards test none of them:

  1. the employer holds an A-rated licence (companies.json handles this upstream);
  2. the occupation code is open to a first CoS -- Higher Skilled (RQF6+), or
     medium skilled AND on the Temporary Shortage List; closed codes never;
  3. the job actually pays the floor for that code.

This module is (2) and (3): it matches a title to a SOC code, parses the
advertised salary, and returns a verdict with the arithmetic shown. Figures come
from data/occupations.json and data/roleFamilies.json, never hardcoded here.

The salary rules differ and are easy to get wrong, so read these:
  - Higher-skilled figures are the new-entrant floor; they PRO-RATE below a
    37.5h week. The £33,400 general minimum applies in full alongside and NEVER
    pro-rates, so a pro-rated higher-skilled floor is still floored at £33,400.
  - Temporary Shortage List figures are the salary the job must actually pay;
    there is NO new-entrant discount on a shortage-list rate.
  - A band is tested at its MINIMUM, not the midpoint or the top: appointment is
    at the band minimum unless the advert says otherwise.
  - Closed codes fail at any salary.
"""
from __future__ import annotations

import datetime
import json
import os
import re

FULL_TIME_HOURS = 37.5


def load(data_dir):
    """Read the two config files. The only I/O in this module, kept apart from
    the pure functions so those stay unit-testable without the filesystem."""
    with open(os.path.join(data_dir, "occupations.json")) as f:
        occ = json.load(f)
    with open(os.path.join(data_dir, "roleFamilies.json")) as f:
        roles = json.load(f)
    return occ, roles


# --- role matching --------------------------------------------------------
# A 4-digit year not glued to a longer number, so "AGGP2027" and "2027 Analyst"
# both trip the future-cohort filter, but a reference like "10023456" does not.
_YEAR = re.compile(r"(?<!\d)(20\d\d)(?!\d)")


def match_role(title, roles, today_year=None):
    """The role family for a title, or None if excluded / unmatched.

    Excludes run first: graduate schemes, internships, apprenticeships and
    closed-code safety titles (`excludeTitles`); seniority markers
    (`excludeSeniority`) unless the title is on the `seniorityKeep` allow-list;
    and, when `today_year` is given, a future intake year in the title (the
    "2027 ... Analyst" graduate-cohort shape), which recruits too far out to use.
    First matching family phrase wins, so the list order in roleFamilies.json is
    the priority (exception phrases sit ahead of the family they would fall into).
    """
    t = (title or "").lower()
    if not t:
        return None
    if any(x in t for x in roles.get("excludeTitles", [])):
        return None
    keep = any(k in t for k in roles.get("seniorityKeep", []))
    if not keep and any(s in t for s in roles.get("excludeSeniority", [])):
        return None
    if today_year is not None:
        for y in _YEAR.findall(title or ""):
            if int(y) > today_year:
                return None                       # future cohort / graduate scheme
    for fam in roles.get("families", []):
        for phrase in fam["titles"]:
            if phrase in t:
                return {"soc": fam["soc"], "family": fam["family"],
                        "confidence": fam.get("confidence", "check"), "matched": phrase}
    return None


# --- salary parsing -------------------------------------------------------
_MONEY = re.compile(r"£?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?", re.I)
_UNVERIFIED = ("competitive", "negotiable", "depending on experience", "doe",
               "market rate", "excellent salary", "attractive")
_RANGE = re.compile(r"(?:to|-|–|—|between)")


def _num(raw, suffix):
    v = float(raw.replace(",", ""))
    if suffix:                                    # "35k"
        v *= 1000
    return v


def parse_salary(text, hours=FULL_TIME_HOURS):
    """Parse an advertised salary into {min, max, period, stated, note}.

    A band gives min and max (both annualised); the caller tests min. Hourly and
    daily rates are annualised at the stated hours and flagged in `note`. Where
    the advert is "competitive"/"negotiable"/blank, `stated` is False and the row
    must render as unverified, never as passing.
    """
    s = (text or "").strip().lower()
    out = {"min": None, "max": None, "period": "unstated", "stated": False, "note": ""}
    if not s or any(w in s for w in _UNVERIFIED) and not _MONEY.search(s):
        return out
    nums = [_num(m.group(1), m.group(2)) for m in _MONEY.finditer(s)]
    nums = [n for n in nums if n >= 1]            # drop stray "0"/decimals
    if not nums:
        return out
    period, note = "year", ""
    if re.search(r"per\s*hour|/\s*hour|\bp/?h\b|hourly|per hr", s):
        period, factor, note = "hour", hours * 52, "annualised from an hourly rate at %g h/wk" % hours
        nums = [n * factor for n in nums]
    elif re.search(r"per\s*day|/\s*day|\bday rate\b|daily|per diem", s):
        period, factor, note = "day", 5 * 52, "annualised from a day rate at 5 days/wk"
        nums = [n * factor for n in nums]
    lo, hi = min(nums), max(nums)
    out.update({"min": round(lo), "max": round(hi), "period": period, "stated": True, "note": note})
    return out


# A £-figure or band, optionally with a k suffix and a per-annum/hour/day tail.
_SAL_IN_TEXT = re.compile(
    r"£\s?\d[\d,]*(?:\.\d+)?\s*k?"
    r"(?:\s*(?:-|–|—|to)\s*£?\s?\d[\d,]*(?:\.\d+)?\s*k?)?"
    r"(?:\s*(?:per|/|p)\s*(?:annum|year|hour|hr|day))?", re.I)


def find_salary(text, hours=FULL_TIME_HOURS):
    """Pull an advertised salary out of a free-text advert body (ATS adverts
    carry the real figure, unlike Adzuna's predicted number). Returns the same
    shape as parse_salary; not stated when no plausible salary is found. A lone
    small figure (e.g. "£10 voucher") is rejected as not a salary."""
    blank = {"min": None, "max": None, "period": "unstated", "stated": False, "note": ""}
    if not text:
        return blank
    for m in _SAL_IN_TEXT.finditer(text):
        p = parse_salary(m.group(0), hours)
        if not p["stated"]:
            continue
        if p["period"] == "year" and p["min"] < 12000:
            continue                              # too low to be an annual salary
        return p
    return blank


# --- the verdict engine ---------------------------------------------------
def required_floor(soc, occ, hours=FULL_TIME_HOURS):
    """(floor, basis, prorated, general) the advert must clear, or ('closed'/None).

    basis is 'rate' for a Temporary Shortage List code (fixed, no discount) or
    'floor' for a higher-skilled code (pro-rates below 37.5h, but never below the
    general minimum, which itself never pro-rates)."""
    if soc in occ["closed"]:
        return {"floor": None, "basis": "closed", "prorated": None, "general": None}
    if soc in occ["temporaryShortageList"]:
        return {"floor": occ["temporaryShortageList"][soc], "basis": "rate",
                "prorated": None, "general": None}
    if soc in occ["higherSkilled"]:
        base, general = occ["higherSkilled"][soc], occ["generalMinimum"]
        if hours < FULL_TIME_HOURS:
            prorated = round(base * hours / FULL_TIME_HOURS)
            return {"floor": max(prorated, general), "basis": "floor",
                    "prorated": prorated, "general": general}
        return {"floor": base, "basis": "floor", "prorated": None, "general": general}
    return {"floor": None, "basis": "unknown", "prorated": None, "general": None}


def _pounds(n):
    return "£%s" % format(int(round(n)), ",")


def applicable_deadline(soc, occ):
    """The deadline that governs this code, and why. There are two, and the
    earlier one governs the codes the user most wants:
      - a Temporary Shortage List code is only on the list for a CoS assigned
        before temporaryShortageListExpiry (31 Dec 2026); after that it is closed;
      - a higher-skilled code has no expiry of its own, so the binding date is the
        user's own Graduate visa expiry (31 Mar 2027).
    A closed code has no deadline -- it already fails."""
    if soc in occ["temporaryShortageList"]:
        return occ.get("temporaryShortageListExpiry"), "temporary shortage list"
    if soc in occ["higherSkilled"]:
        return occ.get("graduateVisaExpiry"), "graduate visa"
    return None, ""


def _too_late_for_tsl(closing, occ):
    """True when a shortage-list vacancy's closing date plus the CoS-assignment
    allowance runs past the shortage-list expiry, so a first CoS could not be
    assigned in time. It is the CoS assignment date that matters, so we add the
    allowance (shortlisting, interview, offer, checks, assignment) to the close."""
    exp = occ.get("temporaryShortageListExpiry")
    allow = occ.get("cosAssignmentAllowanceDays", 60)
    if not (closing and exp):
        return False
    try:
        c = datetime.date.fromisoformat(str(closing)[:10])
        e = datetime.date.fromisoformat(exp)
    except ValueError:
        return False
    return c + datetime.timedelta(days=allow) > e


def evaluate(soc, parsed, occ, hours=FULL_TIME_HOURS, closing=""):
    """Verdict for one vacancy: pass / fail / unverified / too_late / closed, with
    the required floor, the advertised floor, the gap, the deadline that governs
    the code, and a plain-English reason. `closing` is the vacancy's closing date
    (ISO), used only for the shortage-list timing check."""
    rf = required_floor(soc, occ, hours)
    basisword = "rate" if rf["basis"] == "rate" else "floor"
    deadline, basis = applicable_deadline(soc, occ)
    res = {"soc": soc, "verdict": None, "requiredFloor": rf["floor"],
           "advertisedFloor": None, "shortfall": None, "headroom": None,
           "generalMinimum": rf["general"], "proratedFloor": rf["prorated"],
           "applicableDeadline": deadline, "deadlineBasis": basis,
           "stated": bool(parsed and parsed.get("stated")), "reason": ""}

    if rf["basis"] == "closed":
        res.update(verdict="closed", reason="closed occupation code %s" % soc,
                   applicableDeadline=None, deadlineBasis="")
        if res["stated"]:
            res["advertisedFloor"] = parsed["min"]
        return res
    if rf["basis"] == "unknown":
        res.update(verdict="unverified",
                   reason="occupation code %s is not on the eligibility list" % soc)
        return res

    tail = " for code %s" % soc
    if rf["prorated"] is not None:                # part-time higher-skilled
        tail += " (pro-rated floor %s at %gh, general minimum %s never pro-rates)" % (
            _pounds(rf["prorated"]), hours, _pounds(rf["general"]))

    # Salary verdict first.
    if not res["stated"]:
        res.update(verdict="unverified",
                   reason="salary not stated — needs %s %s%s" % (_pounds(rf["floor"]), basisword, tail))
    else:
        advertised = parsed["min"]
        res["advertisedFloor"] = advertised
        if advertised >= rf["floor"]:
            res.update(verdict="pass", headroom=advertised - rf["floor"],
                       reason="%s above the %s %s%s" % (_pounds(advertised - rf["floor"]),
                                                        _pounds(rf["floor"]), basisword, tail))
        else:
            res.update(verdict="fail", shortfall=rf["floor"] - advertised,
                       reason="%s below the %s %s%s" % (_pounds(rf["floor"] - advertised),
                                                        _pounds(rf["floor"]), basisword, tail))

    # A shortage-list role that cannot reach an assigned CoS before the list
    # closes is dead whatever the salary, so this overrides the salary verdict
    # (but never a closed code, handled above). Higher-skilled codes never trip
    # it -- their deadline is the personal Graduate visa, shown but not enforced.
    if rf["basis"] == "rate" and _too_late_for_tsl(closing, occ):
        res.update(verdict="too_late",
                   reason="closes %s — a Certificate of Sponsorship could not be assigned "
                          "before the shortage list closes %s (allowing %d days)"
                          % (str(closing)[:10], occ.get("temporaryShortageListExpiry"),
                             occ.get("cosAssignmentAllowanceDays", 60)))
    return res
