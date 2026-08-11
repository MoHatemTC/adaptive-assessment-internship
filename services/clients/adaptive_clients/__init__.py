"""HTTP adapters that satisfy the engine's own seams.

The engine already had the boundaries this needs: `UnifiedBankRepository` is a Protocol,
`GraderAgent` has one method, propagation goes through a single call. So none of these is a
new abstraction over the engine — each is a second implementation of a seam that was
already there, which is why the orchestrator can be pointed at services without one line of
the loop changing.

Every client accepts a `transport`, so a test can wire it straight to the target service's
ASGI app. That is how the parity suite drives an entire assessment through every client
with no socket open anywhere — the only way to compare an in-process run against a service
run without making the comparison itself flaky.
"""

from .bank import BankRegistryClient
from .graph import CompetencyGraphClient
from .scope import CompetencyScopeClient
from .transport import (
    DEFAULT_TIMEOUT,
    BaseClient,
    ClientError,
    ServiceRefused,
    ServiceUnavailable,
)

__all__ = [
    "DEFAULT_TIMEOUT",
    "BankRegistryClient",
    "BaseClient",
    "CompetencyGraphClient",
    "CompetencyScopeClient",
    "ClientError",
    "ServiceRefused",
    "ServiceUnavailable",
]
