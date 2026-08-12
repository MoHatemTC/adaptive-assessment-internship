"""Module settings: topology and surfaces, as opposed to measurement policy.

WHY THIS IS SEPARATE FROM THE ENGINE'S `Settings`

The engine's settings answer one question — what is a candidate SCORED by — and the whole
point of `engine_config_fingerprint` is that the answer must be identical everywhere. These
answer a different question: where do banks live, which write paths are open, how big an
upload may be. Two modules in one process may legitimately disagree about all of them, and
none of them changes a number in a report.

Folding them together would put "the bank directory moved" and "the stopping rule changed"
into the same hash, and a fingerprint that flags a directory change is one that gets ignored
within a week.

WHAT IS GONE FROM HERE

Ports, base urls, request timeouts and CORS origins. Every one of them was a fact about
transport between seven processes, and there is no transport left to configure. A host that
serves this over HTTP configures its own.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from cat_engine.ingest.derive import DEFAULT_EDGE_FLOOR, DEFAULT_RELATION_THRESHOLD

__all__ = ["ModuleSettings"]


class ModuleSettings(BaseSettings):
    """Read from the environment, overridden by `CatConfig`, held per module instance.

    The environment variable names are unchanged from the service era, so a deployment
    already setting `INGEST_API_ENABLED=false` keeps the behaviour it had.
    """

    model_config = SettingsConfigDict(
        # Relative, so it resolves against the host's working directory. See the note in
        # `engine/config/settings.py` about why a module must not read a dotfile out of its
        # own installed directory.
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- surfaces that are off, or refused, unless a host means it -----------
    #: Posteriors, selection reasoning and raw session state. NOT candidate-safe. Refused
    #: rather than thinned when off, so "what is safe to return" is never a judgement call
    #: at a call site.
    author_diagnostics_enabled: bool = False
    #: The bank write path. It can replace the bank a live assessment is running against,
    #: and there is no authentication on it — the host owns that.
    admin_api_enabled: bool = True
    ingest_api_enabled: bool = True
    #: Carries candidate transcripts.
    live_debug_api_enabled: bool = False

    # --- where banks live ---------------------------------------------------
    #: Empty keeps the file store, which is the default and what the suite runs against.
    bank_store_dir: str = ""
    bank_database_url: str = ""

    # --- where live assessments live ----------------------------------------
    #: Empty keeps them in one process: a supported single-replica arrangement, and it means
    #: a restart loses every assessment mid-answer.
    #:
    #: Deliberately a SEPARATE url from `bank_database_url` even when both point at one
    #: server. A bank is content that is published and kept; a session is a record of what a
    #: person answered and has a deletion deadline. One url for both makes the deadline
    #: somebody's afterthought.
    session_database_url: str = ""
    #: False blanks the per-response detail as soon as an assessment ends and keeps the
    #: report. The report is what anybody reads afterwards; the responses are what make the
    #: row personal data.
    session_keep_responses: bool = False

    # --- ingest -------------------------------------------------------------
    max_upload_bytes: int = 32 * 1024 * 1024
    max_retained_uploads: int = 200
    #: Modelling decisions rather than derivations: the right values depend on how heavily a
    #: bank's items measure more than one competency. Read at INGEST time, so changing them
    #: does not re-derive an existing bank — it changes what the next upload produces.
    relation_threshold: float = DEFAULT_RELATION_THRESHOLD
    edge_floor: float = DEFAULT_EDGE_FLOOR
