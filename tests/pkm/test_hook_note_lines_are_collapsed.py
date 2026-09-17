# SPDX-License-Identifier: GPL-3.0-or-later
"""NOTE output that repeats is shown once and counted, never dropped.

WHY THIS FILE EXISTS.

Carrying a canonical hook's stderr into the install output (the NOTE lines
pinned in test_hook_stderr_on_success_is_reported.py) made a real install 184
lines longer, and 141 of those lines were two vendor tools saying the same
thing over and over: gtk-update-icon-cache reporting "Cache file created
successfully" once for each of 69 packages that ship icons, and
update-mime-database repeating the same eight-line XDG advisory for each of 9
packages that ship mime data. A reader scrolling past 69 identical lines is
being trained to skip NOTE output, which is the same outcome as discarding it.

The answer is to COLLAPSE, never to DROP. Repetition is itself a fact, so a
folded line carries how many times it was said and which packages said it, the
first occurrence is shown in full and in place, every distinct line is shown,
and the unfiltered stderr still reaches the install trace in full
(test_hook_note_record_keeps_every_line.py pins that half).

MEASURED, not assumed, on the .199's own R001.2-03 install trace: folding
identical lines inside a single package operation folds 0 of those 184 lines,
because no producing run repeats itself internally. The repeats are ACROSS
package operations. Both scopes are implemented — the per-operation rule
because a hook that repeats itself in one run is a real shape, and the
per-session rule because that is where the 139 repeat lines actually are.

WHAT THESE TESTS PIN.

1. Identical NOTE lines produced by one hook in one operation are shown once,
   carrying the number of times the hook said it.
2. Distinct lines are all shown, in the order of first occurrence, unchanged.
3. Across the package operations of one install session, a block the same hook
   already said is not shown again; it is counted, with the packages named.
4. A different hook saying the same text is a different fact, and is shown.
5. Without a session ledger, nothing is remembered between operations — the
   folding is an argument, not hidden global state.
6. The fold summary states the number of folded lines and names each folded
   block, so a reader can never mistake a folded install for a quiet one.
7. A hook that says something once, and a hook that says nothing, read exactly
   as they did before this change.
"""

from __future__ import annotations

import re

from pkm import hooks


def _hook(hook_id, script, critical=False, description="test hook"):
    return hooks.CanonicalHook(
        id=hook_id,
        description=description,
        pattern=re.compile(r"^usr/share/testhook/"),
        cmd_fn=lambda root, matched: ["/bin/sh", "-c", script],
        critical=critical,
    )


def _run(tmp_path, hook, package="demo-package", note_fold=None):
    return hooks.run_canonical_hooks(
        tmp_path,
        ["usr/share/testhook/marker"],
        package,
        "1.0",
        "install",
        hooks=[hook],
        note_fold=note_fold,
    )


def _notes(result):
    return [m for m in result.messages if "] NOTE " in m]


SAME_LINE_THRICE = "for i in 1 2 3; do echo 'the cache was written' >&2; done; exit 0"


def test_identical_lines_in_one_operation_are_shown_once(tmp_path):
    notes = _notes(_run(tmp_path, _hook("chatty", SAME_LINE_THRICE)))
    assert len(notes) == 1, (
        f"the same line three times was printed three times: {notes!r}"
    )
    assert "the cache was written" in notes[0]


def test_a_folded_line_carries_how_many_times_it_was_said(tmp_path):
    note = _notes(_run(tmp_path, _hook("chatty", SAME_LINE_THRICE)))[0]
    assert "3" in note, f"the fold hid how many times the hook said it: {note!r}"


def test_every_distinct_line_is_shown_in_first_occurrence_order(tmp_path):
    script = "printf 'alpha\\nbeta\\nalpha\\ngamma\\n' >&2; exit 0"
    notes = _notes(_run(tmp_path, _hook("mixed", script)))
    assert len(notes) == 3, f"a distinct line was folded away: {notes!r}"
    assert "alpha" in notes[0] and "beta" in notes[1] and "gamma" in notes[2], (
        f"first-occurrence order was not kept: {notes!r}"
    )


def test_a_line_said_once_is_unchanged(tmp_path):
    note = _notes(_run(tmp_path, _hook("chatty", "echo 'said once' >&2; exit 0")))[0]
    assert note == "  hook[chatty] NOTE (test hook): said once", (
        f"a line said once gained fold decoration: {note!r}"
    )


def test_a_repeat_in_a_later_operation_is_not_shown_again(tmp_path):
    fold = hooks.NoteFold()
    first = _notes(_run(tmp_path, _hook("icons", "echo 'cache written' >&2; exit 0"),
                        package="hicolor-icon-theme", note_fold=fold))
    second = _notes(_run(tmp_path, _hook("icons", "echo 'cache written' >&2; exit 0"),
                         package="adwaita-icon-theme", note_fold=fold))
    assert len(first) == 1 and "cache written" in first[0]
    assert second == [], (
        f"the same hook saying the same thing was printed twice: {second!r}"
    )


def test_a_folded_repeat_is_counted_and_its_packages_named(tmp_path):
    fold = hooks.NoteFold()
    for pkg in ("hicolor-icon-theme", "adwaita-icon-theme", "papirus-icon-theme"):
        _run(tmp_path, _hook("icons", "echo 'cache written' >&2; exit 0"),
             package=pkg, note_fold=fold)
    folded = fold.folded()
    assert len(folded) == 1, f"the repeats were not recorded: {folded!r}"
    entry = folded[0]
    assert entry.count == 3, f"the repeat count is wrong: {entry!r}"
    assert entry.packages == [
        "hicolor-icon-theme", "adwaita-icon-theme", "papirus-icon-theme",
    ], f"the packages that said it were not kept: {entry!r}"


def test_a_multi_line_block_folds_as_a_block(tmp_path):
    fold = hooks.NoteFold()
    script = "printf 'advisory line one\\nadvisory line two\\n' >&2; exit 0"
    first = _notes(_run(tmp_path, _hook("mime", script), package="systemd",
                        note_fold=fold))
    second = _notes(_run(tmp_path, _hook("mime", script), package="shared-mime-info",
                         note_fold=fold))
    assert len(first) == 2, f"the block was not shown in full the first time: {first!r}"
    assert second == [], f"the repeated block was shown again: {second!r}"
    assert fold.folded()[0].count == 2


def test_a_different_hook_saying_the_same_text_is_shown(tmp_path):
    fold = hooks.NoteFold()
    _run(tmp_path, _hook("icons", "echo 'cache written' >&2; exit 0"),
         package="hicolor-icon-theme", note_fold=fold)
    other = _notes(_run(tmp_path, _hook("fonts", "echo 'cache written' >&2; exit 0"),
                        package="dejavu-fonts", note_fold=fold))
    assert len(other) == 1, (
        f"a second hook's own statement was folded into the first hook's: {other!r}"
    )


def test_without_a_ledger_nothing_is_remembered_between_operations(tmp_path):
    hook = _hook("icons", "echo 'cache written' >&2; exit 0")
    first = _notes(_run(tmp_path, hook, package="hicolor-icon-theme"))
    second = _notes(_run(tmp_path, hook, package="adwaita-icon-theme"))
    assert len(first) == 1 and len(second) == 1, (
        "folding leaked across operations with no ledger passed, which means "
        f"hidden global state: {first!r} {second!r}"
    )


def test_the_summary_states_the_folded_count_and_names_the_block(tmp_path):
    fold = hooks.NoteFold()
    for pkg in ("hicolor-icon-theme", "adwaita-icon-theme", "papirus-icon-theme"):
        _run(tmp_path, _hook("icons", "echo 'cache written' >&2; exit 0",
                             description="gtk icon cache"),
             package=pkg, note_fold=fold)
    summary = hooks.format_note_fold_summary(fold)
    assert "2" in summary, f"the number of folded lines is missing: {summary!r}"
    assert "icons" in summary and "cache written" in summary, (
        f"the summary does not say what was folded: {summary!r}"
    )
    assert "hicolor-icon-theme" in summary, (
        f"the summary does not say where the line was shown: {summary!r}"
    )


def test_an_install_that_folded_nothing_has_an_empty_summary(tmp_path):
    fold = hooks.NoteFold()
    _run(tmp_path, _hook("icons", "echo 'cache written' >&2; exit 0"),
         package="hicolor-icon-theme", note_fold=fold)
    assert hooks.format_note_fold_summary(fold) == "", (
        "an install with no repeats produced fold wording anyway"
    )


def test_a_quiet_hook_still_yields_exactly_one_line(tmp_path):
    fold = hooks.NoteFold()
    result = _run(tmp_path, _hook("quiet", "exit 0"), note_fold=fold)
    assert len(result.messages) == 1 and "] OK " in result.messages[0], (
        f"a silent hook's output changed: {result.messages!r}"
    )


def test_a_failing_hook_is_untouched_by_folding(tmp_path):
    fold = hooks.NoteFold()
    result = _run(
        tmp_path,
        _hook("broken", "echo 'it broke' >&2; echo 'it broke' >&2; exit 1",
              critical=True),
        note_fold=fold,
    )
    assert result.critical_failures == ["broken"]
    assert _notes(result) == []
    assert fold.folded() == [], (
        f"a failure was recorded in the NOTE fold ledger: {fold.folded()!r}"
    )
