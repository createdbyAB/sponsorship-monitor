# Company watch seed data

The **Company watch** section watches a fixed list of employers rather than
occupation codes. These files drive it. Everything here is data — no threshold
or company name lives in code.

## `companies.json`

83 UK employers, every one confirmed on the Home Office **register of licensed
sponsors** with an A-rated (or A (Premium)) Skilled Worker licence.

- **Source:** the register of licensed sponsors published by the Home Office
  (`https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers`).
- **Checked on:** 16 September 2026 (each entry's `registerCheckedOn`).
- **Matching rule:** name matching **anchored to the start** of the organisation
  name, then read by a human. This anchoring matters. An earlier *substring*
  match on this same data returned *Aviva Dentistry Limited* for Aviva, *Visage
  Dental Laboratory* for Sage, *Avocado Labs* for Ocado and *Sangerwal Money
  Transfer Services* for the Wellcome Sanger Institute. If you rebuild this file,
  anchor the match and have a human read the output.

Do not regenerate this file casually, do not add companies, and do not "correct"
an entity name in it. A test (`test_eligibility.py`) asserts it holds 83 entries,
that every one has `verifiedAgainstRegister: true`, and that every `searchAliases`
array is non-empty, so a truncated or half-regenerated file fails the build.

Ratings drift — a licence can be downgraded or revoked — so the section footer
surfaces `registerCheckedOn` and warns when it is more than 90 days old.

### Adding a company

Append an object with the schema below. Every field is required. Verify the
company on the register **first**, anchored to the start of the name, and set
`registerCheckedOn` to the date you checked.

```json
{
  "name": "Barclays",
  "sector": "Banking, finance and insurance",
  "tier": 1,
  "rating": "A rating",
  "licensedEntities": ["Barclays Bank PLC", "Barclays Execution Services Limited"],
  "registeredLocations": ["London"],
  "ukHubs": ["Glasgow", "Knutsford", "Northampton", "London"],
  "searchAliases": ["Barclays", "Barclays Bank PLC", "Barclays Execution Services Limited"],
  "verifiedAgainstRegister": true,
  "registerCheckedOn": "2026-09-16"
}
```

- `searchAliases` is what the job-source company filter is queried with. Query
  **every** alias and de-duplicate by advert URL — these firms advertise under
  several legal entities (Barclays posts as Barclays Execution Services; Virgin
  Money's licence is held by Clydesdale Bank PLC; the Wellcome Sanger Institute
  is licensed as Genome Research Limited).
- `rating` is `"A rating"` for most and `"A (Premium)"` for HSBC and Google UK.
  Premium is **higher**, not an anomaly.
- `tier` drives display order only. Tier 1 = large UK operation with real hiring
  volume; tier 3 = sponsors, but hires at a level or place that is a stretch.
- `ukHubs` is general knowledge about where these employers run operations, **not
  register data** — display and location hints only, never presented as verified.
- CGI is deliberately absent: no A-rated licence under that name, and the only
  close register match (*CGI Axis Ltd*) is unrelated. Do not add it back.

## `companyBoards.json`

The primary feed's ATS map: company `name` → a list of boards, each `{ats, slug}`
(or, for Workday, `{ats: "workday", host, tenant, site}`). A company mapped here is
sourced **directly from its ATS's public JSON endpoint** — precise, real salaries,
`employerMatch: confirmed` — instead of the Adzuna company filter. A company absent
here falls back to Adzuna. Supported ATS: greenhouse, lever, ashby, smartrecruiters,
workable, recruitee, workday. 53 of the 83 are mapped as at 16 September 2026.

**Confirm before adding, never guess a slug.** Slug collisions are real — a `tcs`
Greenhouse board is a UK healthcare provider, not Tata Consultancy; a `nationwide`
Workday tenant is US Nationwide Mutual, not the UK society. Open the company's
careers page, find which ATS host serves its listings, fetch the endpoint, and
check the returned jobs are that UK employer's before adding the entry.
`test_ats.py` asserts every board names a real company and carries the coordinates
its adapter needs.

## `occupations.json`

The eligibility figures, so no threshold sits in code. Semantics differ by group:

- **`higherSkilled`** — the new-entrant floor, the higher of £33,400 and 70% of
  the going rate. It **pro-rates** below a 37.5-hour week.
- **`temporaryShortageList`** — the salary the job must actually pay. **No
  new-entrant discount on a shortage-list rate.**
- **`generalMinimum`** (£33,400) — **never pro-rates**, for any code, at any hours.
- **`closed`** — cannot produce a first CoS at any salary. Rendered red with the
  reason, never "unknown".

Update these when the Temporary Shortage List or the going rates are revised, and
move `expiry` to the new review date. Verified against gov.uk on 2026-09-10.

## `roleFamilies.json`

Title → SOC matching. First match wins on lowercase substrings, so exception
phrases are ordered before the general family (`risk analyst` → 3544 ahead of the
audit family; `esg data analyst` → 3544 ahead of `esg analyst`). `confidence:
"check"` marks a SOC that is a judgement call — the card shows a caution to
confirm the code before applying. `excludeTitles` drops graduate schemes,
internships, apprenticeships and closed-code safety roles; `excludeSeniority`
drops senior/lead/principal/head-of/manager/director, with `seniorityKeep` as the
allow-list exception (assistant project manager).
