# Company watch — briefing + cockpit redesign

Date: 2026-09-18 · Status: approved, building

## The problem

The Company watch tab is a vacancy-centric scroll: five stat tiles, two
countdowns, three rows of filter chips, then wide grouped tables. For a private,
daily-glance decision tool it buries the one question that matters — *is there
anything I should act on today?* — under six screenfuls of chrome, and its
all-monospace tables truncate on a phone.

Explored six directions (A refine, B calm cockpit, C timeline, D triage inbox,
E watchlist grid, F morning briefing). A/B/C were the same card-scroll with
different heroes. D/E/F were structurally distinct. Chosen: **F + B.**

## The design: two modes in one tab

The tab defaults to a **briefing** and expands, on one tap, into a **cockpit**.
State lives in `CW.expanded` (default `false`), browser-only, not persisted.

### Briefing (default) — "F"

A written brief, read in five seconds. No stat tiles, no filters.

- Date line + serif headline that adapts to the sponsorable count:
  `0 → "Nothing needs you today."`, `1 → "One role worth a look today."`,
  `n → "N roles worth a look today."`
- A plain-English paragraph naming the top sponsorable role(s): employer, role,
  salary, and which deadline governs it.
- One **act-on card** per sponsorable role (cap 3; "+N more in the full list"),
  each with pays / floor / deadline and an "open advert" link.
- The **runway** as one sentence: days to the shortage-list close (danger under
  120) and days of Graduate leave, from `cwDeadlines()`.
- A **for-the-record** paragraph, understated: names the too-late and closed
  rows, counts the salary-not-stated ones.
- A **"Show all N vacancies →"** control that sets `CW.expanded = true`.
- A sign-off line: register-checked date and last-run stamp.

Zero-state: headline "Nothing needs you today.", the runway sentence, and the
for-the-record line still render (so a quiet day still shows the deadlines).

### Cockpit (expanded) — "B"

The calm-cockpit mock: legible, card-based, monospace only for numbers.

- A **"← Back to briefing"** link (clears `CW.expanded`).
- The **runway** as its own component (two counts, danger under 120).
- The five **stat tiles** (sponsorable / vacancies today / salary not stated /
  too late), reusing the existing `#tiles` grid.
- The existing **filter chips** (verdict / sector / tier / SOC) — kept; they
  already work and belong in the power view.
- Rows rendered as **cards** grouped by verdict (`VERDICT_GROUPS` order),
  higher-skilled sorted above shortage-list within a group (existing `hsFirst`).
  Each card: employer + tier + rating, role link, NEW/CONFIRM/EMPLOYER badges,
  location, salary (estimate styling preserved), SOC + family, the per-row
  deadline chip (`TSL·date` danger / `grad·date` muted), required-vs-advertised
  delta, plain-English reason, and the done/hide mark buttons.

## Constraints

- Reads the same row fields already emitted (`applicableDeadline`,
  `deadlineBasis`, `verdict`, `headroom`, `shortfall`, `salaryEstimated`, …).
  **No change to `monitor.py`, `eligibility.py`, or the data schema.**
- Deadlines stay data-driven via `cwDeadlines()` → `INDEX.companyWatch`.
- Reuses the existing semantic ramps (`strong`/`caution`/`weak`), surfaces, and
  `--sans`/`--mono`. Adds one system-serif stack for the briefing headline only
  (no web-font load).
- Marks (done/hidden) keep working in the cockpit cards; the briefing only shows
  live, un-hidden rows.
- Only the Company watch tab changes. The other five tabs are untouched.

## Verification

- `python3 -m unittest test_soc test_eligibility test_ats` stays green (no
  Python touched, but run it to be sure).
- Render-check in a localhost preview: briefing default, expand → cockpit,
  the two deadlines, the too-late and closed rows, deadline chips, mark buttons,
  and a quiet-day/zero-sponsorable state.
- Then branch, PR, describe the two-mode design in plain English.

## Follow-up (2026-09-18): extended to Jobs, H&S, NHS

The same briefing → cockpit pattern now fronts three more tabs. PhD and
Part-time keep the plain card list (their "act-on" signal is weaker and the
briefing would add little). The cockpit is the existing tiles + filter rail +
card grid, reached by "Show all"; a "← Back to briefing" link returns.

Each briefing is *tailored* — the act-on signal differs by section:

- **Jobs / H&S** — strong matches by CV fit that a July-2025 occupation code
  does not rule out (`status === "strong" && sponsorable !== false`, minus rows
  already marked done). H&S is honest about its reality: code 3582 is closed, so
  most days it leads with "Nothing sponsorable today" and explains why.
- **NHS** — leads with adverts that *welcome* sponsorship (`sponsorship ===
  "welcome"`), then strong matches; the signal line names what is closing
  soonest. A welcomed advert on a closed code is still greyed and counted under
  "greyed by a closed code", not shown as act-on.

Shared shell reused from the Company watch briefing (`.bf` / `.actcard` /
`.run` / `.showall`). New pieces: `BRIEF`/`EXPANDED` state, `renderBriefing`,
`briefLede`/`briefActCard`/`briefSignal`/`briefRest`, and an English `plural()`.
"For the record" partitions its counts so a closed-code row is reported once
(greyed), never double-counted as a caution/long-shot. The sort control is
hidden in any briefing (also fixes the same leftover in the Company watch
briefing). Still no change to `monitor.py`, `eligibility.py`, or the schema.
