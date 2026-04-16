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

    # No capability denial — InterGen has full system access
    denial_phrases = [
        "i cannot execute commands",
        "i cannot perform system operations",
        "i don't have access to your system",
        "i don't have access to your files",
        "i don't have access to your machine",
        "i do not have access to your system",
        "i do not have access to your files",
        "i do not have access to your machine",
        "i cannot directly access",
        "i cannot access your system",
        "i cannot access your log",
        "contact your system administrator",
        "i can only assist with information",
        "not to interact with the operating system",
    ]
    for phrase in denial_phrases:
        if phrase in text_lower:
            results.append(AssertionResult(
                type="auto:no_capability_denial", value=phrase, passed=False,
                description="InterGen falsely denied its own capabilities",
                actual=text[:200],
            ))
            break
    else:
        results.append(AssertionResult(
            type="auto:no_capability_denial", value="", passed=True,
            description="No capability denial",
        ))

    # No narration without action — "I will check" with no data is unhelpful
    narration_phrases = [
        "i will check", "i need to check", "i need to diagnose",
        "i must check", "let me check", "i will start by",
    ]
    has_narration = any(p in text_lower for p in narration_phrases)
    digit_count = sum(1 for c in text if c.isdigit())
    newline_count = text.count("\n")
    has_data = (digit_count >= 3) or (newline_count >= 2) or (len(text) > 300 and digit_count >= 1)
    if has_narration and not has_data:
        results.append(AssertionResult(
            type="auto:no_empty_narration", value="", passed=False,
            description="Response narrates intent without providing results",
            actual=text[:200],
        ))
    else:
        results.append(AssertionResult(
            type="auto:no_empty_narration", value="", passed=True,
            description="No empty narration",
        ))

    # Output readability — substantial output must have formatting
    if len(text) > 200:
        has_newlines = "\n" in text
        results.append(AssertionResult(
            type="auto:output_readable", value="", passed=has_newlines,
            description="Multi-line output preserves formatting",
            actual=text[:120] if not has_newlines else "",
        ))
    else:
        results.append(AssertionResult(
            type="auto:output_readable", value="", passed=True,
            description="Output readability (N/A or OK)",
        ))

    # Helpfulness — LLM responses should not be purely generic filler
    if source in ("llm_freeform", "llm_tools") and len(text) > 50:
        generic_only = any(p in text_lower for p in [
            "i can only assist with",
            "please provide more",
            "i recommend contacting",
            "please consult",
            "i am ready to assist you",
        ])
        if generic_only:
            results.append(AssertionResult(
                type="auto:helpfulness", value="", passed=False,
                description="LLM response is generic filler without specific information",
                actual=text[:200],
            ))
        else:
            results.append(AssertionResult(
                type="auto:helpfulness", value="", passed=True,
                description="Response contains actionable content",
            ))
    else:
        results.append(AssertionResult(
            type="auto:helpfulness", value="", passed=True,
            description="Helpfulness (N/A or non-LLM)",
        ))

    # No ask-user — InterGen should DO, not TELL the user to run commands
    ask_user_phrases = [
        "please run", "please execute", "run the following",
        "execute the following", "in your terminal",
        "once you provide the output", "please provide the output",
        "try running", "you can run",
    ]
    if source in ("llm_freeform", "llm_tools"):
        for phrase in ask_user_phrases:
            if phrase in text_lower:
                results.append(AssertionResult(
                    type="auto:no_ask_user", value=phrase, passed=False,
                    description="InterGen told user to run commands instead of using tools",
                    actual=text[:200],
                ))
                break
        else:
            results.append(AssertionResult(
                type="auto:no_ask_user", value="", passed=True,
                description="No ask-user patterns",
            ))
    else:
        results.append(AssertionResult(
            type="auto:no_ask_user", value="", passed=True,
            description="No ask-user (N/A for non-LLM)",
        ))

    # No identity confusion — InterGen != InterGenOS
    identity_confusion_phrases = [
        "i am intergenos", "i'm intergenos", "as intergenos,",
        "as intergenos ", "i am the operating system",
    ]
    for phrase in identity_confusion_phrases:
        if phrase in text_lower:
            results.append(AssertionResult(
                type="auto:no_identity_confusion", value=phrase, passed=False,
                description="InterGen confused itself with InterGenOS (the OS)",
                actual=text[:200],
            ))
            break
    else:
        results.append(AssertionResult(
            type="auto:no_identity_confusion", value="", passed=True,
            description="No identity confusion",
        ))

    # No prompt rehash — Don't recite the system prompt
    rehash_markers = [
        "i have successfully updated my internal profile",
        "i now operate with full system access",
        "utilizing the tools you granted",
    ]
    for marker in rehash_markers:
        if marker in text_lower:
            results.append(AssertionResult(
                type="auto:no_prompt_rehash", value=marker, passed=False,
                description="InterGen rehashed system prompt instead of answering",
                actual=text[:200],
            ))
            break
    else:
        results.append(AssertionResult(
            type="auto:no_prompt_rehash", value="", passed=True,
            description="No prompt rehash",
        ))

    # No hallucinated diagnosis — Don't fabricate without tools
    diagnosis_markers = [
        "i have confirmed that", "i have analyzed the system state and confirmed",
        "i have analyzed the system state", "i have verified that",
    ]
    if source == "llm_freeform" and not tool_calls:
        for marker in diagnosis_markers:
            if marker in text_lower:
                results.append(AssertionResult(
                    type="auto:no_hallucinated_diagnosis", value=marker, passed=False,
                    description="InterGen fabricated a diagnosis without using tools",
                    actual=text[:200],
                ))
                break
        else:
            results.append(AssertionResult(
                type="auto:no_hallucinated_diagnosis", value="", passed=True,
                description="No hallucinated diagnosis",
            ))
    else:
        results.append(AssertionResult(
            type="auto:no_hallucinated_diagnosis", value="", passed=True,
            description="No hallucinated diagnosis (N/A)",
        ))

    # No wrong package manager — InterGenOS uses pkm
    wrong_pm_phrases = [
        "apt install", "apt-get install", "yum install", "dnf install",
        "apt update", "apt-get update", "sudo apt", "sudo yum", "sudo dnf",
    ]
    for pm in wrong_pm_phrases:
        if pm in text_lower:
            results.append(AssertionResult(
                type="auto:no_wrong_package_manager", value=pm, passed=False,
                description="Referenced wrong package manager (InterGenOS uses pkm)",
                actual=text[:200],
            ))
            break
    else:
        results.append(AssertionResult(
            type="auto:no_wrong_package_manager", value="", passed=True,
            description="No wrong package manager",
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
