-- P3 L2: editable email templates (admin can edit the invitation email).
-- Keyed by a stable string (e.g. 'invitation'). html_body may contain
-- {{candidate_name}}, {{assessment_title}}, {{invite_url}}, {{time_limit}}
-- placeholders that the backend substitutes at send time. If no row exists for a
-- key, the backend falls back to its built-in default template.

create table if not exists email_templates (
  key         text primary key,
  subject     text not null,
  html_body   text not null,
  updated_by  text,
  updated_at  timestamptz default now()
);
