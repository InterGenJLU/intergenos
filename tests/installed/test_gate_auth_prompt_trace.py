# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Installed contract for the GNOME Shell authentication prompt trace.

The default subject is the JavaScript embedded in the installed
``libshell-*.so`` GResource.  A direct invocation may name an extracted source
root with ``--source-root``; that is the positive control used before the
package has been built and installed.  The source-root override is explicit and
never changes what an ordinary installed-gate run measures.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


MESSAGE_ID = "cd6ef3aef777463a86bfb73360865d5d"
RESOURCE_PATH = "/org/gnome/shell/ui/components/polkitAgent.js"
SOURCE_RELATIVE_PATH = Path("js/ui/components/polkitAgent.js")
SOURCE_ROOT_ENV = "INTERGENOS_AUTH_PROMPT_TRACE_SOURCE_ROOT"

EXPECTED_FIELDS = {
    "MESSAGE": "'Authentication prompt lifecycle event'",
    "MESSAGE_ID": "AUTH_PROMPT_MESSAGE_ID",
    "POLKIT_ACTION_ID": "actionId",
    "INTERGENOS_AUTH_PROMPT_SEQUENCE": "`${sequence}`",
    "INTERGENOS_AUTH_PROMPT_EVENT": "event",
    "INTERGENOS_AUTH_PROMPT_OUTCOME": "outcome",
}

EXPECTED_TERMINAL_OUTCOMES = {
    "authorized",
    "dismissed",
    "cancelled-by-authority",
    "deferred-session-locked",
    "failed-to-open",
}


def _installed_source() -> str:
    libraries = sorted(Path("/usr/lib/gnome-shell").glob("libshell-*.so"))
    if len(libraries) != 1:
        raise AssertionError(
            "expected exactly one installed /usr/lib/gnome-shell/libshell-*.so "
            f"artifact, found {[str(path) for path in libraries]}")

    proc = subprocess.run(
        ["/usr/bin/gresource", "extract", str(libraries[0]), RESOURCE_PATH],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"/usr/bin/gresource could not extract {RESOURCE_PATH} from "
            f"{libraries[0]} (exit {proc.returncode}): "
            f"{proc.stderr.decode('utf-8', errors='replace')}")
    if not proc.stdout:
        raise AssertionError(
            f"{RESOURCE_PATH} extracted from {libraries[0]} as an empty file")
    return proc.stdout.decode("utf-8")


def _source_from_root(source_root: Path) -> str:
    path = source_root.resolve() / SOURCE_RELATIVE_PATH
    if not path.is_file():
        raise AssertionError(
            f"the source-root control expected {SOURCE_RELATIVE_PATH} at {path}")
    return path.read_text(encoding="utf-8")


def _load_subject(source_root: Path | None) -> str:
    if source_root is None:
        return _installed_source()
    return _source_from_root(source_root)


def _function_body(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        raise AssertionError(f"required function is absent: {signature}")
    brace = source.find("{", start + len(signature))
    if brace < 0:
        raise AssertionError(f"required function has no body: {signature}")

    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1:index]
    raise AssertionError(f"required function has an unterminated body: {signature}")


def _assert_contract(source: str) -> None:
    assert source.count(MESSAGE_ID) == 1, (
        "the stable authentication prompt MESSAGE_ID must appear exactly once")
    assert f"const AUTH_PROMPT_MESSAGE_ID = '{MESSAGE_ID}';" in source

    helper = _function_body(
        source,
        "function logAuthPromptLifecycle(actionId, sequence, event, outcome)")
    assert "GLib.log_structured('GNOME Shell', " in helper
    assert "GLib.LogLevelFlags.LEVEL_MESSAGE" in helper

    object_match = re.search(
        r"GLib\.log_structured\('GNOME Shell',\s*"
        r"GLib\.LogLevelFlags\.LEVEL_MESSAGE,\s*\{(?P<fields>.*?)\}\s*\);",
        helper,
        flags=re.DOTALL,
    )
    assert object_match, "the bounded GLib.log_structured call is absent"
    field_pairs = re.findall(
        r"^\s*'([A-Z0-9_]+)'\s*:\s*(.+?),\s*$",
        object_match.group("fields"),
        flags=re.MULTILINE,
    )
    fields = dict(field_pairs)
    assert len(field_pairs) == len(fields), "the trace call repeats a journal field"
    assert fields == EXPECTED_FIELDS, (
        "the trace call's journal fields or value sources differ from the "
        f"allowlist: {fields!r}")
    assert "cookie" not in helper.lower(), (
        "the PolicyKit cookie reached the structured trace helper")

    journal_calls = re.findall(
        r"(?:GLib\.log_structured|\blog)\s*\(.*?\);",
        source,
        flags=re.DOTALL,
    )
    assert journal_calls, "no GNOME Shell journal writes were found"
    cookie_writes = [call for call in journal_calls if "this._cookie" in call]
    assert not cookie_writes, (
        "a GNOME Shell journal write interpolates the PolicyKit cookie: "
        f"{cookie_writes!r}")

    wrapper = _function_body(source, "_writeAuthPromptTrace(event, outcome)")
    normalized_wrapper = " ".join(wrapper.split())
    assert normalized_wrapper == (
        "logAuthPromptLifecycle( this.actionId, this._authPromptSequence, "
        "event, outcome);")
    assert "cookie" not in wrapper.lower()

    assert "this._authPromptOpened = false;" in source
    assert "this._authPromptTerminal = false;" in source
    assert "this.connect('opened', this._onOpened.bind(this));" in source
    assert "this._writeAuthPromptTrace('opened', 'pending');" in source

    terminal = _function_body(source, "_emitAuthPromptTerminal(outcome)")
    assert "if (this._authPromptTerminal)" in terminal
    assert "this._authPromptTerminal = true;" in terminal
    assert "this._authPromptOpened ? 'completed' : 'not-shown'" in terminal
    assert "this._writeAuthPromptTrace(event, outcome);" in terminal

    outcomes = set(re.findall(
        r"_emitAuthPromptTerminal\('([^']+)'\)", source))
    outcomes.update(re.findall(
        r"logAuthPromptLifecycle\(\s*actionId,\s*sequence,\s*"
        r"'not-shown',\s*'([^']+)'\s*\)",
        source,
    ))
    assert outcomes == EXPECTED_TERMINAL_OUTCOMES, (
        "terminal outcomes differ from the fixed contract: "
        f"{sorted(outcomes)!r}")
    assert "this._emitAuthPromptTerminal('failed-to-open');" in _function_body(
        source, "_ensureOpen()"), "the failed-open branch is not traced"
    assert "this._emitAuthPromptTerminal('authorized');" in _function_body(
        source, "_onSessionCompleted(session, gainedAuthorization)"), (
            "the successful completion branch is not traced")
    assert "this._emitAuthPromptTerminal('dismissed');" in _function_body(
        source, "\n    cancel()"), "the user-dismissal branch is not traced"
    assert "this._emitAuthPromptTerminal('cancelled-by-authority');" in (
        _function_body(source, "cancelByAuthority()")), (
            "the authority-cancellation branch is not traced")

    assert "this._authPromptSequence = 0;" in source
    assert "this._deferredRequest = null;" in source
    initiate = _function_body(
        source,
        "_onInitiate(_nativeAgent, actionId, message, _iconName, cookie, userNames)",
    )
    sequence_assignment = "const sequence = ++this._authPromptSequence;"
    assert sequence_assignment in initiate
    assert initiate.index(sequence_assignment) < initiate.index(
        "if (Main.sessionMode.isLocked)"
    ), "the sequence is not allocated before a locked-session deferral"
    assert "this._deferredRequest = {" in initiate
    assert "sequence," in initiate
    assert "'deferred-session-locked'" not in initiate, (
        "temporarily deferring a request must not emit a terminal record")

    cancellation = _function_body(source, "_onCancel(_nativeAgent)")
    for fragment in (
        "if (this._deferredRequest)",
        "const {actionId, sequence} = this._deferredRequest;",
        "this._deferredRequest = null;",
        "Main.sessionMode.disconnectObject(this);",
        "logAuthPromptLifecycle( actionId, sequence, 'not-shown', "
        "'deferred-session-locked');",
        "this.complete(false);",
        "this._currentDialog?.cancelByAuthority();",
    ):
        assert " ".join(fragment.split()) in " ".join(cancellation.split()), (
            "the deferred cancellation contract is incomplete: "
            f"missing {fragment!r}")

    constructor = re.search(
        r"new AuthenticationDialog\(\s*"
        r"actionId, message, cookie, userNames, sequence\s*\)",
        source,
    )
    assert constructor, "the generated sequence does not reach the dialog"

    assert source.count("logAuthPromptLifecycle(") == 3, (
        "the structured helper must have one definition and two bounded callers")


def test_installed_authentication_prompt_trace_contract() -> None:
    source_override = os.environ.get(SOURCE_ROOT_ENV)
    source_root = Path(source_override) if source_override else None
    _assert_contract(_load_subject(source_root))


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        type=Path,
        help=(
            "read js/ui/components/polkitAgent.js from this extracted source "
            "root instead of the installed libshell GResource"),
    )
    args = parser.parse_args(argv)
    try:
        _assert_contract(_load_subject(args.source_root))
    except (AssertionError, OSError, UnicodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    subject = args.source_root or "/usr/lib/gnome-shell/libshell-*.so"
    print(f"PASS: authentication prompt trace contract: {subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
