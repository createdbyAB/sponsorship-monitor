# Opportunity Monitor

A daily dashboard of recent UK jobs at employers **licensed to sponsor a Skilled Worker visa**, in your fields, ranked by fit, with a browsable day-by-day archive so you never miss a day.

The interface is the **Control Room** design system: dark mode first, mobile first, with light mode as a first-class swap of the same tokens. Every card leads with the two things worth judging fast, a fit score out of 100 and an eligibility status. Status colour is always paired with an icon and a word, so colour never carries meaning on its own.

## The three sections

One shell, one card language, three tabs.

| Tab | What it holds | What the card adds |
| --- | --- | --- |
| **Jobs** | sponsored roles across the monitored fields | salary, posted age |
| **H&S** | health and safety roles, routed by job title | salary vs visa floor meter |
| **PhD** | funded PhD openings | funding, international eligibility, deadline |

The PhD tab is built and reads a `phd` array from each day file, but no PhD source is wired up yet, so it shows its own empty state. Fill that array and the cards appear with no change to the page.

## Where the data comes from

| Source | Used for | How |
| --- | --- | --- |
| GOV.UK register of licensed sponsors | the sponsor gate on every row | published CSV |
| Adzuna | jobs and H&S | API, one call per keyword per day |
| jobs.ac.uk | H&S at universities | HTML scrape of the public search |

Universities are almost all licensed sponsors and their adverts run for weeks, so the jobs.ac.uk window (`JACUK_MAX_DAYS`) is much wider than the Adzuna one. Two queries saturate the results; more just repeat them. The scraper sends a descriptive user agent, pauses between requests, and only touches `/search/`, which `jobs.ac.uk/robots.txt` allows. If their markup changes the parser returns nothing and the run carries on with Adzuna alone, so a break shows up as a thin H&S tab rather than a failed workflow.

Whatever a source returns, a row only lands in the H&S tab if its **title** matches the H&S pattern, so a loose search term cannot leak into the wrong section.

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

Edit the `KEYWORDS` list at the top of `monitor.py` (search term, field). `HS_KEYWORDS` holds the extra Adzuna sweeps aimed at the H&S tab, and `JACUK_QUERIES` the jobs.ac.uk searches. Salary floors are `NEW_ENTRANT_FLOOR` and `GENERAL_FLOOR`; both are published to the page, so the threshold meter and the card copy follow whatever you set.

**Watch the Adzuna budget.** Every entry in `KEYWORDS` and `HS_KEYWORDS` costs one call per day, currently 15 in total, or about 460 a month. jobs.ac.uk costs nothing. Trim `HS_KEYWORDS` first if you need to cut back; the run prints its call count to the log.

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
