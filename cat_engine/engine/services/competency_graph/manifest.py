"""What propagation configuration a session actually ran under.

INV-P8 asks that every propagation factor appear with its runtime value. That was not
merely unimplemented — there was nowhere for it to live. `ResolvedPolicy.summary()`
records the permission lattice and none of the numeric factors; `PropagationConfig` held
the numbers and had no serialiser; the startup log line carried two booleans and an edge
count. A sweep over nine factors whose cells cannot say which nine values they used is a
sweep that cannot be analysed.

WHY THE HASH, AND WHY DRIFT IS RECORDED

`propagation_config_from_settings()` is called once per response, not once per session.
A setting moved mid-session — by an operator, by a test, by a sweep harness that failed
to isolate a cell — changes behaviour from that response onward while the manifest
written at `begin` still describes the old configuration. The manifest would then be
false in the one way that matters: silently, and only for the sessions where it mattered.

Recomputing the hash per response and recording every change makes that observable. An
empty `propagation_manifest_drift` is the assertion; the drift entries are the diagnosis.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from functools import lru_cache

from .config import PropagationConfig, graph_enabled, shadow_mode_enabled
from .policy import ResolvedPolicy


@lru_cache(maxsize=1)
def code_revision() -> str:
    """The commit the engine is running, or "" when that cannot be determined.

    Best-effort by design. A missing git directory is normal in a container and must not
    stop a session; an unrecorded revision costs traceability, an exception costs the run.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def propagation_manifest(
    config: PropagationConfig,
    resolved: ResolvedPolicy | None = None,
    *,
    bank_id: str = "",
) -> dict:
    """Every factor in force, plus the permission lattice that gates them.

    Both halves are needed and neither substitutes for the other: the factors say what the
    algorithm was configured to do, the lattice says which edges it was allowed to do it
    with. A cell that inferred nothing because `K=3` and a cell that inferred nothing
    because every edge was inert are the same number and different findings.
    """
    return {
        "bank_id": bank_id,
        "code_revision": code_revision(),
        "master_switch": graph_enabled(),
        "shadow_mode": shadow_mode_enabled(),
        "factors": config.as_manifest(),
        "policy": resolved.summary() if resolved is not None else None,
    }


def manifest_hash(manifest: dict) -> str:
    """Stable digest of a manifest. Equal hashes mean equal configuration.

    Sorted keys and no whitespace, so the digest depends on the values and not on how the
    dict happened to be built. The code revision is excluded — it cannot change within a
    process, so including it would only make hashes incomparable across deploys without
    detecting anything a within-session comparison can act on.
    """
    comparable = {k: v for k, v in manifest.items() if k != "code_revision"}
    encoded = json.dumps(comparable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
