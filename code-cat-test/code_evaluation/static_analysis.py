"""Structural signals read from the submission's syntax tree.

Deterministic, offline, and cheap: parsing is not execution, so this runs in-process on
untrusted source safely — `ast.parse` builds a tree, it never evaluates.

These signals are the third evidence source alongside tests and the model. They matter
most exactly where tests are weakest: a submission can pass every test with a quadratic
solution to a linear problem, or by hard-coding the visible cases, and only structure
reveals it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass
class StaticSignals:
    """What the source structure shows. Absent signals are False, never None."""

    syntax_valid: bool
    uses_loop: bool = False
    uses_recursion: bool = False
    uses_comprehension: bool = False
    nested_loop_depth: int = 0
    hard_coded_output_suspected: bool = False
    mutates_argument: bool = False
    has_boundary_guard: bool = False
    cyclomatic_complexity: int = 1
    function_count: int = 0
    max_function_length: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "syntax_valid": self.syntax_valid,
            "uses_loop": self.uses_loop,
            "uses_recursion": self.uses_recursion,
            "uses_comprehension": self.uses_comprehension,
            "nested_loop_depth": self.nested_loop_depth,
            "hard_coded_output_suspected": self.hard_coded_output_suspected,
            "mutates_argument": self.mutates_argument,
            "has_boundary_guard": self.has_boundary_guard,
            "cyclomatic_complexity": self.cyclomatic_complexity,
            "function_count": self.function_count,
            "max_function_length": self.max_function_length,
            "warnings": self.warnings,
        }


class _Visitor(ast.NodeVisitor):
    def __init__(self, function_name: str) -> None:
        self.function_name = function_name
        self.signals = StaticSignals(syntax_valid=True)
        self._loop_depth = 0
        self._returns_in_target: list[ast.Return] = []
        self._target_args: set[str] = set()
        self._branch_count = 0

    # --- control flow -------------------------------------------------------
    def visit_For(self, node: ast.For) -> None:
        self._enter_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self._enter_loop(node)

    def _enter_loop(self, node: ast.AST) -> None:
        self.signals.uses_loop = True
        self._loop_depth += 1
        self.signals.nested_loop_depth = max(self.signals.nested_loop_depth, self._loop_depth)
        self._branch_count += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_If(self, node: ast.If) -> None:
        self._branch_count += 1
        # A guard on emptiness or length is the structural trace of handling the boundary
        # case. Its absence is the single most common defect in these submissions, and it
        # is visible here even when no test happens to exercise it.
        if _looks_like_boundary_guard(node.test):
            self.signals.has_boundary_guard = True
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self._branch_count += max(len(node.values) - 1, 0)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.signals.uses_comprehension = True
        self.generic_visit(node)

    # --- functions ----------------------------------------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.signals.function_count += 1
        length = (node.end_lineno or node.lineno) - node.lineno + 1
        self.signals.max_function_length = max(self.signals.max_function_length, length)

        if node.name == self.function_name:
            self._target_args = {a.arg for a in node.args.args}
            self._returns_in_target = [
                n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value is not None
            ]
            if any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == self.function_name
                for n in ast.walk(node)
            ):
                self.signals.uses_recursion = True

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Mutating a caller's argument is a contract violation that tests comparing only
        # return values will never catch.
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "append", "extend", "insert", "pop", "remove", "sort", "clear", "update"
        }:
            target = node.func.value
            if isinstance(target, ast.Name) and target.id in self._target_args:
                self.signals.mutates_argument = True
        self.generic_visit(node)


def _looks_like_boundary_guard(test: ast.expr) -> bool:
    """True when a condition tests emptiness, length, or a zero/None boundary."""
    for node in ast.walk(test):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len":
            return True
        if isinstance(node, ast.Constant) and node.value in (0, None, "", [], ()):
            return True
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return True
    return False


def analyse(code: str, function_name: str) -> StaticSignals:
    """Extract structural signals. Never raises: unparseable source is itself a signal."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return StaticSignals(
            syntax_valid=False, warnings=[f"SYNTAX_ERROR: line {exc.lineno}"]
        )

    visitor = _Visitor(function_name)
    visitor.visit(tree)
    signals = visitor.signals
    signals.cyclomatic_complexity = visitor._branch_count + 1

    # Hard-coded output: the target function returns only literals and never computes
    # from its arguments. Caught here because it can pass every visible test while
    # demonstrating no competency at all.
    returns = visitor._returns_in_target
    if returns and visitor._target_args:
        all_literal = all(
            isinstance(r.value, (ast.Constant, ast.List, ast.Tuple, ast.Dict))
            and not any(isinstance(n, ast.Name) and n.id in visitor._target_args
                        for n in ast.walk(r.value))
            for r in returns
        )
        if all_literal:
            signals.hard_coded_output_suspected = True
            signals.warnings.append("HARDCODED_OUTPUT: returns literals, ignores arguments")

    if not signals.has_boundary_guard and signals.uses_loop:
        signals.warnings.append("MISSING_EMPTY_INPUT_GUARD: no length or emptiness check")
    if signals.nested_loop_depth >= 2:
        signals.warnings.append(
            f"NESTED_LOOPS: depth {signals.nested_loop_depth} suggests super-linear cost"
        )
    if signals.mutates_argument:
        signals.warnings.append("MUTATES_INPUT: modifies a caller-owned argument")

    return signals
