-- v31 OSINT/vector support
create extension if not exists vector with schema extensions;

create table if not exists public.lead_embeddings (
  lead_id uuid primary key references public.leads(id) on delete cascade,
  embedding extensions.vector(384) not null,
  model text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists lead_embeddings_hnsw
  on public.lead_embeddings using hnsw (embedding vector_cosine_ops);

create or replace function public.match_lead_embeddings(query_embedding extensions.vector(384), match_count int default 20)
returns table (lead_id uuid, similarity float)
language sql stable
as $$
  select e.lead_id, 1 - (e.embedding <=> query_embedding) as similarity
  from public.lead_embeddings e
  order by e.embedding <=> query_embedding
  limit greatest(match_count, 1);
$$;
