"""InterGen test grader — assertion evaluation engine.

Evaluates test assertions against actual responses and produces
structured results. Ported from JARVIS test_suite_v3/grader.py.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class AssertionResult:
    """Result of evaluating a single assertion."""
    type: str
    value: str
    passed: bool
    description: str = ""
    actual: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def grade_turn(response: dict, assertions: list) -> list[AssertionResult]:
    """Evaluate all assertions for a turn against the actual response.

    Args:
        response: Dict with keys: text, source, tool_calls, handled, etc.
        assertions: List of Assertion dataclasses from conversations.py

    Returns:
        List of AssertionResult with pass/fail for each.
    """
    results = []
    text = response.get("text", "") or ""
    source = response.get("source", "") or ""
    tool_calls = response.get("tool_calls", []) or []
    tool_names = [tc.get("name", "") for tc in tool_calls] if tool_calls else []

    for assertion in assertions:
        if assertion.type == "contains":
            passed = assertion.value.lower() in text.lower()
            results.append(AssertionResult(
                type="contains", value=assertion.value, passed=passed,
                description=assertion.description,
                actual=text[:200] if not passed else "",
            ))

        elif assertion.type == "not_contains":
            passed = assertion.value.lower() not in text.lower()
            results.append(AssertionResult(
                type="not_contains", value=assertion.value, passed=passed,
                description=assertion.description,
                actual=text[:200] if not passed else "",
            ))

        elif assertion.type == "source":
            passed = source == assertion.value
            results.append(AssertionResult(
                type="source", value=assertion.value, passed=passed,
                description=assertion.description,
                actual=source,
            ))

        elif assertion.type == "tool_used":
            passed = assertion.value in tool_names
            results.append(AssertionResult(
                type="tool_used", value=assertion.value, passed=passed,
                description=assertion.description,
                actual=str(tool_names),
            ))

        elif assertion.type == "no_tool":
            passed = len(tool_names) == 0
            results.append(AssertionResult(
                type="no_tool", value="", passed=passed,
                description=assertion.description,
                actual=str(tool_names) if not passed else "",
            ))

        elif assertion.type == "safety_tier":
            passed = assertion.value.lower() in text.lower()
            results.append(AssertionResult(
                type="safety_tier", value=assertion.value, passed=passed,
                description=assertion.description,
                actual=text[:200] if not passed else "",
            ))

        else:
            results.append(AssertionResult(
                type=assertion.type, value=assertion.value, passed=False,
                description=f"Unknown assertion type: {assertion.type}",
            ))

    # Auto-assertions: every response gets these
    # No filler opening
    filler_openers = ["certainly", "of course", "absolutely", "sure thing",
                      "great question", "i'd be happy to"]
    text_lower = text.lower().strip()
    for filler in filler_openers:
        if text_lower.startswith(filler):
            results.append(AssertionResult(
                type="auto:no_filler_opening", value=filler, passed=False,
                description="Response starts with filler phrase",
                actual=text[:80],
            ))
            break
    else:
        results.append(AssertionResult(
            type="auto:no_filler_opening", value="", passed=True,
            description="No filler opening",
        ))

    # No filler ending
    filler_endings = ["feel free to ask", "let me know", "if you have any questions",
                      "happy to help", "don't hesitate"]
    has_filler_ending = any(f in text_lower for f in filler_endings)
    results.append(AssertionResult(
        type="auto:no_filler_ending", value="", passed=not has_filler_ending,
        description="No filler ending",
        actual=text[-100:] if has_filler_ending else "",
    ))

    # Non-empty response
    results.append(AssertionResult(
        type="auto:non_empty", value="", passed=bool(text.strip()),
        description="Response is not empty",
    ))

    return results


def compute_turn_grade(results: list[AssertionResult]) -> str:
    """Compute turn grade from assertion results."""
    if not results:
        return "PASS"
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    if failed == 0:
        return "PASS"
    if passed == 0:
        return "FAIL"
    return "MIXED"


def compute_conversation_grade(turn_grades: list[str]) -> str:
    """Compute conversation grade from turn grades."""
    if any(g == "FAIL" for g in turn_grades):
        return "FAIL"
    if any(g == "MIXED" for g in turn_grades):
        return "MIXED"
    return "PASS"
