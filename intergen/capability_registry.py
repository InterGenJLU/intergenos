# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The single, interface-aware source of truth for InterGen's own capability facts.

Why this exists (the capability single-source)
----------------------
Three separate places told the user (or told the MODEL, so it could tell the
user) what InterGen can do, and each kept its OWN hand-written copy of the facts:

  * the router's tool-capability answers ("can you manage services?") — the human
    phrase per tool lived inline in ``router._TOOL_CAP_Q_SPECS``;
  * the model's system-prompt capability guard — the privileged-action + package-
    manager facts lived inline in ``persona.SYSTEM_CAPABILITY_GUARD``;
  * the web review card — the concrete package-update command lived inline in
    ``web_server._card_action_description``.

Three copies of the same facts drift: one says ``pkm sync``, another still says
``pkm update``; one lists a tool the other has dropped. When a capability ANSWER
and the model's system PROMPT disagree, the user is told two different truths
about the same machine — exactly the incoherence the web review-gate work is
closing. This module is the ONE place those facts live; every surface reads from
here, so they cannot diverge, and a drift-guard test pins the router's phrase
table to this table.

Grounding discipline
--------------------
PRESENCE (does a tool exist?) and GATED-ness (does it reach the consent gate?)
are NOT stored here — they are read live from the tool registry / each tool's
declared SafetyTier at answer time (see ``router._answer_tool_capability`` /
``_tool_is_consent_gated``), so this file can never over- or under-promise the
real dispatch posture. What lives here is the user-LANGUAGE layer: the plain
phrase for each capability, the package-manager command names, and the assembled
system-prompt facts — the wording, not the truth of presence.

Interface awareness
-------------------
The consent SURFACE genuinely differs by interface — the web UI renders a review
CARD, a console/desktop turn renders a confirmation PROMPT — so the "you'll be
asked first" tail is a real per-interface fact, exposed through
``confirmation_tail(interface)``. The capability set itself does not differ by
interface (the same tools dispatch through the same authorization boundary),
which this file states plainly rather than inventing a difference that isn't
there.
"""

from __future__ import annotations

# ── Package-manager command facts (single source; the pkm sync change made `sync` canonical) ──
# Consumed by the system-prompt guard AND the web review card, so the command the
# model describes and the command the card shows are byte-identical.
PKM_INDEX_REFRESH_CMD = "pkm sync"
PKM_UPGRADE_CMD = "pkm upgrade"
# The one command line a package-update review card / handoff shows.
PKM_UPDATE_COMMAND = f"{PKM_INDEX_REFRESH_CMD} && {PKM_UPGRADE_CMD}"

# ── The canonical user-language phrase for each user-facing capability ──
# ORDER MATTERS for the router's first-match spec table: verb+object
# disambiguators are sequenced (open-a-file → read_file before open_application;
# read-a-pdf → analyze_file before read_file), so this is an ordered tuple, not a
# dict. The router pairs each phrase with its detection regex; the PHRASE is
# owned here.
TOOL_CAPABILITY_PHRASES: tuple[tuple[str, str], ...] = (
    ("manage_services", "start, stop, and restart system services"),
    ("open_application", "open apps and programs"),
    ("read_file", "read files"),
    ("analyze_file", "analyze files like images, PDFs, and documents"),
    ("write_file", "create, write, and edit files"),
    ("manage_packages", "install, remove, and update software packages"),
    ("run_command", "run terminal commands"),
    ("take_screenshot", "take a screenshot of your screen"),
)

_PHRASE_BY_TOOL = dict(TOOL_CAPABILITY_PHRASES)


def phrase(tool_name: str) -> str | None:
    """The canonical user-language phrase for a capability, or None if the tool
    has no registered phrase (a capability question about it falls through to the
    normal path rather than answering with an invented phrase)."""
    return _PHRASE_BY_TOOL.get(tool_name)


def confirmation_tail(interface: str | None = None) -> str:
    """The 'you'll be asked first' tail for a consent-gated capability answer,
    phrased for the interface that will actually ask. Web renders a review CARD;
    console/desktop renders a confirmation PROMPT — a real per-interface fact, so
    the answer names the surface the user will really see. Unknown/None →
    the neutral phrasing (true on every interface)."""
    if interface == "web":
        return " — you'll get a review card to approve before I make any change"
    return " — you'll get a confirmation prompt before I make any change"


def build_system_capability_guard(interface: str | None = None) -> str:
    """Assemble the model's system-prompt capability guard from the SAME facts the
    router answers with, so the model and the deterministic answers speak one
    voice (the capability single-source). Injected on a system-category turn on the locked-floor lane to
    keep a small model from inventing `sudo` folklore and false capability-denial.

    The package-manager command names come from the shared constants above, so a
    change to the canonical commands updates the guard, the router answers, and
    the web card together. `interface` is accepted for future per-interface
    phrasing; the capability FACTS are identical across interfaces (same tools,
    same authorization boundary), which is stated rather than faked."""
    return (
        "IMPORTANT — the true facts about your OWN capabilities on this "
        "system; answer within them and do not contradict or embellish them:\n"
        "- You CAN carry out privileged, system-changing actions (updates, "
        "service changes, installs). They are dispatched through the system's "
        "own authorization prompt — NEVER tell the user to use `sudo`, and "
        "NEVER say you cannot run commands or act on the system directly.\n"
        f"- The package manager is pkm: `{PKM_INDEX_REFRESH_CMD}` refreshes the "
        f"package index and `{PKM_UPGRADE_CMD}` applies available updates. Do "
        "not invent other commands or mechanisms.\n"
        "- A 'privileged' or 'state-changing' notice is the confirmation and "
        "authorization step that runs BEFORE such an action — it is NOT a "
        "refusal, an error, or an inability, and it does not mean the system "
        "is in a special 'privileged mode'.\n"
        "Answer the question truthfully within these facts; if unsure of a "
        "specific detail, say so plainly rather than guessing."
    )
