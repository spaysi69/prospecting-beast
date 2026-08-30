# Prospecting Beast v32 — Real Progress / Functional Fixes

This build is based on the supplied v33 codebase and preserves the existing Supabase schema:
`jobs`, `companies`, `leads`, `relationships`, `logs`.

## Fixed
- Null-safe browser DOM updates to prevent `Cannot set properties of null (setting 'textContent')` from taking down the watcher.
- `max_people_per_company` is enforced before every save.
- Existing lead count counts toward the quota on resumed jobs.
- When the company quota is reached, no additional Tavily people-search calls run.
- When the company quota is reached, corporate-family Tavily research is also skipped.
- Gemini extraction is checked after every extraction batch and stops once the qualified quota is met.
- Subsidiary / related-company processing uses a priority frontier with bounded `asyncio.gather()` concurrency (3 slots).
- Relationship candidates above the confidence threshold are actually enqueued and processed.
- Live progress is stored in `jobs.stats.progress` and polled by the browser.

## Progress fields
`jobs.stats.progress` contains:
- `overall_percent`
- `current_percent`
- `phase`
- `current_company`
- `root_index` / `root_total`
- `company_index`
- `company_queue`
- `queries_done` / `queries_total`
- `candidates`
- `leads_saved`
- `detail`

The UI displays both job-level and current-company progress. It is event-driven from real backend work; it is not a timer or simulated percentage.

## Validation
- Original tests: passed.
- Added quota/progress regression tests: passed.
- Python compilation: passed.
- Browser JavaScript syntax check: passed.
- Direct DOM reference audit: no missing IDs.
