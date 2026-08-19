# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Recipes whose post_install absorbed every systemctl failure with `|| true`.

WHY THIS EXISTS. `systemctl enable foo.service 2>/dev/null || true` accepts
every outcome the call can produce, including the one that matters: the unit
does not exist. A package whose own unit went missing would then build clean,
archive clean, install clean, and ship a service that is silently never
enabled — nothing in any log, any trace, or any manifest would say so.

The replacement is decided per line, never as a batch. Three shapes are in
use here, and each test below measures the shape that actually applies:

  * UNMASKED — the call runs bare, so a non-zero reaches the caller. Used
    where the operation works in every context the hook runs in and the only
    reachable failure is a genuine one. `systemctl enable` is an offline file
    operation: measured 2026-08-19 inside a chroot built from the systemd
    version this tree pins, enabling a present unit returns 0 and writes the
    symlink with and without /proc mounted, a repeat call returns 0, and only
    an absent unit returns 1. Every unit enabled by these recipes is shipped
    by the very package that enables it.

  * NARROWED — the call is guarded on the concrete condition that makes a
    failure impossible rather than real, and is bare inside that guard.
    daemon-reexec acts on a RUNNING manager, so it is guarded on
    /run/systemd/system, which no chroot has.

  * DELETED — the recipe was a second, silent owner of an operation pkm's
    canonical hook already owns and already reports. Removing the duplicate
    leaves one owner whose result is recorded.

Each test drives the REAL post_install sourced from the REAL build.sh with
systemctl stubbed to a chosen exit status, so it measures the policy a build
would actually apply rather than a description of it. Commands that are not
the mechanism under test (chown, install -d, systemd-sysusers,
apparmor_parser, chmod) are stubbed so the body reaches the line under test
on a host that is not a build chroot; systemctl is never stubbed away, only
instrumented.

Every "the call is gone" assertion is paired with a control that proves the
same harness DOES observe a call when one is present — an instrument that has
never been shown to detect a true positive cannot certify a zero.
"""

import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES = REPO_ROOT / "packages"

# Commands a post_install body calls that are NOT the mechanism under test.
# Stubbed so the body runs to completion on a test host instead of dying at
# the first chown of a path that exists only on a target. systemctl is
# deliberately absent from this list.
NEUTRAL_STUBS = """
    systemd-sysusers() { :; }
    apparmor_parser()  { :; }
    chown()            { :; }
    chmod()            { :; }
    install()          { :; }
"""

# A systemctl stub that both records its invocation and returns a chosen
# status. The marker is distinctive so a test cannot pass on incidental
# output.
def _systemctl_stub(rc):
    return f'    systemctl() {{ echo "SYSTEMCTL_CALLED:$*"; return {rc}; }}\n'


def _script(pkg_rel, rc, drop_systemctl=False):
    build_sh = PACKAGES / pkg_rel / "build.sh"
    assert build_sh.is_file(), f"no recipe at {build_sh}"
    stub = "" if drop_systemctl else _systemctl_stub(rc)
    # The subshell is not a convenience — it is the real call shape. Every
    # bash driver reaches post_install through pkg_run_phase, which runs it
    # as `( set -e; "$func" )` and then captures $?. Calling it directly
    # instead would let the recipe's own `set -e` terminate this harness
    # before any status could be read, which is a property of the harness
    # and not of the recipe. The Python lane reaches the same outcome by
    # running the hook in its own process and reading that process's status.
    return textwrap.dedent(f"""
        set -e
        source "{build_sh}"
{stub}{NEUTRAL_STUBS}
        set +e
        ( set -e; post_install )
        echo "POST_INSTALL_RC=$?"
    """)


def run_post_install(pkg_rel, rc=0, drop_systemctl=False, empty_path=False):
    """Drive the recipe's real post_install with systemctl at a chosen rc."""
    script = _script(pkg_rel, rc, drop_systemctl=drop_systemctl)
    if empty_path:
        # Make `command -v systemctl` genuinely fail: no stub function and no
        # systemctl on PATH. Absolute paths keep the rest of the body working.
        script = "PATH=/nonexistent-for-this-test\n" + script
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def run_post_install_without_a_manager(pkg_rel, rc=0):
    """Same, but with /run replaced by an empty tmpfs in a private mount
    namespace — the condition of a root that no systemd manager owns, which
    is what both the build chroot and the installer's target chroot are.

    Rootless: `unshare -rm` maps the caller to root inside a new user and
    mount namespace, so this needs no privilege and cannot prompt anyone.
    """
    inner = "mount -t tmpfs none /run\n" + _script(pkg_rel, rc)
    return subprocess.run(
        ["unshare", "-rm", "--propagation", "private", "bash", "-c", inner],
        capture_output=True, text=True,
    )


def recipe_text(pkg_rel):
    return (PACKAGES / pkg_rel / "build.sh").read_text()


def post_install_body(pkg_rel):
    """Just the post_install function, so an assertion about it cannot pass
    on a masked call that lives in some other hook of the same recipe."""
    text = recipe_text(pkg_rel)
    start = text.index("post_install()")
    end = text.index("\n}\n", start)
    return text[start:end]


def code_lines(body):
    """The body with comment-only lines removed. A recipe comment may quote
    the retired `|| true` while explaining why it is gone; an assertion that
    reads comments would then fail on correct code, or — worse — pass on a
    recipe whose real call is still masked because the comment happened to
    match first."""
    return "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith("#")
    )


# --------------------------------------------------------------------------
# The harness itself, proven against a known true positive and a known
# negative before any test relies on it.
# --------------------------------------------------------------------------

class TestTheHarness:

    def test_a_recipe_that_calls_systemctl_is_observed(self):
        r = run_post_install("desktop/cups", rc=0)
        assert "SYSTEMCTL_CALLED:enable cups.service" in r.stdout, r.stdout + r.stderr

    def test_a_failing_systemctl_is_observed_as_a_failing_hook(self):
        r = run_post_install("desktop/cups", rc=1)
        assert "POST_INSTALL_RC=1" in r.stdout, r.stdout + r.stderr

    def test_the_namespace_leg_really_removes_the_manager_directory(self):
        r = subprocess.run(
            ["unshare", "-rm", "--propagation", "private", "bash", "-c",
             "mount -t tmpfs none /run && "
             "{ [ -d /run/systemd/system ] && echo PRESENT || echo ABSENT; }"],
            capture_output=True, text=True,
        )
        assert "ABSENT" in r.stdout, r.stdout + r.stderr

    def test_the_manager_directory_is_present_outside_the_namespace(self):
        """The live-manager leg of the narrowed guard is only meaningful on a
        host that has one. Assert it rather than skipping silently."""
        assert Path("/run/systemd/system").is_dir(), (
            "this host has no /run/systemd/system, so the live-manager leg "
            "below would pass vacuously"
        )


# --------------------------------------------------------------------------
# UNMASKED — `systemctl enable` / `systemctl disable`
# --------------------------------------------------------------------------

# (package, the exact call the retrofit must reach)
UNMASKED = [
    ("core/pkm", "enable pkm-check-updates.timer"),
    ("core/networkmanager-pass1", "enable NetworkManager.service"),
    ("core/networkmanager-pass1", "disable systemd-networkd.service"),
    ("core/networkmanager-pass1", "disable systemd-networkd-wait-online.service"),
    ("core/networkmanager-pass1", "enable NetworkManager-wait-online.service"),
    ("desktop/networkmanager", "enable NetworkManager.service"),
    ("desktop/networkmanager", "disable systemd-networkd.service"),
    ("desktop/networkmanager", "disable systemd-networkd-wait-online.service"),
    ("desktop/networkmanager", "enable NetworkManager-wait-online.service"),
    ("desktop/bluez", "enable bluetooth.service"),
    ("desktop/forge", "enable forge-tui.service"),
    ("desktop/cups", "enable cups.service"),
    ("desktop/avahi", "enable avahi-daemon.service"),
    ("desktop/rtkit", "enable rtkit-daemon.service"),
    ("desktop/wireplumber", "enable --global pipewire.socket"),
    ("desktop/wireplumber", "enable --global pipewire-pulse.socket"),
    ("desktop/wireplumber", "enable --global wireplumber.service"),
]

UNMASKED_PACKAGES = sorted({pkg for pkg, _ in UNMASKED})


@pytest.mark.parametrize("pkg,call", UNMASKED)
def test_the_call_is_reached_exactly_as_written(pkg, call):
    """The retrofitted line runs, with the unit named the way the recipe
    names it. Catches a retrofit that silently stopped being reached."""
    r = run_post_install(pkg, rc=0)
    assert f"SYSTEMCTL_CALLED:{call}" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("pkg", UNMASKED_PACKAGES)
def test_a_succeeding_systemctl_leaves_the_hook_green(pkg):
    r = run_post_install(pkg, rc=0)
    assert "POST_INSTALL_RC=0" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("pkg", UNMASKED_PACKAGES)
def test_a_failing_systemctl_fails_the_hook(pkg):
    """The whole point. Both build lanes fail the package on a non-zero
    post_install (the three bash drivers log `FAILED in post_install (exit N)`
    and stop; the Python builder sets success=False), so this rc is what
    turns a missing unit into a halted build instead of a silent ship."""
    r = run_post_install(pkg, rc=1)
    assert "POST_INSTALL_RC=1" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("pkg", UNMASKED_PACKAGES)
def test_no_masked_systemctl_call_survives_in_the_hook(pkg):
    body = code_lines(post_install_body(pkg))
    offenders = [
        line for line in body.splitlines()
        if "systemctl" in line and "|| true" in line
    ]
    assert not offenders, offenders


def test_pkm_still_tolerates_an_absent_systemctl():
    """pkm's `command -v systemctl` guard is a real named condition, not a
    mask: this is a core-tier package and the early build phases that import
    pkm run before systemd exists in the chroot. Measured here by making the
    command genuinely unavailable rather than by reading the guard."""
    r = run_post_install("core/pkm", drop_systemctl=True, empty_path=True)
    assert "POST_INSTALL_RC=0" in r.stdout, r.stdout + r.stderr
    assert "SYSTEMCTL_CALLED" not in r.stdout


def test_pkm_does_not_tolerate_a_failing_systemctl_once_one_exists():
    r = run_post_install("core/pkm", rc=1)
    assert "POST_INSTALL_RC=1" in r.stdout, r.stdout + r.stderr


# --------------------------------------------------------------------------
# NARROWED — `systemctl daemon-reexec`, guarded on a live manager
# --------------------------------------------------------------------------

class TestSystemdPass2Reexec:
    """desktop/systemd-pass2 — daemon-reexec re-executes a RUNNING manager.
    No manager owns the build chroot's root or the installer's target-chroot
    root, so the guard names an impossible operation, not a failed one. pkm
    ships a canonical owner for daemon-reload but none for reexec, so this
    recipe is the only caller and a mask would leave nobody reporting."""

    PKG = "desktop/systemd-pass2"

    def test_the_precondition_this_recipe_checks_holds_on_this_host(self):
        """post_install returns 1 before reaching systemctl if pam_systemd.so
        is missing, which would make every assertion below vacuous."""
        assert Path("/usr/lib/security/pam_systemd.so").is_file(), (
            "pam_systemd.so absent on this host; the reexec leg is unreachable"
        )

    def test_the_reexec_runs_when_a_manager_owns_the_root(self):
        r = run_post_install(self.PKG, rc=0)
        assert "SYSTEMCTL_CALLED:daemon-reexec" in r.stdout, r.stdout + r.stderr
        assert "POST_INSTALL_RC=0" in r.stdout

    def test_a_failing_reexec_fails_the_hook_on_a_live_manager(self):
        r = run_post_install(self.PKG, rc=1)
        assert "POST_INSTALL_RC=1" in r.stdout, r.stdout + r.stderr

    def test_the_reexec_is_skipped_where_no_manager_owns_the_root(self):
        """The narrowing's whole justification. With /run an empty tmpfs the
        call must not run at all — and must not fail the hook even though the
        stub would return 1 if it were reached."""
        r = run_post_install_without_a_manager(self.PKG, rc=1)
        assert "SYSTEMCTL_CALLED" not in r.stdout, r.stdout + r.stderr
        assert "POST_INSTALL_RC=0" in r.stdout, r.stdout + r.stderr

    def test_the_guard_is_not_a_bare_mask(self):
        body = code_lines(post_install_body(self.PKG))
        assert "|| true" not in body
        assert "/run/systemd/system" in body


# --------------------------------------------------------------------------
# DELETED — the duplicate daemon-reload, now owned solely by pkm
# --------------------------------------------------------------------------

# package -> the unit filename its do_install stages, which is what arms
# pkm's canonical hook. Derived from the recipe below rather than trusted
# from this table: the table says which unit to look for, the recipe has to
# actually stage it.
DAEMON_RELOAD_DELETED = {
    "extra/lighttpd": "lighttpd.service",
    "extra/etcd": "etcd.service",
    "extra/caddy": "caddy.service",
    "extra/nginx": "nginx.service",
    "extra/memcached": "memcached.service",
    "extra/influxdb": "influxdb.service",
    "extra/postgresql": "postgresql.service",
    "extra/valkey": "valkey.service",
    "extra/apache-httpd": "httpd.service",
    "extra/mariadb": "mariadb.service",
    "extra/docker": "docker.service",
    "extra/haproxy": "haproxy.service",
}


@pytest.mark.parametrize("pkg", sorted(DAEMON_RELOAD_DELETED))
def test_the_recipe_makes_no_systemctl_call_at_all(pkg):
    """Driven, not read: the body runs to completion with systemctl
    instrumented and failing, and neither is it invoked nor does the hook
    fail. The harness's true-positive control is TestTheHarness above."""
    r = run_post_install(pkg, rc=1)
    assert "SYSTEMCTL_CALLED" not in r.stdout, r.stdout + r.stderr
    assert "POST_INSTALL_RC=0" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("pkg", sorted(DAEMON_RELOAD_DELETED))
def test_the_recipe_stages_the_unit_that_arms_the_canonical_owner(pkg):
    """Read from the recipe's CODE, never its comments.

    The comments added by this change name the very path this test looks
    for, so a whole-file search would be satisfied by the explanation of the
    deletion instead of by the staging line that justifies it — the assertion
    would then hold on a recipe that had stopped shipping its unit entirely.
    Some recipes stage with the destination as a directory
    (`install -m 644 .../foo.service "$DESTDIR"/usr/lib/systemd/system/`) and
    some with the full path, so both parts are required rather than the
    concatenation.
    """
    unit = DAEMON_RELOAD_DELETED[pkg]
    code = code_lines(recipe_text(pkg))
    assert "usr/lib/systemd/system" in code, (
        f"{pkg} stages nothing into usr/lib/systemd/system, so pkm's "
        f"canonical daemon-reload hook is no longer armed for it and the "
        f"deleted recipe call has no successor"
    )
    assert unit in code, (
        f"{pkg} no longer names {unit} in its code, so the unit that armed "
        f"the canonical daemon-reload hook is gone"
    )


@pytest.mark.parametrize("pkg", sorted(DAEMON_RELOAD_DELETED))
def test_the_canonical_owner_claims_that_unit_and_skips_a_chroot(pkg):
    """Measured through pkm's own hook objects — the same pattern and
    command function a real install evaluates — not through a description of
    them."""
    import sys
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from pkm.hooks import CANONICAL_HOOKS

    hook = next(h for h in CANONICAL_HOOKS if h.id == "systemd-daemon-reload")
    rel = f"usr/lib/systemd/system/{DAEMON_RELOAD_DELETED[pkg]}"

    assert hook.pattern.search(rel), f"{rel} does not arm the canonical hook"
    assert hook.cmd_fn("/", [rel]) == ["systemctl", "daemon-reload"]
    assert hook.cmd_fn("/mnt/some-target-root", [rel]) is None, (
        "the canonical owner must do nothing on a root no manager owns — "
        "which is the context the deleted recipe calls ran in at build time"
    )


def test_the_canonical_owner_is_not_silent_about_failure():
    """The honest limit of the deletion, pinned so it cannot drift without
    notice: the canonical owner is cosmetic, so a failing daemon-reload is
    reported and traced but does not halt. Deletion moved the failure from
    invisible to reported, not to fatal."""
    import sys
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from pkm.hooks import CANONICAL_HOOKS

    hook = next(h for h in CANONICAL_HOOKS if h.id == "systemd-daemon-reload")
    assert hook.critical is False


# --------------------------------------------------------------------------
# NARROWED — the nvidia pre-remove script (beyond the 30 recipe lines)
# --------------------------------------------------------------------------

NVIDIA_PRE_REMOVE = PACKAGES / "extra/nvidia/hooks/pre-remove.sh"


def run_nvidia_pre_remove(systemctl_stub):
    """Drive the real pre-remove script with systemctl stubbed.

    Sandboxed deliberately. The script's third step purges
    /lib/modules/*/extra/nvidia, which exists on any machine actually running
    this driver — including the one a developer runs these tests on. So the
    script runs inside a private mount namespace with tmpfs over /lib/modules
    and /var/log, with rm/depmod/modprobe/lsmod stubbed on top. A test must
    not be able to execute the destructive half of the thing it measures.

    Rootless via `unshare -rm`: no privilege, nothing to prompt for.
    """
    inner = (
        "mount -t tmpfs none /lib/modules && "
        "mount -t tmpfs none /var/log && "
        + systemctl_stub + " "
        'rm() { echo "RM_CALLED:$*"; }; '
        "modprobe() { return 1; }; lsmod() { :; }; depmod() { :; }; "
        f"source {NVIDIA_PRE_REMOVE}"
    )
    return subprocess.run(
        ["unshare", "-rm", "--propagation", "private", "bash", "-c", inner],
        capture_output=True, text=True,
    )


class TestNvidiaPreRemove:
    """packages/extra/nvidia/hooks/pre-remove.sh — stop/disable of four
    units, of which a given machine may legitimately carry only some. The
    named condition is unit existence, tested before acting; what survives
    the test is a real failure and is reported through the script's own log.

    THE SCRIPT IS CURRENTLY UNREACHABLE. It is installed to
    /var/lib/pkm/hooks/nvidia/pre-remove and nothing invokes it: pkm
    implements a per-package post-install hook and no remove-side hook at all,
    and pkm's remover runs no external command. The narrowing is therefore
    prospective — correct for the day the hook is wired, and not a live
    silent-failure surface today. These tests measure the script's own
    behaviour directly, because there is no caller to measure it through.
    """

    def test_the_sandbox_really_hides_the_module_tree(self):
        """Proven before anything relies on it: inside the namespace the
        purge target must not resolve, so the destructive loop has nothing
        to walk."""
        r = subprocess.run(
            ["unshare", "-rm", "--propagation", "private", "bash", "-c",
             "mount -t tmpfs none /lib/modules && "
             "ls -d /lib/modules/*/extra/nvidia 2>/dev/null | wc -l"],
            capture_output=True, text=True,
        )
        assert r.stdout.strip() == "0", r.stdout + r.stderr

    def test_no_bare_mask_remains_on_the_systemctl_calls(self):
        lines = [
            line for line in NVIDIA_PRE_REMOVE.read_text().splitlines()
            if not line.lstrip().startswith("#")
            and "systemctl" in line and "|| true" in line
        ]
        assert not lines, lines

    def test_existence_is_tested_before_the_unit_is_acted_on(self):
        text = NVIDIA_PRE_REMOVE.read_text()
        gate = text.index("systemctl cat --")
        stop = text.index('systemctl stop "$unit"')
        assert gate < stop, "the existence test must precede the stop"

    def test_an_absent_unit_is_skipped_without_a_warning(self):
        """`cat` reports absent, so stop/disable must never be reached and no
        warning must be logged: a unit that is simply not installed is an
        impossible operation, not a failed one."""
        r = run_nvidia_pre_remove(
            'systemctl() { case "$1" in cat) return 1 ;; '
            '*) echo "ACTED:$*"; return 1 ;; esac; };'
        )
        combined = r.stdout + r.stderr
        assert "ACTED:" not in combined, combined
        assert "WARNING: 'systemctl" not in combined, combined

    def test_a_present_unit_is_acted_on(self):
        """The control for the test above: with `cat` reporting present, the
        stop and disable really are reached."""
        r = run_nvidia_pre_remove(
            'systemctl() { case "$1" in cat) return 0 ;; '
            '*) echo "ACTED:$*"; return 0 ;; esac; };'
        )
        combined = r.stdout + r.stderr
        assert "ACTED:stop nvidia-persistenced.service" in combined, combined
        assert "ACTED:disable nvidia-persistenced.service" in combined, combined

    def test_a_present_unit_whose_stop_fails_is_reported(self):
        r = run_nvidia_pre_remove(
            'systemctl() { case "$1" in cat) return 0 ;; stop) return 5 ;; '
            '*) return 0 ;; esac; };'
        )
        combined = r.stdout + r.stderr
        assert "WARNING: 'systemctl stop" in combined, combined
        assert "exited 5" in combined, combined

    def test_a_present_unit_whose_disable_fails_is_reported(self):
        r = run_nvidia_pre_remove(
            'systemctl() { case "$1" in cat) return 0 ;; disable) return 7 ;; '
            '*) return 0 ;; esac; };'
        )
        combined = r.stdout + r.stderr
        assert "WARNING: 'systemctl disable" in combined, combined
        assert "exited 7" in combined, combined

    def test_a_clean_stop_and_disable_logs_no_warning(self):
        r = run_nvidia_pre_remove("systemctl() { return 0; };")
        combined = r.stdout + r.stderr
        assert "WARNING: 'systemctl" not in combined, combined
