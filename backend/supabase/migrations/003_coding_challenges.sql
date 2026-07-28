-- Coding challenges: stores generated challenges + test cases + submissions
CREATE TABLE IF NOT EXISTS coding_challenges (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id         UUID NOT NULL REFERENCES candidate_sessions(id) ON DELETE CASCADE,
    question_number    INT NOT NULL DEFAULT 1,
    topic              TEXT NOT NULL DEFAULT '',
    skill_target       TEXT NOT NULL DEFAULT 'work',
    difficulty         TEXT NOT NULL DEFAULT 'medium',
    body               TEXT NOT NULL DEFAULT '',
    starter_code       TEXT,
    sample_solution    TEXT,
    expected_approach  TEXT,
    constraints        TEXT,
    test_cases         JSONB NOT NULL DEFAULT '[]',
    submitted_code     TEXT,
    llm_scores         JSONB,
    overall_score      FLOAT,
    grading_rationale  TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_coding_challenges_session ON coding_challenges(session_id);
CREATE INDEX IF NOT EXISTS idx_coding_challenges_created ON coding_challenges(created_at DESC);

-- Link each answer back to the generated question that produced it
ALTER TABLE candidate_answers
  ADD COLUMN IF NOT EXISTS question_number       INT,
  ADD COLUMN IF NOT EXISTS generated_question_id UUID REFERENCES generated_questions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_answers_gen_q ON candidate_answers(generated_question_id);
