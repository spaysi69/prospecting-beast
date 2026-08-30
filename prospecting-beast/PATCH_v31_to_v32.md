# Prospecting Beast v31 → v32 patch notes

## What changed

### 1. `max_people_per_company` hard stop
The worker now enforces the quota at two levels:

- `web_people_discovery()` checks the locally qualified count immediately after every Gemini extraction batch and exits before another Tavily query can execute.
- `process_company()` checks `saved_here >= max_people_per_company` inside the persistence loop, so the database cannot receive more than the configured company quota.
- When the persistence quota is actually reached, corporate-family Tavily research is skipped for that company as well.

### 2. Subsidiary queue is now live and concurrent
`process_job()` now:

- keeps discovered children in a priority queue keyed by relationship confidence;
- requires confidence > 0.8 before a child enters the prospecting queue (the general minimum remains configurable);
- processes up to 3 family members concurrently with `asyncio.gather()` and a semaphore;
- prevents duplicate queued/processed domains;
- preserves `family_id`, `root_domain`, and `parent_domain` in Supabase;
- honors `max_companies_per_family` across the concurrent batches.

### 3. Seven-day local cache
`app/cache.py` adds a SQLite cache used for Tavily search responses and Gemini structured outputs. Cache entries expire after 7 days by default. The database is local under `data/cache.sqlite3` unless `PB_CACHE_DB` is set in the process environment.

### 4. Public enrichment module
`app/enrichment.py` adds:

- 15 deterministic email permutations;
- async MX lookup with `dnspython`;
- conservative SMTP RCPT catch-all probing without sending a message;
- a 50% confidence multiplier when catch-all behavior is detected;
- public HTML email extraction with `aiohttp` + `BeautifulSoup`;
- no authenticated LinkedIn crawling, CAPTCHA bypass, proxy rotation, or credentialed scraping.

### 5. Minimal terminal UI
`web/index.html` + `static/app.css` replace the former multi-panel dashboard with:

- one domain textarea and SCAN CTA;
- persistent 70/30 results + live-log grid;
- one Advanced modal for all secondary configuration;
- sticky result headers, score badges, status pills, selection actions;
- in-page X-Ray helper inside Advanced;
- CSV/XLSX/JSON export;
- selected-lead deletion;
- automatic polling of the existing Supabase-backed job state.

> Google Search removed the old cached-page feature, so result links are now Google **indexed-search** links that query the candidate/company on LinkedIn's public index rather than dead Google Cache URLs. citeturn465049search1turn465049search3

## Backend file map

```text
/app
  core.py
  main.py
  osint_tools.py
  cache.py           # NEW
  enrichment.py      # NEW
/web
  index.html         # REPLACED
/static
  app.css            # REPLACED
```

## Migration: v31 → v32 without losing Supabase data

1. **Back up Supabase first.** Export the existing `jobs`, `companies`, `relationships`, `leads`, and `logs` tables using your existing Supabase backup/export workflow. No destructive SQL is required by this patch.

2. **Replace application files only.** Deploy the new `app/`, `web/`, and `static/` files. Keep the existing Supabase project, URL, service-role key, table IDs, and data.

3. **Create the local cache directory.** The application creates `data/cache.sqlite3` automatically on first use. The cache is disposable and contains no Supabase state.

4. **Keep your existing environment values.** Continue using `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `TAVILY_API_KEY`, `GEMINI_API_KEY`, and any existing Seamless settings. No key rotation is required for this migration.

5. **Verify schema compatibility.** The worker still writes to the existing `jobs`, `companies`, `relationships`, `leads`, and `logs` tables. The existing v33 migrations are additive and remain compatible; do not drop or recreate the tables.

6. **Deploy and run the smoke tests.** Start the service the same way as v31/v33. Confirm `/api/health` returns Supabase connectivity and that the home page renders.

7. **Run a controlled quota test.** Scan one domain with `Max People / Company = 5`. Confirm the lead count for that company never exceeds 5 and that the log contains `Lead quota reached` before family research continues elsewhere.

8. **Run a subsidiary concurrency test.** Use a company known to have multiple high-confidence subsidiaries. The log should show multiple child domains entering the same bounded processing window; `max_companies_per_family` remains the global cap.

9. **Only then roll out to larger batches.** Existing Supabase rows are preserved; the new local cache merely reduces repeated Tavily/Gemini calls for identical inputs during the seven-day window.

## Unified diff summary

The repository contains the actual patched `app/core.py` and `app/main.py`. The important logical change is split because the current v33 implementation owns `process_job()` in `main.py`, while `web_people_discovery()` and `tavily_search()` live in `core.py`.

```diff
- for every ranked lead: save without an in-loop hard stop
+ if saved_here >= cfg['max_people_per_company']: break
+ save lead
+ saved_here += 1

- sequential queue.pop(0) → await process_company(...)
+ queue sorted by confidence
+ batch = next 3 domains
+ await asyncio.gather(*(bounded_process(item) for item in batch))

- always run the configured Tavily discovery budget
+ after every Gemini extraction batch:
+     if qualified >= max_people: break
```
