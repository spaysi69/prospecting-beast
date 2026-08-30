# Prospecting Beast v31 → v32

## 1. Back up Supabase
No destructive migration is required for the redesign. Export a database backup or keep the existing Supabase project untouched.

## 2. Replace application code
Deploy the `app/`, `static/`, and `web/` directories from this release. Existing tables remain the source of truth.

## 3. New local cache
The app creates `data/cache.sqlite3` on the server. It contains only Tavily/Gemini response cache entries and can be deleted without touching Supabase leads.

## 4. Environment
Keep the existing `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `APP_PASSWORD`, `TAVILY_API_KEY`, `GEMINI_API_KEY`, and Seamless variables. Optional: set `PB_CACHE_DB` and `PB_STARTUP_DOMAINS`.

## 5. Schema
Run the existing v33 SQL migrations already present in `supabase/` if your Supabase project is not current. Do not recreate tables. The UI does not require a new data store.

## 6. Behavioral changes
- `max_people_per_company` is enforced during extraction and again immediately before persistence.
- Related companies above the confidence/queue thresholds are now processed in bounded concurrent batches of 3.
- High-confidence subsidiaries are prioritized.
- No automatic Seamless enrichment is triggered by discovery.
- Exports support `.xlsx`, `.csv`, and `.json`.

## 7. Rollout
Start with one domain in Dry Run, verify the live log and 5-person quota, then run multiple domains. Existing Supabase data is preserved because v32 uses the same job/company/lead/relationship tables.
