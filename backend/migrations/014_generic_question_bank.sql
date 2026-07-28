-- P3 B1/B3: make the Question Bank GENERIC (not locked to one assessment) and
-- allow every question style (adds 'video').
--
-- Back-compat: existing rows keep their template_id. A NULL template_id now means
-- a GLOBAL bank question usable by any assessment. Readers treat (template_id = me
-- OR template_id IS NULL) as available.

-- 1) template_id becomes optional (global questions)
alter table question_bank alter column template_id drop not null;

-- 2) allow the 'video' tool type (parallel to live voice) in the bank
alter table question_bank drop constraint if exists question_bank_tool_type_check;
alter table question_bank add constraint question_bank_tool_type_check
  check (tool_type in ('mcq','voice','video','coding','task','visualization','personality'));

-- 3) optional scope label for filtering global questions by track/topic (nullable)
alter table question_bank add column if not exists scope text;

create index if not exists idx_qbank_global on question_bank(tool_type, skill_target)
  where template_id is null;
