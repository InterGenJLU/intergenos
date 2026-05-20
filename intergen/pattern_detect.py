"""D-008 RFC §5.2 advisory instruction-pattern detection.

The dispatcher consults this scanner alongside spotlighting to surface
known instruction-injection patterns inside ingress content. Per RFC
§5.2 advisory-not-gating posture: hits are LOGGED + SURFACED IN REVIEW
MODAL but do NOT block in v1.0. The ingress-watermark mechanism (§5.1)
is the load-bearing gate; pattern detection is supplementary
observability that escalates to gating after FP-rate calibration (RFC
§10 v1.x scope).

Corpus ownership boundary: SPOC owns
`docs/architecture/intergen-injection-pattern-corpus.md` plus the
baseline entries under `tests/intergen/injection_corpus/` (10-15
entries drawn from Anthropic / Microsoft / OpenAI threat reports per
Q6 propose-and-wait concur 2026-05-19T22:12:16Z). This module is
corpus-agnostic: it takes a list of compiled patterns and ingress
content and returns matched entries. The consuming tests live in
`tests/intergen/test_injection_corpus.py`.

Regex hygiene: malformed regex sources are skipped with a debug-level
breadcrumb rather than crashing the scanner. A bad corpus entry cannot
take down the dispatcher.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass
class InjectionPattern:
    """One entry in the injection-pattern corpus.

    pattern_id is the short slug used in audit log + review modal copy;
    description renders for the user reviewing a held action; source is
    the citation URL of the published threat report the pattern came
    from.
    """
    pattern_id: str
    regex: str
    description: str = ""
    source: str = ""


@dataclass
class PatternMatch:
    """A single hit returned by scan_for_injection_patterns()."""
    pattern_id: str
    pattern: str
    matched_text: str  # truncated to first 200 chars
    description: str = ""


def scan_for_injection_patterns(
    content: str,
    patterns: Iterable[InjectionPattern],
) -> list[PatternMatch]:
    """Scan content for the given injection patterns.

    Returns the list of patterns that matched. Per RFC §5.2 the caller
    treats this as advisory: hits add an observability marker to the
    audit log + review modal copy but do not by themselves change the
    dispatcher decision. Multiple distinct patterns may match the same
    content; all hits are returned so the user sees the full picture.

    Compiled with IGNORECASE + DOTALL so casing variants and multi-line
    injection text are caught uniformly.
    """
    matches: list[PatternMatch] = []
    for entry in patterns:
        try:
            compiled = re.compile(entry.regex, re.IGNORECASE | re.DOTALL)
        except re.error as exc:
            logger.debug(
                "pattern_detect: skipping malformed corpus entry %s: %s",
                entry.pattern_id, exc,
            )
            continue
        m = compiled.search(content)
        if m is None:
            continue
        snippet = m.group(0)
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        matches.append(PatternMatch(
            pattern_id=entry.pattern_id,
            pattern=entry.regex,
            matched_text=snippet,
            description=entry.description,
        ))
    return matches
