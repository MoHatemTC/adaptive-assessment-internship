-- P3 P1: Assessment PIPELINES — a named, ordered series of assessments delivered
-- back-to-back to the same learner account. A pipeline references existing
-- assessment_templates as ordered stages; an enrollment tracks one learner's
-- progress through the stages.

create table if not exists assessment_pipelines (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  description  text,
  is_active    boolean default true,
  created_by   text,
  created_at   timestamptz default now()
);

create table if not exists pipeline_stages (
  pipeline_id  uuid not null references assessment_pipelines(id) on delete cascade,
  template_id  uuid not null references assessment_templates(id) on delete cascade,
  stage_order  int not null default 0,
  primary key (pipeline_id, template_id)
);
create index if not exists idx_pipeline_stages on pipeline_stages(pipeline_id, stage_order);

create table if not exists pipeline_enrollments (
  id              uuid primary key default gen_random_uuid(),
  pipeline_id     uuid not null references assessment_pipelines(id) on delete cascade,
  candidate_email text not null,
  candidate_name  text,
  current_stage   int default 0,           -- index into the ordered stages
  status          text default 'active' check (status in ('active','completed','cancelled')),
  created_at      timestamptz default now()
);
create index if not exists idx_pipeline_enroll on pipeline_enrollments(candidate_email);
