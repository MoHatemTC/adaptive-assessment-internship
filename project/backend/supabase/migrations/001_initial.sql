-- ============================================================
-- Masar — Initial Schema
-- Run this in: Supabase Dashboard → SQL Editor → Run
-- ============================================================

-- Enable UUID extension
create extension if not exists "pgcrypto";

-- ── 1. Assessment Templates ────────────────────────────────
create table if not exists assessment_templates (
  id            uuid primary key default gen_random_uuid(),
  title         text not null,
  description   text,
  track         text,
  admin_prompt  text,                          -- raw admin config prompt
  tool_config   jsonb default '{}'::jsonb,     -- which tools are enabled
  is_published  boolean default false,
  public_link_token text unique,
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);

-- ── 2. Workflow Steps ──────────────────────────────────────
create table if not exists workflow_steps (
  id            uuid primary key default gen_random_uuid(),
  template_id   uuid references assessment_templates(id) on delete cascade,
  position      int not null,
  step_type     text not null check (step_type in ('mcq','interview','coding','diagram','camera')),
  config        jsonb default '{}'::jsonb,
  gate_threshold numeric,
  created_at    timestamptz default now()
);

-- ── 3. Question Bank ───────────────────────────────────────
create table if not exists questions (
  id            uuid primary key default gen_random_uuid(),
  question_type text not null check (question_type in ('mcq','open_ended','coding','diagram','true_false')),
  body          text not null,
  skill         text not null check (skill in ('thinking','soft','work','digital_ai','growth')),
  difficulty    text not null check (difficulty in ('easy','medium','hard')),
  track         text,
  options       jsonb,        -- MCQ: [{id, text}]
  answer_key    jsonb,        -- MCQ: {correct_id, explanation} | coding: {test_cases}
  rubric        jsonb,        -- open-ended scoring rubric
  image_url     text,         -- diagram questions
  qdrant_id     text,         -- reference back to Qdrant point
  is_active     boolean default true,
  created_at    timestamptz default now()
);

-- ── 4. Blueprints ──────────────────────────────────────────
create table if not exists blueprints (
  id            uuid primary key default gen_random_uuid(),
  template_id   uuid references assessment_templates(id) on delete cascade,
  content       jsonb not null,   -- full generated blueprint JSON
  created_at    timestamptz default now()
);

-- ── 5. Candidate Sessions ──────────────────────────────────
create table if not exists candidate_sessions (
  id            uuid primary key default gen_random_uuid(),
  template_id   uuid references assessment_templates(id),
  blueprint_id  uuid references blueprints(id),
  candidate_name  text,
  candidate_email text,
  track         text,
  status        text default 'profile' check (status in (
                  'profile','identity','in_progress','grading','completed','flagged'
                )),
  agent_state   jsonb default '{}'::jsonb,   -- full LangGraph state snapshot
  ability_estimates jsonb default '{}'::jsonb, -- per-skill theta
  asked_question_ids text[] default '{}',
  integrity_status  text default 'clean' check (integrity_status in ('clean','warned','flagged')),
  started_at    timestamptz,
  completed_at  timestamptz,
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);

-- ── 6. Step Sessions ───────────────────────────────────────
create table if not exists step_sessions (
  id            uuid primary key default gen_random_uuid(),
  session_id    uuid references candidate_sessions(id) on delete cascade,
  step_id       uuid references workflow_steps(id),
  step_type     text not null,
  status        text default 'pending' check (status in ('pending','active','completed','skipped')),
  score         numeric,
  started_at    timestamptz,
  completed_at  timestamptz
);

-- ── 7. Candidate Answers ───────────────────────────────────
create table if not exists candidate_answers (
  id            uuid primary key default gen_random_uuid(),
  session_id    uuid references candidate_sessions(id) on delete cascade,
  question_id   uuid references questions(id),
  step_session_id uuid references step_sessions(id),
  answer_text   text,
  answer_data   jsonb,           -- structured answer (MCQ selection, code, etc.)
  score         numeric,
  skill         text,
  skill_scores  jsonb,           -- {thinking: 3, soft: 0, ...}
  grading_rationale text,
  judge_verdict jsonb,           -- LLM judge result
  created_at    timestamptz default now()
);

-- ── 8. Proctoring Events ───────────────────────────────────
create table if not exists proctoring_events (
  id            uuid primary key default gen_random_uuid(),
  session_id    uuid references candidate_sessions(id) on delete cascade,
  event_type    text not null check (event_type in (
                  'tab_switch','copy_paste','camera_off','screenshot',
                  'ai_tool_detected','identity_mismatch','window_blur'
                )),
  severity      text default 'low' check (severity in ('low','medium','high')),
  metadata      jsonb default '{}'::jsonb,
  created_at    timestamptz default now()
);

-- ── 9. Final Reports ───────────────────────────────────────
create table if not exists final_reports (
  id            uuid primary key default gen_random_uuid(),
  session_id    uuid references candidate_sessions(id) on delete cascade unique,
  skill_scores  jsonb not null,   -- {thinking, soft, work, digital_ai, growth}
  total_score   numeric,
  placement     text check (placement in ('BEGINNER','PRO')),
  feedback      text,
  recommendations jsonb,
  integrity_status text,
  email_sent    boolean default false,
  created_at    timestamptz default now()
);

-- ── Indexes ────────────────────────────────────────────────
create index if not exists idx_questions_skill_diff on questions(skill, difficulty);
create index if not exists idx_questions_track on questions(track);
create index if not exists idx_sessions_template on candidate_sessions(template_id);
create index if not exists idx_sessions_status on candidate_sessions(status);
create index if not exists idx_answers_session on candidate_answers(session_id);
create index if not exists idx_proctor_session on proctoring_events(session_id);
