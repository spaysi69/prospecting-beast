#!/usr/bin/env python3
import argparse
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.osint_tools import email_candidates_and_mx

async def main_async():
    ap = argparse.ArgumentParser()
    ap.add_argument("first_name")
    ap.add_argument("last_name")
    ap.add_argument("domain")
    args = ap.parse_args()
    print(json.dumps(await email_candidates_and_mx(args.first_name, args.last_name, args.domain), indent=2))

if __name__ == "__main__":
    asyncio.run(main_async())
