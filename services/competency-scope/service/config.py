"""Configuration for competency-scope.

No store, no credentials, no model. The only thing it reads is a bank's graph and its item
parameters, both of which `bank-registry` owns and both of which are immutable for a bank
version — so there is nothing here to keep in sync and nothing to lose on restart.
"""

from __future__ import annotations

from adaptive_service import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "competency-scope"
    port: int = 8085

    #: The graph and the item parameters come from the registry rather than from a local
    #: copy, for the same reason `competency-graph` fetches its graph: two services
    #: disagreeing about which nodes a bank has produces a scope whose coverage requirement
    #: names something the gate will never see measured.
    bank_registry_url: str = "http://bank-registry:8081"
    bank_timeout_seconds: float = 10.0


settings = Settings()
