create extension if not exists pgcrypto;

create table if not exists public.jobs (
  id uuid primary key default gen_random_uuid(),
  status text not null default 'queued',
  mode text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  config jsonb not null default '{}'::jsonb,
  stats jsonb not null default '{}'::jsonb,
  log jsonb not null default '[]'::jsonb,
  error text,
  owner_token text
);

create table if not exists public.companies (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  family_id text not null,
  root_domain text not null,
  domain text not null,
  name text,
  relationship text not null default 'original',
  parent_domain text,
  status text not null default 'queued',
  evidence jsonb not null default '[]'::jsonb,
  people_found integer not null default 0,
  qualified_people integer not null default 0,
  enriched_people integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(job_id, domain)
);

create table if not exists public.leads (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  family_id text not null,
  company_id uuid references public.companies(id) on delete set null,
  company text,
  domain text,
  relationship text,
  name text,
  title text,
  matched_title text,
  title_score numeric,
  similarity numeric,
  linkedin text,
  phone text,
  email text,
  source text,
  seamless_key_index integer,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.relationships (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  family_id text not null,
  source_domain text not null,
  related_domain text not null,
  related_name text,
  relationship text not null,
  confidence numeric,
  evidence jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  unique(job_id, source_domain, related_domain, relationship)
);

create table if not exists public.logs (
  id bigint generated always as identity primary key,
  job_id uuid not null references public.jobs(id) on delete cascade,
  created_at timestamptz not null default now(),
  level text not null default 'info',
  message text not null
);

create index if not exists idx_companies_job on public.companies(job_id);
create index if not exists idx_leads_job on public.leads(job_id);
alter table public.leads add column if not exists root_domain text;
alter table public.leads add column if not exists parent_domain text;
alter table public.leads add column if not exists relationship_display text;
create unique index if not exists ux_leads_dedupe on public.leads(job_id, lower(coalesce(linkedin,'')), lower(coalesce(email,'')), lower(coalesce(name,'')), lower(coalesce(domain,''))); 
create index if not exists idx_relationships_job on public.relationships(job_id);
create index if not exists idx_logs_job on public.logs(job_id, created_at);

alter table public.jobs enable row level security;
alter table public.companies enable row level security;
alter table public.leads enable row level security;
alter table public.relationships enable row level security;
alter table public.logs enable row level security;

-- The backend uses the Supabase service-role key, so backend requests bypass RLS.
-- No public browser policy is created; data is only exposed through this app's password-protected API.
