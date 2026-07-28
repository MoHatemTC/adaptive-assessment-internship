"""Structured logging for adaptive engine monitoring."""

from __future__ import annotations

import logging
from pathlib import Path

LOG_PATH = Path(__file__).parent / "assessment.log"

_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger("masaar_cat")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    _logger = logger
    return logger


def log_selection(
    comp: str,
    q_count: int,
    item: dict,
    fisher_i: float,
    theta_hat: float,
    *,
    mode: str = "ENGINE",
    criterion: str = "",
) -> None:
    get_logger().info(
        "SELECT | %s | q=%d | id=%s | diff=%s | Fisher_I=%.4f | theta_hat=%.3f | mode=%s | criterion=%s",
        comp,
        q_count,
        item["id"],
        item.get("difficulty", "?"),
        fisher_i,
        theta_hat,
        mode,
        criterion or ("KL" if q_count < 3 else "Fisher"),
    )


def log_update(
    comp: str,
    q_count: int,
    item_id: str,
    correct: bool,
    theta_hat: float,
    se: float,
    certainty_pct: float,
    converged: bool,
) -> None:
    get_logger().info(
        "UPDATE | %s | q=%d | id=%s | correct=%s | theta_hat=%.3f | SE=%.3f | "
        "certainty=%.1f%% | converged=%s",
        comp,
        q_count,
        item_id,
        correct,
        theta_hat,
        se,
        certainty_pct,
        converged,
    )


def log_synthesis(lines: list[str]) -> None:
    logger = get_logger()
    logger.info("SYNTH | bank expansion started (%d items)", len(lines))
    for line in lines:
        logger.info("SYNTH | %s", line)


def log_llm_selection(comp: str, q_count: int, selected_id: str, criterion: str, rule: str) -> None:
    get_logger().info(
        "LLM_SELECT | %s | q=%d | id=%s | criterion=%s | rule=%s",
        comp,
        q_count,
        selected_id,
        criterion,
        rule,
    )


def log_session_start(competencies: list[str], pool_sizes: dict[str, int]) -> None:
    get_logger().info(
        "START | competencies=%s | pool_sizes=%s",
        competencies,
        pool_sizes,
    )
