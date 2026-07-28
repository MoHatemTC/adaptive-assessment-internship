-- ============================================================
-- Migration 008: Missing columns discovered during live testing
-- Run in Supabase SQL Editor
-- ============================================================

-- 1. Reference photo column on user_profiles (used by proctoring identity check)
ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS reference_photo_base64 TEXT;

-- 2. question_number on candidate_answers (links answer to its position in session)
ALTER TABLE candidate_answers
  ADD COLUMN IF NOT EXISTS question_number INT;
