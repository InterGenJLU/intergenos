# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Output-coherence assessment — shape-based, backend-agnostic model-health checks.

Why this exists (the silent-corruption class it defends against)
----------------------------------------------------------------
A model backend can offload every layer, answer ``/health`` green, and still
emit **garbage** — the PI-Z26 facet found on Intel ANV/Gen9.5 (mesa Vulkan),
where the 9B loaded 33/33 layers yet produced system-prompt leakage and token
salad past ~600 generated tokens (a known upstream llama.cpp class:
ggml-org/llama.cpp #17106 / #20104 / #19327 / #16188; the upstream mitigation is
``GGML_VK_DISABLE_F16``). **Zero errors appeared in any log while the engine
emitted salad — the silence IS the defect.** The layer-count offload gate
(``llama_manager._verify_gpu_offload``) cannot see this: the layers *are* on the
GPU; it is the *compute* that is wrong.

So the health signal has to be the *output shape*, judged against what a coherent
completion of a known prompt must look like — never byte-equality against another
backend (CPU and GPU round differently at the last bit; that is expected and is
NOT corruption). This module is that judge, and ONLY that judge: it renders a
verdict on a single (prompt, output) pair. It does not launch servers, decide
offload, or know about GPUs. It is deliberately a standalone, dependency-free,
module-level function so the runtime semantic-health detector
(:mod:`intergen.semantic_health`, which watches live turns for this degradation)
has one predicate to reuse rather than re-deriving one that could drift.

The six shape checks
--------------------
1. **substance** — the output is non-trivial (salad can collapse to empty /
   whitespace). A punctuation smear is longer than the substance floor and is
   caught by check 6, not here.
2. **expected-keyword present** — for a prompt with a known answer, at least one
   expected anchor must appear (case-insensitive). Skipped when the caller
   declares no keywords.
3. **foreign-script ratio** — the fraction of *alphabetic* characters that fall
   outside the Latin block, over a Latin/numeric prompt, must stay below a
   threshold. Vulkan-F16 salad characteristically sprays CJK/Cyrillic/other
   scripts; a coherent English-or-numeric answer sits at ~0.
4. **no verbatim prompt-echo** — the output must not contain a long contiguous
   span of the prompt text. Reproducing the system/user prompt verbatim is the
   "system-prompt leakage" signature seen on a development machine.
5. **no repetition blowup** — no single whitespace token repeated in a long
   consecutive run, and no single token dominating the output. Degenerate
   compute loops into ``the the the`` / ``东 东 东``; legitimate answers
   (including ``1 2 3 ... 200`` counting) never do.
6. **not degenerate** — the output is language at all, rather than a punctuation
   smear or a character-level loop. Two signals measured outside fenced code
   blocks: the share of non-whitespace characters that are not letters or
   digits, and the zlib compression ratio. Check 5 splits on whitespace, so it
   cannot see repetition inside a punctuation cluster or inside text with no
   spaces; this check is that generalization. The serving-floor quality gate
   (:func:`intergen.llm.LLMRouter.check_quality`) consumes
   :func:`degeneracy_reason` directly, so the bring-up gate and the serving
   floor decide "not language" the same way.

A result is coherent iff every applicable check passes. Thresholds are tuned to
be *lenient* — the goal is to catch unambiguous garbage without ever false-DENYing
a genuinely-correct backend, because a false DENY needlessly floors a working
machine to CPU (a real harm to the user's control over their own hardware).
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field

# A character is "foreign script" if it is alphabetic AND its codepoint is above
# the Latin range (Basic Latin + Latin-1 Supplement + Latin Extended-A/B end at
# U+024F). Digits, punctuation, whitespace, and accented Latin are NOT foreign.
_LATIN_ALPHA_CEILING = 0x024F

# Default thresholds. Deliberately permissive — see module docstring.
_FOREIGN_SCRIPT_RATIO_MAX = 0.20      # >20% non-Latin alpha over a Latin prompt = salad
_PROMPT_ECHO_MIN_SPAN = 40            # a >=40-char verbatim prompt span in output = leakage
_REPEAT_RUN_MAX = 8                   # same token >8x in a row = degenerate loop
_TOKEN_DOMINANCE_MAX = 0.40          # one token >40% of a multi-token output = collapse
_SUBSTANCE_MIN_CHARS = 2             # fewer than this (stripped) = no real answer
_TOKEN_DOMINANCE_MIN_TOKENS = 12     # only judge dominance once there are enough tokens

# Degeneracy thresholds (check 6). Both were read off the character distribution
# of 441 real replies from the three sealed 2026-08-07 baseline runs (35B, 9B and
# 2B tiers) rather than chosen a priori; the calibration is recorded in that
# round's evidence. The measured gap either side of each threshold:
#   non-alphanumeric share — coherent replies topped out at 0.279 (a reply full
#                            of markdown emphasis and paths); degenerate replies
#                            started at 0.485.
#   compression ratio      — coherent replies bottomed out at 0.405 (a repetitive
#                            but real disk-usage report); degenerate replies
#                            reached 0.391 and below.
_DEGENERATE_MIN_CHARS = 40           # non-whitespace chars needed to judge shape
_DEGENERATE_NONALNUM_SHARE_MAX = 0.40
_DEGENERATE_COMPRESSION_RATIO_MIN = 0.35

_WS_SPLIT = re.compile(r"\s+")
_NON_WORD = re.compile(r"\W+", re.UNICODE)

# Fenced code blocks are legitimately non-linguistic (a df table, a unit file, a
# command). They are removed before the degeneracy signals are measured so an
# answer that SHOWS output is never judged on that output's character mix.
_CODE_FENCE_RE = re.compile(r"```.*?```", re.S)


@dataclass
class CoherenceResult:
    """The verdict on one (prompt, output) pair.

    ``ok`` is the AND of every applicable check. ``reason`` names the FIRST
    failing check in plain language (None when ok) so a status line / journal
    entry can explain *why* an offload was denied without the reader re-deriving
    it. ``checks`` and ``metrics`` carry the full breakdown for the glass trace.
    """
    ok: bool
    reason: str | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)


def foreign_script_ratio(text: str) -> float:
    """Public: fraction of alphabetic characters outside the Latin block.

    Shared primitive (G7) — the runtime semantic-health detector
    (:mod:`intergen.semantic_health`) consumes this exact computation so the
    bring-up gate and the live detector never disagree on what "foreign script"
    means."""
    return _foreign_script_ratio(text)


def repetition_metrics(text: str) -> tuple[int, float]:
    """Public: (longest consecutive identical-token run, single-token dominance).

    Shared primitive (G7) — reused by :mod:`intergen.semantic_health`."""
    return _repetition_metrics(text)


def nonalnum_share(text: str) -> float:
    """Public: share of non-whitespace characters that are neither letter nor
    digit, measured OUTSIDE fenced code blocks.

    Shared primitive (G7) — the serving-floor quality gate
    (:func:`intergen.llm.LLMRouter.check_quality`) consumes this through
    :func:`degeneracy_reason` so "punctuation smear" has one definition. 0.0
    when there is nothing outside the fences to measure."""
    return _nonalnum_share(_unfenced(text))


def compression_ratio(text: str) -> float:
    """Public: zlib-compressed size over raw size of the text outside fenced
    code blocks — the character-level repetition signal that survives text with
    no whitespace in it.

    Shared primitive (G7), consumed through :func:`degeneracy_reason`. 1.0
    (maximally incompressible, i.e. no signal) when there is nothing to
    measure. ``zlib`` ships with Python; this adds no dependency."""
    raw = _unfenced(text).encode("utf-8")
    if not raw:
        return 1.0
    return len(zlib.compress(raw, 6)) / len(raw)


def degeneracy_reason(text: str) -> str | None:
    """Public: the single "this is not language" predicate, or None.

    Returns a plain-language reason when the text is a punctuation smear, a
    character-level loop, or otherwise carries no letter or digit at all —
    output that the whitespace-token repetition check (:func:`repetition_metrics`)
    passes, because every punctuation cluster is a distinct token. Character
    classes and compressibility only; no dictionary, no model, no new
    dependency.

    Both the bring-up gate (:func:`assess_coherence`, check 6) and the serving
    floor (:func:`intergen.llm.LLMRouter.check_quality`) call this one function
    so the two cannot drift apart."""
    prose = _unfenced(text)
    non_ws = [c for c in prose if not c.isspace()]
    if not non_ws:
        # Nothing but code fences and whitespace: not this predicate's to judge
        # (an empty answer is the substance check's finding).
        return None
    if not any(c.isalnum() for c in non_ws):
        # No letter or digit anywhere — a reply made only of punctuation is not
        # language at any length ('"' alone was served this way).
        return "output has no letter or digit outside code fences (not language)"
    if len(non_ws) < _DEGENERATE_MIN_CHARS:
        # Short replies are legitimately symbol-dense ("12.", "**1989**") and
        # compress badly regardless of content. Below this length the signals
        # carry no information, so the check abstains.
        return None
    share = _nonalnum_share(prose)
    if share >= _DEGENERATE_NONALNUM_SHARE_MAX:
        return (f"non-alphanumeric share {share:.2f} at or above "
                f"{_DEGENERATE_NONALNUM_SHARE_MAX:.2f} (punctuation smear)")
    ratio = compression_ratio(text)
    if ratio <= _DEGENERATE_COMPRESSION_RATIO_MIN:
        return (f"compression ratio {ratio:.2f} at or below "
                f"{_DEGENERATE_COMPRESSION_RATIO_MIN:.2f} "
                f"(character-level repetition)")
    return None


def _unfenced(text: str) -> str:
    """The text with fenced code blocks removed, stripped."""
    return _CODE_FENCE_RE.sub(" ", text or "").strip()


def _nonalnum_share(prose: str) -> float:
    """Share of non-whitespace characters in ``prose`` that are not alphanumeric.
    Takes ALREADY-unfenced text so a caller measuring several signals strips
    once."""
    non_ws = [c for c in prose if not c.isspace()]
    if not non_ws:
        return 0.0
    return sum(1 for c in non_ws if not c.isalnum()) / len(non_ws)


def _foreign_script_ratio(text: str) -> float:
    """Fraction of alphabetic characters outside the Latin block. 0.0 when the
    text has no alphabetic characters (e.g. a pure-numeric counting answer)."""
    alpha = 0
    foreign = 0
    for ch in text:
        if ch.isalpha():
            alpha += 1
            if ord(ch) > _LATIN_ALPHA_CEILING:
                foreign += 1
    if alpha == 0:
        return 0.0
    return foreign / alpha


def _longest_verbatim_prompt_span(output: str, prompt_text: str) -> int:
    """Length (chars) of the longest contiguous prompt substring that also
    appears in the output — the prompt-echo / system-prompt-leakage signal.

    Scans prompt windows from the echo threshold upward; returns the largest
    window length found in the output, capped at a bounded search so a large
    output can never make this quadratic-expensive. Normalizes whitespace on
    both sides so a re-wrapped echo still matches."""
    if not prompt_text or not output:
        return 0
    p = _WS_SPLIT.sub(" ", prompt_text).strip()
    o = _WS_SPLIT.sub(" ", output).strip()
    if len(p) < _PROMPT_ECHO_MIN_SPAN:
        return 0
    best = 0
    step = 8  # coarse stride; we only need to know a long span exists, not its exact length
    for start in range(0, len(p) - _PROMPT_ECHO_MIN_SPAN + 1, step):
        window = p[start:start + _PROMPT_ECHO_MIN_SPAN]
        if window in o:
            # Extend greedily to report a representative span length.
            end = start + _PROMPT_ECHO_MIN_SPAN
            while end < len(p) and p[start:end + 1] in o:
                end += 1
            best = max(best, end - start)
    return best


def _repetition_metrics(output: str) -> tuple[int, float]:
    """(longest consecutive run of an identical whitespace token, dominance
    fraction of the single most-common token). Both flag degenerate loops."""
    tokens = [t for t in _WS_SPLIT.split(output.strip()) if t]
    if not tokens:
        return (0, 0.0)
    # Longest consecutive identical-token run.
    longest = 1
    run = 1
    for i in range(1, len(tokens)):
        if tokens[i] == tokens[i - 1]:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    # Single-token dominance (only meaningful with enough tokens).
    dominance = 0.0
    if len(tokens) >= _TOKEN_DOMINANCE_MIN_TOKENS:
        counts: dict[str, int] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0) + 1
        dominance = max(counts.values()) / len(tokens)
    return (longest, dominance)


def assess_coherence(
    output: str,
    *,
    prompt_text: str = "",
    expected_keywords: list[str] | None = None,
    foreign_script_ratio_max: float = _FOREIGN_SCRIPT_RATIO_MAX,
    repeat_run_max: int = _REPEAT_RUN_MAX,
) -> CoherenceResult:
    """Judge whether ``output`` is a coherent completion — SHAPE-based only.

    ``prompt_text`` (system+user, concatenated) enables the prompt-echo check;
    pass it whenever available. ``expected_keywords`` are answer anchors for a
    known-answer prompt (at least one must appear); omit/empty to skip that
    check. The remaining thresholds are exposed so a caller with a different
    prompt shape can tune them, but the defaults are the tuned bring-up values.

    NEVER compares against another backend's output — see module docstring.
    """
    text = output or ""
    stripped = text.strip()
    keywords = expected_keywords or []

    checks: dict[str, bool] = {}
    metrics: dict[str, float] = {}

    # 1. substance
    checks["substance"] = len(stripped) >= _SUBSTANCE_MIN_CHARS
    metrics["length"] = float(len(stripped))

    # 2. expected keyword (skipped when none declared)
    if keywords:
        low = stripped.lower()
        hits = sum(1 for kw in keywords if kw.lower() in low)
        checks["expected_keyword"] = hits > 0
        metrics["keyword_hits"] = float(hits)
    else:
        checks["expected_keyword"] = True

    # 3. foreign-script ratio
    fsr = _foreign_script_ratio(text)
    metrics["foreign_script_ratio"] = round(fsr, 4)
    checks["foreign_script"] = fsr <= foreign_script_ratio_max

    # 4. verbatim prompt echo
    echo = _longest_verbatim_prompt_span(text, prompt_text)
    metrics["prompt_echo_span"] = float(echo)
    checks["no_prompt_echo"] = echo < _PROMPT_ECHO_MIN_SPAN

    # 5. repetition blowup
    longest_run, dominance = _repetition_metrics(text)
    metrics["max_repeat_run"] = float(longest_run)
    metrics["token_dominance"] = round(dominance, 4)
    checks["no_repetition"] = (
        longest_run <= repeat_run_max and dominance <= _TOKEN_DOMINANCE_MAX
    )

    # 6. not degenerate (punctuation smear / character-level loop)
    degenerate = degeneracy_reason(text)
    metrics["nonalnum_share"] = round(nonalnum_share(text), 4)
    metrics["compression_ratio"] = round(compression_ratio(text), 4)
    checks["not_degenerate"] = degenerate is None

    # First failing check drives the human-readable reason (stable order).
    reason = None
    _order = [
        ("substance", "output was empty or trivial"),
        ("expected_keyword",
         f"expected answer anchor(s) {keywords!r} absent from output"),
        ("foreign_script",
         f"foreign-script ratio {fsr:.2f} exceeds {foreign_script_ratio_max:.2f} "
         f"(token salad)"),
        ("no_prompt_echo",
         f"output echoes a {echo}-char verbatim span of the prompt "
         f"(prompt leakage)"),
        ("no_repetition",
         f"repetition blowup (longest identical-token run {longest_run}, "
         f"dominance {dominance:.2f})"),
        # Ordered last so every pre-existing verdict keeps the reason it always
        # reported; this check only names output the other five let through.
        ("not_degenerate", f"output is not language: {degenerate}"),
    ]
    ok = True
    for key, msg in _order:
        if not checks.get(key, True):
            ok = False
            reason = msg
            break

    return CoherenceResult(ok=ok, reason=reason, checks=checks, metrics=metrics)
