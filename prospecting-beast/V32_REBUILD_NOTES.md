# v32 rebuild notes

This rebuild starts from the original Prospecting Beast v33 archive and keeps its existing FastAPI/Supabase endpoints and data model.

## Verified
- Original test suite: 6 passed.
- `app/core.py`, `app/main.py`, `app/cache.py` compile successfully.
- Browser JS extracted from `web/index.html` passes `node --check`.
- HTML retains all DOM ids used by the existing client logic.
- `GET /`, `/api/jobs`, `/api/jobs/{id}`, enrichment, X-Ray, email-candidate, public-page parsing, reporting, and Excel export routes remain present.

## Critical execution fixes
- `web_people_discovery`: qualification quota is checked immediately after every Gemini extraction batch and before every Tavily call.
- `process_company`: persistence has an independent hard `max_people_per_company` guard; corporate-family research is skipped once the company quota is actually filled.
- `process_job`: discovered corporate-family children are stored in a confidence/queue-score priority frontier and processed in concurrent waves of up to 3 tasks.
- Job Tavily statistic read/modify/write operations are protected by a per-job asyncio lock.
- SQLite cache is local, 7-day TTL, and used for Tavily + Gemini people-extraction calls.

## UX
The visible workspace is intentionally reduced to the requested three surfaces: target input/results on the left and persistent terminal log on the right. Run history and research utilities remain available behind the single Advanced control.
