-- Migration 011: P1 — multi-mode assessments, adaptivity level, admin intake config
-- Additive + backward compatible. Safe to run more than once.

-- 1) New columns on assessment_templates
ALTER TABLE assessment_templates
  ADD COLUMN IF NOT EXISTS modes            jsonb DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS adaptivity_level text  DEFAULT 'high',
  ADD COLUMN IF NOT EXISTS intake_config    jsonb DEFAULT '{}'::jsonb;

-- 2) Adaptivity level must be one of low | medium | high
ALTER TABLE assessment_templates
  DROP CONSTRAINT IF EXISTS assessment_templates_adaptivity_level_check;
ALTER TABLE assessment_templates
  ADD  CONSTRAINT assessment_templates_adaptivity_level_check
  CHECK (adaptivity_level IN ('low', 'medium', 'high'));

-- 3) Retire the stale assessment_type CHECK.
--    The UI already emits 'personality' and 'hr', and modes[] now supersedes the
--    single assessment_type. We keep the assessment_type column for back-compat
--    but stop constraining it.
ALTER TABLE assessment_templates
  DROP CONSTRAINT IF EXISTS assessment_templates_assessment_type_check;

-- 4) Backfill modes[] from the legacy single assessment_type so existing
--    templates keep running unchanged.
UPDATE assessment_templates
   SET modes = to_jsonb(ARRAY[assessment_type])
 WHERE (modes IS NULL OR modes = '[]'::jsonb)
   AND assessment_type IS NOT NULL;
