-- Sprint 7 patch: additional constraints on invitations table

-- Prevent race-condition double-sessions: enforce one session_id per invitation
create unique index if not exists idx_inv_session_unique
  on invitations(session_id)
  where session_id is not null;

-- Prevent duplicate active invitations for same template+candidate
-- (allows re-inviting after completion/expiry)
create unique index if not exists idx_inv_active_unique
  on invitations(template_id, candidate_email)
  where status not in ('expired', 'completed');
