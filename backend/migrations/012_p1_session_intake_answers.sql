-- Migration 012: P1 — store the learner's pre-assessment intake-form answers
-- Additive + backward compatible. Safe to run more than once.

ALTER TABLE candidate_sessions
  ADD COLUMN IF NOT EXISTS intake_answers jsonb DEFAULT '{}'::jsonb;
