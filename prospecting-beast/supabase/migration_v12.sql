-- Run this ONCE in Supabase SQL Editor if you already have the older Prospecting Beast schema.
alter table public.leads add column if not exists root_domain text;
alter table public.leads add column if not exists parent_domain text;
alter table public.leads add column if not exists relationship_display text;
create index if not exists idx_leads_root_domain on public.leads(root_domain);
create index if not exists idx_leads_relationship on public.leads(relationship);
