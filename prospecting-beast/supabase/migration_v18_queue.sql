alter table public.jobs add column if not exists archived_at timestamptz;
create index if not exists idx_jobs_archived on public.jobs(archived_at, created_at desc);
