-- ============================================================
-- Migration 007: Refactor tool_config from flat to nested format
--
-- Old: { mcq: true, mcq_count: 3, voice: true, voice_count: 1, ... }
-- New: { mcq: { enabled: true, count: 3 }, voice: { enabled: true, count: 1 }, ... }
--
-- Run this in Supabase SQL Editor AFTER migration 006.
-- Safe to re-run: WHERE clause only touches rows still in old format.
-- ============================================================

UPDATE assessment_templates
SET tool_config = jsonb_build_object(
  'mcq', jsonb_build_object(
    'enabled', COALESCE((tool_config->>'mcq')::boolean, false),
    'count',   (tool_config->>'mcq_count')::int
  ),
  'voice', jsonb_build_object(
    'enabled', COALESCE((tool_config->>'voice')::boolean, false),
    'count',   (tool_config->>'voice_count')::int
  ),
  'coding', jsonb_build_object(
    'enabled',  COALESCE((tool_config->>'coding')::boolean, false),
    'count',    (tool_config->>'coding_count')::int,
    'language', COALESCE(tool_config->>'coding_language', 'python')
  ),
  'visualization', jsonb_build_object(
    'enabled', COALESCE((tool_config->>'visualization')::boolean, false),
    'count',   (tool_config->>'visualization_count')::int
  ),
  'task', jsonb_build_object(
    'enabled', COALESCE((tool_config->>'task')::boolean, false),
    'count',   1
  )
)
-- Only rows still using the old flat format (mcq is a boolean, not an object)
WHERE tool_config IS NOT NULL
  AND jsonb_typeof(tool_config->'mcq') = 'boolean';
