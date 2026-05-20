"""Privileged-context entry point for the pkexec runner.

Invoked by `intergen.tool_registry.execute()` via the pkexec runner
shim at `intergen/data/intergen-privileged-runner` (landed at 49a585ca,
authored by the build-system coordinator's T0-4-E integration pkexec
gate work). This module is the AUTHORIZATION + ARGUMENT-VALIDATION
boundary that re-enforces the same contract the unprivileged caller
enforced pre-pkexec; PolicyKit is the upstream AUTHENTICATION boundary;
the provenance gate at `intergen/provenance.py` is the upstream INTENT
boundary.

Argv contract (from `intergen-privileged-runner`):

    python3 -m intergen.privileged_dispatch <tool_name> <args_json>

Environment (set by the runner from pkexec):

    PKEXEC_UID   — calling user's uid
    PKEXEC_USER  — calling user's username (resolved via getent passwd)

Exit code:

    0 — tool dispatch succeeded; ToolResult.content on stdout
    1 — tool dispatch failed (validation error / refusal / exception);
        human-readable reason on stdout
    2 — argv shape wrong (caller bug; should never happen via the
        runner shim)

The runner's job is to set up a clean root-context environment + hand
off to this module. This module's job is to re-validate (defense in
depth — the privileged context cannot trust the un-privileged caller's
prior validation in the abstract; we re-run the same validation
against the same source-of-truth so the privileged boundary is
self-contained) + execute + print + exit cleanly.

D-008 RFC v1.0 §6 invariant preserved: privileged operations require
BOTH the provenance gate (intent) AND pkexec (authentication). The
gate ran in the user context (`tool_registry.execute()` → `verify_tool_call`)
before pkexec invoked this module; the user authenticated to PolicyKit;
this module is the post-auth dispatcher that re-validates the tool +
args against the same allowlist + schema the gate consulted.
"""

from __future__ import annotations

import json
import os
import sys

from intergen.tool_registry import ToolRegistry, _PRIVILEGED_TOOLS
from intergen.interfaces.types import SafetyTier


def _emit(message: str) -> None:
    """Print to stdout (the runner captures this via subprocess.run)."""
    print(message)


def _fail(message: str, exit_code: int = 1) -> None:
    """Print error to stdout (so subprocess.run captures it in the
    calling registry's ToolResult.content) and exit with non-zero.
    """
    _emit(message)
    sys.exit(exit_code)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns exit code (0 success / 1 dispatch-failure /
    2 argv-shape-wrong). main() does not return on the exit-2 path; it
    calls _fail() which calls sys.exit().
    """
    args = argv if argv is not None else sys.argv[1:]

    if len(args) < 2:
        _fail(
            "privileged_dispatch: usage: python3 -m intergen.privileged_dispatch "
            "<tool_name> <args_json>",
            exit_code=2,
        )

    tool_name = args[0]
    args_json = args[1]

    # PKEXEC_UID + PKEXEC_USER are set by the runner shim. Their absence
    # means this module was invoked outside the runner — a bug or a
    # malicious bypass attempt. Refuse rather than silently running.
    if not os.environ.get("PKEXEC_UID"):
        _fail(
            "privileged_dispatch: PKEXEC_UID unset; refusing to run "
            "outside the pkexec runner context.",
        )

    # Re-validate tool_name against the PRIVILEGED_STATE_CHANGING
    # allowlist. The gate already filtered, but the privileged boundary
    # must not trust the caller — it re-checks against the same SOT.
    if tool_name not in _PRIVILEGED_TOOLS:
        _fail(
            f"privileged_dispatch: tool {tool_name!r} is not in the "
            f"PRIVILEGED_STATE_CHANGING allowlist; refusing dispatch. "
            f"(allowlist: {sorted(_PRIVILEGED_TOOLS)})",
        )

    # Parse args_json. Malformed JSON is a caller bug — the gate
    # validated upstream, so a bad payload here means tampering or a
    # serialization mismatch. Refuse.
    try:
        arguments = json.loads(args_json)
    except json.JSONDecodeError as exc:
        _fail(
            f"privileged_dispatch: args_json is not valid JSON: {exc}",
        )

    if not isinstance(arguments, dict):
        _fail(
            f"privileged_dispatch: args_json must decode to a JSON "
            f"object; got {type(arguments).__name__}",
        )

    # Discover tools via the same registry the unprivileged caller used.
    # The registry's discover_tools() walks intergen/tools/*.py and
    # instantiates each BaseTool subclass — same SOT as the caller side.
    registry = ToolRegistry()
    discovered = registry.discover_tools()
    if discovered == 0:
        _fail(
            "privileged_dispatch: tool registry discovered 0 tools; "
            "intergen.tools package may be broken upstream of this point.",
        )

    tool = registry.get_tool(tool_name)
    if tool is None:
        _fail(
            f"privileged_dispatch: tool {tool_name!r} is in the privileged "
            f"allowlist but the registry did not discover it; "
            f"intergen.tools.{tool_name} may be missing or broken.",
        )

    # Per-tool argument schema validation. Same call the unprivileged
    # registry's execute() makes — defense-in-depth re-check.
    validation_error = tool.validate_arguments(arguments)
    if validation_error:
        _fail(
            f"privileged_dispatch: argument validation failed for "
            f"{tool_name!r}: {validation_error}",
        )

    # BLOCKED tier remains a tool-level refusal even in privileged
    # context. The gate already filtered, but BLOCKED is the
    # provenance-independent invariant — it must hold here too.
    safety = tool.classify_safety(arguments)
    if safety == SafetyTier.BLOCKED:
        _fail(
            f"privileged_dispatch: tool {tool_name!r} classified BLOCKED "
            f"for this argument set; refusing dispatch.",
        )

    # Execute the tool in root context. tool.execute() returns a
    # ToolResult; we surface .content to stdout and map success/failure
    # to exit code so the calling registry can construct a ToolResult
    # of its own from this subprocess's output.
    try:
        result = tool.execute(arguments)
    except Exception as exc:  # noqa: BLE001 — wrap into ToolResult
        _fail(
            f"privileged_dispatch: tool {tool_name!r} raised "
            f"{type(exc).__name__}: {exc}",
        )

    _emit(result.content)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
