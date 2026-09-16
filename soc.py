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

# Eligible NHS clinical occupations (RQF6+ health professionals: doctors, nurses,
# midwives, AHPs, pharmacists, radiographers, paramedics, psychologists, dental
# and eye-care practitioners, operating department practitioners, biomedical and
# biological scientists). Their pay sits on NHS national pay scales, not the
# going-rate floors this module models, so the pay check is DEFERRED to the
# advert: the code confirms the occupation is open, the advert confirms the
# money. gov.uk (Appendix Skilled Occupations + ONS SOC 2020) verified 2026-09-16.
CLINICAL = {"2112", "2113", "2211", "2212", "2221", "2222", "2223", "2224",
            "2225", "2226", "2229", "2231", "2232", "2233", "2234", "2235",
            "2236", "2237", "2251", "2252", "2253", "2254", "2255", "2259"}

# Medium-skilled health/care support (RQF 3-5): pharmacy and dental/medical
# technicians, healthcare assistants, care workers. Since 22 July 2025 a
# medium-skilled code is sponsorable only if on the Temporary Shortage or
# Immigration Salary list; these are on neither, and care workers (6135/6136)
# are closed to new overseas applicants outright. Closed to a first CoS.
MEDIUM_CLOSED = {"3212", "3213", "6131", "6135", "6136"}

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
    # --- NHS clinical occupations. Closed RQF3-5 support roles are matched
    #     BEFORE the eligible professions, so a "nursing assistant" is not read
    #     as a registered "nurse", nor a "pharmacy technician" as a "pharmacist".
    #     Pay is deferred (see CLINICAL), so the code only decides open vs closed.
    # "nursing associate" is deliberately NOT closed here: it is a registered
    # band-4 role of ambiguous eligibility, so it is left to fall through to the
    # advert (SOC ?) rather than asserted closed on the code.
    (r"\b(healthcare|health\s*care|nursing|ward|clinical)\s+support\s+worker\b|"
     r"\b(healthcare|health\s*care|nursing|ward)\s+assistant\b|"
     r"\bnursing\s+auxiliary\b|\bhca\b|\bphlebotom", "6131"),
    (r"\b(care worker|care assistant|home carer|domiciliary care|"
     r"senior carer|senior care worker|support worker)\b", "6135"),
    (r"\b(pharmacy|pharmaceutical)\s+technician\b", "3212"),
    (r"\bdental\s+(nurse|technician)\b|\bdental\s+laboratory\b", "3213"),
    (r"\b(midwife|midwifery)\b", "2231"),
    (r"\b(district|community)\s+nurse\b", "2232"),
    (r"\b(clinical\s+nurse\s+specialist|specialist\s+nurse)\b", "2233"),
    (r"\b(nurse practitioner|advanced nurse|advanced clinical practitioner)\b", "2234"),
    (r"\b(mental health nurse|\brmn\b)\b", "2235"),
    (r"\b(children'?s|paediatric|neonatal)\s+nurse\b", "2236"),
    (r"\bnurses?\b|\brgn\b", "2237"),
    (r"\b(specialty|speciality|specialist)\s+doctor\b|"
     r"\b(specialist|specialty|speciality|medical)\s+registrar\b|"
     r"\bassociate specialist\b|\bconsultant\s+(psychiatr|physician|surgeon|paediatr|"
     r"anaesth|radiolog|oncolog|cardiolog|geriatric|obstetric|gynaecolog|neurolog|"
     r"nephrolog|haematolog|dermatolog|ophthalmolog|patholog|microbiolog|emergency|"
     r"acute|respiratory|rheumatolog|endocrin|gastroenterolog|urolog|orthopaed)", "2212"),
    (r"\b(general practitioner|\bgp\b|salaried gp|physician|clinical fellow|"
     r"foundation (year|doctor)|junior doctor|(senior )?house officer|\bsho\b|"
     r"medical officer|trust doctor|locum doctor)\b", "2211"),
    (r"\b(physiotherapist|physical therapist)\b", "2221"),
    (r"\boccupational therapist\b", "2222"),
    (r"\b(speech and language therapist|speech (and|&) language|\bslt\b)\b", "2223"),
    (r"\b(psychotherapist|cognitive behaviour|\bcbt\b\s+therapist)\b", "2224"),
    (r"\bclinical psychologist\b", "2225"),
    (r"\bpsychologist\b", "2226"),
    (r"\b(podiatrist|chiropodist|dietitian|dietician|orthoptist|osteopath|"
     r"art therapist|music therapist|drama therapist|therapy (assistant|practitioner)|"
     r"orthotist|prosthetist)\b", "2229"),
    (r"\bpharmacist\b", "2251"),
    (r"\b(optometrist|ophthalmic optician|dispensing optician)\b", "2252"),
    (r"\b(dentist|dental practitioner|dental surgeon|orthodontist|dental officer)\b", "2253"),
    (r"\b(radiographer|sonographer|mammographer)\b", "2254"),
    (r"\bparamedic\b", "2255"),
    (r"\b(operating department practitioner|\bodp\b|theatre practitioner|"
     r"anaesthetic practitioner|surgical care practitioner)\b", "2259"),
    (r"\b(biomedical scientist|clinical scientist|healthcare scientist|"
     r"biomedical science)\b", "2113"),
    (r"\b(microbiologist|biological scientist)\b", "2112"),
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
    is silently dropped for lacking a figure. DEAD, MEDIUM_CLOSED and unmapped
    codes fail. CLINICAL codes are eligible on the code alone: their pay is on
    an NHS national scale this module does not model, so it is deferred to the
    advert rather than floor-checked here."""
    if soc is None or soc in DEAD or soc in MEDIUM_CLOSED:
        return False
    if soc in CLINICAL:
        return True
    req = required_salary(soc, hours)
    if req is None:
        return False
    if salary_max is None:
        return True
    return salary_max >= req
