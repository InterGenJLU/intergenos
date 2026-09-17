# SPDX-License-Identifier: GPL-3.0-or-later
"""What a canonical hook says on its way to a zero exit reaches the person.

WHY THIS FILE EXISTS.

A canonical hook that exited zero produced exactly one line of output — the
word OK and its description — and everything the hook had written to stderr was
discarded. A tool that does its work and warns about the state it found is
therefore silent in precisely the case that happens: it succeeded, so nobody was
told what it said.

That is not hypothetical. Measured in the R001.2-03 install trace this machine
was installed from, 102 canonical hook runs out of 779 exited zero with
something on stderr, and among them were the eight fontconfig invocations
reading `Fontconfig error: Cannot load default config file` — the diagnostic
that named a real ordering defect, printed eight times into an install that
reported every one of those hooks as OK. It took a separate trace analysis to
find a message the install itself had already been handed.

The same class was already closed on the OTHER hook path: an archive's
lifecycle hook has carried both its streams to the caller since the comment in
`run_archive_lifecycle_hook` was written. This file pins the same rule for the
canonical hooks.

WHAT THESE TESTS PIN.

1. A canonical hook that exits zero and writes to stderr produces its OK line
   AND a NOTE line for each non-blank stderr line.
2. NOTE is its own level. It is not OK, WARN or CRITICAL, so no existing
   classification moves and nothing that reads those words changes meaning.
3. A note is not a failure: the hook is counted neither critical nor cosmetic.
4. A quiet hook yields exactly the one OK line it always did, so the great
   majority of hook runs read unchanged.
5. A hook that FAILS keeps the WARN/CRITICAL line it had, with its stderr in
   that line as before.
"""

from __future__ import annotations

import re

from pkm import hooks


def _hook(hook_id, script, critical=False):
    """A canonical hook that runs a real shell command against a real process.

    No mock: the point is the runner's own handling of a CompletedProcess it
    produced itself, so the command is run for real and its streams are the
    real ones.
    """
    return hooks.CanonicalHook(
        id=hook_id,
        description="test hook",
        pattern=re.compile(r"^usr/share/testhook/"),
        cmd_fn=lambda root, matched: ["/bin/sh", "-c", script],
        critical=critical,
    )


def _run(tmp_path, hook):
    return hooks.run_canonical_hooks(
        tmp_path,
        ["usr/share/testhook/marker"],
        "demo-package",
        "1.0",
        "install",
        hooks=[hook],
    )


def _notes(result):
    return [m for m in result.messages if "] NOTE " in m]


def test_a_hook_that_exits_zero_with_stderr_gets_a_note_line(tmp_path):
    result = _run(tmp_path, _hook("chatty", "echo 'the state I found is odd' >&2; exit 0"))
    assert any("] OK " in m for m in result.messages), (
        f"the OK line a successful hook has always printed is gone: {result.messages!r}"
    )
    notes = _notes(result)
    assert len(notes) == 1, (
        "what the hook said on its way to a zero exit was discarded: "
        f"{result.messages!r}"
    )
    assert "the state I found is odd" in notes[0]
    assert "chatty" in notes[0], (
        f"the note is not attached to the hook that produced it: {notes[0]!r}"
    )


def test_one_note_line_per_stderr_line(tmp_path):
    result = _run(
        tmp_path,
        _hook("multiline", "printf 'first\\n\\nsecond\\nthird\\n' >&2; exit 0"),
    )
    notes = _notes(result)
    assert len(notes) == 3, (
        f"expected one note per non-blank stderr line, got: {notes!r}"
    )
    assert "first" in notes[0] and "second" in notes[1] and "third" in notes[2]


def test_a_note_is_its_own_level(tmp_path):
    result = _run(tmp_path, _hook("chatty", "echo chatter >&2; exit 0"))
    note = _notes(result)[0]
    assert " WARN " not in note and " CRITICAL " not in note, (
        f"a note was dressed as a failure level: {note!r}"
    )
    assert " PENDING " not in note, (
        f"a note was dressed as a postponement: {note!r}"
    )


def test_a_note_is_not_a_failure(tmp_path):
    result = _run(tmp_path, _hook("chatty", "echo chatter >&2; exit 0", critical=True))
    assert result.critical_failures == [] and result.cosmetic_failures == [], (
        "a hook that succeeded was counted as a failure because it spoke: "
        f"{result!r}"
    )


def test_a_quiet_hook_still_yields_exactly_one_line(tmp_path):
    result = _run(tmp_path, _hook("quiet", "exit 0"))
    assert len(result.messages) == 1 and "] OK " in result.messages[0], (
        f"a silent hook's output changed: {result.messages!r}"
    )
    assert _notes(result) == []


def test_stdout_on_a_zero_exit_is_not_promoted(tmp_path):
    """Scope held: this change carries stderr, which is where tools warn."""
    result = _run(tmp_path, _hook("talker", "echo ordinary-progress; exit 0"))
    assert _notes(result) == [], (
        f"stdout was promoted into the hook report as well: {result.messages!r}"
    )


def test_a_failing_hook_keeps_its_failure_line(tmp_path):
    result = _run(
        tmp_path, _hook("broken", "echo 'it broke' >&2; exit 1", critical=True)
    )
    assert result.critical_failures == ["broken"]
    line = [m for m in result.messages if "CRITICAL" in m]
    assert len(line) == 1 and "it broke" in line[0], (
        f"the failure line lost its stderr: {result.messages!r}"
    )
    assert _notes(result) == [], (
        f"a failing hook produced a note as well as its failure line: {result.messages!r}"
    )
