"""competency-scope, over HTTP.

Engine-free, like every module here except `engine.py`: a scope is DTOs in and DTOs out, so
nothing in this file needs numpy or a question bank.
"""

from __future__ import annotations

from cat_engine.contracts import ScopeManifest, ScopeRequest, ScopeSelection

from .transport import BaseClient


class CompetencyScopeClient(BaseClient):
    """Build a scope for a bank.

    One method, because there is one question. `GET /scopes/{id}` deliberately does not
    exist: `scope_id` is a hash of the inputs and a hash cannot be inverted, so a service
    that answered it would need a cache — which would make a deterministic, horizontally
    scalable service stateful and bound to one replica for no gain. The caller that needs a
    manifest builds it, and gets the same one every time.
    """

    def scope(self, bank_id: str, selection: ScopeSelection) -> ScopeManifest:
        request = ScopeRequest(bank_id=bank_id, **selection.model_dump())
        return ScopeManifest.model_validate(
            self.post("/scopes", json=request.model_dump()).json()
        )
