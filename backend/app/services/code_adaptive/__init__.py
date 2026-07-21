"""Code-question adaptive assessment engine.

The public surface is `CodeAdaptiveSession` and a `QuestionRepository`. Everything else is
implementation detail and may be reorganised without notice.
"""

from app.services.code_adaptive.bank import JsonQuestionRepository, QuestionRepository
from app.services.code_adaptive.session import CodeAdaptiveSession, active_profile

__all__ = [
    "CodeAdaptiveSession",
    "JsonQuestionRepository",
    "QuestionRepository",
    "active_profile",
]
