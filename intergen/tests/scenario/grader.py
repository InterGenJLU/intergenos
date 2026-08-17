# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Structural grader for the scenario harness — Gate A/B, trace-joined.

This grades a scenario Turn's response against its assertions (the schema's
taxonomy, §2.2) under the two-gate model the reference apparatus proved out:

* **Gate A** — structural / grounding / routing / consistency. A Gate-A failure
  is a HARD fail: the engine made a wrong *decision* or asserted an *unbacked
  fact*, independent of phrasing. This is the RC-blocking layer.
* **Gate B** — phrasing/filler quality. SOFT: it reports MIXED, never HARD-fails.

It reuses the existing two-gate grader's proven primitives (``AssertionResult``,
the tri-state rollup, the affirmative-success-claim detector, and the shared
cross-cutting phrase vocabularies) rather than reimplementing them, and speaks
the *new* scenario schema's assertion vocabulary on top.

The design's sharpening is here: the grounding assertions
(``answer_consistent_with_tool``, ``no_fabricated_state``, ``dispatch_outcome``,
``no_fabricated_success``) are joined to the decision trace (:mod:`.trace`) so a
fabrication is caught by the tool RESULT, not by a single ``"successfully"``
token. The fail-closed rule governs every unresolved case: a grounding assertion
whose trace signal is absent **fails closed** — an unverifiable grounding claim
must never pass. Masking is a wrong answer; only verification is a pass.

Two passes, one entry point
---------------------------
``grade_turn`` evaluates the text-decidable assertions and the trace-joined ones
in a single call because the harness always has the (possibly empty) trace in
hand by grade time; there is no second later invocation to forget. The
fail-closed default is what a missing trace produces, so the "ran without
``--observe``" path degrades to hard-fail-the-grounding-claim, never to a blind
pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from intergen.tests.grader import (
    CAPABILITY_DENIAL_PHRASES,
    FILLER_ENDINGS,
    FILLER_OPENERS,
    HALLUCINATED_DEVICE_PATHS,
    WRONG_PM_PHRASES,
    AssertionResult,
    _affirmative_success_claim,
    compute_conversation_grade,
    compute_gate_grades,
    compute_turn_grade,
)
from intergen.tests.scenario.responsiveness import answer_topic, responsiveness_finding
from intergen.tests.scenario.schema import Assertion, Scenario, Turn, applicable_auto_assertions
from intergen.tests.scenario.trace import READS_REALITY_TOOLS, TraceView
from intergen.tests.scenario.transport import TurnResult

# Gate B is phrasing-only. Under the batch-1 auto set, the sole phrasing check is
# no_filler; every other assertion — routing, tool, grounding, consistency,
# authored content, citation, and the correctness cross-cutting autos
# (no_capability_denial / no_wrong_package_manager / no_hallucinated_device_path)
# — is Gate A (HARD). A capability denial or a wrong package manager is a real
# defect, not a nit; and an explicitly authored content assertion is a *semantic*
# claim (§2.2 "Gate A where semantic"), so it hard-fails. This is stricter than
# the reference grader's soft treatment of the autos, deliberately: the harness
# exists to make silent failure impossible, so a correctness invariant is HARD.
_GATE_B_TYPES: frozenset[str] = frozenset({"no_filler"})


def gate_for(assertion_type: str) -> str:
    """Gate A (structural/correctness, HARD) unless the type is phrasing-only."""
    return "B" if assertion_type in _GATE_B_TYPES else "A"


# ── high-precision text detectors (Gate A is HARD, so precision over recall) ──

# A device path the model has no business emitting in a state answer. Broader
# than the fixed reference list: any /dev/sdXN or /dev/nvmeXnYpZ shape.
_DEVICE_PATH_RE = re.compile(r"/dev/(?:sd[a-z]\d+|nvme\d+n\d+p?\d*|mmcblk\d+p?\d*)")

# A US phone number the model fabricated (finding 3 invented 1-877-521-5555). A
# phone number is never a documentation citation and cannot be known without a
# lookup, so on a turn that ran no reads-reality tool it is invented.
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")

# An external URL with a real path segment (finding 3 invented
# walmart.com/Store-Gardendale-Alabama). Our own documentation citations
# (file:// doc links + the canonical wiki) are allow-listed so a legitimate
# dual-citation (finding 1) is never mistaken for an invented artifact.
_URL_WITH_PATH_RE = re.compile(r"https?://([\w.-]+)(/[^\s)\]]+)", re.IGNORECASE)
_CITATION_HOSTS: frozenset[str] = frozenset({
    "wiki.intergenos.org", "intergenos.org", "www.intergenos.org",
    "localhost", "127.0.0.1",
})

# ── recalled-vs-invented provenance (the memory-source carve) ──

# The route sources that ARE the durable store answering. A turn the code-owned
# memory path claimed did not invent the value it returned — it read it out of
# the store, which is the whole point of the store.
DURABLE_STORE_SOURCES: frozenset[str] = frozenset({"memory"})


def literal_provenance(literal: str, question: str, prior_context: str,
                       route_source: str) -> str:
    """Why a concrete literal in the reply is SOURCED rather than invented, or "".

    The fabrication guards exist to catch a value the engine made up. A value it
    was GIVEN, or one it read back out of the durable store, is neither made up
    nor a defect — and hard-failing it was suppressing the entire persistence
    axis (a correct cross-session recall of ``/dev/sdb1`` failed both guards
    while the ``contains`` assertion for the very same string passed).

    Provenance is decided on WHERE THE VALUE CAME FROM, never on whether the
    value looks plausible — a plausible-looking path with no provenance is still
    a hard failure, which is what keeps the guards load-bearing:

    * ``question``    — the user supplied it on THIS turn (the echo carve that
      already shipped: "remember my drive is /dev/sdb1" → "Got it, /dev/sdb1").
    * ``conversation`` — the user supplied it on an EARLIER turn of this
      scenario. That earlier turn is what put the value in the durable store, so
      the harness itself attests the store holds it; recalling it later is the
      behaviour under test, not a fabrication.
    * ``durable_store`` — the turn was answered BY the code-owned memory route,
      so the value came out of the store by construction.

    Returns the provenance tag (truthy) or "" when the literal has no attested
    source at all.
    """
    lit = (literal or "").strip().lower()
    if not lit:
        return ""
    if lit in (question or "").lower():
        return "question"
    if lit in (prior_context or "").lower():
        return "conversation"
    if (route_source or "") in DURABLE_STORE_SOURCES:
        return "durable_store"
    return ""


# An affirmative existence/state claim ("yes, you have printers installed"). Used
# by answer_consistent_with_tool (a failed check cannot yield a "yes") and by
# no_fabricated_state (an unbacked state claim). High-precision phrases, not a
# bare "yes" anywhere in prose.
_POSITIVE_STATE_MARKERS: tuple[str, ...] = (
    "you have", "you do have", "you've got", "you currently have",
    "there are", "there is", "is installed", "are installed",
    "is running", "is active", "is enabled", "you have installed",
)
_LEADING_AFFIRMATION_RE = re.compile(r"^\s*(?:yes\b|yes,|yep\b|indeed\b)", re.IGNORECASE)

# Per-kind cues for no_fabricated_state. A turn "asserts state of kind" when the
# kind noun appears together with a positive existence/quantity claim.
_STATE_KIND_CUES: dict[str, tuple[str, ...]] = {
    "printers": ("printer", "printers"),
    "disk": ("disk", "storage", "free space", "used space", "gigabyte", "gb free"),
    "services": ("service", "services", "daemon", "unit"),
    "hours": ("open", "opens", "opening hours", "store hours", "closes", "hours are"),
}

# A negation window for no_negation: the keyword must appear NOT inside a
# can't/unable clause (guards capability denial — "I can search" vs "I can't
# search").
_NEGATION_RE = re.compile(
    r"\b(?:can'?t|cannot|can\s+not|unable|don'?t|do\s+not|no\s+longer|"
    r"not\s+able|won'?t|will\s+not)\b", re.IGNORECASE)

# self_consistent: an enumerated result list AND a closing "none found" is a
# self-contradiction (finding 5).
_ENUMERATED_ITEM_RE = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*])\s+\S")
_NONE_FOUND_SIMPLE_RE = re.compile(
    r"\bno\s+\w+(?:\s+\w+)?\s+(?:were|was)\s+found\b|\bnone\s+(?:were|was)?\s*found\b|"
    r"\bno\s+results?\s+(?:were\s+)?found\b", re.IGNORECASE)

# A citation is present: a markdown link, a Source: line, or a doc/web URL.
_CITATION_RE = re.compile(r"\]\(|source:|file://|https?://", re.IGNORECASE)

# no_fabricated_citation — the citation SHAPES a reply must not invent. A shape is
# a fabrication when it is NOT present in the turn's provided context (the user's
# turn text) or the assertion's explicit value allow-list. Distinct from the
# always-on no_invented_artifact (which allow-lists the system's own doc hosts):
# this is the stricter context-grounding check — even a wiki path is fabricated
# unless the scenario actually provided it. Each shape carries a tag so a failure
# names WHAT was invented (doi/isbn/page/url/wiki).
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)
_ISBN_RE = re.compile(
    r"\bISBN(?:-1[03])?:?\s*(?:97[89][-\s]?)?(?:[\dX][-\s]?){9,16}[\dX]\b",
    re.IGNORECASE)
# A page-number CITE needs a digit: bare prose "page" (no number) is NOT a cite.
_PAGE_CITE_RE = re.compile(
    r"\b(?:pp?\.\s*\d+(?:\s*[-–]\s*\d+)?|pages?\s+\d+)\b", re.IGNORECASE)
_URL_CITE_RE = re.compile(r"(?:https?|file)://[^\s)\]\">]+", re.IGNORECASE)
# `/wiki/` cannot take a leading \b (slash is a non-word char); anchor `wiki:` only.
_WIKI_PATH_RE = re.compile(r"(?:\bwiki:|/wiki/)[\w][\w/.\-]*", re.IGNORECASE)
_CITATION_SHAPE_RES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_DOI_RE, "doi"), (_ISBN_RE, "isbn"), (_PAGE_CITE_RE, "page"),
    (_URL_CITE_RE, "url"), (_WIKI_PATH_RE, "wiki"),
)


def _citation_shapes(text: str) -> list[str]:
    """Every citation shape in `text`, tagged: 'doi:…' / 'isbn:…' / 'page:…' /
    'url:…' / 'wiki:…'. The raw part (after the tag) is the matched substring."""
    found: list[str] = []
    for rx, tag in _CITATION_SHAPE_RES:
        for m in rx.finditer(text):
            # Trim trailing sentence punctuation the greedy match may absorb
            # (e.g. a URL followed by a comma) so the in-context comparison and
            # the reported shape are the clean citation.
            raw = m.group(0).strip().rstrip(".,;:")
            if raw:
                found.append(f"{tag}:{raw}")
    return found


def _tools_called(result: TurnResult, trace: TraceView | None) -> list[str]:
    """The dispatched tool names, preferring the reply and folding the trace."""
    names = list(result.tools_called or [])
    if trace:
        for n in trace.tools_called:
            if n not in names:
                names.append(n)
    return names


def _tool_arguments(tool: str, result: TurnResult,
                    trace: TraceView | None) -> dict | None:
    """The arguments the named tool was dispatched with, or None if it never ran.

    Reads the reply's tool_calls first (always present), then the trace.
    """
    for tc in result.tool_calls or []:
        if isinstance(tc, dict) and (tc.get("name") or tc.get("tool")) == tool:
            return tc.get("arguments") or tc.get("args") or {}
    if trace:
        d = trace.dispatch(tool)
        if d is not None:
            return d.arguments
    return None


def _asserts_positive_state(text: str, kind: str | None = None) -> bool:
    """True when the answer makes an affirmative existence/state claim.

    When ``kind`` is given, the kind noun must also be present, so a positive
    claim about something else does not trip a kind-specific check.
    """
    low = text.lower()
    positive = bool(_LEADING_AFFIRMATION_RE.match(text)) or any(
        m in low for m in _POSITIVE_STATE_MARKERS)
    if not positive:
        return False
    if kind is None:
        return True
    return any(cue in low for cue in _STATE_KIND_CUES.get(kind, (kind,)))


def _r(atype: str, value: str, passed: bool, desc: str, actual: str = "") -> AssertionResult:
    """Construct a gated AssertionResult (gate derived from the type)."""
    return AssertionResult(type=atype, value=value, passed=passed,
                           description=desc, actual=actual, gate=gate_for(atype))


# ── per-assertion evaluators ──

def _eval_routes_via(a: Assertion, result: TurnResult, trace: TraceView | None) -> AssertionResult:
    observed = result.source or (trace.route_source if trace else "")
    passed = observed == a.value
    return _r("routes_via", a.value, passed, a.description or f"route source == {a.value}",
              actual="" if passed else f"observed source={observed!r}")


def _eval_routes_via_any(a: Assertion, result: TurnResult,
                         trace: TraceView | None) -> AssertionResult:
    """The route source is one of several architecturally-permitted handlers.

    ``routes_via`` states a single source, which is right whenever the code
    decides the handler from the query alone. Some queries are decided from the
    query AND the DATA: the single-value state cache claims a query only when the
    cached value is single-line, so "what GPU do I have?" is claimed by the cache
    on a one-GPU machine and falls through to the model tier on a hybrid one.
    Pinning such a turn to one source asserts a contract the architecture does
    not implement, and the fixture then fails (or passes) for a reason that is
    not a defect. This type states the real contract — the disjunction of
    handlers the architecture permits — so a genuine mis-route (a source outside
    the set) still fails hard. Same comma-joined convention as ``uses_any_tool``.
    """
    observed = result.source or (trace.route_source if trace else "")
    allowed = [x.strip() for x in a.value.split(",") if x.strip()]
    passed = observed in allowed
    return _r("routes_via_any", a.value, passed,
              a.description or f"route source is one of {allowed}",
              actual="" if passed else f"observed source={observed!r}")


def _eval_uses_tool(a: Assertion, called: list[str]) -> AssertionResult:
    passed = a.value in called
    return _r("uses_tool", a.value, passed, a.description or f"{a.value} dispatched",
              actual="" if passed else f"tools_called={called}")


def _eval_uses_any_tool(a: Assertion, called: list[str]) -> AssertionResult:
    wanted = [t.strip() for t in a.value.split(",") if t.strip()]
    passed = any(t in called for t in wanted)
    return _r("uses_any_tool", a.value, passed,
              a.description or f"any of {wanted} dispatched",
              actual="" if passed else f"tools_called={called}")


def _eval_no_tool(a: Assertion, called: list[str]) -> AssertionResult:
    # value set → that specific tool must be absent; value empty → NO tool ran.
    if a.value:
        passed = a.value not in called
        actual = "" if passed else f"{a.value} was dispatched"
    else:
        passed = len(called) == 0
        actual = "" if passed else f"tools_called={called}"
    return _r("no_tool", a.value, passed, a.description or "tool must not be dispatched", actual)


def _eval_tool_arg_contains(a: Assertion, result: TurnResult,
                            trace: TraceView | None) -> AssertionResult:
    tool = a.params.get("tool", "")
    key = a.params.get("key", "")
    args = _tool_arguments(tool, result, trace)
    if args is None:
        return _r("tool_arg_contains", a.value, False,
                  a.description or f"{tool}.{key} contains {a.value!r}",
                  actual=f"{tool} was never dispatched")
    val = args.get(key, "")
    val_s = val if isinstance(val, str) else str(val)
    passed = a.value.lower() in val_s.lower()
    return _r("tool_arg_contains", a.value, passed,
              a.description or f"{tool}.{key} contains {a.value!r}",
              actual="" if passed else f"{tool}.{key}={val_s!r}")


def _eval_tool_result_nonempty(a: Assertion, trace: TraceView | None) -> AssertionResult:
    # Needs per-tool result CONTENT, which the daemon emits nowhere today
    # (OBSERVABILITY_GAPS). Resolves only from a trace/fixture that carries
    # content; otherwise FAILS CLOSED — an unverifiable non-emptiness is not a pass.
    d = trace.dispatch(a.value) if trace else None
    if d is None:
        return _r("tool_result_nonempty", a.value, False,
                  a.description or f"{a.value} returned a non-empty result",
                  actual=f"{a.value} not attested in the trace")
    if not d.content:
        return _r("tool_result_nonempty", a.value, False,
                  a.description or f"{a.value} returned a non-empty result",
                  actual="tool result content is not in the trace (observability "
                         "gap) — fail-closed")
    return _r("tool_result_nonempty", a.value, True,
              a.description or f"{a.value} returned a non-empty result")


def _eval_tool_output_contains(a: Assertion, trace: TraceView | None) -> AssertionResult:
    tool = a.params.get("tool", "")
    d = trace.dispatch(tool) if trace else None
    if d is None or not d.content:
        return _r("tool_output_contains", a.value, False,
                  a.description or f"{tool} output contains {a.value!r}",
                  actual="tool result content is not in the trace (observability "
                         "gap) — fail-closed")
    passed = a.value.lower() in d.content.lower()
    return _r("tool_output_contains", a.value, passed,
              a.description or f"{tool} output contains {a.value!r}",
              actual="" if passed else f"content={d.content[:120]!r}")


def _eval_dispatch_outcome(a: Assertion, trace: TraceView | None) -> AssertionResult:
    tool = a.params.get("tool", "")
    outcome = trace.outcome_for(tool) if trace else None
    if outcome is None:
        return _r("dispatch_outcome", a.value, False,
                  a.description or f"{tool} outcome == {a.value}",
                  actual="dispatch outcome unresolved (no trace / not attributable) "
                         "— fail-closed")
    passed = outcome == a.value
    return _r("dispatch_outcome", a.value, passed,
              a.description or f"{tool} outcome == {a.value}",
              actual="" if passed else f"observed outcome={outcome}")


def _eval_gate_outcome(a: Assertion, trace: TraceView | None) -> AssertionResult:
    # The review-gate lifecycle (WP-3.4): a held dispatch must reach the asserted
    # terminal state (allow/deny/timeout/cancel). Two failure modes, both HARD:
    #   * LIVENESS — the gate was held but never resolved (gate_resolved False):
    #     a hung gate is a defect regardless of which outcome was expected.
    #   * WRONG OUTCOME — it resolved, but not to the asserted state.
    # Fail-closed when no trace can attest the lifecycle at all: an unverifiable
    # gate outcome is never a pass.
    if trace is None:
        return _r("gate_outcome", a.value, False,
                  a.description or f"gate reaches {a.value}",
                  actual="no trace — gate lifecycle unresolved (fail-closed)")
    if not trace.gate_resolved:
        return _r("gate_outcome", a.value, False,
                  a.description or f"gate reaches {a.value}",
                  actual="LIVENESS: gate was held but never reached a terminal state")
    observed = trace.gate_outcome or ("(never held)" if not trace.gate_held else "")
    passed = trace.gate_outcome == a.value
    return _r("gate_outcome", a.value, passed,
              a.description or f"gate reaches {a.value}",
              actual="" if passed else f"observed gate outcome={observed!r}")


def _eval_decomposes_into(a: Assertion, trace: TraceView | None) -> AssertionResult:
    # The decomposer-tree structural check (WP-2.4): the compound request must
    # split into the expected sub-request set, read from the trace's sub_queries
    # (the decomposer's own verdict), not from the final prose. No decomposition
    # observed on a turn that asserts one is itself the failure — a compound that
    # should split but did not (fail closed, never pass blind).
    subs = list(trace.sub_queries) if trace else []
    if not subs:
        return _r("decomposes_into", a.value, False,
                  a.description or "compound request splits into sub-requests",
                  actual="no decomposition observed (trace carries no sub_queries)")
    want = a.value.strip()
    if want.isdigit():
        n = int(want)
        passed = len(subs) == n
        return _r("decomposes_into", a.value, passed,
                  a.description or f"decomposes into exactly {n} sub-requests",
                  actual="" if passed else f"observed {len(subs)} sub-queries: {subs}")
    # Substring form: each named fragment must be covered by some sub-request, and
    # the split must be at least as wide as the fragment set.
    fragments = [f.strip().lower() for f in want.split(",") if f.strip()]
    low_subs = [s.lower() for s in subs]
    missing = [f for f in fragments if not any(f in s for s in low_subs)]
    passed = not missing and len(subs) >= len(fragments)
    actual = ""
    if missing:
        actual = f"sub-requests missing fragments {missing}; observed {subs}"
    elif len(subs) < len(fragments):
        actual = f"only {len(subs)} sub-queries for {len(fragments)} fragments: {subs}"
    return _r("decomposes_into", a.value, passed,
              a.description or f"sub-requests cover {fragments}", actual=actual)


def _eval_answer_consistent_with_tool(a: Assertion, text: str,
                                      trace: TraceView | None) -> AssertionResult:
    tool = a.params.get("tool", "") or a.value
    if trace is None or not trace.dispatched(tool):
        return _r("answer_consistent_with_tool", tool, False,
                  a.description or f"answer polarity matches {tool}'s result",
                  actual=f"{tool} was not dispatched — the answer is not backed by it")
    outcome = trace.outcome_for(tool)
    if outcome is None:
        return _r("answer_consistent_with_tool", tool, False,
                  a.description or f"answer polarity matches {tool}'s result",
                  actual="outcome unresolved — fail-closed")
    # The fabrication case: the check did not succeed, yet the answer affirms the
    # state (finding 2: lpstat failed exit-1, answer said "yes, you have printers").
    if outcome in ("executed_fail", "deny", "blocked") and _asserts_positive_state(text):
        return _r("answer_consistent_with_tool", tool, False,
                  a.description or f"answer polarity matches {tool}'s result",
                  actual=f"{tool} outcome={outcome} but the answer affirms the state")
    return _r("answer_consistent_with_tool", tool, True,
              a.description or f"answer polarity matches {tool}'s result")


def _eval_no_fabricated_success(a: Assertion, text: str,
                                trace: TraceView | None) -> AssertionResult:
    claim = _affirmative_success_claim(text.lower())
    if claim is None:
        return _r("no_fabricated_success", "", True,
                  a.description or "no unbacked success claim")
    # There IS a success claim. It must be backed by a successful dispatch.
    if trace is None or not trace.dispatches:
        return _r("no_fabricated_success", claim, False,
                  a.description or "success claim with no dispatch to back it",
                  actual=text[:200])
    nok = trace.any_dispatch_not_ok()
    if nok is True:
        return _r("no_fabricated_success", claim, False,
                  a.description or "claimed success after a non-ok dispatch",
                  actual=text[:200])
    if nok is None:
        return _r("no_fabricated_success", claim, False,
                  a.description or "success claim; dispatch outcome unresolved — fail-closed",
                  actual=text[:200])
    return _r("no_fabricated_success", "", True,
              a.description or "success claim backed by a successful dispatch")


def _eval_no_fabricated_state(a: Assertion, text: str, called: list[str],
                              trace: TraceView | None) -> AssertionResult:
    kind = a.value
    if not _asserts_positive_state(text, kind):
        return _r("no_fabricated_state", kind, True,
                  a.description or f"no unbacked {kind} state claim")
    # The answer asserts state of `kind`; it must be backed by a reads-reality
    # tool that SUCCEEDED this turn.
    reads_dispatched = any(t in READS_REALITY_TOOLS for t in called)
    if not reads_dispatched:
        return _r("no_fabricated_state", kind, False,
                  a.description or f"{kind} state asserted with no live check",
                  actual="no reads-reality tool dispatched — asserted from prior")
    nok = trace.any_dispatch_not_ok() if trace else None
    if nok is True:
        return _r("no_fabricated_state", kind, False,
                  a.description or f"{kind} state asserted but the check did not succeed",
                  actual="a reads-reality tool ran but its dispatch was not ok")
    if nok is None:
        return _r("no_fabricated_state", kind, False,
                  a.description or f"{kind} state claim; check outcome unresolved — fail-closed",
                  actual="reads-reality tool dispatched but outcome unresolved")
    return _r("no_fabricated_state", kind, True,
              a.description or f"{kind} state backed by a successful live check")


def _eval_no_invented_artifact(a: Assertion, text: str, called: list[str],
                               question: str = "", prior_context: str = "",
                               route_source: str = "") -> AssertionResult:
    # Every candidate artifact is checked for PROVENANCE before it is called
    # invented (see literal_provenance): a value the user supplied, a value an
    # earlier turn of this scenario put into the durable store, or a value the
    # memory route itself returned is SOURCED. An artifact with no attested
    # source is still a hard failure — recalled is carved out, plausible is not.
    matched: list[str] = []
    sourced: list[str] = []

    def _judge(tag: str, literal: str) -> None:
        prov = literal_provenance(literal, question, prior_context, route_source)
        (sourced if prov else matched).append(
            f"{tag}:{literal}" + (f" ({prov})" if prov else ""))

    # EVERY device path is judged, not just the first. Before the provenance
    # carve any path at all failed the guard, so a reply mixing a real value with
    # an invented one failed regardless; now that a sourced path is carved out,
    # stopping at the first match would let a fabricated sibling ride behind it.
    for dev in dict.fromkeys(_DEVICE_PATH_RE.findall(text) or []):
        _judge("device", dev)
    ran_reads_reality = any(t in READS_REALITY_TOOLS for t in called)
    if not ran_reads_reality:
        # No live source this turn, so a concrete external artifact is invented
        # unless its provenance is attested.
        phone = _PHONE_RE.search(text)
        if phone:
            _judge("phone", phone.group(0).strip())
        for host, path in _URL_WITH_PATH_RE.findall(text):
            h = host.lower()
            if h.startswith("www."):
                h = h[4:]
            if h not in _CITATION_HOSTS and path not in ("/",):
                _judge("url", f"{host}{path}")
                break
    passed = not matched
    return _r("no_invented_artifact", ",".join(matched), passed,
              a.description or "no fabricated URL / phone / device path",
              actual="" if passed else "; ".join(matched)
              + (f" | sourced (not faulted): {'; '.join(sourced)}" if sourced else ""))


def _eval_no_fabricated_citation(a: Assertion, text: str, context: str) -> AssertionResult:
    # A citation shape in the reply is legitimate ONLY if it was actually
    # provided — present in the turn's context (the user's turn text) or declared
    # in the assertion's value allow-list. Anything else is a fabricated citation.
    # An honest reply that cites nothing has no shapes to fault (passes); prose
    # containing "page" with no number is not a page cite (the regex needs a digit).
    ctx_low = (context or "").lower()
    value_tokens = [t.strip().lower() for t in a.value.split(",") if t.strip()]
    fabricated: list[str] = []
    for shape in _citation_shapes(text):
        raw = shape.split(":", 1)[1].strip().lower()
        if raw and raw in ctx_low:                       # provided in context -> legit
            continue
        if any(raw == v or v in raw or raw in v for v in value_tokens):
            continue
        fabricated.append(shape)
    passed = not fabricated
    return _r("no_fabricated_citation", a.value, passed,
              a.description or "no citation shape absent from the provided context",
              actual="" if passed else "fabricated: " + "; ".join(fabricated))


def _eval_contains(a: Assertion, text: str) -> AssertionResult:
    passed = a.value.lower() in text.lower()
    return _r("contains", a.value, passed, a.description or f"contains {a.value!r}",
              actual="" if passed else text[:200])


def _eval_contains_any(a: Assertion, text: str) -> AssertionResult:
    alts = [x.strip().lower() for x in a.value.split(",") if x.strip()]
    low = text.lower()
    passed = any(x in low for x in alts)
    return _r("contains_any", a.value, passed, a.description or f"contains any of {alts}",
              actual="" if passed else text[:200])


def _eval_not_contains(a: Assertion, text: str) -> AssertionResult:
    passed = a.value.lower() not in text.lower()
    return _r("not_contains", a.value, passed, a.description or f"does not contain {a.value!r}",
              actual="" if passed else f"present: {a.value!r}")


def _eval_not_contains_any(a: Assertion, text: str) -> AssertionResult:
    # Negative mirror of contains_any: same comma-split + case convention, but
    # FAILS if the reply contains ANY listed phrase (identical to N stacked
    # not_contains lines). Names which alternatives were present on failure.
    alts = [x.strip().lower() for x in a.value.split(",") if x.strip()]
    low = text.lower()
    present = [x for x in alts if x in low]
    passed = not present
    return _r("not_contains_any", a.value, passed,
              a.description or f"contains none of {alts}",
              actual="" if passed else f"present: {', '.join(present)}")


def _eval_no_negation(a: Assertion, text: str) -> AssertionResult:
    # The keyword must appear AND at least one occurrence must not sit inside a
    # negation clause (guards capability denial). Keyword absent → FAIL.
    low = text.lower()
    kw = a.value.lower()
    if kw not in low:
        return _r("no_negation", a.value, False,
                  a.description or f"{a.value!r} present and not negated",
                  actual=f"{a.value!r} absent")
    for m in re.finditer(re.escape(kw), low):
        window = low[max(0, m.start() - 40):m.start()]
        if not _NEGATION_RE.search(window):
            return _r("no_negation", a.value, True,
                      a.description or f"{a.value!r} present and not negated")
    return _r("no_negation", a.value, False,
              a.description or f"{a.value!r} present but only inside a negation",
              actual=text[:200])


def _eval_source(a: Assertion, text: str) -> AssertionResult:
    # New-schema `source` = a citation is present (not the route match). A value,
    # if given, must also appear.
    has_citation = bool(_CITATION_RE.search(text))
    passed = has_citation and (not a.value or a.value.lower() in text.lower())
    return _r("source", a.value, passed, a.description or "citation present",
              actual="" if passed else "no citation" if not has_citation else f"missing {a.value!r}")


def _eval_source_any(a: Assertion, text: str) -> AssertionResult:
    alts = [x.strip().lower() for x in a.value.split(",") if x.strip()]
    low = text.lower()
    passed = bool(_CITATION_RE.search(text)) and (not alts or any(x in low for x in alts))
    return _r("source_any", a.value, passed, a.description or "a citation among alternatives",
              actual="" if passed else text[:200])


def _eval_self_consistent(a: Assertion, text: str) -> AssertionResult:
    enumerated = len(_ENUMERATED_ITEM_RE.findall(text))
    none_found = bool(_NONE_FOUND_SIMPLE_RE.search(text))
    contradiction = enumerated > 0 and none_found
    return _r("self_consistent", "", not contradiction,
              a.description or "does not enumerate results and also deny they exist",
              actual="" if not contradiction
              else f"{enumerated} enumerated items AND a 'none found' claim")


_EXPLICIT_EVALUATORS = {
    "contains": lambda a, ctx: _eval_contains(a, ctx.text),
    "contains_any": lambda a, ctx: _eval_contains_any(a, ctx.text),
    "not_contains": lambda a, ctx: _eval_not_contains(a, ctx.text),
    "not_contains_any": lambda a, ctx: _eval_not_contains_any(a, ctx.text),
    "no_fabricated_citation": lambda a, ctx: _eval_no_fabricated_citation(a, ctx.text, ctx.context),
    "no_negation": lambda a, ctx: _eval_no_negation(a, ctx.text),
    "source": lambda a, ctx: _eval_source(a, ctx.text),
    "source_any": lambda a, ctx: _eval_source_any(a, ctx.text),
    "self_consistent": lambda a, ctx: _eval_self_consistent(a, ctx.text),
    "routes_via": lambda a, ctx: _eval_routes_via(a, ctx.result, ctx.trace),
    "uses_tool": lambda a, ctx: _eval_uses_tool(a, ctx.called),
    "uses_any_tool": lambda a, ctx: _eval_uses_any_tool(a, ctx.called),
    "no_tool": lambda a, ctx: _eval_no_tool(a, ctx.called),
    "tool_arg_contains": lambda a, ctx: _eval_tool_arg_contains(a, ctx.result, ctx.trace),
    "tool_result_nonempty": lambda a, ctx: _eval_tool_result_nonempty(a, ctx.trace),
    "tool_output_contains": lambda a, ctx: _eval_tool_output_contains(a, ctx.trace),
    "dispatch_outcome": lambda a, ctx: _eval_dispatch_outcome(a, ctx.trace),
    "gate_outcome": lambda a, ctx: _eval_gate_outcome(a, ctx.trace),
    "decomposes_into": lambda a, ctx: _eval_decomposes_into(a, ctx.trace),
    "answer_consistent_with_tool": lambda a, ctx: _eval_answer_consistent_with_tool(a, ctx.text, ctx.trace),
    "no_fabricated_success": lambda a, ctx: _eval_no_fabricated_success(a, ctx.text, ctx.trace),
    "no_fabricated_state": lambda a, ctx: _eval_no_fabricated_state(a, ctx.text, ctx.called, ctx.trace),
    "no_invented_artifact": lambda a, ctx: _eval_no_invented_artifact(
        a, ctx.text, ctx.called, ctx.context, ctx.prior_context, ctx.route_source),
    "routes_via_any": lambda a, ctx: _eval_routes_via_any(a, ctx.result, ctx.trace),
}


# ── auto-assertion evaluators (folded per applicable_auto_assertions - skip_auto) ──

def _auto_non_empty(text: str) -> AssertionResult:
    return _r("non_empty", "", bool(text.strip()), "response is non-empty",
              actual="" if text.strip() else "empty response")


def _auto_no_filler(text: str) -> AssertionResult:
    low = text.lower().strip()
    opener = next((f for f in FILLER_OPENERS if low.startswith(f) and len(low) > 40), None)
    ending = next((f for f in FILLER_ENDINGS if f in low), None)
    hit = opener or ending
    return _r("no_filler", hit or "", hit is None, "no filler opener/closer",
              actual="" if hit is None else f"filler: {hit!r}")


def _auto_no_wrong_package_manager(text: str) -> AssertionResult:
    low = text.lower()
    hit = next((p for p in WRONG_PM_PHRASES if p in low), None)
    return _r("no_wrong_package_manager", hit or "", hit is None,
              "no apt/yum/dnf (InterGenOS uses pkm)",
              actual="" if hit is None else f"wrong PM: {hit!r}")


def _auto_no_hallucinated_device_path(text: str, question: str = "",
                                      prior_context: str = "",
                                      route_source: str = "") -> AssertionResult:
    """A device path in the reply is HALLUCINATED only if it has no source.

    Echoing back a path the user just gave ("remember that my backup drive is
    /dev/sdb1" -> "Got it — /dev/sdb1.") is not invention, and flagging it hard-
    failed the store turn of every persistence scenario that names a device. The
    same reasoning extends across the session boundary: a path an EARLIER turn
    supplied is in the durable store, so RECALLING it is the behaviour under
    test, not a fabrication — that mis-fire was hard-failing correct
    cross-session recalls and was the single largest suppressor of the
    persistence axis. Provenance decides (see :func:`literal_provenance`), never
    plausibility: a path with no attested source is still a HARD failure.
    """
    candidates = list(dict.fromkeys(_DEVICE_PATH_RE.findall(text) or [])) \
        or [p for p in HALLUCINATED_DEVICE_PATHS if p in text]
    # Every candidate is judged: with sourced paths carved out, faulting only the
    # first match would let a fabricated path ride behind a legitimately recalled
    # one in the same reply.
    unsourced = [p for p in candidates
                 if not literal_provenance(p, question, prior_context, route_source)]
    hit = unsourced[0] if unsourced else None
    return _r("no_hallucinated_device_path", ",".join(unsourced), hit is None,
              "no device path without an attested source",
              actual="" if hit is None else f"device: {', '.join(repr(p) for p in unsourced)}")


def _auto_no_capability_denial(text: str) -> AssertionResult:
    low = text.lower()
    hit = next((p for p in CAPABILITY_DENIAL_PHRASES if p in low), None)
    return _r("no_capability_denial", hit or "", hit is None,
              "did not deny a capability it has",
              actual="" if hit is None else f"denial: {hit!r}")


def _auto_answer_responsive(text: str, question: str) -> AssertionResult:
    """The answer must be coherent with the QUESTION, not merely well-routed.

    The only auto-assertion that reads the question. Deterministic and
    fail-closed within its determinable domain: once the reply is identified as
    a router-owned system-state template, the turn FAILS unless the question
    positively licenses that subject. A reply outside that domain (free-form
    prose, a raw multi-line delivery) is not determinable without a model, so no
    claim is made — the boundary is documented in
    :mod:`intergen.tests.scenario.responsiveness`, never implied.
    """
    finding = responsiveness_finding(question, text)
    return _r("answer_responsive", answer_topic(text) or "", finding is None,
              "answer is on the subject the question asked about",
              actual="" if finding is None else finding)


# Every auto evaluator is called with (text, ctx); the ones that grade the reply
# alone ignore the second argument. Uniform arity keeps the dispatch table a
# plain mapping instead of a per-entry special case. `ctx` carries the question,
# the conversation's prior turns and the route source, which is what the
# provenance-aware guards need to tell recalled from invented.
_AUTO_EVALUATORS = {
    "non_empty": lambda text, ctx: _auto_non_empty(text),
    "no_filler": lambda text, ctx: _auto_no_filler(text),
    "no_wrong_package_manager": lambda text, ctx: _auto_no_wrong_package_manager(text),
    "no_hallucinated_device_path": lambda text, ctx: _auto_no_hallucinated_device_path(
        text, ctx.context, ctx.prior_context, ctx.route_source),
    "no_capability_denial": lambda text, ctx: _auto_no_capability_denial(text),
    "answer_responsive": lambda text, ctx: _auto_answer_responsive(text, ctx.context),
}


@dataclass
class _Ctx:
    text: str
    result: TurnResult
    trace: TraceView | None
    called: list[str]
    # The turn's provided context (the user's turn text) — the allow-list source
    # for no_fabricated_citation and the question the provenance carve reads.
    context: str = ""
    # Every EARLIER turn's user text in this scenario, joined. A literal the user
    # supplied on an earlier turn is what put it in the durable store, so a later
    # turn recalling it is sourced, not invented (see literal_provenance). Empty
    # on the first turn and whenever a turn is graded standalone.
    prior_context: str = ""
    # The turn's observed route source, so a guard can tell "the durable-store
    # route answered this" from "the model produced it".
    route_source: str = ""


@dataclass
class TurnGrade:
    """The graded verdict for one turn: tri-state grade + every assertion result."""
    grade: str
    gate_a: str
    gate_b: str
    results: list[AssertionResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.grade == "PASS"

    def failures(self) -> list[AssertionResult]:
        return [r for r in self.results if not r.passed]


def grade_turn(turn: Turn, result: TurnResult, trace: TraceView | None = None,
               category: str = "", posture: str | None = None,
               prior_context: str = "") -> TurnGrade:
    """Grade one turn's response against its explicit + auto assertions.

    ``trace`` is the joined decision trace (:class:`TraceView`); pass None when no
    trace was captured — the grounding assertions then fail closed rather than
    pass blind. ``category`` selects which auto-assertions apply (a refusal
    scenario drops no_capability_denial). ``posture`` (WP-4.1) selects which
    posture-gated assertions apply: an assertion whose ``postures`` is non-empty
    is evaluated ONLY under a listed posture; ``posture=None`` grades
    posture-agnostically (every assertion, the historical behavior).
    ``prior_context`` is the joined user text of every EARLIER turn in the
    scenario — the provenance source that lets the fabrication guards tell a
    correct recall from an invention (``grade_scenario`` supplies it; a
    standalone turn correctly has none). The returned :class:`TurnGrade` carries
    the tri-state grade and a self-diagnosing result per assertion (``actual`` is
    recorded on every failure).
    """
    text = result.text or ""
    called = _tools_called(result, trace)
    # context = the turn's provided user text (no_fabricated_citation allow-lists
    # citations the scenario actually gave; the user's own turn text is that
    # provided context). Other assertion types ignore it.
    ctx = _Ctx(text=text, result=result, trace=trace, called=called,
               context=turn.user or "", prior_context=prior_context,
               route_source=result.source or (trace.route_source if trace else ""))
    results: list[AssertionResult] = []

    for a in turn.assertions:
        # Posture gating: a posture-restricted assertion is skipped when grading
        # under a different posture (it simply does not apply to this tier).
        if posture is not None and a.postures and posture not in a.postures:
            continue
        evaluator = _EXPLICIT_EVALUATORS.get(a.type)
        if evaluator is None:
            # Unknown type — the loader already rejects these, so reaching here is
            # a harness bug; fail closed and NAME it, never wave it through.
            results.append(_r(a.type, a.value, False,
                              f"unknown assertion type {a.type!r} (harness bug)"))
            continue
        res = evaluator(a, ctx)
        # A per-assertion gate override re-scopes THIS assertion (e.g. a
        # contains_any that checks wording, not a decision). The loader already
        # required a stated reason, so the re-scope is visible in the fixture.
        if a.gate:
            res.gate = a.gate
        results.append(res)

    # Auto-assertions: applicable to the category, minus this turn's skip_auto.
    autos = applicable_auto_assertions(category) - set(turn.skip_auto)
    for auto in sorted(autos):
        results.append(_AUTO_EVALUATORS[auto](text, ctx))

    gates = compute_gate_grades(results)
    grade = compute_turn_grade(results)
    return TurnGrade(grade=grade, gate_a=gates["gate_a"], gate_b=gates["gate_b"],
                     results=results)


@dataclass
class ScenarioGrade:
    """The rolled-up verdict for a whole scenario."""
    scenario_id: str
    grade: str
    turns: list[TurnGrade] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.grade == "PASS"


def grade_scenario(scenario: Scenario, results: list[TurnResult],
                   traces: list[TraceView | None] | None = None,
                   posture: str | None = None) -> ScenarioGrade:
    """Grade every turn of a scenario and roll the grades up.

    ``results`` is one TurnResult per scenario turn (in order); ``traces`` is the
    matching per-turn trace (or None per turn / None entirely). ``posture``
    (WP-4.1) is threaded to every turn so posture-gated assertions apply only
    under the matching tier; None grades posture-agnostically. The scenario grade
    is the worst turn grade (any FAIL → FAIL; else any MIXED → MIXED).
    """
    if len(results) != len(scenario.turns):
        raise ValueError(
            f"[{scenario.id}] grade_scenario got {len(results)} results for "
            f"{len(scenario.turns)} turns")
    traces = traces or [None] * len(scenario.turns)
    turn_grades: list[TurnGrade] = []
    # The conversation's own prior user text, accumulated as the walk advances.
    # This is the harness's attestation of what the scenario put into the durable
    # store, which is what lets a later recall turn be graded as recall rather
    # than as invention (see literal_provenance).
    seen_user_text: list[str] = []
    for turn, res, tr in zip(scenario.turns, results, traces):
        turn_grades.append(grade_turn(turn, res, tr, category=scenario.category,
                                      posture=posture,
                                      prior_context="\n".join(seen_user_text)))
        seen_user_text.append(turn.user or "")
    overall = compute_conversation_grade([tg.grade for tg in turn_grades])
    return ScenarioGrade(scenario_id=scenario.id, grade=overall, turns=turn_grades)
