-- ============================================================
-- Masar — Platform Upgrade Migration
-- Run after 001_initial.sql
-- ============================================================

-- ── 1. user_profiles ──────────────────────────────────────
create table if not exists user_profiles (
  id          uuid primary key references auth.users(id) on delete cascade,
  role        text not null default 'candidate' check (role in ('admin', 'candidate')),
  full_name   text,
  avatar_url  text,
  created_at  timestamptz default now()
);

-- ── 2. cv_uploads ─────────────────────────────────────────
create table if not exists cv_uploads (
  id          uuid primary key default gen_random_uuid(),
  session_id  uuid references candidate_sessions(id) on delete set null,
  user_id     uuid references auth.users(id) on delete set null,
  file_name   text,
  parsed_json jsonb,
  raw_text    text,
  created_at  timestamptz default now()
);

-- ── 3. learner_profiles ───────────────────────────────────
create table if not exists learner_profiles (
  id               uuid primary key default gen_random_uuid(),
  session_id       uuid references candidate_sessions(id) on delete cascade unique,
  skill_scores     jsonb default '{}'::jsonb,
  known_topics     text[] default '{}',
  gap_topics       text[] default '{}',
  cv_claims        jsonb default '{}'::jsonb,
  questions_asked  int default 0,
  updated_at       timestamptz default now()
);

-- ── 4. memory_cards ───────────────────────────────────────
create table if not exists memory_cards (
  id              uuid primary key default gen_random_uuid(),
  session_id      uuid references candidate_sessions(id) on delete cascade,
  question_number int,
  skill           text,
  topic           text,
  verdict         text check (verdict in ('knows', 'partial', 'gap')),
  evidence        text,
  score           numeric,
  created_at      timestamptz default now()
);

-- ── 5. generated_questions ────────────────────────────────
create table if not exists generated_questions (
  id              uuid primary key default gen_random_uuid(),
  session_id      uuid references candidate_sessions(id) on delete cascade,
  question_number int,
  tool_type       text,
  body            text,
  payload         jsonb,
  skill_target    text,
  topic           text,
  difficulty      text,
  created_at      timestamptz default now()
);

-- ── 6. ALTER candidate_sessions ───────────────────────────
alter table candidate_sessions
  add column if not exists cv_upload_id        uuid references cv_uploads(id) on delete set null,
  add column if not exists time_limit_minutes  int default 60,
  add column if not exists time_started        timestamptz,
  add column if not exists learner_profile_id  uuid references learner_profiles(id) on delete set null;

-- ── 7. ALTER assessment_templates ────────────────────────
alter table assessment_templates
  add column if not exists task_config jsonb;

-- ── Indexes ───────────────────────────────────────────────
create index if not exists idx_cv_uploads_session     on cv_uploads(session_id);
create index if not exists idx_cv_uploads_user        on cv_uploads(user_id);
create index if not exists idx_learner_profiles_sess  on learner_profiles(session_id);
create index if not exists idx_memory_cards_session   on memory_cards(session_id);
create index if not exists idx_memory_cards_verdict   on memory_cards(session_id, verdict);
create index if not exists idx_gen_questions_session  on generated_questions(session_id);
create index if not exists idx_user_profiles_role     on user_profiles(role);
