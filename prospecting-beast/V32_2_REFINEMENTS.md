# Prospecting Beast v32.2 refinements

## Product behavior
- Default people quota is **20 leads per company**.
- The quota applies independently to the root company and every subsidiary/related company processed in the family frontier.
- The per-company quota is adjustable from **1 to 200** in the Command Deck.
- The UI now shows `20 / company` on the primary launch action and estimates root-level potential lead volume as domains × per-company target.
- The company quota stops discovery once reached, including family/Tavily research.

## Reliability fixes
- Removed a duplicated prospects table and duplicate DOM IDs from the previous build.
- Kept DOM writes null-safe to prevent `Cannot set properties of null (setting 'textContent')` from becoming a fatal watcher error.
- Shared family lead accounting across concurrent company workers with a lock.
- Default relationship confidence for family queueing remains configurable via `MIN_RELATIONSHIP_CONFIDENCE`, now defaulting to `0.70`.

## Validation
- 11 automated tests pass.
- Python compilation passes for application modules.
- Browser JavaScript extracted from `index.html` passes `node --check`.
- `/api/health` responds successfully under authenticated TestClient smoke coverage.

## Database
No schema changes. Existing Supabase tables remain the contract: jobs, companies, leads, relationships, logs.
