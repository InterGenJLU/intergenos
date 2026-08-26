# SPDX-License-Identifier: GPL-3.0-or-later
"""Every canonical hook acts on the root it is given — proved, not assumed.

WHY THIS FILE EXISTS.

pkm's canonical hooks each receive the install root and are expected to do one
of two things with it: run the tool against that root, or decline to run at all
because the operation only means something on the running system. Eleven of the
thirteen do exactly that — depmod takes `-b`, ldconfig takes `-r`,
systemd-sysusers and systemd-tmpfiles take `--root`, the schema, icon, desktop
and mime tools are handed root-qualified directories, and apparmor_parser and
`systemctl daemon-reload` decline for a foreign root because they would speak to
the running kernel and the running init.

Two did not, and this file is what caught them. Measured on a real install into
a scratch root (pkm --root <dir> install font-alias, from the mirror): the line
`hook[font-cache] OK (fontconfig cache)` was printed while the command run was
`fc-cache -f` with no root anywhere in it. That rebuilds the cache of the
machine RUNNING pkm and leaves the target's unbuilt — a write outside the
install root during an install into it, and an install that reports success
with the target's font cache never made. `update-ca-trust` carries the same
shape.

WHAT THESE TESTS PIN.

1. No canonical hook, for any root, produces a command that mentions neither
   the root nor a root-scoping flag — unless it declines to run entirely. This
   is the general form, so a hook added later cannot reintroduce the class.
2. fc-cache is given the root through the option fontconfig actually has. This
   machine's fontconfig is 2.17.1 and its `fc-cache --help` lists
   `-y, --sysroot=SYSROOT  prepend SYSROOT to all paths for scanning`; the flag
   is read from the tool, not from memory.
3. update-ca-trust declines for a foreign root rather than rebuilding the
   running system's trust store.
4. The live-system behaviour of both is unchanged, because that is the case
   every installed machine takes.
"""

from __future__ import annotations

from pathlib import Path

from pkm import hooks


def _all_hooks():
    seen = {}
    for group in (hooks.CANONICAL_HOOKS_PRE, hooks.CANONICAL_HOOKS):
        for hook in group:
            seen[hook.id] = hook
    return sorted(seen.values(), key=lambda h: h.id)


def _matched_for(hook):
    """A plausible matched-path list for a hook, from its own pattern.

    The hooks match on install-root-relative paths, so a few representative
    ones covering every pattern in the set are enough to get each cmd_fn to
    produce its command.
    """
    candidates = [
        "usr/lib/modules/6.18.10-igos-4/kernel/fs/ext4/ext4.ko.zst",
        "usr/lib/libfoo.so.1",
        "usr/share/glib-2.0/schemas/org.example.gschema.xml",
        "etc/apparmor.d/usr.bin.demo",
        "etc/ssl/certs/demo.pem",
        "usr/share/ca-certificates/trust-source/anchors/demo.crt",
        "usr/share/pki/trust/anchors/demo.crt",
        "usr/share/icons/hicolor/scalable/apps/demo.svg",
        "usr/share/fonts/demo/Demo-Regular.ttf",
        "usr/share/applications/demo.desktop",
        "usr/share/mime/packages/demo.xml",
        "usr/lib/systemd/system/demo.service",
        "usr/lib/sysusers.d/demo.conf",
        "usr/lib/tmpfiles.d/demo.conf",
        "usr/share/intergenos/account-skel/passwd",
    ]
    return [p for p in candidates if hook.pattern.search(p)]


def test_no_hook_runs_rootless_against_a_foreign_root(tmp_path):
    """The general form: name the root, or decline. Never neither."""
    root = tmp_path / "target"
    (root / "etc").mkdir(parents=True)

    offenders = []
    for hook in _all_hooks():
        matched = _matched_for(hook)
        if not matched:
            continue
        cmd = hook.cmd_fn(str(root), matched)
        if cmd is None:
            continue  # declined, which is the other honest answer
        if not any(str(root) in part for part in cmd):
            offenders.append(f"{hook.id}: {' '.join(cmd)}")
    assert not offenders, (
        "these canonical hooks would run against the machine pkm is running on "
        "while installing into another root, writing outside it and leaving the "
        "target's own state unbuilt:\n  " + "\n  ".join(offenders)
    )


def test_fc_cache_is_given_the_root_through_the_option_fontconfig_has(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    cmd = hooks._fc_cache_cmd(str(root), ["usr/share/fonts/demo/Demo.ttf"])
    assert cmd is not None, "the font cache hook declined for a foreign root"
    assert cmd[0] == "fc-cache"
    joined = " ".join(cmd)
    assert str(root) in joined
    assert "--sysroot" in joined or "-y" in cmd, (
        "the root is passed to fc-cache by some other means than the option it "
        f"documents (-y/--sysroot): {joined}"
    )


def test_fc_cache_on_the_live_system_is_unchanged():
    assert hooks._fc_cache_cmd("/", ["usr/share/fonts/demo/Demo.ttf"]) == [
        "fc-cache", "-f",
    ]


def test_update_ca_trust_declines_for_a_foreign_root(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    assert hooks._update_ca_trust_cmd(str(root), ["etc/ssl/certs/demo.pem"]) is None, (
        "the CA trust hook would rebuild the RUNNING system's trust store "
        "during an install into another root"
    )


def test_update_ca_trust_on_the_live_system_is_unchanged():
    assert hooks._update_ca_trust_cmd("/", ["etc/ssl/certs/demo.pem"]) == [
        "update-ca-trust",
    ]
