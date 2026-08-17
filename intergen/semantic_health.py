# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Runtime semantic-health detector — corruption screen at the completion boundary.

Why
---
A backend can serve fluent text for a short turn and degrade on a long one — the
Intel ANV Vulkan-F16 class only garbled past ~600 generated tokens, with zero
errors logged while it served salad. The silence is the defect. This detector
runs on EVERY served completion (all consumers: web / CLI / panel / D-Bus share
one :class:`LLMRouter`) and flags the corruption signature, so a degraded
accelerator is visible instead of quietly producing garbage.

What happens with a flag is a REPORT, not a demotion:
:mod:`intergen.engine_health` counts sustained flags and the daemon surfaces the
condition loudly in its status and journal. It does not move the user's model
onto the CPU on the strength of this heuristic (decided 2026-07-31).

**Scope is CORRUPTION ONLY — never correctness or quality.** Whether the answer is
*right* is the claim-screen's job; this asks only whether the bytes are structural
garbage. Four deterministic checks, all cheap code, no model judging a model:

1. ``foreign_script_flood`` — non-Latin script sprayed into a response that the
   conversation did not call for. EXEMPTIONS: scripts the user's own turns already
   use (a user writing Chinese gets Chinese back), and backtick-FENCED spans
   (code / verbatim) measured separately. The system prompt instructs the model to
   FENCE any legitimate foreign-script or verbatim-technical output — corrupt
   output will not follow the convention, desired output will.
2. ``system_prompt_echo`` — a verbatim run of >= 8 tokens from the LIVE system
   prompt (we own that text; exact match). Short identity phrasing ("I'm InterGen")
   passes under the length threshold. There is deliberately NO in-band exemption
   marker — an "intentional quote" escape would be a prompt-injection bypass of the
   screen (decided).
3. ``repetition_blowup`` — degenerate n-gram / token loops, generous thresholds,
   fenced spans measured separately (a code block with repeated lines is fine).
4. ``charset_sanity`` — broken unicode (U+FFFD), control characters, or mid-token
   script fusion (a Latin word welded to a CJK glyph with no boundary). Conservative.
5. ``short_nonsense`` — a SHORT reply with no grammatical spine: no verb, no
   number, no identifier, no terminal punctuation. The word-bag class ("As much
   least pragmatic unpaid tool" answering "Is sshd enabled?"), which every other
   check passes because it needs either length or structural damage. Shape only,
   never meaning.

Checks 1 and 3 consume the shared coherence primitives in
:mod:`intergen.coherence`, so "foreign script" and "repetition" have one
definition. The detector is pure and side-effect-free: it returns the flags; the
caller
(:mod:`intergen.llm`) attaches them to the result surface and records the RAW
response + which check fired in the glass trace.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from intergen.coherence import foreign_script_ratio, repetition_metrics

# Flag names — the interface contract's vocabulary (LLMResponse.semantic_flags).
FLAG_FOREIGN_SCRIPT = "foreign_script_flood"
FLAG_SYSTEM_PROMPT_ECHO = "system_prompt_echo"
FLAG_REPETITION = "repetition_blowup"
FLAG_CHARSET = "charset_sanity"
FLAG_SHORT_NONSENSE = "short_nonsense"

# Thresholds — tuned to catch unambiguous corruption without flagging healthy
# output (a false flag needlessly alarms the user about a working machine).
_FOREIGN_FLOOD_RATIO = 0.15          # unfenced non-Latin alpha ratio over a Latin turn
_USER_FOREIGN_EXEMPT_RATIO = 0.05    # the user's turns already use non-Latin script
_ECHO_MIN_TOKENS = 8                 # verbatim system-prompt run length that flags
_REPEAT_RUN_MAX = 12                 # generous: identical token >12x in a row
_TOKEN_DOMINANCE_MAX = 0.50          # generous: one token > half a multi-token output
_NGRAM_LOOP_MIN = 6                  # a bigram repeated this many times = a loop
_CHARSET_FUSION_MIN = 2              # this many Latin<->foreign fusions = corruption
_SHORT_NONSENSE_MAX_WORDS = 8        # above this, the other checks have enough text

# A sentence has a spine. These are the closed-class words that carry one in the
# replies this floor serves — copulas, auxiliaries, modals, and the handful of
# state verbs a system answer is built from. The list is deliberately SHORT and
# closed: it exists to prove a reply IS a sentence, never to judge what it says.
_SPINE_WORDS = frozenset("""
is are was were be been being am s re ve ll has have had do does did done
can could will would shall should may might must let lets need needs
run runs running ran use uses used using see sees show shows shown
find finds found return returns returned report reports reported
install installed installs enable enabled disable disabled start started
stop stopped exist exists list lists set sets get gets make makes made
active inactive available unavailable enabled disabled running stopped
""".split())

_FENCE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_FENCE_INLINE = re.compile(r"`[^`\n]+`")
_WORD = re.compile(r"\S+")
_LATIN_ALPHA_CEILING = 0x024F        # matches coherence's Latin block ceiling
_WELD_SYMBOLS = set("${}|\\^~")      # shell/structural symbols welded mid-token = corruption


@dataclass
class SemanticHealthResult:
    """Flags (empty == clean) plus per-check detail for the glass record."""
    flags: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.flags


def _strip_fences(text: str) -> tuple[str, list[str]]:
    """Return (text with backtick-fenced spans removed, the removed spans).

    Fenced spans are where legitimate verbatim / foreign-script / repeated content
    lives, so the flood + repetition + charset checks run on the UNFENCED remainder
    and the model is instructed to fence such content."""
    fenced: list[str] = []

    def _grab(m: re.Match) -> str:
        fenced.append(m.group(0))
        return " "

    t = _FENCE_BLOCK.sub(_grab, text)
    t = _FENCE_INLINE.sub(_grab, t)
    return t, fenced


def _check_foreign_script(unfenced: str, conversation_texts: list[str]) -> tuple[bool, dict]:
    ratio = foreign_script_ratio(unfenced)
    user_ratio = foreign_script_ratio(" ".join(conversation_texts))
    # Exempt when the user's own turns already carry non-Latin script (they asked
    # for / are conversing in it).
    exempt = user_ratio >= _USER_FOREIGN_EXEMPT_RATIO
    tripped = ratio >= _FOREIGN_FLOOD_RATIO and not exempt
    return tripped, {"ratio": round(ratio, 4), "user_ratio": round(user_ratio, 4),
                     "exempt": exempt}


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text)


def _check_system_prompt_echo(response: str, system_prompt: str) -> tuple[bool, dict]:
    if not system_prompt:
        return False, {"max_run": 0}
    sp = _tokens(system_prompt)
    rp = _tokens(response)
    if len(sp) < _ECHO_MIN_TOKENS or len(rp) < _ECHO_MIN_TOKENS:
        return False, {"max_run": 0}
    # Any system-prompt n-gram of the threshold length appearing verbatim in the
    # response is an echo. Index the response's n-grams once for a linear scan.
    resp_grams: set[tuple[str, ...]] = {
        tuple(rp[i:i + _ECHO_MIN_TOKENS])
        for i in range(len(rp) - _ECHO_MIN_TOKENS + 1)
    }
    matches = sum(
        1 for i in range(len(sp) - _ECHO_MIN_TOKENS + 1)
        if tuple(sp[i:i + _ECHO_MIN_TOKENS]) in resp_grams
    )
    return matches > 0, {"echo_grams": matches, "gram_len": _ECHO_MIN_TOKENS}


def _check_repetition(unfenced: str) -> tuple[bool, dict]:
    longest_run, dominance = repetition_metrics(unfenced)
    toks = _tokens(unfenced)
    # A short-n-gram loop (e.g. "of of of", "the cat the cat") that the
    # single-token metrics miss.
    ngram_loop = 0
    if len(toks) >= 4:
        bigrams: dict[tuple[str, str], int] = {}
        for i in range(len(toks) - 1):
            bg = (toks[i], toks[i + 1])
            bigrams[bg] = bigrams.get(bg, 0) + 1
        ngram_loop = max(bigrams.values())
    tripped = (longest_run > _REPEAT_RUN_MAX or dominance > _TOKEN_DOMINANCE_MAX
               or ngram_loop >= _NGRAM_LOOP_MIN)
    return tripped, {"max_run": longest_run, "dominance": round(dominance, 4),
                     "ngram_loop": ngram_loop}


def _is_foreign_alpha(ch: str) -> bool:
    return ch.isalpha() and ord(ch) > _LATIN_ALPHA_CEILING


def _check_charset(unfenced: str) -> tuple[bool, dict]:
    replacement = unfenced.count("�")
    control = 0
    for ch in unfenced:
        if ch in ("\n", "\t", "\r"):
            continue
        if unicodedata.category(ch) in ("Cc", "Cf") and ch not in ("‍",):
            control += 1
    # Token-level welding anomalies — the corruption where a decode goes off the
    # rails and fuses fragments that a healthy tokenizer would never adjoin:
    #   * script fusion: a Latin alpha char directly adjacent to a foreign-script
    #     alpha char inside a token ("Austin栾");
    #   * symbol weld: a structural symbol ({ } $ | \ ^ ~ < >) embedded between
    #     alphanumerics inside a token ("TOPMk${conskomland").
    fusion = 0
    for tok in _tokens(unfenced):
        for a, b in zip(tok, tok[1:]):
            latin_a = a.isalpha() and ord(a) <= _LATIN_ALPHA_CEILING
            latin_b = b.isalpha() and ord(b) <= _LATIN_ALPHA_CEILING
            if (latin_a and _is_foreign_alpha(b)) or (_is_foreign_alpha(a) and latin_b):
                fusion += 1
        # An interior weld symbol inside a token that also carries letters/digits
        # ("TOPMk${conskomland") — never a normal word, always a decode gone wrong.
        interior = tok[1:-1]
        if any(ch in _WELD_SYMBOLS for ch in interior) and any(ch.isalnum() for ch in tok):
            fusion += 1
    tripped = replacement > 0 or control > 0 or fusion >= _CHARSET_FUSION_MIN
    return tripped, {"replacement": replacement, "control": control, "fusion": fusion}


def _check_short_nonsense(text: str) -> tuple[bool, dict]:
    """A SHORT reply with no grammatical spine — the word-bag class.

    MEASURED: "As much least pragmatic unpaid tool" answering "Is sshd
    enabled?"; "plepp" answering a package question; "T < ( <" answering a
    removal request. Real words (or near-words), no repetition, no foreign
    script, no broken bytes — and no sentence. Every other check in this module
    needs either length or structural damage, so this class passed them all.

    The rule reads SHAPE, never meaning, which is what keeps it inside this
    module's scope and deterministic: a reply of at most eight words that
    carries no spine word, no digit, no identifier character and no terminal
    punctuation is not a sentence. Each of those four escapes is there because a
    real short reply needs it — "sshd is enabled" (spine), "979 packages"
    (digit), "/etc/fstab" (identifier), "Pragmatic unpaid tool." (a finished
    sentence, however odd, is the correctness screens' business).

    Calibrated on 656 sealed replies (126 of them eight words or shorter): 11
    flagged, all 11 judge-failed or judge-flagged, zero false positives.
    """
    t = (text or "").strip()
    words = t.split()
    detail = {"words": len(words), "has_verb": False, "has_digit": False,
              "has_identifier": False, "terminated": False, "latin": True}
    if not words or len(words) > _SHORT_NONSENSE_MAX_WORDS:
        return False, detail
    # SCRIPT GUARD. Everything below reads a whitespace-delimited Latin
    # sentence: word counts, an English spine list, ASCII terminators. A reply
    # written in a script that does not put spaces between words has none of
    # those properties and would flag on sight — a legitimate Chinese answer to
    # a Chinese question is the measured case. Non-Latin output is
    # foreign_script_flood's question, and it already knows when the
    # conversation called for that script.
    #
    # The deferral is only honest at the ratio that check actually acts on.
    # Measured by cross-review on the earlier `> 0` form: appending ONE Cyrillic
    # letter to this module's own worked example took the reply from flagged to
    # no flags at all — 3.2% of its alphabetic characters, far under the flood
    # threshold, so nothing else looked at it either. A stray non-Latin glyph is
    # itself a common corruption signature, which put the escape hatch on
    # exactly the population this check targets.
    if foreign_script_ratio(t) >= _FOREIGN_FLOOD_RATIO:
        detail["latin"] = False
        return False, detail
    detail["has_digit"] = any(ch.isdigit() for ch in t)
    detail["has_identifier"] = (
        any(ch in t for ch in "`/\\_=:%")
        or any("." in w[1:-1] or "-" in w[1:-1] for w in words))
    detail["terminated"] = t[-1] in ".!?\u3002\uff01\uff1f\u2026"
    bare = [re.sub(r"[^a-z']", "", w.lower()) for w in words]
    detail["has_verb"] = any(w in _SPINE_WORDS for w in bare)
    tripped = not (detail["has_verb"] or detail["has_digit"]
                   or detail["has_identifier"] or detail["terminated"])
    return tripped, detail


def assess_semantic_health(
    response_text: str,
    *,
    system_prompt: str = "",
    conversation_texts: list[str] | None = None,
) -> SemanticHealthResult:
    """Screen one served completion for corruption. Returns the tripped flags.

    ``system_prompt`` is the LIVE assembled system prompt (for the echo check);
    ``conversation_texts`` are the user's turn texts (for the foreign-script
    exemption). CORRUPTION ONLY — never a correctness/quality judgment.
    """
    conv = conversation_texts or []
    unfenced, fenced = _strip_fences(response_text or "")

    flags: list[str] = []
    detail: dict = {"fenced_spans": len(fenced)}

    tripped, d = _check_foreign_script(unfenced, conv)
    detail[FLAG_FOREIGN_SCRIPT] = d
    if tripped:
        flags.append(FLAG_FOREIGN_SCRIPT)

    tripped, d = _check_system_prompt_echo(response_text or "", system_prompt)
    detail[FLAG_SYSTEM_PROMPT_ECHO] = d
    if tripped:
        flags.append(FLAG_SYSTEM_PROMPT_ECHO)

    tripped, d = _check_repetition(unfenced)
    detail[FLAG_REPETITION] = d
    if tripped:
        flags.append(FLAG_REPETITION)

    tripped, d = _check_charset(unfenced)
    detail[FLAG_CHARSET] = d
    if tripped:
        flags.append(FLAG_CHARSET)

    # Read on the ORIGINAL text, not the unfenced remainder: a fenced span is
    # exactly the identifier evidence that makes a short reply substantive, and
    # stripping it first would manufacture the very shape this check names.
    tripped, d = _check_short_nonsense(response_text or "")
    detail[FLAG_SHORT_NONSENSE] = d
    if tripped:
        flags.append(FLAG_SHORT_NONSENSE)

    return SemanticHealthResult(flags=flags, detail=detail)
