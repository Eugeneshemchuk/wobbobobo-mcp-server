-- Run this in Supabase SQL Editor before using the MCP server's
-- log_video_performance / correlate_performance tools.

create table public.video_performance (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.video_projects(project_id),
  views integer not null,
  checked_at timestamptz not null default now()
);

create index idx_video_performance_project_id on public.video_performance(project_id);
