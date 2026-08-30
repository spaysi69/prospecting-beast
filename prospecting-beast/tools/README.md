# OSINT toolkit

These utilities are designed for passive/public research:

- `xray_matrix.py` generates Google/Bing-style public-search queries targeting indexed LinkedIn URLs and official pages. It expands by role family, location and industry; it does not automate search-engine pagination or bypass anti-bot controls.
- `passive_ingest.py` fetches permitted public HTML using `aiohttp`, checks `robots.txt`, and parses `application/ld+json` Person objects. It deliberately refuses direct LinkedIn profile crawling.
- `email_candidates.py` generates common corporate email patterns and checks DNS MX records. It intentionally does **not** perform `RCPT TO` mailbox probing; MX/syntax is a safer and more portable validation signal and many servers reject or tarp it anyway.
- `embed_profiles.py` uses `sentence-transformers/all-MiniLM-L6-v2` locally on CPU and writes 384-dimensional vectors to Supabase `pgvector` using `SUPABASE_DB_URL`.

Optional: SpiderFoot and Amass are supported by the main app as passive integrations when installed/configured by the operator.
