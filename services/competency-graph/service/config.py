"""Configuration for competency-graph.

No store, no credentials, no egress beyond the bank registry. That is not an accident: the
service holds no session state (see `main.py`), and the only thing it reads is a graph,
which is immutable for a bank version.
"""

from __future__ import annotations

from adaptive_service import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "competency-graph"
    port: int = 8083

    #: Graphs are bank data, and `bank-registry` owns them. Fetching rather than reading a
    #: local copy is what stops this service and the registry disagreeing about which
    #: edges a bank has — the failure that produces a permanent, silent convergence veto.
    bank_registry_url: str = "http://bank-registry:8081"
    bank_timeout_seconds: float = 10.0


settings = Settings()
