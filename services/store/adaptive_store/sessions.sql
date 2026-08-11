-- Live assessments, so losing a process does not lose a candidate.
--
-- SEPARATE FROM THE BANK SCHEMA, DELIBERATELY.
--
-- A bank is content: published, versioned, kept. A session is a record of what a person
-- answered, and it has a deletion deadline. Sharing one schema would not be wrong so much
-- as forgettable — the retention clock becomes a column in a file mostly about banks, and
-- the next person to add a table there has no reason to think about it. `SESSION_DATABASE_URL`
-- may point at the same server; the separation is about lifetime, not about hardware.

CREATE TABLE IF NOT EXISTS assessment_session (
    session_id    text PRIMARY KEY,
    -- Pinned at `begin` and never refreshed, exactly as the in-process store pinned them:
    -- replacing a bank must not change the pool underneath a live candidate.
    bank_id       text NOT NULL,
    bank_version  text NOT NULL,

    -- `AssessmentState`, verbatim. The 41-point posterior is carried as a list of floats
    -- specifically so it round-trips through JSON, which is what made this table possible
    -- without touching the engine.
    state         jsonb NOT NULL,

    -- The exposure-control stream. A numpy generator cannot be stored, but its
    -- `bit_generator.state` can, and restoring it continues the sequence — so a resumed
    -- session does not redraw its first item, which would make selection predictable after
    -- every restart.
    rng_state     jsonb NOT NULL,

    -- The scope this session was narrowed to, or null for a whole-bank assessment. Pinned
    -- like the bank version: the allowlist a candidate began under must not change.
    scope         jsonb,

    -- THE REASON THERE IS NO LOCK.
    --
    -- Two answers to one assessment can be in flight on two replicas. Each reads a revision,
    -- computes, and writes WHERE revision = the one it read; the loser matches no row and is
    -- refused as stale. That is the same guarantee the single-process path got from an
    -- asyncio lock plus a presented-item check, without holding anything across a grading
    -- call that can legitimately take thirty seconds.
    revision      integer NOT NULL DEFAULT 1,

    created_at    timestamptz NOT NULL DEFAULT now(),
    last_access   timestamptz NOT NULL DEFAULT now(),
    -- Null while the assessment is running. The retention clock starts when this is set,
    -- and a session in progress is never removed — the in-process store never evicted one
    -- either, and failing closed on capacity is preferable to dropping a live candidate.
    finished_at   timestamptz
);

-- What `prune` scans. Partial, because the rows it never touches are the live ones.
CREATE INDEX IF NOT EXISTS assessment_session_finished
    ON assessment_session (finished_at) WHERE finished_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS assessment_session_stale
    ON assessment_session (last_access) WHERE finished_at IS NULL;
