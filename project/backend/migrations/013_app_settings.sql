-- Migration 013: platform settings (global on/off toggle for accepting new sessions)
CREATE TABLE IF NOT EXISTS app_settings (
  key         text PRIMARY KEY,
  value       jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO app_settings (key, value)
VALUES ('platform', '{"accepting_sessions": true}'::jsonb)
ON CONFLICT (key) DO NOTHING;
