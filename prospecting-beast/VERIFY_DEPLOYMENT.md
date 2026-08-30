# Render deployment fix

This package is flattened so `app/` is at the repository root. In Render, use the repository root as the Root Directory and Docker as the runtime.

The Dockerfile sets `PYTHONPATH=/app` and starts `python -m uvicorn app.main:app` on Render's `$PORT` (default 10000).

Do not create a `beast_supabase/` wrapper folder around these files in GitHub.
