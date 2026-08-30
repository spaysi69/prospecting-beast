#!/usr/bin/env python3
"""Generate local MiniLM embeddings for saved leads and upsert into Supabase Postgres.

Requires SUPABASE_DB_URL (the direct Postgres connection string) plus the optional
SUPABASE_EMBED_BATCH environment variable. Runs locally; no paid LLM is used.
"""
from __future__ import annotations
import os, sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / '.env')

import psycopg
from sentence_transformers import SentenceTransformer

MODEL = os.getenv('EMBED_MODEL', 'sentence-transformers/all-MiniLM-L6-v2')
BATCH = int(os.getenv('SUPABASE_EMBED_BATCH', '32'))

def main():
    db_url = os.getenv('SUPABASE_DB_URL', '').strip()
    if not db_url:
        raise SystemExit('Missing SUPABASE_DB_URL. Get the Postgres connection string from Supabase Project Settings → Database.')
    model = SentenceTransformer(MODEL, device='cpu')
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT l.id, coalesce(l.name,\'\'), coalesce(l.title,\'\'), coalesce(l.company,\'\'), coalesce(l.matched_title,\'\')
                           FROM public.leads l
                           LEFT JOIN public.lead_embeddings e ON e.lead_id = l.id
                           WHERE e.lead_id IS NULL
                           ORDER BY l.created_at NULLS LAST''')
            rows = cur.fetchall()
        for start in range(0, len(rows), BATCH):
            chunk = rows[start:start+BATCH]
            texts = [f'{name}\n{title}\n{company}\nTarget role: {matched}' for _,name,title,company,matched in chunk]
            vectors = model.encode(texts, normalize_embeddings=True).tolist()
            with conn.cursor() as cur:
                for (lead_id, *_), vec in zip(chunk, vectors):
                    cur.execute('''INSERT INTO public.lead_embeddings (lead_id, embedding, model)
                                   VALUES (%s, %s::vector, %s)
                                   ON CONFLICT (lead_id) DO UPDATE SET embedding=EXCLUDED.embedding, model=EXCLUDED.model, updated_at=now()''',
                                (lead_id, '[' + ','.join(f'{float(x):.8f}' for x in vec) + ']', MODEL))
            conn.commit()
            print(f'embedded {min(start+BATCH,len(rows))}/{len(rows)}')
    print('done')

if __name__ == '__main__':
    main()
