# Prospecting Beast v31 → v32 Migration Guide

## What changes

v32 is an application-layer re-architecture. Existing Supabase rows are preserved; there is no destructive migration in this release.

The important runtime changes are:

- `max_people_per_company` is a hard persistence quota, not just a discovery hint.
- Gemini extraction batches stop immediately once the qualified quota is reached, so no later Tavily searches run for that company.
- Corporate-family discovery is processed as a priority frontier: highest-confidence subsidiaries first, with bounded concurrency (`Semaphore(3)`).
- A subsidiary with zero qualified prospects is marked `dry` and is not expanded further for that job.
- Tavily and Gemini requests use a local 7-day SQLite cache in `data/cache.sqlite3`.
- Public enrichment uses `aiohttp` + BeautifulSoup for public pages and DNS/SMTP checks for catch-all signals.
- The UI is reduced to one scan surface, one live log, and one Advanced modal.

## 1. Back up Supabase first

Run a logical backup/export of the existing project before changing anything. At minimum, preserve:

`jobs`, `companies`, `leads`, `relationships`, `logs`, `lead_embeddings`, and any other project-specific tables.

Do **not** drop or truncate these tables as part of this migration.

## 2. Confirm the existing schema migrations

For a database that is already running v31, the normal chain is:

1. `supabase/migration_v12.sql`
2. `supabase/migration_v18_queue.sql`
3. `supabase/migration_v31_osint.sql`
4. `supabase/migration_v33_ultimate.sql` (safe additive audit/suppression fields retained from v33)

Every statement in the supplied migrations is additive (`IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`) except index creation and function replacement, so existing lead/company rows are retained.

If `archived_at` is missing from `jobs`, apply `migration_v18_queue.sql` before using the queue/archive endpoints.

## 3. Deploy the v32 application

Replace the application files with the v32 tree. Keep the existing `.env` values for Supabase and any provider keys.

Required environment variables remain:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `APP_PASSWORD`
- `TAVILY_API_KEY` (for public-web search)
- `GEMINI_API_KEY` (for extraction/family classification)

Seamless remains optional and is only called when the user explicitly runs enrichment.

## 4. Install dependencies

From the project root:

```bash
python -m pip install -r requirements.txt
```

All dependencies used by this application are open-source. Gemini/Tavily are external APIs and should be kept on the tiers/accounts appropriate to your deployment.

## 5. Start v32 and perform a smoke test

Run the existing deployment entry point (`run.py`, Docker, or your Render service configuration).

Then verify:

- `/api/health` reports Supabase connectivity.
- The home page loads behind the existing password.
- A single domain can be scanned.
- A test job with `Max People / Company = 5` stores no more than 5 leads for a company.
- The live log records subsidiary discovery and bounded concurrent processing.
- Export works for XLSX, CSV, and JSON.

## 6. Cache behavior

The SQLite cache is local to the application instance:

`data/cache.sqlite3`

Entries expire after 7 days by default. Configure with:

```bash
PB_CACHE_DB=/path/to/cache.sqlite3
PB_CACHE_TTL_DAYS=7
```

For multi-instance deployments, treat this as a per-instance optimization rather than a shared source of truth. Supabase remains authoritative.

## 7. Rollback

Rollback is simple because the v32 changes are application-layer changes and the database changes are additive.

1. Stop the v32 service.
2. Restore the previous v31 application image/files.
3. Keep the Supabase data unchanged.
4. Re-enable the previous environment variables if they were changed.

Do not delete v32-added columns or tables during rollback unless you have a separate reason to do so; v31 can continue operating while additive columns remain present.
