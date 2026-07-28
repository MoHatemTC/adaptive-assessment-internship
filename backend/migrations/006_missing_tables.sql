-- ============================================================
-- Migration 006: Missing tables and columns
-- Run this in Supabase SQL Editor
-- ============================================================

-- 1. Add generated_question_id to candidate_answers
--    (links each answer back to the generated_questions row)
ALTER TABLE candidate_answers
  ADD COLUMN IF NOT EXISTS generated_question_id UUID REFERENCES generated_questions(id) ON DELETE SET NULL;

ALTER TABLE candidate_answers
  ADD COLUMN IF NOT EXISTS answer_data JSONB;

-- 2. Create coding_challenges table
CREATE TABLE IF NOT EXISTS coding_challenges (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id        UUID NOT NULL REFERENCES candidate_sessions(id) ON DELETE CASCADE,
  question_number   INT NOT NULL,
  topic             TEXT,
  skill_target      TEXT DEFAULT 'work',
  difficulty        TEXT DEFAULT 'medium',
  body              TEXT,
  starter_code      TEXT,
  sample_solution   TEXT,
  expected_approach TEXT,
  constraints       TEXT,
  test_cases        JSONB DEFAULT '[]',
  submitted_code    TEXT,
  llm_scores        JSONB,
  overall_score     NUMERIC(4, 2),
  grading_rationale TEXT,
  submitted_at      TIMESTAMPTZ,
  created_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_coding_challenges_session
  ON coding_challenges(session_id);

-- 3. Create proctoring_frames table
--    (stores per-frame camera snapshots / flagged frame metadata)
CREATE TABLE IF NOT EXISTS proctoring_frames (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id   UUID NOT NULL REFERENCES candidate_sessions(id) ON DELETE CASCADE,
  frame_url    TEXT,
  flagged      BOOLEAN DEFAULT false,
  flag_reason  TEXT,
  confidence   NUMERIC(5, 4),
  captured_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_proctoring_frames_session
  ON proctoring_frames(session_id);
