-- P3 B2: reusable Question SETS. An admin can group bank questions into a named
-- set and attach a set to an assessment at creation time. The chosen set id is
-- stored on the template's tool_config JSON as `question_set_id` (no column needed).

create table if not exists question_sets (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  description  text,
  track        text,
  created_by   text,
  created_at   timestamptz default now()
);

create table if not exists question_set_items (
  set_id       uuid not null references question_sets(id) on delete cascade,
  question_id  uuid not null references question_bank(id) on delete cascade,
  sort_order   int default 0,
  primary key (set_id, question_id)
);

create index if not exists idx_qset_items_set on question_set_items(set_id);
