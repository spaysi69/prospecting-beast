#!/usr/bin/env python3
"""Optional local agentic public-web planner. Uses Gemini only if configured; otherwise falls back to deterministic X-Ray queries."""
from __future__ import annotations
import argparse, asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import gemini_plan_search_queries, build_role_queries, title_dictionary, norm_domain

async def main_async():
    ap=argparse.ArgumentParser()
    ap.add_argument("company")
    ap.add_argument("--mode", choices=["f","nf","both"], default="both")
    ap.add_argument("--location", default="")
    ap.add_argument("--industry", default="")
    ap.add_argument("--max", type=int, default=8)
    args=ap.parse_args()
    domain=norm_domain(args.company)
    q=await gemini_plan_search_queries(domain,domain,args.mode,args.location,args.industry,[],args.max)
    if not q:
        q=[x[0] for x in build_role_queries(domain,domain,args.mode,args.location,args.industry)[:args.max]]
    print(json.dumps({"queries":q},indent=2,ensure_ascii=False))

if __name__=="__main__": asyncio.run(main_async())
