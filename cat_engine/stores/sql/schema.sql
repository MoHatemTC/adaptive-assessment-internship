-- The bank store, relationally.
--
-- APPLIED IDEMPOTENTLY AT STARTUP, NOT BY A MIGRATION TOOL.
--
-- Eight tables owned by one service. Alembic buys ordered, reversible migrations across
-- many authors, and costs a dependency, a version table and a directory of scripts that
-- have to be reviewed as carefully as the schema. That trade is worth making when the
-- schema is contested; this one is not, yet. `CREATE TABLE IF NOT EXISTS` is honest about
-- where it sits, and the moment a column has to change type under live data, that is the
-- moment to add the tool rather than before.
--
-- WHY `source_bytes` EXISTS BESIDE THE NORMALISED ROWS
--
-- The version of a bank is a sha256 over the bytes of its bank file, its graph file and its
-- declared profile. Every orchestrator cache is keyed on it and every assessment pins it.
-- Rebuilding a hash from normalised rows would produce a DIFFERENT number for the same
-- bank — every cache would invalidate, every pinned expectation in the test suite would
-- move, and five banks would quietly become five different banks with the same names.
--
-- So the bytes are kept and hashed exactly as the file store hashes them, and the rows are
-- what queries read. The bytes are provenance and identity; the rows are the index.

-- The identity of a bank, and nothing that varies between its versions. The LAYER a bank
-- is served from is a property of the version, not of the bank — see `bank_version.source`.
CREATE TABLE IF NOT EXISTS bank (
    bank_id     text PRIMARY KEY,
    title       text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bank_version (
    version                 text PRIMARY KEY,
    bank_id                 text NOT NULL REFERENCES bank(bank_id) ON DELETE CASCADE,
    title                   text NOT NULL DEFAULT '',
    -- THE LAYER, AND IT BELONGS TO THE VERSION RATHER THAN THE BANK.
    --
    -- The file store has two layers: checked-in seeds, and whatever has been written over
    -- them. A stored bank SHADOWS a seed of the same id, and deleting the shadow makes the
    -- seed reappear — which is what makes an accidental overwrite recoverable without a
    -- redeploy.
    --
    -- Putting this on `bank` instead loses that: replacing a seed would have to overwrite
    -- the row, and deleting the replacement would cascade the seed's rows away with it.
    -- Both layers coexist as versions of one bank; only one is current.
    source                  text NOT NULL DEFAULT 'stored',
    coverage_critical_only  boolean,
    mains                   text[] NOT NULL DEFAULT '{}',
    -- Exactly what the file store hashes, so the version is the same number.
    items_bytes             bytea NOT NULL,
    graph_bytes             bytea,
    profile_bytes           bytea NOT NULL,
    -- Only one version of a bank is servable at a time, which is what `PUT` replaces and
    -- what a live session pins against.
    is_current              boolean NOT NULL DEFAULT true,
    registered_at           timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS bank_version_one_current
    ON bank_version (bank_id) WHERE is_current;

CREATE TABLE IF NOT EXISTS item (
    version                     text NOT NULL REFERENCES bank_version(version) ON DELETE CASCADE,
    item_id                     text NOT NULL,
    modality                    text NOT NULL,
    -- Whether selection may administer this at all. NOT a payload field: an item the bank
    -- has retired must be invisible to ranking, and a bank that carries retired items is
    -- ordinary — `JAI-600` retires 64 whose hidden tests are incomplete.
    status                      text NOT NULL DEFAULT 'active',
    competency                  text NOT NULL DEFAULT '',
    sub_competency              text NOT NULL DEFAULT '',
    cat_a                       double precision NOT NULL,
    cat_b                       double precision NOT NULL,
    cat_c                       double precision NOT NULL DEFAULT 0,
    estimated_time_seconds      double precision,
    minimum_success_confidence  double precision,
    -- Genuinely per-modality — a code item carries tests, a rubric and a reference
    -- solution; an MCQ carries options and an answer index — and nothing queries inside it.
    -- Normalising this would buy a migration per authored payload change and no query.
    payload                     jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (version, item_id)
);

CREATE INDEX IF NOT EXISTS item_by_version_status ON item (version, status);

-- THE TABLE THE WHOLE FEATURE RESTS ON.
--
-- Everything else here transcribes what is already in the bank file. This one is the index
-- that turns "which questions measure C1.1 and C1.4" from a scan of 600 items into a
-- membership test.
CREATE TABLE IF NOT EXISTS item_measure (
    version   text NOT NULL,
    item_id   text NOT NULL,
    node_id   text NOT NULL,
    -- The item's loading on that node. NOT normalised and under no sum constraint — a
    -- different quantity from the node-to-main mass on `competency_edge`, and conflating
    -- the two is the standing trap in this codebase.
    weight    double precision NOT NULL,
    PRIMARY KEY (version, item_id, node_id),
    FOREIGN KEY (version, item_id) REFERENCES item(version, item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS item_measure_by_node ON item_measure (version, node_id);

CREATE TABLE IF NOT EXISTS competency_node (
    version             text NOT NULL REFERENCES bank_version(version) ON DELETE CASCADE,
    node_id             text NOT NULL,
    title               text NOT NULL DEFAULT '',
    node_type           text NOT NULL DEFAULT 'sub_competency',
    -- The critical set the coverage gate requires before a competency may converge.
    critical            boolean NOT NULL DEFAULT false,
    context_specific    boolean NOT NULL DEFAULT false,
    -- An array because a sub-competency may serve SEVERAL mains. `C1.6` serves both C1 and
    -- C6, and one node holds one authoritative state that every main it serves reads.
    main_competencies   text[] NOT NULL DEFAULT '{}',
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (version, node_id)
);

CREATE INDEX IF NOT EXISTS competency_node_by_main
    ON competency_node USING gin (main_competencies);

CREATE TABLE IF NOT EXISTS competency_edge (
    version                 text NOT NULL REFERENCES bank_version(version) ON DELETE CASCADE,
    from_id                 text NOT NULL,
    to_id                   text NOT NULL,
    relation                text NOT NULL,
    strength                double precision NOT NULL DEFAULT 1.0,
    weight                  double precision NOT NULL DEFAULT 1.0,
    -- The AUTHORED permissions. What an edge is permitted to do after the deployment,
    -- bank and edge levels are ANDed together is a different question, answered per
    -- deployment by `policy.py` and never stored here.
    allow_upward_inference  boolean NOT NULL DEFAULT true,
    allow_downward_blocking boolean NOT NULL DEFAULT true,
    -- `validation_status` lives in here, and it is what decides whether an edge may ever
    -- infer or block. Dropping it would hand back a graph whose edges had lost their
    -- authority to be inert.
    metadata                jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (version, from_id, to_id, relation)
);

CREATE INDEX IF NOT EXISTS competency_edge_by_source ON competency_edge (version, from_id);

CREATE TABLE IF NOT EXISTS bank_policy (
    version                       text PRIMARY KEY REFERENCES bank_version(version) ON DELETE CASCADE,
    upward_inference              boolean NOT NULL DEFAULT false,
    descendant_blocking           boolean NOT NULL DEFAULT false,
    minimum_failures_to_block     integer,
    accepted_validation_statuses  text[] NOT NULL DEFAULT '{}',
    notes                         text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS upload (
    upload_id          text PRIMARY KEY,
    bank_id            text NOT NULL DEFAULT '',
    filename           text NOT NULL DEFAULT '',
    status             text NOT NULL,
    -- Kept before anything is parsed. A bank that fails to parse is still the artefact
    -- somebody uploaded, and a rejection that cannot be reproduced from the original input
    -- is a support ticket with no evidence in it.
    raw                bytea,
    validation_report  jsonb,
    version            text,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS upload_recent ON upload (created_at DESC);
