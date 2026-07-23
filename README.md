# Opportunity Monitor

A daily dashboard of recent UK jobs at employers **licensed to sponsor a Skilled Worker visa**, in your fields, ranked by fit, with a browsable day-by-day archive so you never miss a day.

The interface is the **Control Room** design system: dark mode first, mobile first, with light mode as a first-class swap of the same tokens. Every card leads with the two things worth judging fast, a fit score out of 100 and an eligibility status. Status colour is always paired with an icon and a word, so colour never carries meaning on its own.

## The three sections

One shell, one card language, three tabs.

| Tab | What it holds | What the card adds |
| --- | --- | --- |
| **Jobs** | sponsored roles across the monitored fields | salary, posted age |
| **H&S** | health and safety roles, routed by job title | salary vs visa floor meter |
| **PhD** | funded chemical engineering studentships | funding, international eligibility, deadline |

## Funded PhDs

Chemical engineering studentships, ranked against the research interests in `PHD_INTERESTS`: waste valorisation and circular economy first, then carbon capture, sustainability and decarbonisation, biomass, catalysis and reactors, hydrogen, life cycle assessment, and water. Reorder that list to change the ranking.

**Eligibility drives the ramp here, not the sponsor register.** A studentship is not a Skilled Worker vacancy, so a licence is irrelevant. What decides whether an opening is any use to an international applicant is whether it is funded and whether that funding is open to them:

| Status | Means |
| --- | --- |
| **strong** | funded, and international students can hold the funding |
| **caution** | funded, but the advert does not say who is eligible |
| **weak** | home students only, or funding not stated |

jobs.ac.uk publishes eligibility as a structured field (`Funding for: UK Students` versus `UK Students, EU Students, International Students`), but only on the advert page, not in search results. So the best scoring studentships each get one extra request to read it, capped at `PHD_ENRICH` per run. A home-only studentship is pushed well down the ranking, because a perfect topic you cannot be funded for is not a good match.

Stipends are sanity checked the same way salaries are: adverts mix monthly figures, fee-only amounts and part-time rates into one field, so anything outside £8,000 to £80,000 a year is shown as "stipend not stated" rather than presented as an annual figure.

### Coverage, honestly

Two sources feed this tab. **jobs.ac.uk** covers the UK. **EURAXESS** covers doctoral posts across Europe and several partner portals, which is where the rest of the world creeps in. A typical run returns around 140 openings across 20-odd countries:

> UK, Netherlands, France, Belgium, Spain, Sweden, Germany, Portugal, Italy, Poland, Switzerland, Finland, Ireland, Denmark, Norway, Austria, Czech Republic, Croatia, Luxembourg, Israel, China

**jobRxiv** is what reaches North America. Two gates apply to it: the title has to be a studentship rather than a post that merely requires a doctorate (a board like this mixes in postdocs, faculty roles, and AI data-labelling gigs advertised as "Chemistry Expert (PhD)"), and it has to be in the field. Note that its visible `/jobs/` page renders listings in the browser and serves an empty shell to a scraper, so the source reads the AJAX endpoint the page itself calls.

**Two sources fail from GitHub Actions but work from a laptop.** EURAXESS honours its keyword facet from a residential IP and silently ignores it from a data centre one, returning an identical generic page for every query with HTTP 200 and no error. That is why every source is judged on its own text as well as its query, and why the run logs bytes and item counts per page.

Australia and New Zealand remain uncovered. Nothing found so far reaches them: their academic boards either refuse a plain client or carry no doctoral vacancies.

EURAXESS searches by POST, but the redirect reveals that keywords are really a facet, so a plain GET works once the query is built as `f[0]=keywords:...`. Positions are filtered to `job_research_profile:447`, which is how EURAXESS labels First Stage Researcher (R1), its PhD level.

A Marie Sklodowska-Curie post is the one case where eligibility can be stated without guessing: MSCA funding is open to any nationality by design, subject to a mobility rule, so those are marked strong and the note says what to check. Other European doctoral posts are usually salaried contracts rather than student stipends, and rarely state nationality rules, so they land as caution with a note saying so.

**FindAPhD** is not used: it sits behind a Cloudflare challenge that returns a CAPTCHA even for `robots.txt`.

**Nature Careers** was tried and dropped. Its listing pages parse cleanly, but the `q` parameter on `/naturecareers/jobs/` is silently ignored: "carbon capture", "quantum physics" and "marine biology" all return the same unfiltered list. The endpoint that does search, `/naturecareers/jobs/search`, is disallowed in their `robots.txt`, as is their jobs RSS feed. What is left is an unfiltered board that is mostly postdoc and faculty posts, so it would have cost a pile of requests for a couple of noisy rows a day.

Both are indexed by Google, which is the point of the next section: the search engine reaches the sites the scrapers cannot.

## Where the data comes from

| Source | Used for | How | Needs a key |
| --- | --- | --- | --- |
| GOV.UK register of licensed sponsors | the sponsor gate on every row | published CSV | no |
| Adzuna | jobs and H&S | API, one call per keyword per day | yes, already set |
| jobs.ac.uk | H&S at universities, and UK PhDs | scrape of the public search | no |
| EURAXESS | doctoral posts across Europe | scrape of the public search | no |
| jobRxiv | doctoral posts worldwide, including North America | its listings JSON endpoint | no |
| reed.co.uk | H&S across the whole UK market | the page's own JSON payload | no |
| Google Programmable Search | H&S and PhD leads from the open web, including the US and Canada | JSON API | optional |

Every source is polite: a descriptive user agent, a pause between requests, and only paths the site's `robots.txt` allows. If a site changes shape the parser returns nothing and the run carries on with the others, so a break shows up as a thinner H&S tab rather than a failed workflow.

Whatever a source returns, a row only lands in the H&S tab if its **title** matches the H&S pattern, so a loose search term cannot leak into the wrong section. Adverts on these boards run for weeks rather than days, so each source has its own window (`JACUK_MAX_DAYS`, `REED_MAX_DAYS`).

### What is deliberately not scraped

- **LinkedIn.** `linkedin.com/robots.txt` is `Disallow: /` for everyone, with an email address to apply for whitelisting. Their terms also prohibit scraping and most job results sit behind a login. There is no way to do this that is both working and above board, so the monitor does not touch it.
- **Google result pages.** `google.com/robots.txt` has `Disallow: /search`. Scraping the result pages is off limits and gets CAPTCHA'd from a CI runner in any case. The **Programmable Search JSON API** is the supported way to run a web search, so that is what the optional Google source uses.
- **CV-Library, Totaljobs, Jobsite, Jooble** all refuse a plain HTTP client (403 or a dropped connection). They are technically reachable only by pretending to be a browser, which is exactly the line worth not crossing.

### Turning on the Google source

It is off unless both secrets exist.

1. Create a [Programmable Search Engine](https://programmablesearchengine.google.com/). The create form asks for sites to search, and a site-restricted engine is the better choice here: it reaches the job boards the scrapers cannot, without dragging in the rest of the web. Add the domains below, one per *Add*. The engine ID it gives you is `GOOGLE_CSE_ID`.
2. Get a key from the [Custom Search JSON API](https://developers.google.com/custom-search/v1/overview). 100 queries a day free; the monitor uses 8. That is `GOOGLE_API_KEY`.
3. Add both under *Settings → Secrets and variables → Actions*.

Worth adding to the engine, in rough order of usefulness. The first three are exactly the sites that block scraping, so this is how their content gets in:

```
*.findaphd.com
*.academicpositions.com
*.nature.com
*.phdportal.com
*.jobrxiv.org
*.academicjobsonline.org
*.universityaffairs.ca
*.higheredjobs.com
*.timeshighereducation.com
*.seek.com.au
*.gradconnection.com.au
*.scholarshipdb.net
*.euraxess.ec.europa.eu
*.jobs.ac.uk
```

To search the open web instead, create the engine with any single site, then turn on **Search the entire web** in its settings afterwards. That casts wider but returns far more noise, and every web row is marked weak regardless.

A web search result carries a title and a link but no employer, salary or date. The monitor guesses the employer from the page title and, because nothing has confirmed it against the register, marks every one of these rows **weak** with a note saying so. Treat them as leads to look into, not as checked vacancies. If you find them noisy, untick Weak in the Eligibility filter and they disappear.

## Two judgement calls the pipeline makes for you

**Agency adverts are flagged, not trusted.** Recruiters advertise on behalf of an employer they do not name. The agency may well hold a licence, but that tells you nothing about who would actually sponsor you. Reed's data distinguishes the two directly (`ouType`), and there is a name-pattern fallback for other sources, so these land as **caution** with a note telling you to ask who the employer is.

**Implausible salary bands are treated as no salary.** Boards pad ranges out to catch more searches, so a role advertised as "£10,000 to £50,000" is not really offering £10,000. Where the top of the band is at least double the bottom, or the bottom is under £12,000, the figure is dropped rather than believed, and the row becomes **caution** with a note. Without this, real roles would silently vanish under the visa floor.

## What is new since last time

The boards behind this advertise for weeks, so most of what a run returns was already there yesterday. Every opportunity therefore carries a **`firstSeen`** date, tracked in `docs/data/seen.json` against a stable key (the advert URL without its query string, plus title and employer, so churning tracking parameters do not make an old row look new).

The dashboard **opens on the New view**, which is only what turned up since the last run. One tap on **All open** gets the full list back, per section. On a quiet day you get a "nothing new" panel telling you how many are still open rather than a blank screen.

Repeats are filtered, not deleted. Dropping them from the day file would be simpler, but anything you did not act on the day it appeared would vanish from your daily view while still being open for another month. `seen.json` is pruned at `SEEN_KEEP_DAYS`, currently 180.

Note that **Reset** and **Clear all** land on All open rather than New. Someone clearing filters wants to see more, and returning them to a view that can be empty does the opposite.

## Marking roles done or hidden

Every card has two buttons:

- **Done** for a role you have applied to. It stays in the counts and moves to the Done view.
- **Hide** for one you do not want. It drops out of the list and out of the tile counts.

Both are reversible with **Undo**, and the **Show** control in the filters switches between Open, Done and Hidden. Marks live in that browser's local storage, keyed on the advert so a role stays marked as it reappears on later days. They do not sync between devices, and clearing site data clears them.

## What's in here

- `monitor.py` — the daily script (Python standard library only, nothing to install)
- `docs/index.html` — the dashboard, self-contained apart from the web font
- `docs/data/` — dated result files, created automatically each run
- `.github/workflows/monitor.yml` — runs the script every morning and publishes results

## One-time setup (~10 minutes, entirely in the browser)

1. **Create the repo** — github.com → *New repository* → name it `sponsorship-monitor` → **Public** → *Create repository*.
2. **Add the files** — *Add file → Upload files*, then drag in `monitor.py`, the whole `docs` folder and the whole `.github` folder (keep the folders). Commit. (If drag-and-drop flattens folders, use *Add file → Create new file* and type the path, e.g. `docs/index.html`, then paste the contents.)
3. **Add your Adzuna keys** — *Settings → Secrets and variables → Actions → New repository secret*. Add two: `ADZUNA_ID` (your Adzuna app_id) and `ADZUNA_KEY` (your app_key).
4. **Turn on Pages** — *Settings → Pages → Source: Deploy from a branch → Branch: `main`, Folder: `/docs` → Save*.
5. **Run it once now** — *Actions* tab → *sponsorship-job-monitor* → *Run workflow*. Give it a minute.
6. **Open your dashboard** — `https://YOUR-USERNAME.github.io/sponsorship-monitor/`. It refreshes automatically every morning (06:00 UTC, about 7am UK).

## Change what it watches

Edit the `KEYWORDS` list at the top of `monitor.py` (search term, field). `HS_KEYWORDS` holds the extra Adzuna sweeps aimed at the H&S tab, and `JACUK_QUERIES`, `REED_QUERIES` and `GOOGLE_QUERIES` the searches for the other sources. Salary floors are `NEW_ENTRANT_FLOOR` and `GENERAL_FLOOR`; both are published to the page, so the threshold meter and the card copy follow whatever you set.

**Watch the Adzuna budget.** Every entry in `KEYWORDS` and `HS_KEYWORDS` costs one call per day, currently 15 in total, or about 460 a month. jobs.ac.uk and reed.co.uk cost nothing. Trim `HS_KEYWORDS` first if you need to cut back; the run prints a per-source summary to the log.

## The data file

Each `docs/data/YYYY-MM-DD.json` looks like this:

```json
{
  "date": "2026-07-23",
  "count": 6,
  "counts": { "jobs": 4, "hs": 2, "phd": 0 },
  "floors": { "newEntrant": 33400, "general": 41700 },
  "jobs": [ { "score": 92, "title": "...", "status": "strong", "note": "", "...": "..." } ],
  "hs":   [],
  "phd":  []
}
```

`status` is `strong`, `caution` or `weak`, and drives the colour, icon and word on the card. `note` is the plain-language line shown in the annotation panel underneath. Day files written before the redesign only carry a flat `jobs` array; the page derives section and status for those, so the whole archive keeps working.

## Good to know

- Adzuna's free tier is limited, and the script makes one call per keyword per day. Watch the count if you add keywords.
- A licensed sponsor *can* sponsor; a specific role still needs to be a genuine sponsored vacancy above the salary floor, so always confirm on the advert.
- Employer names on job boards don't always match the register exactly, so a few genuine sponsors may be missed. Treat a miss as "check manually", not "can't sponsor".
- Preview locally without keys: `python monitor.py --demo` then open `docs/index.html` through a local server (the page fetches its data, so `file://` won't work).
