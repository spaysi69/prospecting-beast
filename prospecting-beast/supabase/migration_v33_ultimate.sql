-- v33 ultimate: audit/evidence fields and persistent skip list
alter table public.leads add column if not exists evidence jsonb not null default '[]'::jsonb;
alter table public.leads add column if not exists local_ai jsonb;
alter table public.leads add column if not exists source_urls text[];
create table if not exists public.suppression_contacts (
  id uuid primary key default gen_random_uuid(),
  name text,
  domain text,
  email text,
  linkedin text,
  reason text default 'already_have_contact',
  created_at timestamptz not null default now(),
  unique(lower(coalesce(name,'')), lower(coalesce(domain,'')), lower(coalesce(email,'')), lower(coalesce(linkedin,'')))
);
create index if not exists idx_suppression_email on public.suppression_contacts(lower(email));
create index if not exists idx_suppression_linkedin on public.suppression_contacts(lower(linkedin));
