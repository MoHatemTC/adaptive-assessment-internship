"""The question bank, behind a seam.

`QuestionRepository` is the only thing the engine knows about where questions come from.
The JSON implementation ships so the engine runs standalone; a database implementation
replaces it without touching selection, scoring or the session.

Questions are validated on load, not on use. A malformed question discovered mid-session
is a candidate sitting in front of a broken assessment, so the bank is parsed through the
`Question` schema once at startup and a bad entry is refused there — loudly, where an
operator can see it.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.schemas.code_adaptive import Question

logger = logging.getLogger(__name__)

BANK_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "code_bank.json"


class QuestionRepository(Protocol):
    """Where questions come from. Implement this to back the engine with a database."""

    def all_questions(self) -> list[Question]:
        """Every question, including inactive ones — filtering is selection's job."""
        ...

    def get(self, question_id: str) -> Question | None:
        ...

    def competencies(self) -> list[str]:
        """Every competency any question assesses, sorted."""
        ...


class JsonQuestionRepository:
    """Reads the bundled JSON bank. Parsed once and cached for the process lifetime."""

    def __init__(self, path: Path | str = BANK_PATH) -> None:
        self._path = Path(path)

    @lru_cache(maxsize=1)  # noqa: B019 — one repository per path, bounded by construction
    def _load(self) -> tuple[Question, ...]:
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        entries = raw["questions"] if isinstance(raw, dict) else raw

        questions: list[Question] = []
        rejected: list[str] = []
        for entry in entries:
            try:
                questions.append(Question.model_validate(entry))
            except ValidationError as exc:
                # Named and skipped rather than raised: one malformed question should not
                # deny every candidate an assessment, but it must not pass silently either.
                identifier = entry.get("question_id", "<no id>") if isinstance(entry, dict) else "<not an object>"
                rejected.append(identifier)
                logger.error("question %s failed validation and was dropped: %s", identifier, exc)

        if rejected:
            logger.error("%d of %d questions rejected: %s", len(rejected), len(entries), rejected)
        if not questions:
            raise ValueError(f"no valid questions in {self._path}")
        return tuple(questions)

    def all_questions(self) -> list[Question]:
        return list(self._load())

    def get(self, question_id: str) -> Question | None:
        return next((q for q in self._load() if q.question_id == question_id), None)

    def competencies(self) -> list[str]:
        return sorted({c.competency_id for q in self._load() for c in q.competencies})

    def coverage(self) -> dict[str, int]:
        """How many active questions assess each competency.

        The number that decides whether a competency can be adaptively assessed at all: a
        competency with two questions offers no choice, so the test cannot adapt over it
        however good the selection criterion is.
        """
        counts: dict[str, int] = {}
        for question in self._load():
            if question.status != "active":
                continue
            for entry in question.competencies:
                if entry.weight > 0:
                    counts[entry.competency_id] = counts.get(entry.competency_id, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
