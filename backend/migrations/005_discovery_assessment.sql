-- ============================================================
-- Migration 005: Discovery assessment type
-- Run this in Supabase SQL Editor
-- ============================================================

-- Step 1: Add assessment_type to assessment_templates
ALTER TABLE assessment_templates
  ADD COLUMN IF NOT EXISTS assessment_type TEXT DEFAULT 'track'
    CHECK (assessment_type IN ('track', 'discover'));

-- Step 2: Add discovery_result to final_reports
ALTER TABLE final_reports
  ADD COLUMN IF NOT EXISTS discovery_result JSONB;

-- Step 3: Seed the default discovery template
INSERT INTO assessment_templates
  (title, track, track_slug, assessment_type, is_default, is_published, time_limit_minutes, admin_prompt, tool_config)
VALUES (
  'Career Discovery Assessment',
  NULL,
  'discover',
  'discover',
  true,
  false,
  30,
  'This is a career discovery assessment. Your goal is NOT to test domain knowledge or technical skill. Your goal is to understand the candidate''s natural aptitude, personality traits, and work preferences to recommend the most suitable career track from our 9 tracks. Ask questions that reveal: what kinds of problems energize them, how they prefer to process information (analytical vs creative vs systematic vs social), what type of work output feels meaningful to them, and how they approach uncertainty and collaboration. Use MCQ, Voice, and Visualization only. NEVER ask domain-specific technical questions. Ask preference, scenario, and aptitude questions that any person — technical background or not — can answer authentically.',
  '{"mcq": true, "mcq_count": 3, "voice": true, "voice_count": 2, "coding": false, "coding_count": 0, "visualization": true, "visualization_count": 2, "task": false, "task_count": 0}'::jsonb
);
