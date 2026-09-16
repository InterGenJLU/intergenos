# SPDX-License-Identifier: GPL-3.0-or-later
"""The certificate-trust hook fires on the trust inputs THIS tree configures.

WHY THIS FILE EXISTS.

The trust updater landed (ca-certificates r3 ships /usr/bin/update-ca-trust,
p11-kit r2 validates the anchors) and the canonical hook that is supposed to
invoke it never fired on this tree, because its trigger named a directory
family this tree does not use. The hook matched
`^(etc|usr/share)/ca-certificates/`; no recipe in this tree installs anything
under either of those paths. p11-kit is built with
`-D trust_paths=/etc/pki/anchors` and ca-certificates emits its trusted roots
there. So a package could add or replace a trust anchor and the extracted
outputs every TLS client reads would never be regenerated — with nothing said
about it, because a trigger that selects no path looks exactly like a package
that shipped no trust anchor.

WHAT THESE TESTS PIN.

1. The trigger selects a file under the trust source directory the recipes
   actually configure, including its `anchors/` and `blocklist/` subdirectories,
   which is how p11-kit organises a trust path.
2. It does not select the directory entry on its own, a sibling directory whose
   name merely starts the same way, the two namespaces the old trigger named
   (which nothing in this tree writes), the Debian-style source directory, or
   the EXTRACTED outputs under /etc/ssl/certs — regenerating trust because the
   regenerated output changed would be a loop.
3. The trigger is read from the tree's own configuration rather than from a
   constant repeated here: the p11-kit recipe's `trust_paths=` value is what the
   test derives the expected directory from, so moving the configured directory
   without moving the trigger fails here instead of silently going quiet again.
4. The hook keeps its critical classification, its command builder, and that
   builder's refusal to rebuild the RUNNING system's trust store while
   installing into another root.
"""

from __future__ import annotations

import re
from pathlib import Path

from pkm import hooks

REPO_ROOT = Path(__file__).resolve().parents[2]
P11_KIT_BUILD = REPO_ROOT / "packages/core/p11-kit/build.sh"
CA_CERTIFICATES_BUILD = REPO_ROOT / "packages/core/ca-certificates/build.sh"

CA_TRUST_HOOK = next(
    hook for hook in hooks.CANONICAL_HOOKS if hook.id == "ca-trust"
)


def _configured_trust_dir():
    """The trust source directory p11-kit is actually built with, from its recipe."""
    match = re.search(
        r"trust_paths=(\S+)", P11_KIT_BUILD.read_text(encoding="utf-8")
    )
    assert match, (
        "the p11-kit recipe no longer states a trust_paths= value, so what the "
        "certificate-trust hook is supposed to watch can no longer be derived "
        f"from the tree: {P11_KIT_BUILD}"
    )
    return match.group(1).strip().rstrip("/")


def test_the_recipes_still_agree_on_one_trust_source_directory():
    """The premise of this whole file, checked rather than assumed."""
    configured = _configured_trust_dir()
    assert configured.startswith("/"), configured
    assert configured in CA_CERTIFICATES_BUILD.read_text(encoding="utf-8"), (
        "ca-certificates no longer emits its trusted roots into the directory "
        f"p11-kit is configured with ({configured}); the hook trigger, the "
        "bundle recipe and the trust library have to name one directory."
    )


def test_the_trigger_selects_the_configured_trust_source():
    relative = _configured_trust_dir().lstrip("/")
    selected = [
        f"{relative}/a3418fda.0.pem",
        f"{relative}/local.p11-kit",
        f"{relative}/anchors/local-root.pem",
        f"{relative}/blocklist/revoked.pem",
    ]
    missed = [p for p in selected if not CA_TRUST_HOOK.pattern.search(p)]
    assert not missed, (
        "a package can install these trust inputs and the extracted trust "
        "outputs every TLS client reads are never regenerated, with nothing "
        "reported: " + ", ".join(missed)
    )


def test_the_trigger_ignores_paths_that_are_not_trust_inputs_here():
    relative = _configured_trust_dir().lstrip("/")
    ignored = [
        # The directory entry itself carries no anchor to act on.
        f"{relative}/",
        # A sibling that merely shares a prefix is a different directory.
        f"{relative}-disabled/root.pem",
        # Nothing in this tree writes either of these two.
        "etc/ca-certificates/root.crt",
        "usr/share/ca-certificates/root.crt",
        # Shipped empty as a future drop-in point; p11-kit does not read it.
        "etc/pki/ca-trust/source/anchors/root.crt",
        # The EXTRACTED outputs. Triggering on these would make the hook
        # respond to its own result.
        "etc/ssl/certs/ca-certificates.crt",
        "etc/ssl/certs/01234567.0",
        "etc/pki/tls/certs/ca-bundle.crt",
    ]
    selected = [p for p in ignored if CA_TRUST_HOOK.pattern.search(p)]
    assert not selected, (
        "the certificate-trust hook fires on paths that are not this tree's "
        "trust inputs: " + ", ".join(selected)
    )


def test_the_hook_keeps_its_critical_class_and_its_command_builder():
    assert CA_TRUST_HOOK.critical is True
    assert CA_TRUST_HOOK.cmd_fn is hooks._update_ca_trust_cmd


def test_the_builder_still_refuses_a_foreign_root(tmp_path):
    relative = _configured_trust_dir().lstrip("/")
    root = tmp_path / "target"
    root.mkdir()
    assert CA_TRUST_HOOK.cmd_fn(str(root), [f"{relative}/root.pem"]) is None


def test_the_builder_still_names_the_reviewed_program_on_the_live_system():
    relative = _configured_trust_dir().lstrip("/")
    assert CA_TRUST_HOOK.cmd_fn("/", [f"{relative}/root.pem"]) == [
        "/usr/bin/update-ca-trust",
    ]
