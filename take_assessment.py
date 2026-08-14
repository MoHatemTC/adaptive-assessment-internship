"""Take the AI assessment from the terminal — and watch the engine work.

This script plays the HOST BACKEND: it owns the questions (loaded from the packaged AIE
bank), owns persistence (one JSON file on disk), and talks to the engine only through
the public runtime — compile_assessment / start_assessment / advance_assessment.

It is also a MONITORING TOOL: after every answer it diffs the stored state against the
returned one and prints what happened in the background — how the response was graded,
which posteriors moved and by how much, what the competency graph concluded, whether a
competency finalised, and why the next question was chosen. A real candidate must never
see this panel (it reveals correctness mid-assessment); it exists to observe the engine.

To prove the engine is stateless, the state is written to disk after every answer and
read back from disk before the next one — exactly what a real backend would do between
two HTTP requests. Ctrl+C mid-run, then rerun, and the assessment resumes.

    uv run python take_assessment.py                # take it (resumes if interrupted)
    uv run python take_assessment.py --targets C1   # assess one competency only
    uv run python take_assessment.py --reset        # discard a half-finished run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from adaptive_engine import (
    AdaptiveState,
    AssessmentDefinition,
    InitialCompetency,
    InvalidAnswer,
    QuestionResponse,
    advance_assessment,
    compile_assessment,
    start_assessment,
)

REPO = Path(__file__).resolve().parent
DATA = REPO / "cat_engine" / "engine" / "data"
STATE_FILE = REPO / ".assessment_state.json"  # the "backend database"
RULE = "─" * 62


def load_definition(targets: list[str] | None) -> AssessmentDefinition:
    """The backend's copy of the assessment content: the AIE bank's mcq items + graph.

    MCQ only, because this terminal has no code sandbox and no microphone — the runtime
    would happily take host-graded code/voice evidence, but a demo should not grade
    itself.
    """
    raw = json.loads((DATA / "question_bank_AIE.json").read_text(encoding="utf-8"))
    entries = raw["items"] if isinstance(raw, dict) else raw
    items = [entry for entry in entries if entry.get("modality") == "mcq"]
    graph = json.loads((DATA / "competency_graph_AIE.json").read_text(encoding="utf-8"))
    return AssessmentDefinition(
        assessment_id="AIE",
        version="v1",
        items=items,
        graph=graph,
        targets=targets,
    )


def main_titles(definition: AssessmentDefinition) -> dict[str, str]:
    return {
        node.competency_id: node.title or node.competency_id
        for node in definition.graph.nodes
        if node.node_type == "main"
    }


def ask_intake(targets, titles) -> dict[str, InitialCompetency]:
    print("\nBefore we start: rate yourself 1-5 per competency (Enter to skip).")
    intake: dict[str, InitialCompetency] = {}
    for target in targets:
        raw = input(f"  {target} — {titles.get(target, target)} [1-5]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= 5:
            intake[target] = InitialCompetency(level=int(raw), confidence=0.8)
    return intake


def ask_option(question) -> int:
    """One option index from the candidate, revalidated until it parses."""
    item = question.item
    print(f"\n[{question.variable}] {item.stem}\n")
    for index, option in enumerate(item.options):
        print(f"  {index}) {option}")
    while True:
        raw = input("\nYour answer (number): ").strip()
        if raw.isdigit() and 0 <= int(raw) < len(item.options):
            return int(raw)
        print(f"Please enter a number between 0 and {len(item.options) - 1}.")


def present_current(compiled, state: AdaptiveState):
    """Re-project the question a stored session is waiting on."""
    from cat_engine import projection

    item, candidate = compiled.orchestrator.next_item(state.engine_state)
    return projection.presenting(item, candidate)


# --- the monitoring panel ---------------------------------------------------


def show_priors(decision) -> None:
    print(f"\n{RULE}\nENGINE — priors seeded")
    for variable, vs in sorted(decision.state.engine_state.variables.items()):
        print(f"  {variable}: theta {vs.theta_hat:+.2f}  SE {vs.standard_error:.2f}")
    _show_pick(decision.state.engine_state)
    print(RULE)


def show_background(before, decision, item_id: str, answer: int, compiled) -> None:
    """Everything the engine just did, read off the state diff."""
    after = decision.state.engine_state
    print(f"\n{RULE}\nENGINE — behind the scenes")

    item = compiled.bank.get(item_id)
    key = item.payload["answer_index"]
    verdict = "correct" if answer == key else f"wrong (key was {key})"
    measured = ", ".join(f"{m.variable} w={m.weight:g}" for m in item.measures)
    print(f"  graded : option {answer} — {verdict}  |  evidences {measured}")

    for variable in sorted(after.variables):
        b, a = before.variables[variable], after.variables[variable]
        if a.posterior != b.posterior:
            print(
                f"  update : {variable}  theta {b.theta_hat:+.2f} → {a.theta_hat:+.2f}"
                f"  SE {b.standard_error:.2f} → {a.standard_error:.2f}"
                f"  observations {a.observations}"
            )
        if a.finalised and not b.finalised:
            state_word = "CONVERGED" if a.converged else "stopped"
            print(f"  final  : {variable} {state_word} — {a.stop_reason}")

    _show_graph_diff(before, after)

    if len(after.aberrant_responses) > len(before.aberrant_responses):
        print("  fit    : response flagged aberrant (posterior could not explain it)")

    if decision.status == "question":
        _show_pick(after)
    else:
        print(f"  stop   : session over — {decision.stop_reason}")
    answered = after.items_administered
    open_now = ", ".join(after.open_variables) or "none"
    print(f"  status : {answered} answered  |  still open: {open_now}")
    print(RULE)


def _show_graph_diff(before, after) -> None:
    changes = []
    for label, field in (
        ("measured", "graph_direct_measured_nodes"),
        ("mastered", "graph_direct_mastered_nodes"),
        ("not mastered", "graph_direct_not_mastered_nodes"),
        ("inferred mastered", "graph_inferred_mastered_nodes"),
        ("blocked", "graph_blocked_nodes"),
        ("contradicted", "graph_contradicted_nodes"),
    ):
        added = set(getattr(after, field)) - set(getattr(before, field))
        removed = set(getattr(before, field)) - set(getattr(after, field))
        if added:
            changes.append(f"{label} +{', +'.join(sorted(added))}")
        if removed:
            changes.append(f"{label} -{', -'.join(sorted(removed))}")
    if changes:
        print(f"  graph  : {'  |  '.join(changes)}")


def _show_pick(engine_state) -> None:
    pick = engine_state.presenting
    if pick is None:
        return
    line = (
        f"  pick   : {pick.item_id} for {pick.variable} via {pick.criterion}"
        f"  info {pick.information:.2f}"
    )
    if pick.reason:
        line += f"  — {pick.reason}"
    print(line)
    if len(pick.shortlist_ids) > 1:
        print(f"  ranked : shortlist was {', '.join(pick.shortlist_ids)}")


def show_report(report) -> None:
    print("\n" + "=" * 62)
    print(f"ASSESSMENT COMPLETE — {report.items_administered} questions ({report.stop_reason})")
    print("=" * 62)
    for row in report.variables:
        if row.decision_status == "not_assessed":
            print(f"  {row.variable}: not assessed")
            continue
        low, high = row.credible_interval_95
        print(
            f"  {row.variable}: level {row.level} ({row.band})  "
            f"theta {row.theta_hat:+.2f} in [{low:+.1f}, {high:+.1f}]  "
            f"P(band) {row.p_reported_band:.0%}  "
            f"{row.observations} questions  "
            f"[{'converged' if row.converged else row.stop_reason}]"
        )
        if row.graph_unmeasured_nodes:
            print(f"      not directly checked: {', '.join(row.graph_unmeasured_nodes)}")
    print("=" * 62)


# --- the backend loop -------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", nargs="*", help="main competencies to assess (default: all)")
    parser.add_argument("--reset", action="store_true", help="discard a half-finished run")
    args = parser.parse_args()

    if args.reset:
        STATE_FILE.unlink(missing_ok=True)
        print("Saved session discarded.")

    definition = load_definition(args.targets)
    compiled = compile_assessment(definition)
    titles = main_titles(definition)

    if STATE_FILE.exists():
        # The backend found a stored session: resume it. Nothing but this file and the
        # definition is needed — the engine remembers nothing.
        state = AdaptiveState.model_validate(json.loads(STATE_FILE.read_text()))
        print(f"Resuming: {state.questions_answered} questions already answered.")
        question = present_current(compiled, state)
    else:
        print(f"AI assessment — competencies: {', '.join(compiled.targets)}")
        print("(Ctrl+C anytime; rerun to resume.)")
        decision = start_assessment(
            compiled, initial_competencies=ask_intake(compiled.targets, titles)
        )
        STATE_FILE.write_text(json.dumps(decision.state.model_dump(mode="json")))
        show_priors(decision)
        question = decision.question

    while True:
        answer = ask_option(question)
        stored = AdaptiveState.model_validate(json.loads(STATE_FILE.read_text()))
        try:
            decision = advance_assessment(
                compiled,
                state=stored,
                response=QuestionResponse(question_id=question.item.item_id, answer=answer),
            )
        except InvalidAnswer as error:
            print(f"Rejected: {error.message}")
            continue
        STATE_FILE.write_text(json.dumps(decision.state.model_dump(mode="json")))
        show_background(stored.engine_state, decision, question.item.item_id, answer, compiled)
        if decision.status != "question":
            break
        question = decision.question

    show_report(decision.report)
    STATE_FILE.unlink(missing_ok=True)  # the backend closes the finished session


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nPaused. Your progress is saved — rerun to resume.")
        sys.exit(0)
