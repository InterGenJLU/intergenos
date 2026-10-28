# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen tool registry — discovers, registers, and executes tools.

Ported from a prior internal AI assistant project. Simplified: tools are class-based
(BaseTool subclasses) rather than module-based, and safety classification
is built into each tool.

D-008 RFC v1.0 dispatcher gate integration:
every execute() call passes through `intergen.provenance.verify_tool_call`
before the tool runs. The gate enforces RFC §3 provenance taxonomy,
§5.1 ingress-tool watermark escalation, §5.3 no-fallback policy,
§6 tool risk classification, and the §7 review modal handoff via the
`review_callback` keyword argument. RFC §9 audit log is written on every
dispatch decision.

I-027 closure: the prior path classified SafetyTier.CONFIRM but never
enforced it (the gate did not exist). With this commit, CONFIRM-equivalent
behavior is delivered by the gate's hold_for_review action on the
behavior matrix. The classify_safety surface is preserved for the BLOCKED
tier (which remains a tool-level refusal independent of provenance).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shlex
import shutil
import stat as stat_module
import subprocess
import time
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from importlib import import_module
from typing import Any, Callable, NamedTuple

# Canonical pkexec runner path — installed by packages/ai/intergen/build.sh
# from intergen/data/intergen-privileged-runner per the build-system
# coordinator's 49a585ca T0-4-E integration pkexec gate artifacts. The
# constant lives here so the runtime import contract is colocated with
# the dispatcher that invokes it.
_PKEXEC_RUNNER_PATH = "/usr/bin/intergen-privileged-runner"

# The tool that asks this account's own systemd user manager to start a
# short-lived unit. Decided 2026-08-24: the privileged runner is started this
# way rather than as a direct child of the daemon. The daemon's unit sets
# NoNewPrivileges=yes, which is inherited by every child and cannot be cleared,
# and which makes the kernel ignore pkexec's setuid bit — pkexec then refuses
# before PolicyKit is ever contacted. The user manager does not run under that
# flag, so a unit it starts begins from the manager's own context. Nothing about
# the daemon's hardening changes; only who starts the runner does.
# ABSOLUTE, deliberately (2026-08-24). This is the privileged entry path: the
# runner and the interpreter beyond it are both named absolutely, and resolving
# the first word of that chain through PATH would make the way in the only link
# whose identity depends on the environment the daemon happens to carry.
_SYSTEMD_RUN = "/usr/bin/systemd-run"

#: How the manager's state is measured, for the same reason and in the same
#: place. `is-system-running` is the manager's own answer about itself.
_SYSTEMCTL = "/usr/bin/systemctl"

#: Wall-clock ceiling on one privileged dispatch, applied to the transient unit
#: itself so a runner that never returns cannot hold the boundary open
#: indefinitely. Generous: the slowest legitimate action here is a package
#: transaction.
_DISPATCH_UNIT_MAX_SECONDS = 900

#: Ceiling on the CALL that waits for the unit, which is a different thing from
#: the ceiling on the unit. With --wait, systemd-run is a client: if the unit
#: reaches its own ceiling systemd reports that and the client returns, but a
#: bus that never answers leaves the client waiting on a unit it can no longer
#: hear from. DELIBERATELY LONGER than the unit's ceiling, so the unit's own
#: bound fires first and the ordinary slow case ends in systemd's answer rather
#: than in this backstop, which can only ever report that nothing is known.
_DISPATCH_WAIT_MAX_SECONDS = _DISPATCH_UNIT_MAX_SECONDS + 60

#: How long the manager probe may take before it is abandoned. It is a local
#: query on a failure path; a bound stops a wedged bus turning a diagnostic
#: into a second hang.
_MANAGER_PROBE_TIMEOUT_SECONDS = 5

#: The words `systemctl --user is-system-running` prints that mean no manager is
#: there to run anything. Every other word it prints — running, degraded,
#: starting, stopping, maintenance, initializing — describes a manager that
#: exists, and "degraded" in particular is a RUNNING manager, so treating it as
#: absence would reintroduce the defect this replaced.
#:
#: WHY THIS REPLACED A PATH CHECK (2026-08-24). The earlier probe answered the
#: question by asking whether $XDG_RUNTIME_DIR/systemd/private exists. An
#: independent review measured that probe reading false while the manager still
#: started a unit — the variable can point elsewhere, and that socket is a
#: systemd-internal detail rather than a supported presence interface. The old
#: answer was evidence about a path, and the sentence it fed was about a manager.
_MANAGER_ABSENT_ANSWERS = ("offline",)

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolCall, ToolResult, ToolSchema
from intergen.interfaces.provenance import (
    INGRESS_TOOLS_V1,
    ConversationTrustState,
    DispatchDecision,
    IngressTracker,
    Provenance,
    ToolRiskTier,
    UserDecision,
    escalate_provenance,
)
from intergen.provenance import (
    build_audit_record,
    build_refusal_audit_record,
    record_user_decision,
    verify_tool_call,
)
from intergen.audit_log import write_record
from intergen.dispatch_token import (secret_file_named_in_command,
                                     secret_file_reason)
from intergen.trace import get_tracer
from intergen.dispatch_token import (
    DispatchTokenError,
    mint_token,
)
from intergen.spotlighting import is_wrapped, wrap_ingress_content
from intergen import glass
from intergen import privileged_request
from intergen import tool_process
from intergen.interfaces.scanner import (
    ScanContext,
    ScanDirection,
    ScanDisposition,
)
from intergen.scanner.policy import ScannerPolicy

logger = logging.getLogger(__name__)


# Tools whose semantics are PRIVILEGED_STATE_CHANGING per RFC §6.
# Discovered by tool name; the registry uses this for the gate's
# tool_risk_tier argument. Tools NOT in this set are classified based on
# their own SafetyTier (AUTO -> READ_ONLY, BLOCKED -> rejected before gate
# entry; CONFIRM -> USER_SCOPE_STATE_CHANGING).
_PRIVILEGED_TOOLS: frozenset[str] = frozenset({
    # State-changing system tools that escalate via pkexec per D-007 Option A
    "manage_services",  # systemctl start/stop/restart on system units
    "manage_packages",  # pkm install/remove (root-level package state)
    "run_command",      # arbitrary shell command (may be privileged)
    "write_file",       # may target system paths (/etc/, /usr/, etc.)
})


#: THE DISPATCHER'S OWN ACCOUNT OF A DISPATCH THAT DID NOT SUCCEED (2026-10-22).
#: The privileged dispatcher runs in another process, and only its stdout and its
#: exit status come back across the boundary. A refusal it made, or one a tool
#: made under it, arrived as text and an exit status alone, and the result
#: rebuilt from them said the call had run (executed left True) and lost its
#: blocked flag, its outcome and its words. So on every non-zero exit it makes,
#: the dispatcher ends its output with ONE line: this prefix and a JSON object
#: naming the request it answers and the result's executed, blocked, outcome and
#: refusal. _privileged_failure_result reads it from the FINAL line only, and
#: only when it names this dispatch's request identifier, so text inside a
#: tool's content (always written before that line) cannot pose as it, and
#: neither can a final line the dispatcher did not write for this dispatch. A
#: success (exit 0) carries no such line and is read exactly as before.
PRIVILEGED_RESULT_PREFIX = "privileged_dispatch result: "
_PRIVILEGED_RESULT_FIELDS = {
    "request_id": str, "executed": bool, "blocked": bool,
    "outcome": str, "refusal": str,
}


def privileged_result_line(request_id: str, *, executed: bool, blocked: bool,
                           outcome: str, refusal: str) -> str:
    """The one line the dispatcher ends a non-zero exit's output with."""
    return PRIVILEGED_RESULT_PREFIX + json.dumps({
        "request_id": request_id, "executed": bool(executed),
        "blocked": bool(blocked), "outcome": str(outcome or ""),
        "refusal": str(refusal or ""),
    }, sort_keys=True)


def read_privileged_result_line(stdout: str,
                                request_id: str) -> tuple[dict | None, str]:
    """Split the dispatcher's result line off the end of `stdout`.

    Returns the line's fields and the output before it when the final line is
    the dispatcher's line for `request_id`; otherwise None and `stdout` whole.
    A final line that carries the prefix but names another request, or whose
    body is not the dispatcher's shape, is read as output and logged: the
    dispatcher did not write it for this dispatch.
    """
    head, _, last = stdout.rstrip("\n").rpartition("\n")
    if not last.startswith(PRIVILEGED_RESULT_PREFIX):
        return None, stdout
    try:
        fields = json.loads(last[len(PRIVILEGED_RESULT_PREFIX):])
    except ValueError:
        fields = None
    if (not isinstance(fields, dict)
            or set(fields) != set(_PRIVILEGED_RESULT_FIELDS)
            or any(type(fields[name]) is not kind
                   for name, kind in _PRIVILEGED_RESULT_FIELDS.items())
            or not request_id or fields["request_id"] != request_id):
        logger.warning(
            "the privileged dispatch's final line carries the result prefix "
            "but is not the dispatcher's line for request %s; it is read as "
            "output", request_id or "(none)",
        )
        return None, stdout
    return fields, head


def _on_the_users_own_manager(arguments: dict[str, Any]) -> bool:
    """manage_services' own answer to whether a call acts on the person's own
    service manager — the same one its builder adds --user on."""
    from intergen.tools.manage_services import runs_on_the_users_own_manager
    return runs_on_the_users_own_manager(
        arguments if isinstance(arguments, dict) else {})


def _declared_command_leaves(tool: BaseTool, arguments: dict[str, Any]) -> bool:
    """True when a command line the tool declares sends a request off the
    machine. The same declaration the install-secret screen reads: a tool that
    hands a parameter to a process names it in command_line_parameters."""
    from intergen.safety import command_leaves_the_machine
    args = arguments if isinstance(arguments, dict) else {}
    return any(isinstance(args.get(name), str) and command_leaves_the_machine(args[name])
               for name in tool.command_line_parameters)


def _classify_risk_tier(
    tool: BaseTool | None,
    arguments: dict[str, Any],
    tool_name: str,
) -> ToolRiskTier:
    """Map SafetyTier + tool-name to ToolRiskTier per RFC §6.

    PRIVILEGED_STATE_CHANGING wins over SafetyTier for tools that escalate
    via pkexec; SafetyTier.CONFIRM maps to USER_SCOPE_STATE_CHANGING
    (the gate's hold-for-review path handles what classify_safety
    historically called CONFIRM); SafetyTier.AUTO maps to READ_ONLY;
    BLOCKED tier never reaches this helper (rejected before gate entry).

    A genuinely READ-ONLY action stays READ_ONLY even on a tool in
    _PRIVILEGED_TOOLS. Reading your own machine's state — `systemctl status`,
    `pkm search`, `ls` — changes nothing, escalates nothing, and must remain
    freely available: a person is always allowed to inspect their own system.
    Gating a read behind the same elevation as a `restart` makes InterGen
    refuse benign questions and is the opposite of putting the user in control.
    Trusting AUTO here is safe by construction: every tool derives AUTO from an
    explicit, audited read-only allowlist (manage_services AUTO_ACTIONS,
    manage_packages AUTO_SUBCOMMANDS, run_command's _AUTO_COMMANDS_* with
    unknown→CONFIRM and destructive/shell-spawn→BLOCKED). Only state-changing
    actions (CONFIRM) reach the privileged/escalation path below.

    AN AUTO CALL THAT LEAVES THE MACHINE IS NETWORK_EGRESS (2026-10-22). AUTO
    means nothing changes here; it does not mean nothing is sent. A command
    line a tool declares (command_line_parameters) that sends a request to a
    host its arguments name — ping, dig, a print server's -h, a remote's fetch
    (safety.command_leaves_the_machine) — is NETWORK_EGRESS, which the matrix
    executes on the person's direct ask with nothing from outside read and
    holds otherwise. An external handler is the same tier: whatever its server
    does with the arguments happens off this machine.
    """
    if tool is None:
        # External handler or unknown — can't introspect the action. A known
        # privileged name is privileged; otherwise its arguments go to a server
        # that is not this machine.
        return (ToolRiskTier.PRIVILEGED_STATE_CHANGING
                if tool_name in _PRIVILEGED_TOOLS
                else ToolRiskTier.NETWORK_EGRESS)
    safety = tool.classify_safety(arguments)
    if safety == SafetyTier.AUTO:
        if _declared_command_leaves(tool, arguments):
            return ToolRiskTier.NETWORK_EGRESS
        return ToolRiskTier.READ_ONLY
    if tool_name == "manage_services" and _on_the_users_own_manager(arguments):
        # A change on the person's own service manager (systemctl --user) is a
        # change within their own account. It must not cross the privileged
        # boundary: run as root, --user reaches root's manager, not theirs.
        # The registry still reviews it like every held act of this tool
        # (_execute_impl, held_in_person).
        return ToolRiskTier.USER_SCOPE_STATE_CHANGING
    if tool_name in _PRIVILEGED_TOOLS:
        return ToolRiskTier.PRIVILEGED_STATE_CHANGING
    if safety == SafetyTier.CONFIRM:
        return ToolRiskTier.USER_SCOPE_STATE_CHANGING
    return ToolRiskTier.READ_ONLY


# ── User-language translation of the computed classification (2026-07-14) ────
#
# A review card / block message / honest handoff is a TRUST SURFACE: it must
# describe what the system actually classified, in the user's language — never
# the raw enum label "privileged_state_changing". This is the SINGLE source for
# that translation, consumed by the in-web review card, the desktop card, and
# the no-surface honest handoff, so every interface speaks one voice. The card's
# administrator-password footer is separate (tier_needs_admin gates it); this is
# only the "what kind of change is this" line.

def classification_sentence(tier: "ToolRiskTier") -> str:
    """Translate a computed ToolRiskTier into ONE user-language sentence.

    Never returns the raw label. Read-only actions do not gate, so their line is
    for completeness only; the two state-changing tiers are what a card or a
    handoff actually surfaces."""
    if tier is ToolRiskTier.PRIVILEGED_STATE_CHANGING:
        return ("This changes system software or settings and needs "
                "administrator approval.")
    if tier is ToolRiskTier.USER_SCOPE_STATE_CHANGING:
        return "This makes a change within your own account or session."
    if tier is ToolRiskTier.NETWORK_EGRESS:
        return ("This changes nothing here, but sends a request from this "
                "computer to another one on the network.")
    return "This only reads information from your system and changes nothing."


def tier_needs_admin(tier: "ToolRiskTier") -> bool:
    """True when the tier will trigger the OS administrator (polkit/pkexec)
    prompt — the card footer and the handoff use this to state the boundary."""
    return tier is ToolRiskTier.PRIVILEGED_STATE_CHANGING


def _runs_as_the_account(command: str) -> bool:
    """True for a command that acts on the account itself (pip install --user,
    systemctl --user stop ...): it is the account's own, so the handoff never
    puts sudo in front of it, whatever tier run_command gave it."""
    return "--user" in (command or "").split()


def honest_handoff_message(what: str, command: str, needs_admin: bool,
                           outcome: str = "") -> str:
    """The 3-part honest handoff (the honest-handoff design), shown when a state-changing action
    cannot be carried out in this window — the user declined, or there is no way
    to collect approval on this surface.

    Decided 2026-07-14: this is an advisory, not an error — surfacing a
    constraint the assistant cannot circumvent, honestly, reinforces trust:
    (1) name the action in the user's own terms; (2) say, in plain language, why
    it can't proceed here — NEVER "blocked", "safety layer", or any gate/tool
    name; (3) hand over the exact command to run. Same single source for the web
    card's no-surface/deny handoff and the router's offer-accept deny path, so
    every interface advises with one voice.

    `what` is the plain-language action sentence (from the card translator);
    `command` is the concrete command; `needs_admin` states whether the OS
    administrator prompt is the real authorization boundary. `outcome` is the
    registry's word for why the call did not run (ToolResult.outcome): the
    reason given is that outcome's own sentence, so a person who pressed Deny
    is not told that approval could not be collected. A command that runs as
    the account itself (a --user command) is never prefixed with sudo."""
    what = (what or "").strip()
    command = (command or "").strip()
    if _runs_as_the_account(command):
        needs_admin = False
    if outcome in _NOT_RUN_WHY:
        why = _NOT_RUN_WHY[outcome]
        run_cmd = command
        if needs_admin and command:
            # sudo on every command of a sequence, as below.
            run_cmd = " && ".join(
                part if part.startswith("sudo ") else f"sudo {part}"
                for part in (p.strip() for p in command.split("&&")) if part)
    elif needs_admin:
        why = ("This needs administrator approval, which I can't collect from "
               "here.")
        # Prefix sudo only when there is a real command — an empty command must
        # never render a bare "`sudo`" — and on EVERY command of a sequence:
        # "sudo pkm sync && pkm upgrade" would run the upgrade without root.
        segments = [part.strip() for part in command.split("&&")]
        run_cmd = "" if not command else " && ".join(
            part if part.startswith("sudo ") else f"sudo {part}"
            for part in segments if part)
    else:
        why = "I'm not able to make that change from here."
        run_cmd = command
    if what:
        if not what.endswith((".", "!", "?")):
            what = what + "."
        msg = f"{what} {why}"
    else:
        msg = why
    if run_cmd.strip():
        msg += ("\n\nYou can do it yourself in a terminal or the desktop app:\n\n"
                f"`{run_cmd.strip()}`")
    return msg


# ── Privileged escalation posture (the daemon's own environment, only) ──────
#
# A privileged dispatch escalates: the registry asks the user manager to run
# pkexec, pkexec asks PolicyKit, and PolicyKit raises an INTERACTIVE
# AUTHENTICATION DIALOG on the desktop of whoever is sitting at the machine.
# Measured 2026-09-16 on a development box: a test cell answered a consent gate
# with "allow", the dispatch escalated, and a polkit dialog appeared in front of
# the person using that machine, attributed to nothing they had done.
#
# A daemon can now be told to REFUSE the escalating class outright, at the
# dispatch boundary, before a request file is staged and before any process is
# started. The refusal is absolute for that daemon: nothing a turn carries can
# lift it, because the only thing read is the daemon's own environment.
PRIVILEGED_ESCALATION_ENV = "INTERGEN_PRIVILEGED_ESCALATION"
# The ONE value that permits escalation when the variable is set at all.
ESCALATION_ALLOWED = "allow"


class EscalationPosture(NamedTuple):
    """What this daemon will do with an action that needs administrator rights.

    `allows` is the decision. `setting` is the raw value read, or "" when
    nothing set it, and `source` says which of those two it was — a daemon
    running with escalation refused must never be mistakable for one that would
    escalate, so both halves are stated in the trace rather than inferred.
    """
    allows: bool
    setting: str
    source: str


def privileged_escalation_posture() -> EscalationPosture:
    """Read the posture from THE DAEMON'S OWN ENVIRONMENT, and nowhere else.

    WHAT CAN SET IT: the environment of the daemon process itself — a systemd
    unit's Environment=/EnvironmentFile=, or a shell that exports it before
    starting the daemon. The test harness sets it for every test process
    (conftest.py), so no cell written later can reach an authentication prompt.

    WHAT CANNOT SET IT: a turn, a tool argument, a gate decision, a conversation,
    a config file, a web request, an MCP handler or any other input that arrives
    after the daemon started. None of them are read here, which is the point: a
    posture that a request could influence would be a posture an untrusted input
    could lift.

    FAIL CLOSED ON ANYTHING UNRECOGNISED. Only the exact value "allow" permits
    escalation once the variable is set; an empty value, a typo, "true", "1" or
    "yes" all REFUSE. The variable does not exist on a shipped install, so the
    only way to hold an unrecognised value is to have set one deliberately, and
    reading a mistyped restriction as permission is the failure this avoids.

    The default — nothing set — allows escalation, which is what an installed
    system does and must keep doing: refusing by default would silently take
    away a person's ability to change their own machine through the assistant.
    """
    raw = os.environ.get(PRIVILEGED_ESCALATION_ENV)
    if raw is None:
        return EscalationPosture(True, "", "default")
    return EscalationPosture(raw == ESCALATION_ALLOWED, raw, "environment")


# ── THE ONE DESCRIPTION OF A HELD ACT (every consent surface renders it) ─────
#
# A consent surface asks a person to approve an act, so it must describe the
# act that runs and nothing else. Each surface used to compose its own text
# from the call's raw arguments: the web card's "what" and command line, the
# honest handoff, the desktop headline, the zenity body, the console line and
# the router's offer. Measured over every call shape a review card can be
# raised for, every row that showed a surface described something other than
# what the tool's own builder runs — an "update" card promising installed
# updates over a refresh, a --yes and a --user that ran unshown, "ssh" on a
# card for sshd, a handoff whose sudo reached only its first command, and raw
# argument dicts.
#
# describe_call derives ONE description from the argv the tool's own builder
# returns for the call (manage_packages.build_commands,
# manage_services.build_command, run_command's split and resolution), and every
# surface renders that one result. A tool with no command line gets a fixed
# sentence naming its argument as the tool resolves it; an external handler is
# described by its name and the exact payload it is sent. A call that will be
# refused is refused BEFORE any card, with the reason (refusal).

#: The PATH the privileged runner gives every root-side command
#: (intergen/data/intergen-privileged-runner, "export PATH=..."): a command a
#: held call runs as root is resolved against this, not the daemon's PATH.
_RUNNER_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


class ActDescription(NamedTuple):
    """One held act, described from the argv its tool's builder returns.

    `what` is the act in plain words; `commands` the argv lists in run order
    (empty for a tool with no command line); `root` whether the act runs through
    the privileged runner as root; `refusal` the reason the call is refused
    before any card ("" when it is not); `blocked` whether that refusal is the
    safety tier's or the shell-free executor's hard refusal, which is never
    handed to a person as a command to run; `words` that refusal in the words
    a person reads when they name nothing of the call (ToolResult.refusal),
    and `kind` its outcome, for the refusals that have them."""
    what: str
    commands: tuple[tuple[str, ...], ...]
    root: bool
    refusal: str = ""
    blocked: bool = False
    words: str = ""
    kind: str = ""

    @property
    def command_line(self) -> str:
        """The shell-joined argv, one per command, joined with &&; "" for a
        tool with no command line."""
        return " && ".join(shlex.join(argv) for argv in self.commands)

    @property
    def shown(self) -> str:
        """What a surface shows in its command slot: the command line, or for
        a tool with no command line the sentence naming its act."""
        return self.command_line or self.what


def _pkm_act(argv: tuple[str, ...]) -> str:
    """One pkm command line, in words, read from its own argv."""
    verb = argv[1] if len(argv) > 1 else ""
    rest = argv[2:]
    names = [w for w in rest if not w.startswith("-")]
    package = names[0] if names else ""
    if verb == "list":
        return "list the installed packages"
    if verb == "search":
        return f"search the package index for {package!r}"
    if verb == "info":
        return f"show the details of the package {package!r}"
    if verb == "verify":
        return f"verify the installed files of the package {package!r}"
    if verb == "install":
        return f"install the package {package!r} and the packages it needs"
    if verb == "remove":
        return f"remove the package {package!r}"
    if verb == "sync":
        return "refresh the package index"
    if verb == "upgrade" and "--all" in rest:
        return ("upgrade every installed package that has a newer signed "
                "build, leaving held packages and the kernel (linux-kernel) as "
                "they are")
    if verb == "upgrade":
        return f"upgrade the package {package!r}"
    return f"run pkm {shlex.join(argv[1:])}"


def _pkm_sentence(commands: tuple[tuple[str, ...], ...]) -> str:
    acts = [_pkm_act(argv) for argv in commands]
    sentence = ", then ".join(acts)
    sentence = sentence[:1].upper() + sentence[1:] + "."
    if any("--yes" in argv for argv in commands):
        sentence += (" Approving this answers pkm's own confirmation pause "
                     "(--yes).")
    return sentence


_SYSTEMCTL_CHANGES = {
    "start": "Start", "stop": "Stop", "restart": "Restart", "reload": "Reload",
    "enable": "Enable", "disable": "Disable", "mask": "Mask",
    "unmask": "Unmask",
}
_SYSTEMCTL_READS = {
    "status": "Show the status of {unit}",
    "is-active": "Check whether {unit} is running",
    "is-enabled": "Check whether {unit} is enabled",
    "is-failed": "Check whether {unit} has failed",
    "show": "Show the properties of {unit}",
    "cat": "Show the unit file of {unit}",
    "list-dependencies": "List the dependencies of {unit}",
}
_SYSTEMCTL_LISTINGS = {
    "list-units": "List the units",
    "list-unit-files": "List the unit files",
    "list-timers": "List the timers",
    "list-sockets": "List the sockets",
}


def _systemctl_sentence(argv: tuple[str, ...]) -> str:
    """One systemctl command line, in words, read from its own argv."""
    words = [w for w in argv[1:] if w != "--user"]
    verb = words[0] if words else ""
    unit = words[1] if len(words) > 1 else ""
    manager = ("your own (user) service manager" if "--user" in argv
               else "the system service manager")
    if verb == "daemon-reload":
        return (f"Reload the configuration of {manager}: it re-reads every unit "
                f"file, and no unit is started or stopped.")
    if verb in _SYSTEMCTL_CHANGES:
        return f"{_SYSTEMCTL_CHANGES[verb]} {unit} on {manager}."
    if verb in _SYSTEMCTL_READS:
        return _SYSTEMCTL_READS[verb].format(unit=unit) + f" on {manager}."
    if verb in _SYSTEMCTL_LISTINGS:
        matching = f" matching {unit}" if unit else ""
        return f"{_SYSTEMCTL_LISTINGS[verb]}{matching} on {manager}."
    return f"Run systemctl {shlex.join(words)} on {manager}."


def _no_argv_sentence(name: str, args: dict[str, Any]) -> str | None:
    """The fixed sentence for a tool with no command line, naming its one
    argument; None for a tool this table does not know."""
    if name == "read_file":
        return f"Read the file {args.get('path', '')}."
    if name == "analyze_file":
        return f"Analyze the file {args.get('path', '')}."
    if name == "web_search":
        return f"Search the web for {str(args.get('query', ''))!r}."
    if name == "take_screenshot":
        source = str(args.get("source", "") or "")
        return "Take a screenshot" + (f" ({source})." if source else ".")
    if name == "open_application":
        if args.get("list_apps"):
            return "List the applications that can be opened."
        return f"Open the application {str(args.get('name', ''))!r}."
    return None


def describe_call(tool_call: "ToolCall",
                  tool: "BaseTool | None" = None) -> ActDescription:
    """THE description of a call's act, derived from the argv the tool's own
    builder returns for it. Every consent surface renders this one result."""
    name = tool_call.name
    args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
    tier = _classify_risk_tier(tool, args, name) if tool is not None else (
        _classify_risk_tier(_builtin_tool(name), args, name))
    root = tier is ToolRiskTier.PRIVILEGED_STATE_CHANGING
    if name == "manage_packages":
        from intergen.tools.manage_packages import ManagePackagesTool
        pkg_tool = (tool if isinstance(tool, ManagePackagesTool)
                    else ManagePackagesTool())
        commands = pkg_tool.build_commands(args)
        if commands is None:
            reason = (pkg_tool.validate_arguments(args)
                      or "the package tool builds no command for these "
                         "arguments")
            return ActDescription("", (), root, reason)
        built = tuple(tuple(argv) for argv in commands)
        return ActDescription(_pkm_sentence(built), built, root)
    if name == "manage_services":
        from intergen.tools.manage_services import (
            ManageServicesTool, daemon_reload_naming_a_unit)
        svc_tool = (tool if isinstance(tool, ManageServicesTool)
                    else ManageServicesTool())
        refusal = daemon_reload_naming_a_unit(args)
        if refusal:
            return ActDescription("", (), root, refusal)
        argv = svc_tool.build_command(args)
        if argv is None:
            return ActDescription(
                "", (), root,
                f"'{args.get('action', '')}' needs a service name; nothing "
                f"was proposed")
        return ActDescription(_systemctl_sentence(tuple(argv)), (tuple(argv),),
                              root)
    if name == "run_command":
        from intergen.tools.run_command import (
            _SHELL_CONSTRUCT_CHARS, EMPTY_COMMAND_WORDS, SHELL_CONSTRUCT_WORDS,
            UNPARSEABLE_WORDS)
        command = str(args.get("command", "") or "").strip()
        if not command:
            return ActDescription("", (), root, "Error: empty command", True,
                                  EMPTY_COMMAND_WORDS, "invalid_arguments")
        offending = sorted({c for c in command if c in _SHELL_CONSTRUCT_CHARS})
        if offending:
            shown = ", ".join(repr(c) for c in offending)
            # run_command's own words for the same refusal (its execute()),
            # given here, before any card, instead of after an approval.
            return ActDescription(
                "", (), root,
                "This tool runs a single command with no shell, so shell "
                "constructs are not supported (found: " + shown + "). Pipes, "
                "redirection, ; && & , command substitution $(), subshells, "
                "and newlines can't be used here — ask for one plain command "
                "at a time.", True, SHELL_CONSTRUCT_WORDS, "shell_refused")
        try:
            words = shlex.split(command)
        except ValueError as exc:
            return ActDescription(
                "", (), root,
                f"Could not parse the command (unbalanced quotes?): {exc}", True,
                UNPARSEABLE_WORDS, "invalid_arguments")
        program = words[0]
        if "/" in program:
            resolved = os.path.abspath(program)
        else:
            resolved = shutil.which(program, path=_RUNNER_PATH if root else None)
        argv = (resolved or program, *words[1:])
        return ActDescription(
            f"Run {shlex.join(argv)} as its own short-lived process.",
            (argv,), root)
    if name == "write_file":
        from intergen import dispatch_token
        path_text = str(args.get("path", "") or "")
        if not path_text:
            return ActDescription("", (), root, "Error: no path provided")
        try:
            named = dispatch_token.expanduser_for_install_owner(path_text)
            if not named.is_absolute():
                return ActDescription(
                    "", (), root,
                    f"the path {path_text!r} is relative, and a relative path "
                    f"is resolved against a working directory no card can "
                    f"show; nothing was proposed — name the file by its full "
                    f"path, or start it with ~/")
            target = dispatch_token.resolved_for_a_write(named)
        except Exception as exc:  # noqa: BLE001 — the tool's own refusal path
            return ActDescription("", (), root, f"{type(exc).__name__}: {exc}")
        size = len(str(args.get("content", "") or ""))
        sentence = (f"Write {size} character{'' if size == 1 else 's'} to "
                    f"{target}, replacing its content if it exists")
        if args.get("create_dirs"):
            sentence += "; missing parent directories are created"
        return ActDescription(sentence + ".", (), root)
    sentence = _no_argv_sentence(name, args)
    if sentence is not None:
        return ActDescription(sentence, (), root)
    payload = json.dumps(args, sort_keys=True)
    return ActDescription(
        f"Send this to the external tool {name}: {payload} — what it does with "
        f"it is decided outside this machine's own tools.", (), root)


def _builtin_tool(name: str) -> "BaseTool | None":
    """A fresh instance of a built-in tool by name, for a caller that has none
    to hand (the risk tier reads the tool's own classification)."""
    from intergen.tools.manage_packages import ManagePackagesTool
    from intergen.tools.manage_services import ManageServicesTool
    from intergen.tools.run_command import RunCommandTool
    from intergen.tools.write_file import WriteFileTool
    return {"manage_packages": ManagePackagesTool,
            "manage_services": ManageServicesTool,
            "run_command": RunCommandTool,
            "write_file": WriteFileTool}.get(name, lambda: None)()


def card_action_description(tool_call: "ToolCall") -> tuple[str, str]:
    """(what, command) for a state-changing action: the plain-language sentence
    and the concrete command line, both from describe_call — so the card shows
    the shell-joined argv the tool's own builder returns, and for a tool with no
    command line the sentence naming its act.

    Lives here, beside honest_handoff_message, because more than one surface
    needs it: the web review card renders it, and the router renders it when a
    dispatch it drove was denied at the gate. Two copies would drift, and the
    drift would be invisible — each surface would look right on its own.
    """
    act = describe_call(tool_call)
    return act.what, act.shown


def _terminal_line(act: ActDescription) -> str:
    """The act's command line as a person types it at a terminal: pkm's --yes
    dropped (a person there answers pkm's own pause), and sudo on every command
    when the act runs as root, on none when it runs on the person's own
    manager, and none before a command that acts on the account itself (a
    --user command)."""
    lines = []
    for argv in act.commands:
        words = [w for w in argv
                 if not (os.path.basename(argv[0]) == "pkm" and w == "--yes")]
        line = shlex.join(words)
        lines.append(f"sudo {line}"
                     if act.root and not _runs_as_the_account(line) else line)
    return " && ".join(lines)


def handoff_command(tool_call: "ToolCall") -> str:
    """The concrete command to hand a person for an action they can run
    themselves — ONLY for tools whose action maps to a real command line
    (manage_packages / manage_services / run_command), and only for a call that
    is not refused. Empty for tools where no such line exists (write_file /
    take_screenshot / analyze_file / …), so the honest handoff omits a bogus
    command rather than inventing one."""
    if tool_call.name not in ("manage_packages", "manage_services", "run_command"):
        return ""
    act = describe_call(tool_call)
    if act.refusal or not act.commands:
        return ""
    return _terminal_line(act)


#: How the registry's result opens when a tool's own argument validation
#: refused a call, before the gate and before any card; the refusal message
#: reads the reason after it.
VALIDATION_REFUSED = "Validation error: "


def gate_refusal_message(tool_call: "ToolCall", tool_result: "ToolResult",
                         tool_obj: "BaseTool | None" = None) -> str:
    """What a person is told when the registry did NOT run a held/denied action.

    A BLOCKED action (a destructive command the safety tier hard-refuses) NEVER
    gets a "run it yourself" command — that would hand the person the very thing
    the block exists to prevent (fail-closed, security-first); it is told in the
    block's own words. A call whose command line may not be handed over
    (handoff_withheld) is told the kind of action and why it did not run, and
    nothing of the call itself; any other call refused before any card gets its
    reason; the person's own call with no command line is told the action and
    why (refusal_without_handoff). A state-changing action of the person's own
    that was denied, or that had no consent surface, gets the 3-part honest
    handoff, so the person is honestly advised of the real path forward rather
    than dead-ended.

    THE SINGLE SOURCE for every surface. The web panel's tool-call loop renders
    its refusal here, and so does the router when a dispatch it drove was denied
    at the card — before that, the router delivered the registry's audit string
    ("Tool call denied by user via review modal.") to the person, because the
    renderer written for this case could not be reached from that path.

    A REFUSAL IN ITS OWN WORDS (ToolResult.refusal) is told in them: the launch
    boundary's entry and remedy, the shell-free executor's reason, a tool's own
    check, the image gate's sentence. Code-owned words, so they are never a
    command to run; told in the classifier's line, or as the approval a person
    could not give here, they dropped what the person needs to mend it.
    """
    if getattr(tool_result, "blocked", False):
        return refusal_without_handoff(tool_call, tool_result)
    words = str(getattr(tool_result, "refusal", "") or "").strip()
    if words:
        if getattr(tool_result, "executed", True):
            # Refused after its act ran (the image gate): the words say what
            # was refused, and nothing claims that nothing ran.
            return words
        return f"Nothing was run: {words.rstrip('.')}."
    act = describe_call(tool_call, tool_obj)
    if act.blocked:
        return refusal_without_handoff(tool_call, tool_result)
    withheld = handoff_withheld(tool_call, tool_result, tool_obj)
    content = str(getattr(tool_result, "content", "") or "")
    reason = act.refusal or (content[len(VALIDATION_REFUSED):]
                             if content.startswith(VALIDATION_REFUSED) else "")
    if reason and not withheld:
        # Refused before any card: the reason, never a command to run.
        return f"Nothing was run: {reason.rstrip('.')}."
    command = handoff_command(tool_call)
    if withheld or not command:
        return refusal_without_handoff(tool_call, tool_result,
                                       name_the_call=not withheld)
    return honest_handoff_message(act.what, command, act.root,
                                  outcome=getattr(tool_result, "outcome", "") or "")


def handoff_withheld(tool_call: "ToolCall", tool_result: "ToolResult | None" = None,
                     tool_obj: "BaseTool | None" = None) -> str:
    """Why the command line of a call that ran nothing must NOT be handed to the
    person, or "" when it may be. A refusal that hands the refused call back as
    "you can do it yourself" turns the refusal into an instruction in the
    product's own voice, so the line is handed over only for the person's own
    ask, and never for:

      * a call whose arguments name an install secret: the registry's own
        screen, asked of the same arguments with the tool's command-line
        parameters, so the renderer and the screen cannot disagree;
      * a call that is not the person's own ask: the provenance the gate read
        once ingress was weighed (ToolResult.effective_provenance), or, for a
        result that carries none, the call's declared one; anything but
        user_direct is a command the person did not give, a page's among them;
      * a call its own tool refused as invalid (a verb the tool does not have),
        or a call naming a tool there is none of: the line would tell the
        person to run a command that cannot work, and the name is the call's;
      * a call its tool's safety tier refuses outright (BLOCKED): the block
        exists to stop that very command, on every path that could repeat it;
      * a call the shell-free executor refused (outcome shell_refused): its line
        is not one plain command, and that refusal is never handed to a person
        as a command to run (ActDescription.blocked), now that its result
        carries its own words rather than the classifier's flag;
      * a call refused outright before any review (REFUSED_WITHOUT_REVIEW: a
        forbidden zone, a governance hard deny): never the assistant's to take,
        so the person is told it, as the zone's own refusal tells them, without
        the command typed out for them.
    """
    arguments = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
    params = (getattr(tool_obj, "command_line_parameters", ()) or ()) if tool_obj is not None else ()
    secret = _names_an_install_secret(arguments, params)
    if secret is not None:
        return f"the arguments name {secret}"
    provenance = (getattr(tool_result, "effective_provenance", "") or ""
                  if tool_result is not None else "")
    if not provenance:
        provenance = getattr(tool_call.source_of_request, "value", "") or ""
    if provenance != Provenance.USER_DIRECT.value:
        return f"the call is not the person's own ask ({provenance or 'no provenance'})"
    if getattr(tool_result, "outcome", "") == "invalid_arguments" or (
            tool_obj is not None and tool_obj.validate_arguments(arguments)):
        return "its own tool refused the arguments"
    if getattr(tool_result, "outcome", "") == "unknown_tool":
        return "there is no tool by that name"
    if getattr(tool_result, "blocked", False) or (
            tool_obj is not None
            and tool_obj.classify_safety(arguments) == SafetyTier.BLOCKED):
        return "its tool's safety tier refuses it outright"
    if getattr(tool_result, "outcome", "") == "shell_refused":
        return "the shell-free executor refuses it outright"
    if getattr(tool_result, "outcome", "") == REFUSED_WITHOUT_REVIEW:
        return "it was refused outright before any review"
    return ""


# The answers a review callback gives the registry. Only an approval runs the
# held call. "deny" and "deny_conversation" are the person's own refusal and are
# recorded as theirs. The outcomes below are what a review surface answers when
# no person decided, and each is recorded as what happened: a refusal the person
# gave must be told apart, in the record, from a card that went unanswered or a
# review that never worked. An answer the registry does not know is recorded as
# a failed review, and nothing runs on it.
REVIEW_APPROVALS = frozenset({"allow_once", "allow_conversation"})


def _consent_state(user_outcome: str) -> str:
    """The consent state an audit outcome word records, in four words a reader
    of the decision path can use without the list in build_audit_record:
    not_asked (no review was needed and the call ran), granted (the person
    approved it at the card), refused (the person, the gate or the registry said
    no), asked_not_granted (a card was needed and nobody approved it). A word
    this does not know is returned as unknown:<word>, never guessed at. An
    ingress suffix ("+ingress_...") is about the call's result, not its consent,
    and is read past."""
    word = (user_outcome or "").split("+", 1)[0]
    if word == "executed":
        return "not_asked"
    if word in ("allowed_once", "allowed_conversation"):
        return "granted"
    if word in ("denied", "deny_conversation", "egress_blocked",
                "refused_without_review", "dispatch_token_failed",
                SEARCH_NOT_ASKED):
        return "refused"
    if word in ("no_review_ui", "review_timed_out", "review_refused",
                "review_failed", "review_ended"):
        return "asked_not_granted"
    return f"unknown:{word}"
REVIEW_TIMED_OUT = "review_timed_out"
REVIEW_REFUSED = "review_refused"
REVIEW_FAILED = "review_failed"
REFUSED_WITHOUT_REVIEW = "refused_without_review"
REVIEW_ENDED = "review_ended"
# A held call with no review surface at all: nothing could ask the person, so
# nothing runs, and the record says that rather than that they refused.
NO_REVIEW_UI = "no_review_ui"
# The person approved a privileged call and the single-use token that carries
# that approval to the root boundary could not be minted: nothing runs, and
# the record says the token failed, not that the person refused.
DISPATCH_TOKEN_FAILED = "dispatch_token_failed"
# An action the router does not offer again in a conversation because it did
# not run when it was offered before (the loop-killer's handoff).
HANDED_OFF_BEFORE = "handed_off_before"
_REVIEW_NOT_DECIDED = {
    REVIEW_TIMED_OUT: "the review card was not answered before its deadline",
    REVIEW_REFUSED: ("a review card for this call was already open, so a "
                     "second one was refused"),
    REVIEW_FAILED: ("the review card could not be shown, or its answer could "
                    "not be read"),
    REFUSED_WITHOUT_REVIEW: ("this machine refuses the action outright, so no "
                             "review card was raised"),
    REVIEW_ENDED: ("the conversation it belonged to ended before anyone "
                   "answered its review card"),
}


# The reason a handoff gives for a call that ran nothing, one per outcome the
# registry records on the result (ToolResult.outcome). An outcome not listed
# keeps the handoff's general sentence.
_NOT_RUN_WHY = {
    "denied": "You declined it at the review card, so I didn't run it.",
    "deny_conversation": ("You declined it at the review card for the rest of "
                          "this conversation, so I didn't run it."),
    REVIEW_TIMED_OUT: ("Its review card wasn't answered before its deadline, "
                       "so nothing ran."),
    REVIEW_REFUSED: "A review card for it was already open, so nothing ran.",
    REVIEW_FAILED: ("Its review card couldn't be shown, or its answer couldn't "
                    "be read, so nothing ran."),
    REFUSED_WITHOUT_REVIEW: ("This machine refuses that action outright, so "
                             "nothing ran."),
    REVIEW_ENDED: ("The conversation it belonged to ended before its review "
                   "card was answered, so nothing ran."),
    NO_REVIEW_UI: ("There's no way to ask for your approval here, so nothing "
                   "ran."),
    # The recovery step rides with the reason: the record names it, and a
    # person told only that the approval failed has no way to mend it.
    DISPATCH_TOKEN_FAILED: ("You approved it, but the approval couldn't be "
                            "confirmed to the system (its signing key is "
                            "missing or unreadable), so nothing ran. Running "
                            "'intergen setup' provisions the key again."),
    "escalation_refused": ("This assistant runs with administrator actions "
                           "turned off, so nothing ran."),
    "gate_rejected": "It was refused before it reached a review, so nothing ran.",
    # Not the registry's: the router's loop-killer, for an action it does not
    # offer again because it did not run when it came up earlier.
    HANDED_OFF_BEFORE: ("It didn't run when it came up earlier in this "
                        "conversation, so I'm not offering it again."),
}


#: What the audit record of a held call that leaves the machine says first:
#: the class it was held for, never what was read before it (2026-10-22).
NETWORK_EGRESS_HELD = (
    "Held: the call sends a request off this machine, and the person had not "
    "asked for it directly with nothing from outside read in the conversation")


def _held_summary(decision: DispatchDecision, summary: str) -> str:
    """A held call's audit summary, led by the class it was held for when
    the call leaves the machine; any other held call's summary is unchanged."""
    if (decision.action == "hold_for_review"
            and decision.risk_tier is ToolRiskTier.NETWORK_EGRESS):
        return f"{NETWORK_EGRESS_HELD}. {summary}"
    return summary


def review_not_decided(answer: object) -> tuple[str, str]:
    """(outcome, what happened) for a review answer that is neither an
    approval nor the person's denial. An answer the registry does not know is
    recorded as a failed review and quoted, so a callback that starts answering
    something new is seen in the record rather than obeyed."""
    what = _REVIEW_NOT_DECIDED.get(answer) if isinstance(answer, str) else None
    if what is not None:
        return str(answer), what
    return REVIEW_FAILED, ("the review gave an answer that is not an approval "
                           f"({str(answer)[:40]!r})")


# ── A WEB SEARCH RUNS ON THE PERSON'S WORD, NEVER ON THE MODEL'S (2026-10-21) ──
#
# On the native floor every conversational turn is offered every schema, and
# web_search is read-only, so a search the model decided on ran with no word
# from the person: the query the model composed left the machine for the search
# engine, and nothing on the way asked. The locked floors search only on a
# recognised explicit ask or an accepted offer, because there the code owns
# every dispatch. This gate gives the native floor the same rule, here, at the
# one place every call passes.
#
# WHO BUILT A CALL IS NOT SOMETHING THE CALL CAN SAY. Its source_of_request is
# the model's own label: the client takes it out of the arguments the model
# emitted, so a call the model decided can carry user_direct, the label the
# router's own calls carry. The caller of execute() is the one that knows, and
# it says so in ``origin``. The default is the model, so a caller that says
# nothing is gated: a dispatch path added later without the keyword holds its
# searches instead of running them.
#
# THE PERSON'S WORD is a SearchAsk, the router's own reading of the turn, made
# by its recogniser of an explicit ask or by its resolver of an accepted offer,
# held for that turn and gone at the next. It is an object the router builds and
# hands over beside the call, never a tool argument: nothing the model emits can
# make one, and an argument that claims to be one is only an argument.


class CallOrigin(Enum):
    """Who built a ToolCall, as the caller of ToolRegistry.execute knows it."""

    #: The model emitted the call. The default: a caller that names no origin
    #: is treated as handing over a call the model decided.
    MODEL = "model"
    #: The router or the panel built the call from the person's own words or
    #: their own button press, and passes it on unchanged.
    CODE = "code"


@dataclass(frozen=True)
class SearchAsk:
    """The person asked for a web search on this turn: the router's reading.

    ``how`` is ``"explicit_ask"`` when the router's recogniser read the turn as
    a request to search, or ``"accepted_offer"`` when the person accepted a
    search the assistant offered; ``target`` is what they named, for the
    record. The registry accepts nothing else as the person's word.
    """

    how: str
    target: str = ""


#: The two ways the router can come to hold the person's word for a search.
SEARCH_ASK_KINDS = frozenset({"explicit_ask", "accepted_offer"})

#: Tools a call from the model may run only on the person's word. A search
#: sends the model's query off the machine; with the web lane it also fetches
#: the first result's page, which happens inside the same call.
_PERSONS_WORD_TOOLS: frozenset[str] = frozenset({"web_search"})

#: The outcome the audit record carries for a call held for want of the
#: person's word, and the content of the result the caller receives. Neither is
#: a message for the person: the caller renders its own offer.
SEARCH_NOT_ASKED = "held_search_not_asked"
SEARCH_NOT_ASKED_SUMMARY = (
    "Held: the model asked for a web search that the person did not ask for "
    "on this turn, so no query was sent.")


def is_search_ask(value: object) -> bool:
    """True only for a SearchAsk the router made, of a kind it makes."""
    return isinstance(value, SearchAsk) and value.how in SEARCH_ASK_KINDS


def is_search_hold(result: "ToolResult | None") -> bool:
    """True when the registry held this result's call for want of the
    person's word, so the caller offers the search instead of reporting a
    failure."""
    return (result is not None
            and not getattr(result, "executed", True)
            and getattr(result, "content", None) == SEARCH_NOT_ASKED_SUMMARY)


# The reason a refusal gives when no command line is handed over, one per
# outcome the registry records on the result: the handoff's own sentences, and
# one for each refusal that no review reached. A fixed "I'm not able to do that
# from here right now" in their place told a person who had pressed Deny, a
# credential the assistant never reads and a blocked action alike that the
# assistant could not act just now, and its offer to walk them through how to
# do it instead handed the refused call back in other words.
_REFUSED_WHY = {
    **_NOT_RUN_WHY,
    "secret_refused": ("It names one of the assistant's own credential "
                       "files, which the assistant never reads and no "
                       "approval can open, so nothing ran."),
    "invalid_arguments": ("That isn't something the tool can do as asked, so "
                          "nothing ran."),
    "unknown_tool": "There's no tool by that name here, so nothing ran.",
    "egress_blocked": ("It would have sent content off this machine that the "
                       "outbound check stopped, so nothing ran."),
    "blocked": "This machine refuses that action outright, so nothing ran.",
    SEARCH_NOT_ASKED: "You didn't ask for a web search, so no query was sent.",
}
# A result that carries no outcome the table knows: what is certain is that
# the call did not run.
_REFUSED_WHY_UNKNOWN = "It didn't run, so nothing changed."

# The kind of action a call asked for, named without any of its arguments: what
# a refusal says of a call whose content must not be repeated to the person (a
# package name, a unit, a path or a verb a page supplied would otherwise come
# back in the product's voice). A tool not listed is named by nothing but the
# reason.
_ACTION_KIND = {
    "run_command": "Run a system command.",
    "manage_packages": "Change the installed packages.",
    "manage_services": "Change a system service.",
    "write_file": "Write to a file.",
}


def refusal_without_handoff(tool_call: "ToolCall",
                            tool_result: "ToolResult | None" = None,
                            outcome: str = "", *,
                            name_the_call: bool = False) -> str:
    """What a person is told about a call that ran nothing when no command line
    is handed over: the action and the outcome's own reason (`outcome`, or the
    registry's word on `tool_result`), and nothing more — no command, and no
    offer to carry the refused call out. The action is named by its kind alone
    (_ACTION_KIND), none of the call's arguments, unless `name_the_call` says
    the call is the person's own ask with nothing withheld (a tool with no
    command line), when it is named as its review card names it. A blocked call
    is told in the block's own words, computed from the call as the registry
    computes them (safety.get_blocked_response under the provenance the gate
    read, the result's effective_provenance): a result's content is written for
    the model and the record, so it is never taken as the person's text."""
    if getattr(tool_result, "blocked", False):
        from intergen.safety import get_blocked_response
        arguments = (tool_call.arguments
                     if isinstance(tool_call.arguments, dict) else {})
        return get_blocked_response(
            str(arguments.get("command", str(arguments))),
            provenance=getattr(tool_result, "effective_provenance", "") or "")
    outcome = outcome or (getattr(tool_result, "outcome", "") or "")
    why = _REFUSED_WHY.get(outcome, _REFUSED_WHY_UNKNOWN)
    if name_the_call:
        what, _cmd = card_action_description(tool_call)
    else:
        what = _ACTION_KIND.get(tool_call.name, "")
    what = (what or "").strip()
    if not what:
        return why
    if not what.endswith((".", "!", "?")):
        what = what + "."
    return f"{what} {why}"


def _ingress_source_attribution(
    tool_name: str, arguments: dict[str, Any]
) -> tuple[str, str]:
    """Derive (source, source_type) for an ingress tool's spotlight wrapper.

    AI-2 (audit 2026-05-29): the source mapping lives in ONE central place keyed
    off the same INGRESS_TOOLS_V1 set that drives the §5.1 watermark — NOT in
    each tool (per-tool wrapping is what let the watch-list drift). The source
    string is surfaced in the review modal when ingress content motivates a held
    action; source_type tags the wrapper marker.
    """
    if tool_name in ("read_file", "analyze_file"):
        return (str(arguments.get("path", "")), "file")
    if tool_name == "web_search":
        return (str(arguments.get("query", "")), "web_search")
    if tool_name == "take_screenshot":
        return ("screen capture", "screenshot")
    return (tool_name, "untrusted")


# Sentinel build seq step 3 — on-the-fly scan of every external/MCP interaction.
# These helpers feed the ScannerPolicy at the dispatch chokepoint (design plan §6).
def _scan_surface(tool_name: str, arguments: dict[str, Any], *, external: bool) -> str:
    """Human/audit-readable surface label for a ScanContext.

    External/MCP tools register as `mcp_<server>_<tool>` (mcp_client.py); the
    rest of the external set keeps its own name. Built-in ingress tools map to
    their file/web source so an egress/ingress verdict points at WHERE the
    content crossed the boundary.
    """
    if external:
        if tool_name.startswith("mcp_"):
            return f"mcp:{tool_name[len('mcp_'):]}"
        return f"external:{tool_name}"
    source, source_type = _ingress_source_attribution(tool_name, arguments)
    return f"{source_type}:{source}" if source else source_type


def _names_an_install_secret(arguments: object,
                            command_line_parameters: tuple[str, ...] = (),
                            ) -> str | None:
    """The reason any value in `arguments` names an install secret, or None.

    Walks the whole argument structure rather than a list of parameter names: a
    path can arrive as a bare string, inside a list, or under a key this function
    has never heard of, and the screen is worth nothing if its coverage depends on
    a tool's choice of parameter name. The predicates live in the module that owns
    the files, so there is one place that knows what the install's secrets are.

    KEYS ARE WALKED AS WELL AS VALUES. A tool is free to take its paths as the
    keys of a mapping and consume them from there, and a walk that looked only at
    values never saw those paths at all.

    The screen asks one question of every string it meets: is this value, taken
    whole, a path to one of those files. For a parameter the tool DECLARES as a
    command line it also asks whether any word of the command is such a path,
    because a command is the one shape where the thing that opens the file is a
    process this package cannot instrument at the point of opening. It asks
    nothing else of any other string: text that a tool will never hand to a
    process opens nothing, and refusing it stopped ordinary work while protecting
    nothing. What a tool will actually open is established at the file boundary in
    the consumers, not here.

    `command_line_parameters` is empty unless the dispatched tool declares
    otherwise, so a tool whose semantics this package does not know gets the path
    question only. Nested structures are walked with it empty: a declaration names
    a parameter of the call, not a key somewhere inside one of its values.
    """
    if isinstance(arguments, dict):
        for key, value in arguments.items():
            reason = secret_file_reason(key)
            if reason is not None:
                return reason
            if key in command_line_parameters:
                reason = secret_file_named_in_command(value)
                if reason is not None:
                    return reason
            reason = _names_an_install_secret(value)
            if reason is not None:
                return reason
        return None
    if isinstance(arguments, (list, tuple, set)):
        for value in arguments:
            reason = _names_an_install_secret(value)
            if reason is not None:
                return reason
        return None
    return secret_file_reason(arguments)


def _effective_before_gate(call: ToolCall,
                           ingress_tracker: IngressTracker) -> Provenance:
    """The provenance the dispatcher gate gives a call, for a call refused
    before the gate is consulted (an unknown tool, arguments its tool refuses,
    the BLOCKED tier): the declared label after the ingress watermark, or
    ingress_derived when none was declared, exactly as verify_tool_call and
    build_refusal_audit_record compute it. A refusal's wording reads it, so a
    call the gate would not read as the person's own is never worded as theirs.
    """
    if call.source_of_request is None:
        return Provenance.INGRESS_DERIVED
    return escalate_provenance(
        call.source_of_request,
        ingress_tracker.ingress_fired_this_turn(),
        ingress_tracker.ingress_fired_this_conversation(),
    )


def _egress_payload(arguments: dict[str, Any]) -> str:
    """Serialize a call's arguments for an EGRESS (args-leaving) scan.

    default=str so a non-JSON-native value (Path, bytes-repr, etc.) cannot
    raise and silently skip the scan — the scanner sees a faithful string.
    """
    try:
        return json.dumps(arguments, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return str(arguments)


def _withheld_notice(tool_name: str, reason: str) -> str:
    """The notice the LLM receives in place of content Sentinel withheld.

    Decision #2: a flagged result is DROPPED — the model is told the content
    was withheld (and why, briefly) rather than fed the poison it would have
    otherwise re-entered context as.
    """
    return (
        f"[Sentinel withheld the result of '{tool_name}': the returned content was "
        f"flagged as unsafe to re-enter context ({reason or 'policy'}). "
        "The content was NOT delivered. Do not infer its contents; ask the user "
        "how to proceed if this result is needed."
    )


class ToolRegistry:
    """Discovers, registers, and dispatches tool calls."""

    def __init__(self, scanner_policy: ScannerPolicy | None = None):
        self._tools: dict[str, BaseTool] = {}
        self._external_handlers: dict[str, Callable] = {}
        self._external_rules: dict[str, str] = {}
        self._ready = False
        # Sentinel scanner (design plan §6). The production wiring
        # (dbus_daemon) constructs the always-on policy and injects it; None
        # here means scanning is inactive (headless tooling / unit tests that
        # exercise dispatch without the scan layer). The chokepoint never
        # silently allows when a policy IS present — it fails toward the human
        # modal / withhold on FLAG/BLOCK (HG #10).
        self._scanner: ScannerPolicy | None = scanner_policy
        # Read-through cache for AUTO read-only calls (perceived-latency design):
        # serves repeated identical reads instantly, skipping the system call AND
        # the LLM re-synthesis (the cached ToolResult carries its model_summary).
        # Per-user keyed, short TTLs, invalidated by successful state-changing
        # calls. Only the tool layer populates it (Zone 3).
        from intergen.tool_cache import ToolCache
        self._cache = ToolCache()
        # Per-tool invocation tally (Usage tab → Top Tools). Plain dict to avoid
        # a new import; dispatch is single-threaded so no lock is needed.
        self._tool_call_counts: dict[str, int] = {}
        # DISPATCH LOCKDOWN structural backstop (lockdown red-team guard #2):
        # when True, get_tool_schemas() returns [] so the MODEL is never offered
        # tools on ANY surface (router P3, the WS streaming generate, the prompt-
        # cache warmup) and therefore cannot emit a ToolCall at all. This is the
        # second, independent guard behind the router's lock_dispatch gates — a
        # future model-facing surface that forgets the source/lock check still
        # cannot leak dispatch to the model. Defaults OFF (no behavior change for
        # tests / non-daemon callers); the daemon sets it from the tier resolver.
        # CODE-OWNED dispatch is UNAFFECTED — get_tool()/execute() (the P1/P2 +
        # route-to-tools + staged-command paths) never consult get_tool_schemas().
        self._tool_offering_locked = False

    def set_tool_offering_locked(self, locked: bool) -> None:
        """Lock/unlock model-facing tool offering (the dispatch-lockdown backstop).

        When locked, get_tool_schemas() returns [] so the model is structurally
        never offered tools. Code-owned dispatch (get_tool/execute) is unaffected.
        """
        self._tool_offering_locked = locked

    def get_tool_call_counts(self) -> dict[str, int]:
        """Cumulative per-tool invocation counts since daemon start."""
        return dict(self._tool_call_counts)

    def discover_tools(self, tools_dir: Path | None = None) -> int:
        """Auto-discover BaseTool subclasses in the tools directory.

        Scans intergen/tools/*.py for classes that subclass BaseTool.
        Each module should define a class that can be instantiated with no args.
        """
        if tools_dir is None:
            tools_dir = Path(__file__).parent / "tools"

        if not tools_dir.exists():
            logger.warning("Tools directory does not exist: %s", tools_dir)
            return 0

        count = 0
        for path in sorted(tools_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            mod_name = f"intergen.tools.{path.stem}"
            try:
                mod = import_module(mod_name)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (isinstance(attr, type)
                            and issubclass(attr, BaseTool)
                            and attr is not BaseTool):
                        tool = attr()
                        self.register(tool)
                        count += 1
            except Exception as e:
                logger.error("Failed to load tool module %s: %s", mod_name, e)

        self._ready = True
        logger.info("Tool registry ready — %d tools discovered", count)
        return count

    def attach_deep_scanner(self, scanner) -> bool:
        """Attach the deep-scan tier (LocalQwen / cloud) to the active policy.

        The wiring layer (dbus_daemon) calls this once the deep scanner is
        constructed, so a floor FLAG can escalate to the semantic tier (and
        depth=deep can always escalate). No-op returning False when scanning is
        inactive (no policy injected) — the floor still holds at the chokepoint.
        """
        if self._scanner is None:
            return False
        self._scanner.set_deep_scanner(scanner)
        return True

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        if tool.name in self._tools:
            logger.warning("Tool %s already registered — overwriting", tool.name)
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s", tool.name)

    def register_external(self, name: str, schema: ToolSchema,
                          handler: Callable[[dict], str],
                          system_prompt_rule: str) -> None:
        """Register a tool from an external source (e.g., MCP server)."""
        self._external_handlers[name] = handler
        self._external_rules[name] = system_prompt_rule
        logger.info("Registered external tool: %s", name)

    def execute(
        self,
        call: ToolCall,
        *,
        ingress_tracker: IngressTracker | None = None,
        trust_state: ConversationTrustState | None = None,
        source_attribution: str = "",
        excerpt: str = "",
        review_callback: Callable[[ToolCall, DispatchDecision], str] | None = None,
        origin: CallOrigin = CallOrigin.MODEL,
        search_ask: SearchAsk | None = None,
    ) -> ToolResult:
        """Execute a tool call, wrapped in a ``tool.execute`` decision span.

        The span (a no-op unless ``INTERGEN_TRACE`` is on) gives the decision-
        trace harness a per-tool record on the path: the tool name, its argument
        shape (content-gated — args only land under INTERGEN_TRACE_CONTENT, with
        credential-shaped keys redacted), and the fired outcome
        (success/executed/blocked), nested under whatever routing span is active.
        A nested ``tool.gate`` span inside :meth:`_execute_impl` records the
        provenance-gate verdict. All dispatch logic lives in ``_execute_impl``;
        this wrapper only observes, so tracing changes no dispatch behavior.
        """
        with get_tracer().span("tool.execute", kind="tool") as _tspan:
            _tspan.set_attribute("tool_name", call.name)
            _tspan.set_content("tool_args", call.arguments)
            _t0 = time.monotonic()
            result = self._execute_impl(
                call,
                ingress_tracker=ingress_tracker,
                trust_state=trust_state,
                source_attribution=source_attribution,
                excerpt=excerpt,
                review_callback=review_callback,
                origin=origin,
                search_ask=search_ask,
            )
            _tspan.set_attribute("success", result.success)
            _tspan.set_attribute("executed", getattr(result, "executed", True))
            _tspan.set_attribute("blocked", getattr(result, "blocked", False))
            # The call on the turn's decision path: what was asked, by whom,
            # what came back. Its consent entry (written beside the audit row,
            # _audit_log_decision / _audit_log_refusal) carries the same call id.
            # The output is recorded whole; the glass redacts every row it
            # writes (glass.redact_persisted), this field included.
            glass.path_entry(
                "tools", event="result", tool=call.name, call_id=call.call_id,
                arguments=call.arguments,
                origin=getattr(origin, "value", repr(origin)),
                success=result.success,
                executed=getattr(result, "executed", True),
                blocked=getattr(result, "blocked", False),
                outcome=getattr(result, "outcome", None),
                output=result.content,
                dur_ms=round((time.monotonic() - _t0) * 1000.0, 3))
            return result

    def _execute_impl(
        self,
        call: ToolCall,
        *,
        ingress_tracker: IngressTracker | None = None,
        trust_state: ConversationTrustState | None = None,
        source_attribution: str = "",
        excerpt: str = "",
        review_callback: Callable[[ToolCall, DispatchDecision], str] | None = None,
        origin: CallOrigin = CallOrigin.MODEL,
        search_ask: SearchAsk | None = None,
    ) -> ToolResult:
        """Execute a tool call through the D-008 provenance dispatcher gate.

        Per RFC docs/architecture/intergen-provenance-gate-design.md:
        every tool call passes through `verify_tool_call` before execution.

        Args:
            call: the LabelledToolCall (must have non-None
                source_of_request per §5.3; enforced by
                ToolCall.__post_init__).
            ingress_tracker: per-turn ingress-fire tracker. If None, a fresh
                empty tracker is used — appropriate for direct CLI
                invocations where no ingress context exists.
            trust_state: per-conversation symmetric allow/deny memory.
                If None, a fresh empty state is used.
            source_attribution: short label identifying the ingress source
                that motivated this call (URL / file path / etc.). Empty
                string for user-direct calls with no ingress context.
            excerpt: optional snippet of ingress content that motivated
                this call; surfaced in the review modal + audit log.
            review_callback: invoked when dispatch returns hold_for_review.
                Receives (call, decision) and returns one of
                "allow_once" | "allow_conversation" | "deny" |
                "deny_conversation", or, when no person decided, one of
                REVIEW_TIMED_OUT | REVIEW_REFUSED | REVIEW_FAILED |
                REFUSED_WITHOUT_REVIEW | REVIEW_ENDED. Only the
                two approvals run the call; every other answer, including one
                not listed here, is recorded as what happened and runs
                nothing. TWO-LAYER timeout architecture:

                - review_callback=None at THIS registry boundary means
                  IMMEDIATE deny (no-UI-available context — headless test
                  contexts, boot phase, automated tooling). The registry
                  does NOT itself wait for a UI to appear; it refuses on
                  the spot so a no-UI path cannot silently execute a
                  held action.

                - When a callback IS provided (the router constructs one
                  via intergen.review_modal.make_review_callback), the
                  RFC §7.2 one-hour-implicit-deny semantic lives INSIDE
                  review_modal.py: the libnotify-fallback path posts a
                  critical notification + polls _session_active in 5s
                  intervals up to FALLBACK_TIMEOUT_SECONDS=3600 + re-
                  prompts via zenity on session-return + implicit-denies
                  on timeout. The 1-hour promise is preserved through
                  review_modal.py, not through tool_registry.
            origin: who built the call (CallOrigin). The default is the
                model, so a caller that names no origin is gated as one.
            search_ask: the router's SearchAsk when the person asked for a
                web search on this turn; a web_search the model built runs
                only with one (see the section above CallOrigin).

        Returns:
            ToolResult with success=True on completed dispatch + tool
            success; success=False on gate-refusal, user-deny, validation
            failure, or tool exception. The result.content includes a
            human-readable reason on the failure paths.
        """
        t0 = time.monotonic()
        tool_name = call.name
        arguments = call.arguments

        if ingress_tracker is None:
            ingress_tracker = IngressTracker()
        if trust_state is None:
            trust_state = ConversationTrustState()

        tool = self._tools.get(tool_name)
        external_handler = self._external_handlers.get(tool_name)
        # A call refused here, at validation or at the BLOCKED tier below,
        # returns before the gate is consulted; RFC §9 records every tool call,
        # so each of these refusals is recorded like the gate's own reject
        # before it returns (_audit_log_refusal).
        if tool is None and external_handler is None:
            content = f"Unknown tool: {tool_name}"
            self._audit_log_refusal(
                call, ingress_tracker, content,
                source_attribution=source_attribution,
                excerpt=excerpt,
            )
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=content,
                success=False,
                executed=False,
                outcome="unknown_tool",
                effective_provenance=_effective_before_gate(
                    call, ingress_tracker).value,
            )

        # Per-tool usage tally for the Usage tab's Top Tools chart (real tool
        # invocations only — counted after the unknown-tool guard above).
        self._tool_call_counts[tool_name] = self._tool_call_counts.get(tool_name, 0) + 1

        # Argument validation (tool-side) runs before the gate so a malformed
        # call does not waste a review-modal cycle.
        if tool is not None:
            validation_error = tool.validate_arguments(arguments)
            if validation_error:
                content = f"{VALIDATION_REFUSED}{validation_error}"
                self._audit_log_refusal(
                    call, ingress_tracker, content,
                    source_attribution=source_attribution,
                    excerpt=excerpt,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=content,
                    success=False,
                    executed=False,
                    outcome="invalid_arguments",
                    effective_provenance=_effective_before_gate(
                        call, ingress_tracker).value,
                )

            # BLOCKED tier remains a tool-level refusal independent of
            # provenance — does not escalate regardless of declared source.
            # Its WORDING reads the provenance, though: the redirect names the
            # call's own packages only for the person's own ask, as the gate
            # would read it (get_blocked_response).
            safety = tool.classify_safety(arguments)
            if safety == SafetyTier.BLOCKED:
                from intergen.safety import get_blocked_response
                cmd = arguments.get("command", str(arguments))
                effective = _effective_before_gate(call, ingress_tracker)
                self._audit_log_refusal(
                    call, ingress_tracker,
                    f"Blocked: {tool_name} classifies this call in the BLOCKED "
                    "safety tier, so it was refused before the dispatcher gate.",
                    source_attribution=source_attribution,
                    excerpt=excerpt,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=get_blocked_response(
                        cmd, provenance=effective.value),
                    success=False,
                    # Safety block — surface it like run_command's so the router
                    # synth-skips it (deterministic refusal, never narrated).
                    blocked=True,
                    executed=False,
                    outcome="blocked",
                    effective_provenance=effective.value,
                )

            # A CALL THAT WILL BE REFUSED IS REFUSED BEFORE ANY CARD. Every
            # surface describes the act from the argv the tool's own builder
            # returns (describe_call). When there is no act to describe — a
            # shell construct the shell-free executor would refuse after an
            # approval, a relative path no card can resolve — the call is
            # refused here, with its reason, and no person is asked to approve
            # an act that will not happen. A description that cannot be
            # computed refuses too (fail closed).
            try:
                act = describe_call(call, tool)
            except Exception as exc:  # noqa: BLE001 — fail closed
                act = ActDescription(
                    "", (), False,
                    f"the act could not be described ({type(exc).__name__}: "
                    f"{exc}), so it was not proposed", True,
                    "the call could not be described, so it was not proposed",
                    "gate_rejected")
            if act.refusal:
                self._audit_log_refusal(
                    call, ingress_tracker,
                    f"Refused before review: {act.refusal}",
                    source_attribution=source_attribution,
                    excerpt=excerpt,
                )
                # Never `blocked`: the classifier's own block returned above,
                # so a refusal here is the executor's or the tool's, told in
                # its own words (act.words) — the classifier's line in their
                # place dropped the reason. A refusal with no such words is told
                # its reason as the renderer tells any refusal before a card.
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=act.refusal,
                    success=False,
                    executed=False,
                    outcome=act.kind,
                    refusal=act.words,
                    effective_provenance=_effective_before_gate(
                        call, ingress_tracker).value,
                )

        # Classify tool risk tier + fire the dispatcher gate. The gate verdict
        # is the decision-trace's provenance seam (06-11 harness plan item 3):
        # a ``tool.gate`` span records the gate ACTION (execute/hold/reject),
        # the effective provenance after watermark escalation, and the risk
        # tier, so the harness can see WHY a tool did or didn't fire — not just
        # that it did. No-op unless INTERGEN_TRACE is on; the gate reason is
        # content-gated (it can quote a source attribution).
        risk_tier = _classify_risk_tier(tool, arguments, tool_name)
        with get_tracer().span("tool.gate", kind="gate") as _gspan:
            decision = verify_tool_call(
                call,
                ingress_tracker,
                trust_state,
                risk_tier,
                source_attribution,
            )
            _gspan.set_attribute("gate_action", decision.action)
            _gspan.set_attribute("risk_tier",
                                 getattr(risk_tier, "value", str(risk_tier)))
            _gspan.set_attribute("effective_provenance",
                                 getattr(decision.effective_provenance, "value",
                                         str(decision.effective_provenance)))
            _gspan.set_attribute("needs_pkexec", decision.needs_pkexec)
            _gspan.set_content("gate_reason", decision.reason)

        # THE PERSON'S WORD FOR A SEARCH (the section above CallOrigin). Read
        # after the gate's verdict exists, so the record carries it, and before
        # anything can run, raise a card or answer from the read cache: a held
        # search sends nothing and asks nothing. Only an origin the caller
        # stated as CODE is exempt, and only a SearchAsk the router made opens
        # it; the call's own arguments are never consulted for either.
        if (tool_name in _PERSONS_WORD_TOOLS
                and origin is not CallOrigin.CODE
                and not is_search_ask(search_ask)):
            self._audit_log_decision(
                call, decision, ingress_tracker,
                user_outcome=SEARCH_NOT_ASKED,
                exit_code=1,
                result_summary=SEARCH_NOT_ASKED_SUMMARY,
                source_attribution=source_attribution,
                excerpt=excerpt,
            )
            glass.emit("decision", "dispatch_held", detail={
                "tool": tool_name,
                "reason": SEARCH_NOT_ASKED,
                "origin": getattr(origin, "value", repr(origin)),
                # What stood in for the person's word, when anything did: a
                # declaration of a kind the router never makes, or an object
                # that only looks like one, is named here rather than obeyed.
                "search_ask_given": (type(search_ask).__name__
                                     if search_ask is not None else None),
            })
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=SEARCH_NOT_ASKED_SUMMARY,
                success=False,
                executed=False,
                outcome=SEARCH_NOT_ASKED,
                effective_provenance=decision.effective_provenance.value,
            )

        # DIRECT ARGUMENT REFERENCES ARE SCREENED HERE. This check can reject a
        # canonical textual reference before review or execution, but it does not
        # prove which files a tool will open and must not be described as
        # covering every tool or the whole argument structure.
        #
        # intergen/dispatch_token.py justifies keeping the per-install signing key
        # in a mode-0600 file on the ground that prompt injection "can only make
        # the LLM emit a ToolCall -- it cannot read the key file". A ToolCall can
        # read any file this account can read, and the key is owned by this
        # account, so that sentence held only as an intention. What makes it a
        # property of the code, for the three consumers that open a caller's
        # path, is the boundary in them, which decides on the file that opened;
        # this screen is defence in depth in front of it. A tool that runs as its
        # own process, run_command's child or an MCP server, opens the file
        # itself, outside that boundary; it starts behind the process boundary in
        # intergen/tool_process.py instead, where the files are inaccessible to
        # it, and this screen is defence in depth in front of that one too.
        #
        # This screen is HERE, at the one place every tool call passes, so a
        # direct textual reference is rejected before review or execution and a
        # tool added tomorrow inherits that much without its author knowing the
        # screen exists. It is not the boundary: it cannot establish which file a
        # tool will open, and the three consumers that open a caller's path
        # decide that on the object they opened. What it asks of a string is
        # whether the value is a path to one of these files, and for a parameter
        # a tool declares as a command line, whether any word of the command is;
        # a string a tool will never hand to a process opens nothing.
        #
        # It lands before the review path on purpose. A person must never be shown
        # an approval modal for reading the key that makes approvals verifiable:
        # the whole point of the key is that root can tell a human approval from an
        # injected one, so an approval prompt for it is a question that should not
        # be asked.
        secret_reason = _names_an_install_secret(
            arguments,
            tool.command_line_parameters if tool is not None else (),
        )
        if secret_reason is not None:
            refusal = (
                "Tool call refused: the arguments name "
                f"{secret_reason}. The assistant does not read its own "
                "credential files, and no approval can authorise it to."
            )
            self._audit_log_decision(
                call, decision, ingress_tracker,
                user_outcome="denied",
                exit_code=1,
                result_summary=refusal,
                source_attribution=source_attribution,
                excerpt=excerpt,
            )
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=refusal,
                success=False,
                executed=False,
                outcome="secret_refused",
                effective_provenance=decision.effective_provenance.value,
            )

        # Reject path — gate refused (missing source_of_request, prior
        # deny-conversation, or schema violation).
        if decision.action == "reject":
            self._audit_log_decision(
                call, decision, ingress_tracker,
                user_outcome="denied",
                exit_code=1,
                result_summary=decision.reason,
                source_attribution=source_attribution,
                excerpt=excerpt,
            )
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=f"Tool call refused by dispatcher: {decision.reason}",
                success=False,
                executed=False,
                # A reject the trust state decided is the person's own earlier
                # deny for the rest of the conversation, and is told as that.
                outcome=("deny_conversation" if decision.trust_state_consulted
                         else "gate_rejected"),
                effective_provenance=decision.effective_provenance.value,
            )

        # Review path — route to review_callback (the zenity/libnotify modal
        # in production; None during headless test contexts or boot phases
        # where the modal is not yet wired).
        #
        # AI-6 (option iii, decided 2026-05-29): the PRIVILEGED tier
        # ALWAYS requires a human review-modal approval, independent of the
        # gate's provenance decision. The gate is doubly inert today (per-turn
        # taint reset + a mis-specified ingress watch-list) and therefore cannot
        # be trusted to decide which privileged calls a human sees — so every
        # privileged action prompts ("friction by design" = the HG price). A
        # human approval is then bound to THIS exact call by a freshly minted,
        # single-use dispatch token that the root boundary verifies.
        # Non-privileged calls keep the gate's semantics: prompt only on
        # hold_for_review.
        # EGRESS scan (design plan §6 step 5 + decision #6 scan-on-derivation):
        # before an external/MCP tool dispatches, scan its outbound arguments for
        # exfil (secret/credential shapes, exfil URLs) so the model cannot be
        # tricked into leaking data through a tool's args. Per decision #6 the
        # INITIAL user-authorized egress (USER_DIRECT — the human directly named
        # it, reviewed via show-before-send) is trusted at source and NOT
        # auto-scanned; everything the model DERIVED (user_implied / ingress_
        # derived) is inspected. Ingress (results coming back) is always scanned
        # regardless of provenance — content arriving from outside is never
        # user-authorized. BLOCK refuses before the args leave the machine; FLAG
        # folds into the ONE consolidated human modal (decision #1) below.
        egress_flag_reason: str | None = None
        if (
            external_handler is not None
            and self._scanner is not None
            and decision.effective_provenance is not Provenance.USER_DIRECT
        ):
            egress_verdict = self._scanner.scan(
                _egress_payload(arguments),
                ScanContext(
                    surface=_scan_surface(tool_name, arguments, external=True),
                    direction=ScanDirection.EGRESS,
                    tool_name=tool_name,
                    trust_tier=decision.effective_provenance.value,
                ),
            )
            if egress_verdict.disposition is ScanDisposition.BLOCK:
                self._audit_log_decision(
                    call, decision, ingress_tracker,
                    user_outcome="egress_blocked",
                    exit_code=1,
                    result_summary=f"Sentinel egress-scan BLOCK: {egress_verdict.reason}",
                    source_attribution=source_attribution,
                    excerpt=excerpt,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=(
                        "Tool call refused: Sentinel blocked the outbound content "
                        f"before it left the machine ({egress_verdict.reason or 'exfil risk'})."
                    ),
                    success=False,
                    executed=False,
                    outcome="egress_blocked",
                    effective_provenance=decision.effective_provenance.value,
                )
            if egress_verdict.disposition is ScanDisposition.FLAG:
                egress_flag_reason = egress_verdict.reason or "outbound content flagged"

        user_outcome = "executed"
        dispatch_token_value: str | None = None
        privileged = decision.needs_pkexec
        # A held act of a tool on the privileged list that runs in the person's
        # own process — a change on their own service manager, which must not
        # cross the privileged boundary (_classify_risk_tier) — is reviewed
        # exactly as the privileged ones are: a card every time, and an
        # approval for the conversation honoured once and never remembered.
        held_in_person = (
            not privileged
            and tool_name in _PRIVILEGED_TOOLS
            and risk_tier is ToolRiskTier.USER_SCOPE_STATE_CHANGING
        )
        must_review = (
            privileged
            or held_in_person
            or decision.action == "hold_for_review"
            or egress_flag_reason is not None
        )
        if must_review:
            if review_callback is None:
                # Per RFC §7.2: no review UI implies implicit refusal rather
                # than silent execute. The gate's job is intent verification;
                # without a UI to verify, the safe behavior is refusal. No
                # person refused it, so it is recorded as no_review_ui, never
                # as "denied", the word the person's own Deny writes.
                self._audit_log_decision(
                    call, decision, ingress_tracker,
                    user_outcome=NO_REVIEW_UI,
                    exit_code=1,
                    result_summary=_held_summary(
                        decision, "No review UI available; implicit refusal"),
                    source_attribution=source_attribution,
                    excerpt=excerpt,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=(
                        "Tool call held for user review but no review UI is "
                        f"available in this context. Reason: {decision.reason}"
                    ),
                    success=False,
                    executed=False,
                    outcome=NO_REVIEW_UI,
                    effective_provenance=decision.effective_provenance.value,
                )

            # Decision #1 — ONE consolidated modal: when the gate AND the egress
            # scan both want sign-off on the same call, the human sees a single
            # prompt listing both reasons, one allow/deny. Fold the scan reason
            # into the decision the modal renders (its reason field is what
            # review_modal surfaces); default-deny is preserved by the modal.
            review_decision = decision
            if egress_flag_reason is not None:
                scan_note = f"Sentinel egress scan flagged outbound content: {egress_flag_reason}"
                review_decision = replace(
                    decision,
                    reason=(f"{decision.reason} | {scan_note}" if decision.reason else scan_note),
                )
            # The review surface lives outside the registry and can fail. A
            # callback that raised decided nothing: nothing runs, the record
            # says the review failed and keeps the exception, and no one is
            # recorded as having decided.
            review_error: Exception | None = None
            try:
                user_choice = review_callback(call, review_decision)
            except Exception as exc:
                logger.error("review callback failed for %s: %r",
                             tool_name, exc)
                review_error = exc
                user_choice = REVIEW_FAILED
            if user_choice in ("deny", "deny_conversation"):
                if user_choice == "deny_conversation":
                    record_user_decision(
                        UserDecision.now_utc(user_choice),
                        call, trust_state, source_attribution,
                    )
                self._audit_log_decision(
                    call, decision, ingress_tracker,
                    user_outcome=(
                        "denied" if user_choice == "deny" else "deny_conversation"
                    ),
                    exit_code=1,
                    result_summary=_held_summary(
                        decision, "User denied via review modal"),
                    source_attribution=source_attribution,
                    excerpt=excerpt,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content="Tool call denied by user via review modal.",
                    success=False,
                    executed=False,
                    # The line above is this result's AUDIT record; it is not a
                    # message for the person who pressed deny. The mark is what
                    # tells every delivery path to render its own refusal rather
                    # than deliver that sentence (see gate_refusal_message).
                    denied_by_user=True,
                    outcome=("denied" if user_choice == "deny"
                             else "deny_conversation"),
                    effective_provenance=decision.effective_provenance.value,
                )
            if user_choice not in REVIEW_APPROVALS:
                # Nobody approved it and nobody denied it: the card went
                # unanswered, a second card under an open id was refused, or
                # the review could not be shown or its answer read. Nothing
                # runs, and the record says which — never that the person
                # denied it, and never, for an answer not known here, that it
                # was approved.
                outcome, what = review_not_decided(user_choice)
                self._audit_log_decision(
                    call, decision, ingress_tracker,
                    user_outcome=outcome,
                    exit_code=1,
                    result_summary=_held_summary(decision, f"Not run: {what}" + (
                        f" ({type(review_error).__name__}: {review_error})"
                        if review_error is not None else "")),
                    source_attribution=source_attribution,
                    excerpt=excerpt,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=f"Nothing was run: {what}.",
                    success=False,
                    executed=False,
                    outcome=outcome,
                    effective_provenance=decision.effective_provenance.value,
                )
            # Approved (allow_once / allow_conversation) — proceed to execution.
            if privileged:
                # No allow-for-conversation caching for the privileged tier
                # (closes the AI-14 cache landmine): each privileged action is a
                # FRESH human approval + a FRESH single-use token. An
                # allow_conversation choice is honored as allow_once and is
                # NEVER recorded to trust_state, so a prior conversation-allow
                # can never auto-satisfy a later privileged dispatch.
                user_outcome = "allowed_once"
                try:
                    dispatch_token_value = mint_token(
                        tool_name, arguments, os.getuid(),
                    )
                except DispatchTokenError as exc:
                    # The human approved, but without a verifiable token the root
                    # boundary cannot confirm that approval — fail CLOSED rather
                    # than dispatch an unbacked privileged action. The only
                    # decision the person made was an approval, so the record
                    # names the token, never "denied".
                    logger.error(
                        "dispatch-token mint failed for %s: %s", tool_name, exc
                    )
                    self._audit_log_decision(
                        call, decision, ingress_tracker,
                        user_outcome=DISPATCH_TOKEN_FAILED,
                        exit_code=1,
                        result_summary=f"dispatch-token mint failed: {exc}",
                        source_attribution=source_attribution,
                        excerpt=excerpt,
                    )
                    return ToolResult(
                        call_id=call.call_id,
                        name=tool_name,
                        content=(
                            "Privileged action approved, but the approval token "
                            "could not be minted (signing key missing or "
                            "unreadable); refusing dispatch. Run 'intergen setup' "
                            "to (re)provision the dispatch key."
                        ),
                        success=False,
                        executed=False,
                        outcome=DISPATCH_TOKEN_FAILED,
                        effective_provenance=decision.effective_provenance.value,
                    )
            elif held_in_person:
                # Honoured once and never recorded, as for the privileged tier.
                user_outcome = "allowed_once"
            elif user_choice == "allow_conversation":
                record_user_decision(
                    UserDecision.now_utc(user_choice),
                    call, trust_state, source_attribution,
                )
                user_outcome = "allowed_conversation"
            else:
                user_outcome = "allowed_once"

        # Execute path — dispatch to tool or external handler.
        # When decision.needs_pkexec is True AND the tool is a built-in
        # (external handlers cannot route through pkexec — they live in
        # a separate trust domain and have their own authentication
        # surface), route through the pkexec runner per the build-system
        # coordinator's 49a585ca integration contract. Otherwise direct
        # tool.execute() in the user context. Per RFC §6 line 161 the
        # provenance gate (intent) ran upstream of this point and
        # PolicyKit (authentication) runs at pkexec invocation; both
        # fire for privileged operations.
        if tool is not None:
            try:
                if decision.needs_pkexec:
                    result = self._dispatch_via_pkexec(
                        call, tool_name, arguments, dispatch_token_value,
                    )
                    if not result.executed and not result.effective_provenance:
                        # A refusal before anything started (the escalation
                        # posture) is told under the provenance the gate read,
                        # as every other refusal is (handoff_withheld).
                        result.effective_provenance = (
                            decision.effective_provenance.value)
                else:
                    # Read-through cache for AUTO read-only calls: a fresh hit
                    # skips the system call (and downstream re-synthesis). Privileged
                    # (pkexec) calls are never cached — they take the branch above.
                    _uid = os.getuid()
                    result = self._cache.get(tool_name, arguments, _uid)
                    if result is None:
                        result = tool.execute(arguments)
                        self._cache.put(tool_name, arguments, _uid, result)
                exit_code = 0 if result.success else 1
                if getattr(result, "image_data", None):
                    # A picture the tool produced meets the image gate here,
                    # before the result leaves the registry (intergen.image_ingress).
                    result = self._gate_tool_picture(result, tool_name, arguments)
                    exit_code = 0 if result.success else 1
                # Invalidate cached reads after a successful state-changing call
                # (no-op for read-only tools). Covers both dispatch paths above.
                if result.success:
                    self._cache.invalidate_for_write(
                        tool_name, arguments, os.getuid())
            except Exception as e:  # noqa: BLE001 — wrap into ToolResult
                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.error(
                    "Tool %s failed in %.0fms: %s", tool_name, elapsed_ms, e
                )
                result = ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=f"Error executing {tool_name}: {e}",
                    success=False,
                )
                exit_code = 1
        else:
            # External handler dispatch (MCP / cloud / etc.)
            try:
                result_text = external_handler(arguments)  # type: ignore[misc]
                result = ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=result_text,
                    success=True,
                )
                exit_code = 0
            except Exception as e:  # noqa: BLE001
                logger.error("External tool %s failed: %s", tool_name, e)
                result = ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=f"Error: {e}",
                    success=False,
                )
                exit_code = 1

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.debug(
            "Tool %s completed in %.0fms (%d chars)",
            tool_name, elapsed_ms, len(result.content),
        )

        # INGRESS scan (design plan §6 step 10 + decision #2): before a result
        # from an external/MCP tool OR an INGRESS_TOOLS_V1 tool re-enters the LLM
        # context, scan it for injection content. ALWAYS scanned regardless of
        # provenance — content arriving from outside is never user-authorized.
        #   BLOCK -> withhold: the model gets a "result withheld" notice, never
        #            the poison (decision #2, high-confidence drop).
        #   FLAG  -> the human modal (decision #2, medium-confidence); a deny or
        #            a no-UI context fails closed to withhold (HG #10); an
        #            approval admits the content and is recorded as one.
        # Runs BEFORE the audit write so the audit reflects the withhold outcome;
        # the verdict reason (not the raw poison) is what lands in the log.
        # External/MCP content is scanned whenever it will re-enter context —
        # success OR failure (defense-in-depth, peer-review hardening). The router feeds
        # result.content back to the LLM unconditionally, so a handler that returns
        # FAILURE carrying outside-influenced text must not bypass the scan: the
        # stated guarantee ("content from outside is ALWAYS scanned regardless of
        # provenance") has to hold on the failure path too. Built-in INGRESS_TOOLS_V1
        # results keep the success gate — their failure content is our own local
        # error string, not outside text. (The scanner ALLOWs empty content, so an
        # empty result still short-circuits cleanly.)
        result_summary = result.content
        content_withheld = False
        scan_ingress = self._scanner is not None and (
            external_handler is not None
            or (result.success and tool_name in INGRESS_TOOLS_V1)
        )
        if scan_ingress:
            # G3-22 structured returns / D-3: when a tool emits a model_summary,
            # the SUMMARY is the model-facing field — the router and web server
            # feed `model_summary or content` back to the model. So the summary
            # is a NEW model-facing trust boundary and MUST be scanned, not just
            # `content`. Scan BOTH (content = the full source, catches injection
            # anywhere incl. the middle a head/tail summary would drop; summary =
            # the derived model-facing text, catches injection in a non-subset
            # transform). Injection in EITHER blocks; a block withholds BOTH so a
            # poisoned summary can never reach the model behind a clean content
            # or vice-versa. (docs/architecture/intergen-structured-tool-returns-
            # design.md §7.)
            scan_text = result.content
            if result.model_summary is not None:
                scan_text = f"{result.content}\n{result.model_summary}"
            ingress_verdict = self._scanner.scan(
                scan_text,
                ScanContext(
                    surface=_scan_surface(
                        tool_name, arguments, external=external_handler is not None
                    ),
                    direction=ScanDirection.INGRESS,
                    tool_name=tool_name,
                ),
            )
            if ingress_verdict.disposition is ScanDisposition.BLOCK:
                content_withheld = True
                user_outcome = f"{user_outcome}+ingress_blocked"
                result_summary = (
                    f"Sentinel ingress-scan BLOCK: {ingress_verdict.reason}; "
                    "content withheld from context"
                )
                result.content = _withheld_notice(tool_name, ingress_verdict.reason)
                # Withhold the model-facing summary too — else the model would
                # fall through to a scanned-clean notice but a poisoned summary
                # could survive. None => router/web fall back to content (= the
                # withheld notice).
                result.model_summary = None
            elif ingress_verdict.disposition is ScanDisposition.FLAG:
                flag_decision = replace(
                    decision,
                    reason=(
                        "Sentinel ingress scan flagged the returned content: "
                        f"{ingress_verdict.reason}"
                    ),
                )
                # The call has already run here, so a callback that raised
                # must still leave its record: the content is withheld and the
                # review recorded as failed, the exception kept.
                flag_error: Exception | None = None
                if review_callback is None:
                    choice = None
                else:
                    try:
                        choice = review_callback(call, flag_decision)
                    except Exception as exc:
                        logger.error("ingress-flag review callback failed for "
                                     "%s: %r", tool_name, exc)
                        flag_error = exc
                        choice = REVIEW_FAILED
                # Only an approval admits the flagged content; any other
                # answer withholds it, an answer not known here included.
                if choice not in REVIEW_APPROVALS:
                    content_withheld = True
                    if choice in ("deny", "deny_conversation"):
                        user_outcome = f"{user_outcome}+ingress_flag_denied"
                        result_summary = (
                            f"Sentinel ingress-scan FLAG denied: {ingress_verdict.reason}; "
                            "content withheld from context"
                        )
                    else:
                        # Nobody denied it: withheld all the same, and recorded
                        # as what happened rather than as the person's refusal.
                        outcome, what = (
                            (NO_REVIEW_UI, "no review surface was available")
                            if review_callback is None
                            else review_not_decided(choice))
                        user_outcome = f"{user_outcome}+ingress_flag_{outcome}"
                        if flag_error is not None:
                            what = (f"{what}; {type(flag_error).__name__}: "
                                    f"{flag_error}")
                        result_summary = (
                            f"Sentinel ingress-scan FLAG not approved ({what}): "
                            f"{ingress_verdict.reason}; content withheld from context"
                        )
                    result.content = _withheld_notice(tool_name, ingress_verdict.reason)
                    result.model_summary = None
                else:
                    # The person approved it: the content is admitted, and the
                    # record names that approval and the verdict's reason, never
                    # the flagged text as its summary.
                    user_outcome = f"{user_outcome}+ingress_flag_approved"
                    result_summary = (
                        f"Sentinel ingress-scan FLAG approved ({choice}): "
                        f"{ingress_verdict.reason}; content admitted to context"
                    )

        # Audit-log the dispatch decision + execution outcome (+ scan verdict).
        self._audit_log_decision(
            call, decision, ingress_tracker,
            user_outcome=user_outcome,
            exit_code=exit_code,
            result_summary=result_summary,
            source_attribution=source_attribution,
            excerpt=excerpt,
        )

        # Record this tool fire in the per-turn tracker so subsequent
        # dispatches in the same conversation turn observe it in their
        # watermark history per RFC §5.1 — the tracker captures what
        # fired BEFORE the call under review, so the record happens
        # AFTER our dispatch decision on this call.
        ingress_tracker.record_tool_call(tool_name)

        # AI-2 (audit 2026-05-29): spotlight ingress output at this CENTRAL
        # dispatch chokepoint — wrap any successful result from an INGRESS_TOOLS_V1
        # tool OR an external/MCP tool in UNTRUSTED-INGRESS markers BEFORE it
        # re-enters the LLM context, so the model is structurally aware of the
        # trust boundary and labels any follow-on action ingress_derived (RFC §10
        # spotlighting). Sentinel build seq step 3 BROADENS this to external/MCP:
        # MCP is the canonical outside-party surface, even more than read_file.
        # Withheld content is NOT spotlighted (the notice is our own text, not
        # untrusted ingress). The same INGRESS_TOOLS_V1 set drives both this and
        # the §5.1 watermark, so the two cannot drift apart (the AI-13 root cause).
        # External/MCP results are spotlighted success-or-not (matching the
        # ingress-scan reach above — defense-in-depth); built-in
        # INGRESS_TOOLS_V1 keeps the success gate (failure content is our own text).
        if (
            not content_withheld
            and (
                external_handler is not None
                or (result.success and tool_name in INGRESS_TOOLS_V1)
            )
        ):
            source, source_type = _ingress_source_attribution(tool_name, arguments)
            if not is_wrapped(result.content):
                result.content = wrap_ingress_content(
                    result.content, source, source_type)
            # The model_summary is ALSO model-facing untrusted ingress (D-3), so
            # wrap it with the same markers — the model must see the trust
            # boundary on whichever field it synthesizes from. The is_wrapped
            # guards keep this idempotent if a cached result is re-served (the
            # ToolCache stores the result before this point, so a cache hit can
            # re-enter already-wrapped; do not double-wrap).
            if (result.model_summary is not None
                    and not is_wrapped(result.model_summary)):
                result.model_summary = wrap_ingress_content(
                    result.model_summary, source, source_type)

        return result

    @staticmethod
    def _gate_tool_picture(result: ToolResult, tool_name: str,
                           arguments: dict[str, Any]) -> ToolResult:
        """A picture a tool produced (a screen capture) meets the image gate
        before the result leaves the registry. Admitted: the result is returned
        as it is, its picture registered for the model layer. Refused: the
        result carries the gate's own sentence as its refusal and no picture —
        the model never sees it, and every delivery path tells the sentence
        without a synthesis hop (gate_refusal_message). It stays executed: the
        capture ran, and the record keeps that. It is not `blocked`, which is
        the safety classifier's alone; told as a safety block, the sentence was
        kept only where the call happened to carry no command."""
        from intergen import image_ingress
        source, source_type = _ingress_source_attribution(tool_name, arguments)
        admission = image_ingress.admit(
            result.image_data, source=source, source_type=source_type)
        if admission.admitted:
            return result
        sentence = image_ingress.refusal_text(admission)
        return ToolResult(
            call_id=result.call_id, name=result.name,
            content=sentence,
            success=False, executed=True, image_data=None,
            outcome="image_refused", refusal=sentence,
        )

    @staticmethod
    def _dispatch_via_pkexec(
        call: ToolCall,
        tool_name: str,
        arguments: dict[str, Any],
        dispatch_token: str | None = None,
    ) -> ToolResult:
        """Route a privileged tool call through the pkexec runner.

        PolicyKit (authentication) wraps a thin sh shim at
        /usr/bin/intergen-privileged-runner that hands off to
        `python3 -m intergen.privileged_dispatch --request-id <request_id>` in
        root context. The privileged dispatcher re-validates against the same
        _PRIVILEGED_TOOLS allowlist + per-tool argument schema the gate
        consulted; defense-in-depth at the trust boundary.

        TWO THINGS CHANGED HERE, 2026-08-24, and they share this one call.

        1. WHO STARTS THE RUNNER. The daemon no longer starts pkexec as its own
           child. Its unit sets NoNewPrivileges=yes, which every child inherits
           and which cannot be cleared; under that flag the kernel ignores
           pkexec's setuid bit, so pkexec starts unprivileged, sees its own
           effective uid is not root, and refuses with exit 127 BEFORE PolicyKit
           is contacted. That is why no install of R001.1 could perform any
           privileged action. The daemon now asks its own systemd user manager
           to start a short-lived unit that runs the same pkexec invocation. The
           manager is not running under the flag, so the unit's process does not
           carry it. Measured on real hardware: a caller carrying the flag asked
           the manager to run a probe and the probe reported NoNewPrivs 0, with
           the caller's own context reporting 1 in the same capture.

           The daemon's hardening is not touched, and the PolicyKit action is
           the same narrow, purpose-built one whose exec.path names this runner.
           The unit is deliberately plain: imposing NoNewPrivileges on it would
           reproduce the defect exactly.

        2. WHAT IS ON THE COMMAND LINE. The tool arguments and the approval
           token used to be argv words 3, 4 and 5, and a command line is
           world-readable through /proc for the life of the process. They now
           travel in an owner-only file (intergen.privileged_request); the
           command line carries the tool's name and the request's identifier.
           pkexec still scrubs the environment; this does not fight that, it
           changes what needs to survive.

        3. ONE AUTHENTICATION IS ONE CALL (2026-10-22). pkexec chooses the
           PolicyKit action from the program and its first argument, so the
           tool's name selects the tool's own action, auth_admin with no keep,
           whose prompt names the kind of action. pkexec asks PolicyKit about
           its parent, and the parent of every runner is this account's user
           manager: a kept authorization would have covered any runner the
           account asked that manager for in the next five minutes. The name is
           the action's class, never its arguments.

        A None token means a privileged dispatch was reached without a minted
        approval — fail CLOSED rather than invoke the runner tokenless (the
        runner and dispatcher also refuse, but refusing here avoids the round
        trip and makes the invariant explicit). A request that cannot be written
        also fails closed: the only other way to carry those values is the
        command line, and that is the exposure being closed.

        Return shape: ToolResult constructed from the runner's stdout and exit
        code. systemd-run --wait propagates the unit's exit status as its own
        (measured), so 0 = success and non-zero = failure (validation, refusal,
        an authentication denial, or the manager itself failing to start it).
        """
        # THE POSTURE IS READ FIRST, BEFORE ANYTHING IS STAGED OR STARTED.
        # Under a refusing posture nothing is written to disk, no unit is asked
        # for, and no process is started — so there is no path from here to an
        # authentication dialog. The row is emitted on BOTH postures: a daemon
        # that escalates and one that refuses must be told apart from the record
        # alone, never inferred from the absence of a line.
        posture = privileged_escalation_posture()
        glass.emit("dispatch", "privileged_escalation", detail={
            "tool": tool_name,
            "escalates": posture.allows,
            "setting": posture.setting,
            "source": posture.source,
            "variable": PRIVILEGED_ESCALATION_ENV,
        })
        if not posture.allows:
            logger.warning(
                "privileged dispatch of %s refused at the boundary: %s=%r "
                "(only %r permits escalation)",
                tool_name, PRIVILEGED_ESCALATION_ENV, posture.setting,
                ESCALATION_ALLOWED,
            )
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=(
                    f"This assistant is running with administrator actions "
                    f"turned off, so {tool_name} was refused before anything "
                    f"was started. Nothing was changed on this machine and no "
                    f"password was asked for. You can still make the change "
                    f"yourself in a terminal."
                ),
                success=False,
                executed=False,
                outcome="escalation_refused",
            )

        if dispatch_token is None:
            logger.error(
                "privileged dispatch of %s reached without an approval token; "
                "refusing", tool_name,
            )
            # Nothing was dispatched: refused before the runner, told in these
            # words, never narrated as a run that errored.
            words = (f"{tool_name} cannot be dispatched without the approval "
                     f"token a person's approval mints, so it was refused (fail "
                     f"closed)")
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=(
                    f"Privileged tool {tool_name} cannot be dispatched without a "
                    "human-approval token; refusing (fail closed)."
                ),
                success=False,
                executed=False,
                outcome="not_started",
                refusal=words,
            )

        try:
            request_path = privileged_request.write_request(
                tool_name, arguments, dispatch_token,
            )
        except privileged_request.RequestError as exc:
            logger.error(
                "cannot stage the privileged request for %s: %s", tool_name, exc,
            )
            words = (f"the privileged request for {tool_name} could not be "
                     f"written, so the action was not dispatched: {exc}")
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=words,
                success=False,
                executed=False,
                outcome="not_started",
                refusal=words,
            )

        # A recognisable unit name so the dispatch is findable in the journal
        # afterwards; random so two dispatches in flight cannot collide.
        unit_name = f"intergen-privileged-{secrets.token_hex(8)}"

        # WHAT CROSSES THE BOUNDARY IS AN IDENTIFIER, NOT A PATH (2026-08-24).
        # The runner used to receive the request file's full path and root
        # defended itself by inspecting whatever it found there. Those checks
        # remain, but the input class they defend against no longer needs to
        # exist: the request reference on this command line is thirty-two hex
        # characters, and the privileged side derives the path itself from the
        # uid pkexec reports. A traversal, an absolute path or a symlinked
        # parent is not rejected here — it cannot be written down. The tool's
        # name rides ahead of it (2026-10-22): it is how pkexec picks the
        # tool's PolicyKit action, and the root side refuses a request naming
        # any other tool.
        request_id = privileged_request.request_id_for(request_path)

        # Collect anything a previous dispatch left behind. The one path that
        # deliberately leaves a request is the timeout below, where the unit
        # outlives this client and may still be about to read it; the next
        # dispatch is the natural moment to decide that a leftover has stopped
        # being possibly-in-flight. Housekeeping only — it never refuses a
        # dispatch, and the bound is far longer than the unit's own ceiling.
        collected = privileged_request.prune_stale_requests()
        if collected:
            logger.info(
                "collected %d stale privileged request(s) left by earlier "
                "dispatches", len(collected),
            )

        # The transient unit's properties are FIXED and request-independent.
        # The user manager is a launch mechanism, not an authenticity boundary,
        # so nothing about the request may influence how the unit is
        # configured.
        #
        # EXACTLY ONE property is set, and it is a bound rather than a policy:
        # RuntimeMaxSec stops a runner that never returns from holding the
        # boundary open forever. Killing this process would not do it — with
        # --wait, systemd-run is a client, and the unit outlives it — so the
        # ceiling has to be on the unit. NoNewPrivileges is deliberately NOT
        # named, not even as "no": the default is already no, and a unit that
        # mentions the flag at all is one edit away from reproducing the defect
        # this whole design exists to correct.
        argv = [
            _SYSTEMD_RUN, "--user", "--quiet", "--collect", "--wait", "--pipe",
            f"--unit={unit_name}",
            f"--property=RuntimeMaxSec={_DISPATCH_UNIT_MAX_SECONDS}",
            # Everything after this is the command. A request identifier is
            # generated by us and is hex, but separating explicitly means no
            # future argument shape can be read as an option.
            "--",
            "pkexec", _PKEXEC_RUNNER_PATH, tool_name, request_id,
        ]

        leave_request = False
        try:
            # The unit meets the gate every tool's service meets: a drop-in or
            # dependency directory the account can change would apply to it
            # (intergen-privileged-.service.d, intergen-.service.d, service.d
            # and the rest), and could replace the command the person's
            # authentication approves. Refused before anything is started, and
            # the request written above is discarded in the finally below.
            try:
                tool_process.check_unit_dropins(f"{unit_name}.service")
            except tool_process.ToolProcessNotStarted as exc:
                logger.error("privileged dispatch of %s not started: %s",
                             tool_name, exc)
                # Told in these words (the directory, the reason, the remedy),
                # never as the approval a person could not give here.
                words = (f"{tool_name} was not dispatched, and no password "
                         f"was asked for: {exc}")
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=words,
                    success=False,
                    executed=False,
                    outcome="not_started",
                    refusal=words,
                )
            try:
                completed = subprocess.run(
                    argv, capture_output=True, text=True, check=False,
                    timeout=_DISPATCH_WAIT_MAX_SECONDS,
                )
            except subprocess.TimeoutExpired:
                # LEAVE THE REQUEST. Every other failure here means the runner
                # never got there, so removing it is this side's half of the
                # contract. A timeout is the one case where that is false: the
                # unit outlives this client by construction, so the runner may
                # be about to read the very file this would delete. Removing it
                # would not clean anything up — it would turn a dispatch that
                # was going to complete into one that fails at the privilege
                # boundary, and make an already-unknown outcome less knowable.
                # prune_stale_requests above is what eventually collects it.
                leave_request = True
                # THE OUTCOME IS NOT KNOWN, and saying anything more definite
                # would be a claim this code cannot support. systemd-run was a
                # client; the unit outlives it. By the time this fires the
                # runner may never have started, may be waiting on a dialog
                # nobody answered, or may have spent the approval and completed
                # the action. "It failed" would be false comfort, and it is the
                # sentence that would make an automatic retry look safe, so it
                # is not said. Nothing is retried here: a fresh approval proves
                # a person authorized the action, not that the previous attempt
                # did not perform it.
                logger.error(
                    "privileged dispatch of %s exceeded %ss with no answer; "
                    "outcome unknown (unit %s)",
                    tool_name, _DISPATCH_WAIT_MAX_SECONDS, unit_name,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=(
                        f"the outcome of {tool_name} is not known. The "
                        f"privileged dispatch did not report within "
                        f"{_DISPATCH_WAIT_MAX_SECONDS} seconds, so it was "
                        f"abandoned here — but the unit that carries it runs "
                        f"independently of this wait, and the action may have "
                        f"been performed. It has NOT been attempted again, "
                        f"because repeating an action that may already have "
                        f"happened is a decision for you rather than for this "
                        f"program. What ran is the transient unit "
                        f"{unit_name}; its own record is the account of what "
                        f"happened."
                    ),
                    success=False,
                )
            except FileNotFoundError as exc:
                logger.error(
                    "cannot start the privileged runner for %s: %s",
                    tool_name, exc,
                )
                words = (f"{_SYSTEMD_RUN} is not available, so the privileged "
                         f"runner for {tool_name} could not be started. It is "
                         f"part of systemd; if it is missing the installation "
                         f"is broken upstream of this point.")
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=words,
                    success=False,
                    # The runner never started, so nothing was dispatched.
                    executed=False,
                    outcome="not_started",
                    refusal=words,
                )
            except OSError as exc:
                logger.error(
                    "privileged dispatch invocation failed for %s: %s",
                    tool_name, exc,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=f"privileged dispatch invocation error: {exc}",
                    success=False,
                    executed=False,
                    outcome="not_started",
                    refusal=(f"the privileged runner for {tool_name} could not "
                             f"be started ({exc.strerror or exc}), so nothing "
                             f"was dispatched"),
                )

            if completed.returncode == 0:
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=completed.stdout.rstrip("\n"),
                    success=True,
                )

            return ToolRegistry._privileged_failure_result(
                call, tool_name, completed, request_id=request_id,
            )
        finally:
            # The runner removes the request as it reads it. This is the
            # caller's side of that contract: on any path where the runner
            # never got there — it was never started, it died early, the
            # manager refused — the request must not be left on disk, because
            # a request left behind is an approval token left behind.
            #
            # The single exception is a timed-out wait, which sets
            # leave_request: there the runner may still be holding it. See the
            # TimeoutExpired branch.
            if not leave_request:
                privileged_request.discard_request(request_path)

    #: What a path probe can establish. Each value is a MEASUREMENT, never a
    #: cause: "absent" means stat() said ENOENT, not that anyone is at fault.
    _PATH_ABSENT = "absent"
    _PATH_PRESENT = "present"
    _PATH_UNDETERMINED = "undetermined"

    @staticmethod
    def _probe_path(path: str) -> dict:
        """Report what the filesystem says about `path`. Infer nothing.

        `os.path.exists()` is a single boolean covering several different
        findings, and the diagnostic used to speak as though it covered one:

          * it answers False when the path is absent AND when a component of it
            cannot be traversed — absence and "permission denied" are different
            findings with different repairs;
          * it answers True for a directory, a socket, a device, and for a
            regular file that nothing on this system can execute.

        So this returns the states separately:

          absent        stat() reported ENOENT.
          undetermined  stat() failed some other way; `detail` says how, and
                        NOTHING about presence is claimed.
          present       stat() succeeded; `regular` and `executable` carry the
                        two further facts that decide whether it could run.

        Decided 2026-08-24: none of these is a cause, and no caller may turn
        one into a verdict about which component is at fault.
        """
        facts: dict[str, Any] = {
            "state": ToolRegistry._PATH_UNDETERMINED,
            "detail": "",
            "regular": None,
            "executable": None,
        }
        try:
            info = os.stat(path)
        except FileNotFoundError:
            facts["state"] = ToolRegistry._PATH_ABSENT
            return facts
        except NotADirectoryError as exc:
            facts["detail"] = (
                f"a component of the path is not a directory "
                f"({exc.strerror or exc})"
            )
            return facts
        except PermissionError as exc:
            facts["detail"] = (
                f"permission denied while resolving it ({exc.strerror or exc})"
            )
            return facts
        except OSError as exc:
            facts["detail"] = f"the check itself failed ({exc.strerror or exc})"
            return facts

        facts["state"] = ToolRegistry._PATH_PRESENT
        facts["regular"] = stat_module.S_ISREG(info.st_mode)
        try:
            facts["executable"] = os.access(path, os.X_OK)
        except OSError:
            facts["executable"] = None
        return facts

    #: What the manager probe can establish, on the same terms as _probe_path:
    #: each value is a MEASUREMENT and none of them is a cause.
    _MANAGER_RUNNING = "running"
    _MANAGER_ABSENT = "absent"
    _MANAGER_UNDETERMINED = "undetermined"

    @staticmethod
    def _probe_user_manager() -> dict:
        """Ask this account's systemd user manager about itself.

        The question is put the way the dispatch reaches the manager — through
        systemctl, over the same bus — rather than by looking for a file that
        the manager happens to create. That is the whole correction: the old
        probe read a path and the sentence it produced was about a manager, and
        an independent review measured the two disagreeing.

        `answer` carries the manager's own word whenever it gave one, so the
        diagnostic can quote a measurement instead of paraphrasing it. Three
        states, and the third is not a polite spelling of the second:

          running       the manager answered with a word describing itself.
                        "degraded" is in this state, because a degraded manager
                        is a running one and will start a unit.
          absent        the manager answered one of _MANAGER_ABSENT_ANSWERS.
          undetermined  the question could not be put, or was not answered in
                        time; `detail` says why and NOTHING about the manager
                        is claimed.
        """
        facts: dict[str, Any] = {
            "state": ToolRegistry._MANAGER_UNDETERMINED,
            "answer": "",
            "detail": "",
        }
        argv = [_SYSTEMCTL, "--user", "is-system-running"]
        try:
            completed = subprocess.run(
                argv, capture_output=True, text=True, check=False,
                timeout=_MANAGER_PROBE_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            facts["detail"] = f"{_SYSTEMCTL} is not present, so it was not asked"
            return facts
        except subprocess.TimeoutExpired:
            facts["detail"] = (
                f"{_SYSTEMCTL} did not answer within "
                f"{_MANAGER_PROBE_TIMEOUT_SECONDS}s"
            )
            return facts
        except OSError as exc:
            facts["detail"] = f"the question could not be put ({exc})"
            return facts

        # is-system-running exits non-zero for every state except "running",
        # so the EXIT CODE is not the answer — the word is. An empty answer
        # with a non-zero code means it declined to say, which is undetermined.
        answer = (completed.stdout or "").strip().splitlines()
        answer = answer[0].strip() if answer else ""
        facts["answer"] = answer
        if not answer:
            facts["detail"] = (
                f"{_SYSTEMCTL} exited {completed.returncode} without naming a "
                f"state"
            )
            return facts
        if answer in _MANAGER_ABSENT_ANSWERS:
            facts["state"] = ToolRegistry._MANAGER_ABSENT
            return facts
        if answer == "unknown":
            facts["detail"] = f"{_SYSTEMCTL} answered {answer!r}"
            return facts
        facts["state"] = ToolRegistry._MANAGER_RUNNING
        return facts

    @staticmethod
    def _describe_user_manager(facts: dict) -> str:
        """One line of measured fact about the manager. No conclusion."""
        state = facts["state"]
        if state == ToolRegistry._MANAGER_RUNNING:
            return (
                f"systemd user manager: answered {facts['answer']!r} (checked "
                f"with {_SYSTEMCTL} --user is-system-running)"
            )
        if state == ToolRegistry._MANAGER_ABSENT:
            return (
                f"systemd user manager: answered {facts['answer']!r} (checked "
                f"with {_SYSTEMCTL} --user is-system-running)"
            )
        return f"systemd user manager: could not be determined ({facts['detail']})"

    @staticmethod
    def _describe_path(path: str, facts: dict) -> str:
        """One line of measured fact about `path`. No conclusion."""
        state = facts["state"]
        if state == ToolRegistry._PATH_ABSENT:
            return f"{path}: not present (checked)"
        if state == ToolRegistry._PATH_UNDETERMINED:
            return (
                f"{path}: presence could not be determined — "
                f"{facts['detail']} (checked)"
            )
        kind = "a regular file" if facts["regular"] else "NOT a regular file"
        if facts["executable"] is None:
            runnable = "executability could not be determined"
        elif facts["executable"]:
            runnable = "executable by this account"
        else:
            runnable = "NOT executable by this account"
        return f"{path}: present, {kind}, {runnable} (checked)"

    @staticmethod
    def _privileged_failure_result(
        call: ToolCall,
        tool_name: str,
        completed: Any,
        *,
        request_id: str = "",
    ) -> ToolResult:
        """Build the failure ToolResult, stating ONLY what was measured.

        WHY THIS IS NARROWER THAN IT LOOKS. The exit codes on this path do not
        identify a cause, and an earlier version of this function spoke as if
        they did. Two claims in particular were inferences wearing a
        measurement's clothes:

          * "exit 126 means the authentication prompt was dismissed". pkexec(1)
            propagates the exit status of a program it successfully executed,
            and this runner ends in `exec python3 ...` — so a 126 can equally
            come from the CHILD. Telling a person their prompt was dismissed
            when the tool actually failed sends them to repeat an action that
            already ran, or to hunt an authentication problem that never
            happened.
          * "exit 127 with the runner present means pkexec never reached it, so
            the installed package is not at fault". Presence was read from
            os.path.exists(), which proves neither a regular file nor
            executability nor correct contents; and 127 can likewise be the
            child's own status. An independent review drove three genuinely
            different states — a directory, a regular file nothing can execute,
            and a healthy executable whose program returned 127 — and every one
            of them produced that same innocence verdict.

        Decided 2026-08-24: this function reports the exit code, whatever the
        boundary said on stderr, and a per-component probe of each thing the
        dispatch depends on. Where a component is MEASURABLY absent, that is
        stated, because it was established. Where it is not, the message says
        the exit code does not identify a cause on its own — which is true, and
        is more useful to someone repairing a system than a confident wrong
        answer.

        The runner's own words still lead when it got far enough to speak: that
        is the one account in this whole path that was produced by something
        which knew what it was doing.

        WHAT IT SAID, AS WELL AS WHY (2026-10-22). When the dispatcher's own
        result line ends the output for this request (PRIVILEGED_RESULT_PREFIX),
        the result built here takes its executed, blocked, outcome and refusal,
        so a refusal made under the runner arrives as the refusal it was rather
        than as a run that errored; the line itself is not part of the content.
        Without that line nothing about the call's shape is known, and the
        result is built as it always was.
        """
        shape, stdout_text = read_privileged_result_line(
            completed.stdout, request_id)
        stdout_message = stdout_text.rstrip("\n")
        stderr_message = completed.stderr.rstrip("\n")

        runner_facts = ToolRegistry._probe_path(_PKEXEC_RUNNER_PATH)
        systemd_run_facts = ToolRegistry._probe_path(_SYSTEMD_RUN)
        manager_facts = ToolRegistry._probe_user_manager()

        if stdout_message:
            # The runner reached its own logic and said why it stopped. That is
            # the most specific account anyone has; it leads.
            headline = stdout_message
        elif systemd_run_facts["state"] == ToolRegistry._PATH_ABSENT:
            headline = (
                f"{_SYSTEMD_RUN} is not present (checked), so the privileged "
                f"runner for {tool_name} could not be started"
            )
        elif manager_facts["state"] == ToolRegistry._MANAGER_ABSENT:
            # ONLY the manager's own word produces this sentence. An
            # undetermined probe deliberately falls through to the
            # nothing-was-established headline below: reporting "I could not
            # tell" as "it is not running" is the defect this branch corrects,
            # and it would be a strange place to reintroduce it.
            headline = (
                f"this account's systemd user manager answered "
                f"{manager_facts['answer']!r}, so the privileged runner for "
                f"{tool_name} could not be started"
            )
        elif runner_facts["state"] == ToolRegistry._PATH_ABSENT:
            headline = (
                f"the privileged runner is not present at "
                f"{_PKEXEC_RUNNER_PATH} (checked), so {tool_name} could not be "
                f"dispatched"
            )
        else:
            headline = (
                f"the privileged dispatch of {tool_name} did not complete "
                f"(exit {completed.returncode}). This exit code does not "
                f"identify a cause on its own — it is returned both by the "
                f"privilege boundary and by the program it runs — so what was "
                f"measured is listed rather than guessed at"
            )

        measured = [
            f"exit code: {completed.returncode}",
            ToolRegistry._describe_path(_PKEXEC_RUNNER_PATH, runner_facts),
            ToolRegistry._describe_path(_SYSTEMD_RUN, systemd_run_facts),
            ToolRegistry._describe_user_manager(manager_facts),
        ]
        if stderr_message:
            measured.append(f"it said: {stderr_message}")
        content = f"{headline} [{'; '.join(measured)}]"
        logger.error(
            "privileged dispatch of %s failed: rc=%s runner=%s systemd_run=%s "
            "manager=%s stderr=%r",
            tool_name, completed.returncode, runner_facts,
            systemd_run_facts, manager_facts, stderr_message,
        )
        result = ToolResult(
            call_id=call.call_id,
            name=tool_name,
            content=content,
            success=False,
        )
        if shape is None:
            return result
        return replace(
            result,
            executed=shape["executed"],
            blocked=shape["blocked"],
            outcome=shape["outcome"],
            refusal=shape["refusal"],
        )

    @staticmethod
    def _audit_log_decision(
        call: ToolCall,
        decision: DispatchDecision,
        ingress_tracker: IngressTracker,
        *,
        user_outcome: str,
        exit_code: int,
        result_summary: str,
        source_attribution: str,
        excerpt: str,
    ) -> None:
        """Best-effort audit log write per RFC §9."""
        record = build_audit_record(
            call=call,
            decision=decision,
            ingress_tracker=ingress_tracker,
            user_outcome=user_outcome,
            exit_code=exit_code,
            result_summary=result_summary,
            source_attribution=source_attribution,
            excerpt=excerpt,
        )
        write_record(record)
        # The same decision on the turn's decision path, joined to the audit
        # row by the turn id the row now carries and to the call by its id.
        glass.path_entry(
            "tools", event="consent", tool=call.name, call_id=call.call_id,
            consent=user_outcome, consent_state=_consent_state(user_outcome),
            exit_code=exit_code, gate_action=getattr(decision, "action", None),
            effective_provenance=getattr(decision.effective_provenance,
                                         "value", None),
            needs_pkexec=getattr(decision, "needs_pkexec", None),
            result_summary=result_summary)

    @staticmethod
    def _audit_log_refusal(
        call: ToolCall,
        ingress_tracker: IngressTracker,
        reason: str,
        *,
        source_attribution: str,
        excerpt: str,
    ) -> None:
        """Best-effort audit log write per RFC §9 for a call refused before
        the dispatcher gate was consulted, so no DispatchDecision exists: the
        record says denied, exit code 1, with the reason as its summary."""
        write_record(build_refusal_audit_record(
            call=call,
            ingress_tracker=ingress_tracker,
            reason=reason,
            source_attribution=source_attribution,
            excerpt=excerpt,
        ))
        glass.path_entry(
            "tools", event="consent", tool=call.name, call_id=call.call_id,
            consent="denied", consent_state="refused_before_the_gate",
            exit_code=1, reason=reason)

    def get_tool(self, name: str) -> BaseTool | None:
        """Get a registered tool by name."""
        return self._tools.get(name)

    def get_schemas(self, names: set[str] | None = None) -> list[dict]:
        """Get OpenAI-compatible schemas for the given tools (or all)."""
        schemas = []
        for tool in self._tools.values():
            if names is None or tool.name in names:
                schemas.append(tool.schema.to_openai())
        return schemas

    def get_tool_schemas(self, names: set[str] | None = None) -> list[ToolSchema]:
        """Get ToolSchema objects for the given tools (or all).

        DISPATCH LOCKDOWN backstop: returns [] when tool-offering is locked, so
        the model is never offered tools on any surface and cannot emit a
        ToolCall. Code-owned dispatch does not call this method, so it is
        unaffected. (See set_tool_offering_locked.)
        """
        if self._tool_offering_locked:
            return []
        schemas = []
        for tool in self._tools.values():
            if names is None or tool.name in names:
                schemas.append(tool.schema)
        return schemas

    def get_all_names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys()) + list(self._external_handlers.keys())

    def classify_safety(self, tool_name: str,
                        arguments: dict[str, Any]) -> SafetyTier:
        """Classify the safety tier for a specific tool invocation."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return SafetyTier.CONFIRM
        return tool.classify_safety(arguments)

    def build_prompt_rules(self, active_tools: set[str] | None = None) -> str:
        """Build numbered system prompt rules for active tools.

        Only includes rules for tools that are in the active set.
        """
        rules = [
            "When the user's request can be answered from your training data "
            "alone, answer directly without calling a tool.",
            "When the user asks about the current state of their system "
            "(files, packages, services, hardware), ALWAYS use a tool.",
            "Never fabricate system information. If unsure, use a tool to check.",
        ]

        for tool in self._tools.values():
            if active_tools is None or tool.name in active_tools:
                rules.append(
                    f"Tool '{tool.name}': {tool.description}"
                )

        for name, rule in self._external_rules.items():
            if active_tools is None or name in active_tools:
                rules.append(rule)

        numbered = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rules))
        return "Tool usage guidelines:\n" + numbered

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def tool_count(self) -> int:
        return len(self._tools) + len(self._external_handlers)
