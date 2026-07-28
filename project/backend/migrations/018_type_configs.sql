-- P4 Group C: per-assessment-TYPE configuration. Each selected assessment type
-- (career/technical/behavioural/psychometric) can carry its own adaptivity level,
-- question set, language, length, and linked competencies. Stored as a JSON map
-- keyed by type. Empty {} → fall back to the template's global fields (back-compat).
--
-- Shape:
-- type_configs = {
--   "technical":   {"adaptivity":"high","question_set_id":"…","language":"English",
--                   "length":"medium","competencies":["Problem Solving","…"]},
--   "behavioural": {…}, "career": {…}, "psychometric": {…}
-- }

alter table assessment_templates add column if not exists type_configs jsonb default '{}'::jsonb;
