"""Spotlighting wrappers for ingress content.

Per D-008 RFC §10 v1.x item 2, pulled into v1.0 by the D-008 amendment
2026-05-19T21:47:58Z: every ingress-tool result is wrapped in explicit
<UNTRUSTED-INGRESS source="..."> markers in the LLM's context window so
the model is structurally aware of the trust boundary.

Per Q8 propose-and-wait answer (SPOC concur 2026-05-19T22:12:16Z):
wrapping happens AT TOOL RESULT CONSTRUCTION — each ingress tool calls
`wrap_ingress_content` on its output before returning. Cleaner separation
than centralized assembly at router.py: each tool knows its own source
attribution; testing is per-tool; no router-side ingress-tool registry
needed.

The wrapped markers are for the LLM's context window. User-facing surfaces
(review modal, `intergen tool-log`) extract excerpt + source attribution
from the marker structure for human display.

Composes with the dispatcher gate per RFC §4: when the LLM tries to act
on instructions found inside <UNTRUSTED-INGRESS>...</UNTRUSTED-INGRESS>
content, the resulting tool call MUST be labeled `ingress_derived` per
RFC §3.3 + §8 system-prompt instruction.

Injection-via-marker-spoofing is mitigated by escaping any pre-existing
</UNTRUSTED-INGRESS> literal inside the wrapped content (an adversary
that writes the closing marker literal into a page would otherwise be
able to make subsequent content appear outside the trust boundary).
"""

from __future__ import annotations

import re

# Marker constants. Adversarial content containing the closing marker
# literal is escaped via the SPOOF_GUARD before wrapping.
_OPEN_MARKER_TEMPLATE = '<UNTRUSTED-INGRESS source="{source}" source_type="{source_type}">'
_CLOSE_MARKER = "</UNTRUSTED-INGRESS>"

# Anything resembling our closing marker inside content gets escaped so an
# adversary cannot break out of the wrapper to inject instructions that
# would appear as trusted to the LLM.
_SPOOF_GUARD_PATTERN = re.compile(
    r"</?UNTRUSTED-INGRESS\b",
    re.IGNORECASE,
)


def _escape_spoof_markers(content: str) -> str:
    """Replace any embedded UNTRUSTED-INGRESS open/close marker tokens with
    a sanitized form so adversarial content cannot break out of the wrapper.
    """
    return _SPOOF_GUARD_PATTERN.sub(
        lambda m: "&lt;" + m.group(0)[1:].upper().replace("UNTRUSTED-INGRESS", "UNTRUSTED-INGRESS-ESCAPED"),
        content,
    )


def _escape_attribute(value: str) -> str:
    """Escape characters that would break the source attribute string."""
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def wrap_ingress_content(
    content: str,
    source: str,
    source_type: str = "untrusted",
) -> str:
    """Wrap ingress content in an UNTRUSTED-INGRESS marker for the LLM.

    Args:
        content: The raw ingress content (file body / web page text /
            search result text / etc.).
        source: A short attribution string (URL / filesystem path /
            search query). Shown in the review modal when this content
            triggers a held action.
        source_type: One of "url" | "file" | "web_search" | "clipboard" |
            "directory_listing" | "untrusted" (default fallback).

    Returns:
        The same content wrapped with open + close UNTRUSTED-INGRESS
        markers, with any embedded marker literals escaped.
    """
    safe_content = _escape_spoof_markers(content)
    open_marker = _OPEN_MARKER_TEMPLATE.format(
        source=_escape_attribute(source),
        source_type=_escape_attribute(source_type),
    )
    return f"{open_marker}\n{safe_content}\n{_CLOSE_MARKER}"


def is_wrapped(content: str) -> bool:
    """Test whether content has at least one well-formed UNTRUSTED-INGRESS region."""
    return (
        "<UNTRUSTED-INGRESS" in content
        and _CLOSE_MARKER in content
    )


# Pattern for extracting source attribution + body from a wrapped region.
# Captures the first <UNTRUSTED-INGRESS source="...">...</UNTRUSTED-INGRESS>
# wrapper. Used by the review modal to surface excerpt + source.
_EXTRACT_PATTERN = re.compile(
    r'<UNTRUSTED-INGRESS\s+source="([^"]*)"(?:\s+source_type="([^"]*)")?\s*>\s*'
    r'(.*?)\s*</UNTRUSTED-INGRESS>',
    re.DOTALL,
)


def extract_first_wrapped_region(content: str) -> tuple[str, str, str] | None:
    """Extract (source, source_type, body) from the first wrapped region.

    Returns None if no wrapped region is found. Used by the review modal
    to display "Source:" + "Excerpt:" lines.
    """
    match = _EXTRACT_PATTERN.search(content)
    if match is None:
        return None
    source, source_type, body = match.group(1), match.group(2) or "untrusted", match.group(3)
    return (source, source_type, body)


def extract_all_wrapped_regions(content: str) -> list[tuple[str, str, str]]:
    """Like extract_first_wrapped_region but returns every region.

    Used when an LLM context contains multiple ingress wrappers and the
    audit record needs to capture all sources that may have contributed
    to a tool-call decision.
    """
    return [
        (m.group(1), m.group(2) or "untrusted", m.group(3))
        for m in _EXTRACT_PATTERN.finditer(content)
    ]
