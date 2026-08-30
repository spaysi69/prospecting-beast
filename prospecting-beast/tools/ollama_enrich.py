#!/usr/bin/env python3
"""Local AI enrichment via Ollama; no paid LLM required.
Reads a JSON array of leads and writes AI summaries/classifications to JSON.
"""
from __future__ import annotations
import argparse, asyncio, json
import httpx

async def enrich(lead: dict, base: str, model: str) -> dict:
    prompt=(f"Return JSON only with keys summary,pain_points,industry,technology_signals. "
            f"Do not invent facts; use only this lead data: {json.dumps(lead)[:12000]}")
    payload={"model":model,"prompt":prompt,"stream":False,"format":"json"}
    async with httpx.AsyncClient(timeout=120) as c:
        r=await c.post(base.rstrip('/')+'/api/generate',json=payload); r.raise_for_status()
        obj=json.loads(r.json().get('response','{}'))
    return {**lead,"local_ai":obj}

async def main_async():
    ap=argparse.ArgumentParser(); ap.add_argument('input'); ap.add_argument('-o','--output',default='ollama_enriched.json'); ap.add_argument('--model',default='gemma4:26b'); ap.add_argument('--url',default='http://127.0.0.1:11434'); args=ap.parse_args()
    data=json.loads(open(args.input,encoding='utf-8').read())
    out=[]
    for lead in data: out.append(await enrich(lead,args.url,args.model))
    open(args.output,'w',encoding='utf-8').write(json.dumps(out,indent=2,ensure_ascii=False))
    print(f'wrote {len(out)} leads to {args.output}')

if __name__=='__main__': asyncio.run(main_async())
