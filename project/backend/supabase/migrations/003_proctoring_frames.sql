-- ============================================================
-- Masar — Proctoring Frames Migration
-- Run after 002_platform_upgrade.sql
-- ============================================================

-- Add reference photo column to user_profiles
ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS reference_photo_base64 TEXT;

-- Proctoring frames table — linked to both session AND user
-- so admins can review across all sessions for the same person
CREATE TABLE IF NOT EXISTS proctoring_frames (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id      uuid REFERENCES candidate_sessions(id) ON DELETE CASCADE,
  user_id         uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  frame_base64    TEXT NOT NULL,
  gemini_verdict  jsonb,
  is_flagged      boolean DEFAULT false,
  created_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_proctoring_frames_session  ON proctoring_frames(session_id);
CREATE INDEX IF NOT EXISTS idx_proctoring_frames_user     ON proctoring_frames(user_id);
CREATE INDEX IF NOT EXISTS idx_proctoring_frames_flagged  ON proctoring_frames(user_id, is_flagged);
