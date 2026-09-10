"""Skilled Worker occupation-code gate. gov.uk verified 2026-09-10.

Since 22 July 2025 a first Certificate of Sponsorship needs the OCCUPATION CODE
to be Higher Skilled on the eligible-occupations list, OR medium skilled AND on
the Temporary Shortage List. The employer holding a sponsor licence is necessary
but no longer sufficient. This module is the code-level half of that test.

Pure by design: no I/O, no network, so it is unit-testable on its own
(see test_soc.py). The `from __future__` import keeps the `str | None`
annotations lazy so the module also imports on Python 3.9.
"""
from __future__ import annotations

import re

# Higher Skilled -- sponsorable outright
HIGHER = {"2111", "2119", "2125", "2127", "2129", "2131", "2133", "2136", "2141",
          "2142", "2151", "2152", "2162", "2311", "2431", "2440", "2482", "2483"}

# Medium skilled but on the Temporary Shortage List -- open to a switcher
TSL = {"3111", "3112", "3113", "3115", "3116", "3120", "3131", "3132", "3133",
       "3429", "3533", "3541", "3544", "3549", "3554", "3571"}

DEAD = {"3582", "3581", "3319"}

# New-entrant floor = max(33400, 70% of going rate). Only codes above 33400 listed.
FLOOR = {"2482": 33740, "2431": 35140, "2127": 36330, "2311": 36820,
         "2133": 38430, "2440": 39550, "2131": 40740}

# TSL codes publish the salary the job must actually pay
TSL_RATE = {"3111": 33400, "3115": 33400, "3549": 33400, "3554": 33400, "3571": 33400,
            "3132": 33400, "3120": 33800, "3133": 34600, "3116": 34800, "3544": 34900,
            "3131": 35200, "3541": 35300, "3112": 39300, "3429": 39300, "3113": 42500,
            "3533": 48700}

GENERAL_FLOOR = 33400

# Title -> SOC inference. Order matters: DEAD patterns must match before the
# generic ones, so "health and safety advisor" never falls through to 2482.
# The "health & safety" alternative accepts the separators real titles use
# ("and", ",", "&", "/"); without that, e.g. "Head of Health, Safety and
# Compliance" would slip past 3582 and match the generic "compliance" rule,
# surfacing a closed occupation as eligible (a false positive the whole gate
# exists to prevent).
TITLE_MAP = [
    (r"\b(health\s*(?:&|and|,|/)?\s*safety|h&s|hse|ehs|sheq|hseq|qshe|\bshe\b|fire safety|"
     r"safety (officer|advis|co-?ordinator|manager|technician))", "3582"),
    (r"\b(inspector of standards|trading standards|building control)", "3581"),
    (r"\b(process safety|process engineer|chemical engineer|production engineer|"
     r"control and instrumentation)", "2125"),
    (r"\b(energy engineer|acoustic engineer|food technologist)", "2129"),
    (r"\b(chemist|analytical scientist|formulation scientist)", "2111"),
    (r"\b(ux|ui|user experience|product design|interaction design|web design)", "2141"),
    (r"\b(graphic design|multimedia design|motion design)", "2142"),
    (r"\b(sustainability|environmental (consultant|scientist|engineer)|energy manager)", "2152"),
    (r"\benvironmental health\b", "2483"),
    (r"\b(ecologist|conservation|heritage officer)", "2151"),
    (r"\b(research (assistant|associate|fellow)|postdoc)", "2162"),
    (r"\b(qa analyst|test analyst|software quality)", "2136"),
    (r"\b(compliance|regulatory affairs|quality assurance professional|qms)", "2482"),
    (r"\b(business analyst|risk analyst|management consultant)", "2431"),
    (r"\b(project engineer|engineering project manager)", "2127"),
    (r"\b(lecturer|teaching fellow)", "2311"),
    (r"\b(data engineer|systems analyst|data architect)", "2133"),
    (r"\b(project manager|programme manager|business change)", "2440"),
    (r"\b(data analyst|performance analyst|insight analyst)", "3544"),
    (r"\b(process technician|production planner|planning technician)", "3116"),
    (r"\b(lab(oratory)? technician)", "3111"),
    (r"\bqa technician|quality control technician", "3115"),
    (r"\bcad technician|design technician", "3120"),
]

# Compile once. First matching pattern wins, so the list order is the priority.
_TITLE_RX = [(re.compile(pat, re.I), code) for pat, code in TITLE_MAP]


def infer_soc(title: str) -> str | None:
    """First matching pattern wins. Returns None when nothing matches."""
    text = title or ""
    for rx, code in _TITLE_RX:
        if rx.search(text):
            return code
    return None


def required_salary(soc: str, hours: float = 37.5) -> int | None:
    """TSL codes use the published rate. Higher-skilled codes use the
    new-entrant floor, pro-rated for hours below 37.5, never below 33400.
    Returns None for codes outside both sets."""
    if soc in TSL_RATE:
        return TSL_RATE[soc]
    if soc in HIGHER:
        base = FLOOR.get(soc, GENERAL_FLOOR)
        if hours < 37.5:
            base = base * hours / 37.5
        return max(GENERAL_FLOOR, round(base))
    return None


def sponsorable(soc: str | None, salary_max: int | None, hours: float = 37.5) -> bool:
    """True when the code is sponsorable and the top of the advertised band
    clears the requirement. Unknown or unpublished salary passes, so nothing
    is silently dropped for lacking a figure. DEAD and unmapped codes fail."""
    if soc is None or soc in DEAD:
        return False
    req = required_salary(soc, hours)
    if req is None:
        return False
    if salary_max is None:
        return True
    return salary_max >= req
