# SPDX-License-Identifier: GPL-3.0-or-later
"""A critical hook that was selected and then did nothing says so.

WHY THIS FILE EXISTS.

A canonical hook is selected when a package installs a file its trigger
matches. Its command builder then answers one of several ways, and one of those
answers — returning no command at all — was a silent `continue`: the hook was
selected, nothing ran, and the install's output said nothing whatsoever about
it. For a hook classified CRITICAL, that is a gap between what happened and
what was reported, and it is the one class of hook where the gap matters most.

The certificate-trust hook is the live case. It declines for a foreign root
rather than rebuild the running machine's trust store during an install into
another root — the right decision, and silent. Correcting that hook's trigger
(it had never fired at all) makes the decision happen more often, not less, so
the silence had to close with it.

WHAT THESE TESTS PIN.

1. A critical hook selected by a package, whose builder returns no command,
   produces a DECLINED line naming the hook.
2. When the builder gives a reason, the reason is in the line.
3. A declined hook is not a failure: neither critical nor cosmetic counts move,
   because nothing failed.
4. A cosmetic hook that declines stays silent, exactly as before. This change
   does not add a line to the common quiet cases.
5. The certificate-trust hook, selected on a foreign root through its real
   trigger, is reported — proved through run_canonical_hooks, not by reading
   the builder in isolation.
6. DECLINED is not PENDING. A postponement says the work will be done later;
   a decline says this root is not where it can be done. Nothing in this tree
   rebuilds a target's trust store after the install, so the honest word is the
   one that does not promise it.
"""

from __future__ import annotations

import re

from pkm import hooks


def _hook(hook_id, answer, critical):
    return hooks.CanonicalHook(
        id=hook_id,
        description="test hook",
        pattern=re.compile(r"^usr/share/testhook/"),
        cmd_fn=lambda root, matched: answer,
        critical=critical,
    )


def _run(tmp_path, hook, files=("usr/share/testhook/marker",)):
    return hooks.run_canonical_hooks(
        tmp_path, list(files), "demo-package", "1.0", "install", hooks=[hook]
    )


def _declines(result):
    return [m for m in result.messages if "] DECLINED " in m]


def test_a_critical_hook_that_declines_is_reported(tmp_path):
    result = _run(tmp_path, _hook("needed", None, critical=True))
    declined = _declines(result)
    assert len(declined) == 1, (
        "a critical hook was selected, ran nothing, and the install said "
        f"nothing about it: {result.messages!r}"
    )
    assert "needed" in declined[0]


def test_the_reason_reaches_the_line(tmp_path):
    result = _run(
        tmp_path,
        _hook("needed", hooks.HookDecline("this root has no such tool"), critical=True),
    )
    assert "this root has no such tool" in _declines(result)[0]


def test_a_decline_is_not_a_failure(tmp_path):
    result = _run(tmp_path, _hook("needed", None, critical=True))
    assert result.critical_failures == [] and result.cosmetic_failures == [], (
        f"a hook that did not fail was counted as a failure: {result!r}"
    )


def test_a_cosmetic_hook_that_declines_stays_silent(tmp_path):
    result = _run(tmp_path, _hook("nicety", None, critical=False))
    assert result.messages == [], (
        f"the quiet cases gained a line they did not have: {result.messages!r}"
    )


def test_a_hook_that_was_never_selected_says_nothing(tmp_path):
    result = _run(
        tmp_path,
        _hook("needed", None, critical=True),
        files=("usr/share/somethingelse/marker",),
    )
    assert result.messages == [], (
        "a hook no package selected produced output: " f"{result.messages!r}"
    )


def test_declined_is_not_pending(tmp_path):
    result = _run(tmp_path, _hook("needed", None, critical=True))
    assert not any("] PENDING " in m for m in result.messages), (
        "a decline was reported as a postponement, which promises work that "
        f"nothing in this tree performs: {result.messages!r}"
    )


def test_the_certificate_trust_hook_reports_its_decline_on_a_foreign_root(tmp_path):
    """The live case, through the real trigger and the real runner."""
    root = tmp_path / "target"
    root.mkdir()
    ca_trust = [h for h in hooks.CANONICAL_HOOKS if h.id == "ca-trust"][0]
    result = hooks.run_canonical_hooks(
        root,
        ["etc/pki/anchors/demo.pem"],
        "ca-certificates",
        "3",
        "install",
        hooks=[ca_trust],
    )
    declined = _declines(result)
    assert len(declined) == 1, (
        "the certificate-trust hook was selected on a foreign root and the "
        f"install reported nothing about it: {result.messages!r}"
    )
    assert "ca-trust" in declined[0]
    assert "root" in declined[0], (
        f"the line does not say why the hook declined: {declined[0]!r}"
    )
    assert result.critical_failures == []
