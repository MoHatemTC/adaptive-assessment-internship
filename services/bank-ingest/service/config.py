"""Configuration for bank-ingest.

THE ONLY SERVICE THAT WRITES A BANK. `bank-registry` serves candidate-facing reads on the
hot path; ingest is an authoring concern with a different security posture and a different
availability requirement, and the current arrangement — both behind one `ADMIN_API_ENABLED`
flag in the service candidates read from — is why that flag has to exist at all.
"""

from __future__ import annotations

from adaptive_service import ServiceSettings

from .derive import DEFAULT_EDGE_FLOOR, DEFAULT_RELATION_THRESHOLD


class Settings(ServiceSettings):
    service_name: str = "bank-ingest"
    port: int = 8084

    #: The same store `bank-registry` reads. Registering a bank must not be a rebuild and
    #: must survive a restart, so it is a volume rather than an image layer.
    bank_store_dir: str = ""

    #: A read-only ingest service is a contradiction, but the switch exists so a deployment
    #: can stop accepting banks without stopping the service — the difference between "not
    #: taking uploads today" and "the authoring API is down".
    ingest_api_enabled: bool = True

    #: The largest bank checked in is 1.8 MB. The ceiling is here so that an accidental
    #: upload of something enormous fails at the door rather than in a parser.
    max_upload_bytes: int = 32 * 1024 * 1024
    #: Bounded, because the raw bytes of every upload are retained for reproducibility and
    #: an unbounded list of them is a memory leak with a nice name.
    max_retained_uploads: int = 200

    #: DERIVATION POLICY. A sub-to-sub relation at or above the threshold is PREREQUISITE,
    #: below it CONTRIBUTES_TO; below the floor, no edge at all. Settings rather than
    #: constants because the right values depend on how heavily a bank's items multi-measure,
    #: and that is a property of the content rather than of this code.
    relation_threshold: float = DEFAULT_RELATION_THRESHOLD
    edge_floor: float = DEFAULT_EDGE_FLOOR

    cors_allow_origins: str = "*"

    def allowed_origins(self) -> list[str]:
        raw = (self.cors_allow_origins or "").strip()
        return ["*"] if raw in ("", "*") else [o.strip() for o in raw.split(",") if o.strip()]

    #: Postgres, when this deployment uses it. EMPTY KEEPS THE FILE STORE, which is the
    #: default and the tested-by-default path — `docs/architecture-proposal.md` §2.14 calls
    #: this phase 6. Set it and the seeds are loaded on boot and every read comes from the
    #: database instead of two directories.
    bank_database_url: str = ""


settings = Settings()
