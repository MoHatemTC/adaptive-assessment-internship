"""What every service in this repository does the same way.

Not business logic — the operator surface. `/health` and `/config` are what somebody reads
at 3am, and five hand-written copies of them is five chances for the one that matters to be
the one that was wrong.
"""

from .errors import install_error_handlers
from .operator import ServiceSettings, operator_router, redact

__all__ = [
    "ServiceSettings",
    "install_error_handlers",
    "operator_router",
    "redact",
]
