-- Sprint 7: Invitations + Question Bank

-- ── Invitations ────────────────────────────────────────────────────────────────
create table if not exists invitations (
  id                uuid primary key default gen_random_uuid(),
  template_id       uuid references assessment_templates(id) on delete cascade,
  candidate_email   text not null,
  candidate_name    text,
  invitation_token  text unique not null default encode(gen_random_bytes(16), 'hex'),
  status            text default 'pending' check (status in ('pending','opened','started','completed','expired')),
  expires_at        timestamptz,           -- admin-set expiry; null = never expires
  invited_at        timestamptz default now(),
  opened_at         timestamptz,
  started_at        timestamptz,
  completed_at      timestamptz,
  invited_by        text,                  -- admin email
  notes             text,
  session_id        uuid references candidate_sessions(id) on delete set null
);

create index if not exists idx_inv_template   on invitations(template_id);
create index if not exists idx_inv_email      on invitations(candidate_email);
create index if not exists idx_inv_token      on invitations(invitation_token);
create index if not exists idx_inv_status     on invitations(status);

-- ── Question Bank ──────────────────────────────────────────────────────────────
create table if not exists question_bank (
  id            uuid primary key default gen_random_uuid(),
  template_id   uuid not null references assessment_templates(id) on delete cascade,
  tool_type     text not null check (tool_type in ('mcq','voice','coding','task','visualization','personality')),
  skill_target  text not null,
  difficulty    text not null check (difficulty in ('easy','medium','hard')),
  tags          text[] default '{}',
  body          text not null,
  payload       jsonb default '{}',   -- options, answer_key, rubric, test_cases, etc.
  is_active     boolean default true,
  created_by    text,
  created_at    timestamptz default now()
);

create index if not exists idx_qbank_template    on question_bank(template_id);
create index if not exists idx_qbank_tool_skill  on question_bank(tool_type, skill_target);
create index if not exists idx_qbank_tags        on question_bank using gin(tags);
create index if not exists idx_qbank_fts         on question_bank using gin(to_tsvector('english', body));
