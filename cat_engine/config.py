"""What a host configures, and the one thing it cannot configure twice.

THREE LAYERS, IN THIS ORDER

    CatConfig(...)   what the host passes explicitly — wins
    the environment  LITELLM_API_KEY and friends, exactly as before
    the defaults     declared on the engine's `Settings` and on `ModuleSettings`

A host that passes nothing gets the behaviour the environment used to give, which is what
makes this drop-in for a deployment already running the services.

A FLAT SURFACE OVER THREE TARGETS

A host should not have to know that `cat_se_target` is engine policy, `bank_store_dir` is
module topology and `allow_text_fallback` is a voice setting. So `CatConfig` is flat and
`apply()` routes each field to whichever object owns it. The three-way split is real and
load-bearing internally; it is not the host's problem.

WHY THERE IS A FINGERPRINT GUARD

The engine reads its policy from a module-level singleton — `settings`, imported by name at
a hundred call sites and constructed once at import. That was unremarkable when this tree
was one deployment, and it is why a `CatConfig` is APPLIED onto that singleton rather than
threaded through the engine: threading it would touch every one of those call sites for a
capability nothing has asked for.

The cost is real and is stated rather than hidden: **one measurement policy per process.**
Two `AssessmentModule`s cannot disagree about what a candidate is scored by.

The failure that would cause is the one `engine_config_fingerprint` was written for. A
grader running `CODE_APPROACH=C` beside an orchestrator that believes it is `B` produces a
session whose scores were computed one way and whose stopping rule assumed another:
internally consistent, entirely wrong, and no request failing. That was a risk across seven
services; inside one process, letting the second module quietly win would make it a
certainty. So a second module whose MEASUREMENT settings differ raises `ConfigConflict`.

Module topology — store locations, which surfaces are open, upload ceilings — is NOT in the
fingerprint and is per-instance. Those change where things are read from, not what a number
means.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import BaseModel, ConfigDict

from cat_engine.engine.config.fingerprint import (
    MEASUREMENT_SETTINGS,
    engine_config_fingerprint,
    measurement_settings,
)
from cat_engine.engine.config.settings import Settings
from cat_engine.engine.config.settings import settings as engine_settings
from cat_engine.engine.config.voice_settings import voice_settings
from cat_engine.errors import ConfigConflict
from cat_engine.settings import ModuleSettings

logger = logging.getLogger(__name__)

__all__ = ["CatConfig", "applied_fingerprint"]

#: Set by the first module built in this process, so the second can be compared against it.
_APPLIED: dict[str, Any] | None = None


def applied_fingerprint() -> str | None:
    """The measurement fingerprint in force, or None before any module is built."""
    return None if _APPLIED is None else engine_config_fingerprint()


class CatConfig(BaseModel):
    """Everything a host may set, with the existing defaults underneath.

    Every field is optional. An unset field is not "empty" — it means *do not override*,
    which is what lets a host set three things and leave the rest to the environment. That
    distinction is why this is a plain model with `None` defaults rather than a
    `BaseSettings` subclass: a settings class would eagerly fill every field from the
    environment and there would be no way left to tell "unset" from "set to the default".
    """

    model_config = ConfigDict(extra="forbid")

    # --- model access and sandbox (engine) ----------------------------------
    litellm_base_url: str | None = None
    litellm_api_key: str | None = None
    litellm_model: str | None = None
    litellm_live_preview_model: str | None = None
    litellm_transcribe_model: str | None = None
    e2b_api_key: str | None = None

    # --- which bank (engine) ------------------------------------------------
    active_bank: str | None = None

    # --- measurement policy (engine; IN THE FINGERPRINT) --------------------
    cat_se_target: float | None = None
    cat_max_questions: int | None = None
    cat_min_questions: int | None = None
    code_approach: str | None = None
    code_rubric: str | None = None
    competency_graph_enabled: bool | None = None
    graph_upward_inference_enabled: bool | None = None
    graph_descendant_blocking_enabled: bool | None = None
    graph_convergence_gate_enabled: bool | None = None
    graph_coverage_critical_only: bool | None = None
    orchestrator_max_items: int | None = None
    orchestrator_time_limit_minutes: int | None = None

    # --- voice (engine voice settings) --------------------------------------
    #: Typed answers to spoken items. Keeps remote and accessibility testing possible when
    #: realtime audio is unavailable; it is not the measurement the item was authored for.
    allow_text_fallback: bool | None = None

    # --- where data lives (module, plus one environment variable) -----------
    #: Set in the ENVIRONMENT rather than on any settings object, because
    #: `engine/config/paths.py` reads it directly and has to resolve before anything that
    #: opens a file — including the bank store, which is built at import.
    engine_data_dir: str | None = None
    bank_store_dir: str | None = None
    bank_database_url: str | None = None
    session_database_url: str | None = None
    session_keep_responses: bool | None = None

    # --- surfaces (module) --------------------------------------------------
    author_diagnostics_enabled: bool | None = None
    admin_api_enabled: bool | None = None
    ingest_api_enabled: bool | None = None
    live_debug_api_enabled: bool | None = None

    # --- ingest tuning (module) ---------------------------------------------
    max_upload_bytes: int | None = None
    max_retained_uploads: int | None = None
    relation_threshold: float | None = None
    edge_floor: float | None = None

    def overrides(self) -> dict[str, Any]:
        """The fields the host actually set. `None` means "leave it alone"."""
        return {k: v for k, v in self.model_dump().items() if v is not None}

    def apply(self) -> ModuleSettings:
        """Route these to whichever object owns each, and return the module's own settings.

        The measurement guard runs BEFORE anything is mutated, so a rejected second module
        leaves the first one's configuration exactly as it was. Applying and rolling back
        would leave a window in which a concurrent request read the wrong policy.
        """
        global _APPLIED

        overrides = self.overrides()

        data_dir = overrides.pop("engine_data_dir", None)
        if data_dir is not None:
            os.environ["ENGINE_DATA_DIR"] = str(data_dir)

        module_fields = set(ModuleSettings.model_fields)
        voice_fields = set(type(voice_settings).model_fields)
        engine_fields = set(type(engine_settings).model_fields)

        for_module = {k: v for k, v in overrides.items() if k in module_fields}
        rest = {k: v for k, v in overrides.items() if k not in module_fields}
        for_voice = {k: v for k, v in rest.items() if k in voice_fields}
        for_engine = {k: v for k, v in rest.items() if k in engine_fields}

        unknown = set(rest) - for_voice.keys() - for_engine.keys()
        if unknown:
            # A field that reaches no target would be silently ignored, and the host would
            # run the default believing it had changed something.
            raise ConfigConflict(
                f"these settings belong to nothing this module configures: {sorted(unknown)}"
            )

        candidate = _would_be(for_engine)
        if _APPLIED is not None and candidate != _APPLIED:
            differing = {
                k: {"in force": _APPLIED[k], "requested": candidate[k]}
                for k in candidate
                if _APPLIED[k] != candidate[k]
            }
            raise ConfigConflict(
                "a module with different measurement policy already exists in this "
                "process, and engine settings are process-wide — so building this one "
                f"would silently rescore the first. Differing: {differing}"
            )

        for name, value in for_engine.items():
            setattr(engine_settings, name, value)
        for name, value in for_voice.items():
            setattr(voice_settings, name, value)

        _APPLIED = measurement_settings()
        logger.info(
            "engine configured: fingerprint %s, bank %s",
            engine_config_fingerprint(),
            engine_settings.active_bank,
        )
        return ModuleSettings(**for_module)


def _would_be(overrides: dict[str, Any]) -> dict[str, Any]:
    """The measurement settings that WOULD be in force if these overrides were applied."""
    current = measurement_settings()
    return {
        name: overrides.get(name, current.get(name)) for name in MEASUREMENT_SETTINGS
    }


def _reset_for_tests() -> None:
    """Forget which policy was applied, and rebuild `Settings` from the environment.

    For tests only, and named so. Without it, every test building a module with a different
    `cat_max_questions` would be the "second module" and would raise.
    """
    global _APPLIED
    _APPLIED = None
    fresh = Settings()
    for name in type(fresh).model_fields:
        setattr(engine_settings, name, getattr(fresh, name))
