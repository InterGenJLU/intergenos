# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Everything that belongs to ONE conversation, in one object.

WHY THIS MODULE EXISTS. The assistant serves every browser tab, the console and
the desktop bus from a single router object. Until this module existed, that
router also HELD the conversation: one history, one consent record, one ingress
watermark, one set of pending offers, one grounding window, one handed-off set,
one turn index, one first-interaction flag. So there was exactly one
conversation on the machine no matter how many people or tabs were talking, and
the surfaces that say "new session" or "switch session" could not end one
without ending them all.

WHAT IS HERE. A plain container holding exactly the state a conversation reset
replaces, and a factory that builds a fresh one. Nothing in this module makes
policy: the router still decides what a turn does, and the frontends still
decide which conversation a turn belongs to. The container's only rule is that
two conversations never share a mutable object — every list, set and record is
built per conversation, so a decision taken in one cannot appear in another.

WHO OWNS ONE. The browser server keeps one per connected client, replaced when
that client starts a new conversation and cleared when it switches to another.
The desktop bus keeps one for its own conversation. A router constructed
directly — by a test, or by any single-frontend caller — is given one at
construction, because a router that is not shared is not multiplexed.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any

from intergen.interfaces.types import Message

__all__ = ["ConversationState", "ExecutedOutput", "command_line",
           "new_conversation_state"]


@dataclass(frozen=True)
class ExecutedOutput:
    """One run on the record of what ran: the registry tool that ran, the
    command line it ran ("" for a tool that runs none, a run recorded with no
    call, or an output no command of the call can be shown to have printed),
    and what it returned, its full content and the summary the model was given
    when the tool made one. A call that runs a sequence of commands (the
    package tool's update: the index refresh, then the upgrade) is one run per
    command that ran, each holding its own output."""

    tool: str
    command: str
    texts: tuple[str, ...]


def command_line(call: Any) -> str:
    """The command line a tool call runs, read from the builder that runs it:
    run_command's own command, the pkm argument lists the package tool builds
    (its update action runs `pkm sync && pkm upgrade --all --yes`), the
    systemctl line of a service action; "" for a tool that runs no command
    line, or for no call."""
    if call is None:
        return ""
    args = call.arguments if isinstance(call.arguments, dict) else {}
    if call.name == "run_command":
        return str(args.get("command", "") or "")
    if call.name == "manage_packages":
        from intergen.tools.manage_packages import ManagePackagesTool
        commands = ManagePackagesTool().build_commands(args, pkm="pkm")
        return " && ".join(" ".join(argv) for argv in commands or ())
    if call.name == "manage_services":
        from intergen.tool_registry import handoff_command
        return handoff_command(call)
    return ""


def _pkm_words(text: str) -> list[str] | None:
    """The words of a pkm command line as the package tool's builder names
    them, the program by its base name (execute runs pkm by its vendor path);
    None for text that is no pkm command line."""
    try:
        words = shlex.split(text)
    except ValueError:
        return None
    if not words or words[0].rsplit("/", 1)[-1] != "pkm":
        return None
    return ["pkm", *words[1:]]


def _names_the_unrun(line: str, unrun: list[list[str]]) -> bool:
    """Is `line` the package tool's note that the commands `unrun` were not
    run? A failed privileged run's report (ToolRegistry._privileged_failure_
    result) follows the content's last line on that same line, after " [", so
    the note is read with and without it."""
    from intergen.tools.manage_packages import SEQUENCE_NOT_RUN
    if not line.startswith(SEQUENCE_NOT_RUN):
        return False
    named = line[len(SEQUENCE_NOT_RUN):]
    for text in (named, named.split(" [", 1)[0]):
        segments = text.split(" && ")
        if len(segments) == len(unrun) and all(
                _pkm_words(segment) == argv
                for segment, argv in zip(segments, unrun)):
            return True
    return False


def _package_sequence_runs(call: Any, content: Any) -> list[tuple[str, str]] | None:
    """Each command of a package call's sequence that ran, as (its command
    line, its own output), read from the content the package tool returned;
    None for a call that runs one command or none.

    One call of the package tool's update runs two commands under one approval,
    the index refresh and then the upgrade, and execute writes each command's
    output under a "$ <command>" line of its own, stops at the first command
    that fails and names the ones it did not run. Kept as one run, the
    refresh's own "Package index updated." and "All packages are up to date."
    were read as the upgrade's report, and "Your system is now up to date."
    after an update whose upgrade failed read as a result that was produced.

    Every line is given to the command it came from or to none. A content
    that does not open with the first command's line shows no command of the
    sequence running (the privileged runner refused it, or the tool stopped
    before it), so it is one run with no command line. A command named in the
    note of commands not run did not run, wherever a line naming it appears,
    and a command's output starts at the LAST line naming it: a line of an
    earlier command's output that reads like the next command's own line is
    still that earlier command's output."""
    if call is None or getattr(call, "name", "") != "manage_packages":
        return None
    from intergen.tools.manage_packages import (
        ManagePackagesTool, SEQUENCE_COMMAND_LINE,
    )
    args = call.arguments if isinstance(call.arguments, dict) else {}
    commands = ManagePackagesTool().build_commands(args, pkm="pkm")
    if not commands or len(commands) < 2 or not isinstance(content, str):
        return None

    def names(line: str, argv: list[str]) -> bool:
        return (line.startswith(SEQUENCE_COMMAND_LINE)
                and _pkm_words(line[len(SEQUENCE_COMMAND_LINE):]) == argv)

    lines = content.split("\n")
    if not names(lines[0], commands[0]):
        return [("", content)]
    ran = len(commands)
    for index in range(1, len(commands)):
        if any(_names_the_unrun(line, commands[index:]) for line in lines):
            ran = index
            break
    starts = [0]
    for argv in commands[1:ran]:
        named_at = [at for at in range(starts[-1] + 1, len(lines))
                    if names(lines[at], argv)]
        if not named_at:
            break
        starts.append(named_at[-1])
    ends = starts[1:] + [len(lines)]
    return [(" ".join(commands[index]),
             "\n".join(lines[start + 1:end]).strip("\n"))
            for index, (start, end) in enumerate(zip(starts, ends))]


@dataclass
class ConversationState:
    """The state of one conversation.

    The fields are exactly what ``ConversationRouter.reset_conversation_state``
    replaces, under the names the router used when it owned them:

    ``history``               the model-facing buffer the prompt is built from
    ``trust_state``           allow/deny decisions the user made for this
                              conversation (D-008 RFC section 5.3)
    ``ingress_tracker``       the per-turn and per-conversation ingress
                              watermarks (D-008 RFC section 5.1)
    ``pending_action_offer``  an offered command awaiting a yes or no
    ``pending_ipv6_offer``    the follow-up IPv6 offer awaiting a yes or no
    ``pending_memory_offer``  an offered preference awaiting a yes or no
    ``pending_search_offer``  the question an offered web search would answer
                              (a live-data question, or a search the model
                              wanted and the person had not asked for),
                              awaiting a yes or no
    ``action_offer_ttl``      turns the preventive-grounding window stays open
    ``offer_in_recent_history`` whether that window is open for THIS turn
    ``offer_topic_terms``     the offered command's content words
    ``handed_off_commands``   commands already declined or handed off here
    ``transcript``            every exchange this conversation has had, in
                              order and verbatim, never trimmed
    ``executed_outputs``      the output of every tool that ran in this
                              conversation, failed or not, in order, each
                              beside its tool and command line
    ``turn_index``            the relevance index over this conversation's turns
    ``first_interaction``     whether nothing has been said yet
    ``memory_session_id``     the long-term memory session this conversation
                              records into, or None when memory is not wired
    """

    history: list[Message] = field(default_factory=list)
    trust_state: Any = None
    ingress_tracker: Any = None
    pending_action_offer: tuple[str, str, str] | None = None
    pending_ipv6_offer: str | None = None
    pending_memory_offer: tuple[str, str, str, str] | None = None
    # The question a standing web-search offer would answer. Held because the
    # offer and the acceptance are two different turns: without it the "yes"
    # arrives with nothing behind it and is routed as a brand-new sentence,
    # which is how three accepted offers in one person's first three days ran
    # no search at all.
    pending_search_offer: str | None = None
    # The follow-up action path (2026-10-20). ``last_lapsed_offer`` is the
    # action offer that lapsed THIS turn: a turn that is neither yes nor no
    # lapses the offer before the follow-up resolver runs, and "run that
    # install" names exactly the command that just lapsed. Cleared at the
    # start of every turn. ``pending_referent_gap`` is the ONE question the
    # resolver asked when a follow-up named nothing it could find —
    # (verb, kind), so a bare name on the next turn answers it. Cleared by
    # any other turn.
    last_lapsed_offer: tuple | None = None
    pending_referent_gap: tuple[str, str] | None = None
    action_offer_ttl: int = 0
    offer_in_recent_history: bool = False
    offer_topic_terms: frozenset[str] = frozenset()
    handed_off_commands: set[str] = field(default_factory=set)
    # Every exchange, in order, verbatim, unbounded — (user_input, response).
    #
    # `history` is the MODEL-FACING buffer and is trimmed in place to the last
    # twenty messages, so the opening exchange is destroyed once a conversation
    # is ten turns old. `turn_index` holds verbatim exchanges too, but it is
    # queried by semantic similarity and only ever holds a turn the embedder
    # managed to embed — so neither can answer an ORDINAL question about the
    # conversation ("what was my first question"), and the assistant answered
    # one with a later turn. This list is the record those questions read: no
    # model, no embedder, no trim. It is display/answer state only and is never
    # placed in a prompt, so it cannot grow the model's context.
    transcript: list[tuple[str, str]] = field(default_factory=list)
    # What every tool that RAN in this conversation returned, failed or not,
    # verbatim and in order, each run beside the tool and the command line that
    # produced it — the code-owned record of what this conversation actually
    # executed. The claim screen reads it (safety.screen_execution_claim): an
    # answer quoting an output held here gives an account of a result that was
    # produced, as long as it names the tool that produced it, and an output
    # found only in an earlier ANSWER was not necessarily produced at all (a
    # how-to answer can show what a command would print). Written by
    # record_execution at every place a result comes back from the tool
    # registry, with the call that produced it; never placed in a prompt.
    executed_outputs: list[ExecutedOutput] = field(default_factory=list)
    turn_index: Any = None
    first_interaction: bool = True
    memory_session_id: str | None = None

    def __post_init__(self) -> None:
        # Built here rather than defaulted in the annotation so that two
        # conversations can never be handed the same record by accident — the
        # defect this whole object exists to close.
        from intergen.interfaces.provenance import (
            ConversationTrustState, IngressTracker,
        )
        if self.trust_state is None:
            self.trust_state = ConversationTrustState()
        if self.ingress_tracker is None:
            self.ingress_tracker = IngressTracker()

    def clear(self) -> None:
        """Return this conversation to the state a fresh one starts in.

        In place, not by replacement: the frontends and the router hold
        references to this object, and handing back a new one would leave a
        caller writing into the conversation the user just ended.

        The turn index and the memory session are NOT cleared here — both are
        built from components the router owns, so the router replaces them (see
        ``ConversationRouter.reset_conversation_state``). Everything else a
        conversation carries is cleared here, in one place, so a slot added
        later is cleared by the same code that clears the rest.
        """
        self.history.clear()
        self.trust_state.reset()
        self.ingress_tracker.reset_conversation()
        self.clear_offers()
        # Loop-killer set is per-conversation: a fresh conversation may re-offer
        # an action a prior (discarded) one declined.
        self.handed_off_commands.clear()
        # The verbatim transcript is per-conversation like everything else here:
        # a discarded conversation's first question must not be readable as the
        # fresh one's.
        self.transcript.clear()
        # So is the record of what ran: an output a discarded conversation
        # produced is no result of the fresh one's.
        self.executed_outputs.clear()
        self.first_interaction = True

    def record_execution(self, result: Any, call: Any = None) -> None:
        """Keep what a tool that RAN returned, failed or not: its full content,
        and the summary the model was given when the tool made one, beside the
        tool and the command line of `call`, the call that produced it; a call
        that ran a sequence of commands as one run per command that ran, each
        beside its own output (_package_sequence_runs). A result the gate
        refused, the person denied or the safety classifier blocked ran
        nothing, so nothing of it is kept."""
        if result is None or not getattr(result, "executed", False):
            return
        if getattr(result, "blocked", False):
            return
        tool = str(getattr(result, "name", "") or getattr(call, "name", "") or "")
        content = getattr(result, "content", None)
        summary = getattr(result, "model_summary", None)
        runs = _package_sequence_runs(call, content)
        split = runs is not None
        if runs is None:
            runs = [(command_line(call), content)]
        for index, (command, output) in enumerate(runs):
            # The summary is of the whole result, so the last run keeps it.
            last = index == len(runs) - 1
            texts = tuple(text for text in (output, summary if last else None)
                          if isinstance(text, str) and text.strip())
            # A command of a sequence that ran is on the record even when it
            # printed nothing, so the latest run is the command that ran last.
            if texts or (split and command):
                self.executed_outputs.append(ExecutedOutput(
                    tool=tool, command=command, texts=texts))

    def clear_offers(self) -> None:
        """Withdraw every offer awaiting a yes or no, and the grounding window
        that rides on one.

        Part of ``clear``, and also what emptying the model buffer needs beside
        it: an offer outliving the buffer it was made in is still taken up by
        a bare "yes" the person types afterwards.
        """
        # F3 (offer/accept fix, 2026-07-01): ALL offer slots, not just memory —
        # a "yes" in a fresh conversation must never bind to an action or ipv6
        # offer staged in a discarded one (the stale-bind-across-reset hole).
        self.pending_action_offer = None
        self.pending_ipv6_offer = None
        self.pending_memory_offer = None
        self.pending_search_offer = None
        self.last_lapsed_offer = None
        self.pending_referent_gap = None
        # M3(ii) option B + PI-Z29: the preventive-grounding window is
        # per-conversation — a discarded conversation's offer must not inject a
        # no-dispatch note into a fresh one. Clear the TTL AND the topic terms.
        self.action_offer_ttl = 0
        self.offer_in_recent_history = False
        self.offer_topic_terms = frozenset()


def new_conversation_state(embedder: Any = None,
                           window_turns: int = 10) -> ConversationState:
    """Build a conversation with its own record of everything.

    ``embedder`` is the shared embedding client the router was given. When one
    is present the conversation gets its OWN relevance index over its own turns,
    so a retrieval in one conversation cannot surface another's exchanges. A
    failure to build the index degrades this conversation to its raw history
    window and is never allowed to take a caller down — the same guarded
    construction the router used when it owned the index.
    """
    state = ConversationState()
    if embedder is not None:
        try:
            from intergen.memory import SessionTurnIndex
            state.turn_index = SessionTurnIndex(
                embedder, window_turns=max(1, window_turns))
        except Exception:  # noqa: BLE001 — memory must never be a startup risk
            import logging
            logging.getLogger(__name__).warning(
                "M2b session memory disabled for this conversation: "
                "SessionTurnIndex failed to construct; the raw history window "
                "is the only context.", exc_info=True)
            state.turn_index = None
    return state
