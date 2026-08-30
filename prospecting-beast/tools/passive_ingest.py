#!/usr/bin/env python3
"""Fetch permitted public pages and parse JSON-LD Person records.

LinkedIn profile URLs are intentionally rejected. Use public search-result URLs or
other permitted pages instead; do not bypass login, robots.txt, CAPTCHAs, or anti-bot controls.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.osint_tools import fetch_public_page, parse_public_people

async def main_async():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()
    out=[]
    for url in args.urls:
        try:
            page = await fetch_public_page(url)
            out.extend(parse_public_people(page))
        except Exception as exc:
            out.append({"url":url,"error":str(exc)})
        await asyncio.sleep(max(0.0,args.delay))
    print(json.dumps(out, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main_async())
