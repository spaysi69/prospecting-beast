# Prospecting Beast v31 // OSINT + Web-Only

A production-minded B2B prospecting dashboard that discovers public candidates, maps titles to the configured F/NF role taxonomy, discovers corporate relationships, and lets the operator manually trigger Seamless enrichment for selected leads.

## Important architecture

- **People discovery:** public company pages + targeted public-web / LinkedIn-indexed X-ray queries.
- **Corporate family:** Tavily search evidence + Gemini classification.
- **Title matching:** local fuzzy matching against `config/titles.json`, including your priority weights.
- **Contact enrichment:** Seamless only after the user selects leads and clicks **Seamless Enrich**.
- **Persistence:** Supabase Postgres.
- **Optional passive OSINT:** SpiderFoot and Amass integrations controlled by environment variables.
- **Optional local semantic search:** `tools/embed_profiles.py` + Supabase `pgvector`.

The app does **not** automate LinkedIn logins or scrape authenticated LinkedIn pages, bypass CAPTCHAs, rotate IPs to evade rate limits, or use SMTP `RCPT TO` probing. LinkedIn states that third-party software/extensions that scrape or automate activity are prohibited. citeturn831176search3

## Main run

The v32.1 interface keeps the original v33 Command Deck structure and improves hierarchy, spacing, progress visibility, results ergonomics, and error resilience. Research utilities are intentionally removed from the main UI; the core discovery, family mapping, jobs, diagnostics, and manual enrichment workflow remain.


1. Paste one or more company domains.
2. Choose F, NF, or BOTH.
3. Optionally enter Location and Industry.
4. Choose WEB ONLY (default) or WEB + PASSIVE OSINT.
5. Set `Web minimum qualified`, `Web search queries`, and `Tavily run budget`.
6. Add your existing contacts to the Seamless skip list.
7. Start the job.
8. Review prospects in **02 // RESULTS**.
9. Select contacts and click **Seamless Enrich**. Discovery never enriches automatically.
10. Review Corporate Map / Diagnostics as needed.

## APIs required by the web app

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
TAVILY_API_KEY
GEMINI_API_KEY
GEMINI_MODEL=gemini-3.5-flash-lite
SEAMLESS_API_KEYS=key1,key2,key3
APP_PASSWORD
```

## Optional OSINT connectors

### SpiderFoot

Run a SpiderFoot instance you control and set:

```text
SPIDERFOOT_ENABLED=true
SPIDERFOOT_URL=http://127.0.0.1:5001
```

Only passive results should be enabled for this prospecting use case.

### Amass

Install Amass locally and set:

```text
AMASS_ENABLED=true
AMASS_BIN=/path/to/amass
```

The integration uses passive domain enumeration only.

## Standalone tools

```text
tools/xray_matrix.py
```
Generates Google/Bing-style X-ray query variants.

```text
tools/passive_ingest.py
```
Fetches permitted public pages asynchronously and extracts JSON-LD Person objects.

```text
tools/email_candidates.py
```
Generates address permutations and performs DNS MX validation. It does not probe inboxes with `RCPT TO`.

```text
tools/embed_profiles.py
```
Uses `sentence-transformers/all-MiniLM-L6-v2` locally on CPU and writes 384-dimensional embeddings to Supabase `pgvector`.

Install optional tool dependencies:

```bash
pip install -r tools/requirements_tools.txt
```

## pgvector setup

Run once in Supabase SQL Editor:

```text
supabase/migration_v31_osint.sql
```

Supabase documents enabling the `vector` extension and using HNSW indexes for pgvector. citeturn831176search0turn831176search2

For local embeddings, add the direct Postgres connection string to `.env`:

```text
SUPABASE_DB_URL=postgresql://...
```

Then run locally:

```bash
python tools/embed_profiles.py
```

## Search-engine / provider limits

Do not use IP rotation, mobile-hotspot cycling, fake browser fingerprints, or other techniques intended to evade provider rate limits. The app uses bounded, adaptive searches and local post-processing instead.

## Render Free + Supabase

Deploy as a Docker Web Service. Leave the Render Root Directory blank if the repository root contains `Dockerfile`. Use Supabase for persistent application data because Render Free web services have ephemeral filesystems.

## F/NF role weights

Edit:

```text
config/titles.json
```

The matcher preserves your numeric weights and adds a human-readable mapping:

```text
Actual title → Closest target role → Match type → Similarity → Priority
```

## Email disclaimer

Generated corporate email patterns are **candidates**, not proof of a live mailbox. MX presence only proves that the domain publishes mail exchangers; it does not prove a specific address exists.


## v33 Ultimate OSINT additions
- Public-web X-Ray discovery, JSON-LD public-page parsing, email candidate generation + MX checks.
- Optional agentic query planner using Gemini; deterministic fallback remains available.
- Optional local Ollama enrichment and local sentence-transformer embeddings.
- Optional FORGE and Maigret command bridges (installed locally only).
- Manual Seamless enrichment only.
- Direct LinkedIn scraping/login automation, cache-bypass links, anti-bot/IP-evasion, and SMTP RCPT mailbox probing are intentionally not included.
