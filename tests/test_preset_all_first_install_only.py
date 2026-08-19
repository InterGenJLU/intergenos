# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The systemd package applies the preset policy on a first install only.

WHY THIS EXISTS. The systemd recipe's post_install ran `systemctl preset-all`
unconditionally. That is correct in the three contexts where no user has chosen
anything yet — the build chroot, the image chroot, and the target the installer
is populating — and wrong in the fourth. pkm fires a package's sealed
post_install on every install AND every upgrade (pkm/installer.py has exactly
one archive-lifecycle call site and it names post_install; the upgrade command
reaches it through installer.install), so upgrading systemd on a running
machine re-applied the whole default policy over that machine and turned off
every service its owner had turned on — remote login among them — with nothing
written anywhere to say it had happened.

WHAT THIS MEASURES. The recipe's real post_install, sealed by the real sealer
(igos-build/hookseal.py) exactly as the build seals it into the archive, run
against real filesystem roots, with enablement resolved by the real preset
engine — `systemctl --root <root> preset-all`, the same command the installer
runs. Nothing here reimplements a predicate or a resolver.

Both directions are measured:

  * a first-install root — no /etc/machine-id — still resolves the full preset
    policy, so nothing about a fresh install changes;
  * a populated root — a valid machine-id, as any booted system has — keeps an
    enablement its owner made, across the hook firing again.

THE PREDICATE IS SYSTEMD'S OWN. /etc/machine-id is what systemd itself uses to
decide whether a boot is the first one (machine-id(5), FIRST BOOT SEMANTICS):
absent means first boot, the literal string "uninitialized" means first boot,
and an EMPTY file means the boot is NOT a first boot. The empty case is the one
that is easy to get backwards, so it is pinned below in its own test.

⛔ NO TEST HERE MAY REACH THE MACHINE RUNNING IT, AND THAT IS ENFORCED RATHER
THAN INTENDED. The pre-fix hook issues an UNROOTED `systemctl preset-all`,
which acts on whatever system runs it — so a harness that simply fired the
recipe's hook would re-apply the default policy to the host running the suite.
It is not enough to fire only the fixed hook, because the hook under test is
read from the recipe and a regression would put the unrooted form back. Every
firing below therefore goes through a stand-in `systemctl` placed earlier on
PATH which records the argv, passes a `--root` call through to the real engine
unchanged, and REFUSES an unrooted call with a distinct exit status. The real
engine still does the real resolving; what it cannot be handed is the host.
(Written after the first draft of this file, which lacked the guard, issued
four unrooted preset-alls at this machine on the pre-fix tree. They failed
closed on authentication and changed nothing — proven by enablement-symlink
change times, not by the error text — but the harness, not the luck, is what
has to make that impossible.)
"""

import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SYSTEMD_BUILD_SH = REPO_ROOT / "packages" / "core" / "systemd" / "build.sh"

# A unit the preset policy resolves to enabled, and one it resolves to
# disabled. Both are read back from the engine in the harness proofs below
# rather than assumed, so a preset-file edit that moved either one fails
# loudly here instead of quietly weakening every assertion that uses them.
ENABLED_BY_POLICY = "bluetooth.service"
DISABLED_BY_POLICY = "cups.service"

VALID_MACHINE_ID = "0123456789abcdef0123456789abcdef"

# The guard's refusal status. Distinct from anything systemctl itself returns,
# so a refusal can never be mistaken for a resolver verdict.
GUARD_REFUSED = 97


def _hookseal():
    """Load the real sealer the build uses, by path.

    igos-build is not an importable package name (the hyphen), and the build
    lane invokes this module as a script for the same reason. Loading the real
    file is the point: a test that re-implemented the extraction would be
    proving something other than what ships.
    """
    path = REPO_ROOT / "igos-build" / "hookseal.py"
    spec = importlib.util.spec_from_file_location("_hookseal_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sealed_post_install(build_sh_text, tmp_path, name="systemd"):
    """The archive's .scripts/post_install.sh, produced by the real sealer."""
    hookseal = _hookseal()
    body = hookseal.extract_function(build_sh_text, "post_install")
    assert body is not None, "the systemd recipe declares no post_install"
    script = hookseal.render_script("post_install", body, name, "259.1")
    path = tmp_path / "post_install.sh"
    path.write_text(script)
    path.chmod(0o755)
    return path


def _preset_files():
    """Every *.preset file the tree ships, keyed by the name systemd sorts on.

    Tree-wide, not packages/-only: a preset can be authored anywhere a recipe
    can reach, and a search that cannot see a file it is meant to honour does
    not report nothing — it reports the wrong thing.
    """
    found = {}
    for p in sorted(REPO_ROOT.rglob("*.preset")):
        if ".git" in p.parts:
            continue
        found.setdefault(p.name, p)
    return found


def _make_root(tmp_path, units, machine_id=None, name="root"):
    """A filesystem root the preset engine can resolve against.

    `machine_id` is written verbatim when given — including the empty string,
    which is a state with its own meaning — and the file is absent when it is
    None, which is the first-install state.
    """
    root = tmp_path / name
    (root / "usr/lib/systemd/system").mkdir(parents=True)
    (root / "usr/lib/systemd/system-preset").mkdir(parents=True)
    (root / "etc/systemd/system").mkdir(parents=True)

    for preset_name, src in _preset_files().items():
        shutil.copy(src, root / "usr/lib/systemd/system-preset" / preset_name)

    # A stand-in unit per name. The preset verb for a unit depends on its NAME,
    # not its contents; an [Install] section is what makes enable/disable mean
    # anything at all.
    for unit in units:
        (root / "usr/lib/systemd/system" / unit).write_text(
            "[Unit]\nDescription=preset resolution stand-in\n"
            "[Service]\nExecStart=/bin/true\n"
            "[Install]\nWantedBy=multi-user.target\n"
        )

    if machine_id is not None:
        (root / "etc/machine-id").write_text(machine_id)
    return root


def _is_enabled(root, unit):
    r = subprocess.run(
        [_REAL_SYSTEMCTL, "--root", str(root), "is-enabled", unit],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


_REAL_SYSTEMCTL = shutil.which("systemctl") or "/usr/bin/systemctl"


def _guard(tmp_path):
    """Install the guarding `systemctl` and return (bindir, call-log path).

    Records every invocation, forwards a `--root` call to the real engine so
    the resolving under test is genuinely done by systemd, and refuses an
    unrooted call rather than letting it reach this machine.
    """
    bindir = tmp_path / "guard-bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "systemctl-calls.log"
    shim = bindir / "systemctl"
    shim.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        'if [ "$1" = "--root" ]; then\n'
        f'    exec "{_REAL_SYSTEMCTL}" "$@"\n'
        "fi\n"
        'printf "REFUSED: an unrooted systemctl call would act on this '
        'machine: %s\\n" "$*" >&2\n'
        f"exit {GUARD_REFUSED}\n"
    )
    shim.chmod(0o755)
    return bindir, log


def _run_hook(script, root, tmp_path, root_env="__use_root__"):
    """Fire the sealed hook the way pkm fires it — bash -e, on the host, with
    PKM_PACKAGE_ROOT naming the root it acts on — behind the guard.

    Returns (CompletedProcess, [argv strings the hook passed to systemctl]).
    """
    bindir, log = _guard(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["PKM_PACKAGE_NAME"] = "systemd"
    env["PKM_PACKAGE_VERSION"] = "259.1"
    env["PKM_PACKAGE_OPERATION"] = "post_install"
    env.pop("PKM_PACKAGE_ROOT", None)
    if root_env == "__use_root__":
        env["PKM_PACKAGE_ROOT"] = str(root)
    elif root_env is not None:
        env["PKM_PACKAGE_ROOT"] = root_env

    result = subprocess.run(
        ["bash", "-e", str(script)],
        env=env, capture_output=True, text=True, timeout=120,
    )
    calls = [c for c in log.read_text().splitlines() if c.strip()] \
        if log.exists() else []
    return result, calls


# --------------------------------------------------------------------------
# The harness proves itself before anything relies on it.
# --------------------------------------------------------------------------

class TestTheHarness:

    def test_the_real_preset_engine_is_available(self):
        r = subprocess.run([_REAL_SYSTEMCTL, "--version"],
                           capture_output=True, text=True)
        assert r.returncode == 0, "no systemctl on this host; this cannot run"

    def test_the_sealer_produces_a_runnable_script(self, tmp_path):
        script = _sealed_post_install(SYSTEMD_BUILD_SH.read_text(), tmp_path)
        text = script.read_text()
        assert text.startswith("#!/bin/bash"), text[:80]
        r = subprocess.run(["bash", "-n", str(script)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def test_the_policy_units_resolve_the_way_this_file_assumes(self, tmp_path):
        """True positives in both directions. Every assertion below reads
        `enabled` or `disabled` as a measurement, which is only meaningful if
        the engine really produces both against this tree's preset files."""
        units = [ENABLED_BY_POLICY, DISABLED_BY_POLICY]
        root = _make_root(tmp_path, units)
        subprocess.run([_REAL_SYSTEMCTL, "--root", str(root), "preset-all"],
                       capture_output=True, text=True, check=True)
        assert _is_enabled(root, ENABLED_BY_POLICY) == "enabled"
        assert _is_enabled(root, DISABLED_BY_POLICY) == "disabled"

    def test_the_guard_refuses_an_unrooted_call(self, tmp_path):
        bindir, log = _guard(tmp_path)
        env = dict(os.environ)
        env["PATH"] = f"{bindir}:{env['PATH']}"
        r = subprocess.run(["bash", "-c", "systemctl preset-all"],
                           env=env, capture_output=True, text=True)
        assert r.returncode == GUARD_REFUSED, (r.returncode, r.stderr)
        assert log.read_text().strip() == "preset-all"

    def test_the_guard_passes_a_rooted_call_through_to_the_real_engine(
            self, tmp_path):
        """Without this, every `enabled` verdict below could be the guard
        swallowing the call rather than systemd resolving it."""
        root = _make_root(tmp_path, [ENABLED_BY_POLICY])
        bindir, log = _guard(tmp_path)
        env = dict(os.environ)
        env["PATH"] = f"{bindir}:{env['PATH']}"
        r = subprocess.run(
            ["bash", "-c", f"systemctl --root {root} preset-all"],
            env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert _is_enabled(root, ENABLED_BY_POLICY) == "enabled"
        assert log.read_text().strip() == f"--root {root} preset-all"


# --------------------------------------------------------------------------
# What the pre-fix recipe does — measured, safely, so the fix has a red to be
# green against.
# --------------------------------------------------------------------------

PRE_FIX_POST_INSTALL = """post_install() {
    set -e
    # Enable/disable services per preset policy
    systemctl preset-all
}
"""


class TestTheDefectTheFixRemoves:
    """The pre-fix hook, and what its verb does to a user's enablement.

    The pre-fix text is carried here verbatim rather than read from git,
    because this is a regression guard: it states the shape that must never
    come back, and it keeps stating it after the commit that removed it is
    ancient history.
    """

    def test_the_pre_fix_hook_presets_unconditionally_and_unrooted(
            self, tmp_path):
        """It issues the same command whether the root has booted or not, and
        it names no root — so on a live upgrade it acts on that live system.
        The guard is what turns that into a recorded refusal here instead of a
        preset-all against the machine running the suite."""
        script = _sealed_post_install(PRE_FIX_POST_INSTALL, tmp_path)
        seen = []
        for name, mid in (("fresh", None), ("booted", VALID_MACHINE_ID)):
            sub = tmp_path / name
            sub.mkdir()
            root = _make_root(sub, [ENABLED_BY_POLICY], machine_id=mid)
            r, calls = _run_hook(script, root, sub)
            assert r.returncode == GUARD_REFUSED, (
                "the pre-fix hook was expected to issue an unrooted call the "
                "guard refuses", r.returncode, r.stderr)
            seen.extend(calls)
        assert seen == ["preset-all", "preset-all"], seen
        assert not any("--root" in c for c in seen), (
            "the pre-fix hook was expected to name no root at all", seen)

    def test_that_verb_reverts_an_enablement_a_user_made(self, tmp_path):
        """The real engine, the pre-fix verb, a root that has booted: the
        user's choice is gone. This is the harm, measured rather than argued."""
        root = _make_root(tmp_path, [DISABLED_BY_POLICY],
                          machine_id=VALID_MACHINE_ID)
        subprocess.run(
            [_REAL_SYSTEMCTL, "--root", str(root), "enable",
             DISABLED_BY_POLICY],
            capture_output=True, text=True, check=True,
        )
        assert _is_enabled(root, DISABLED_BY_POLICY) == "enabled"

        subprocess.run([_REAL_SYSTEMCTL, "--root", str(root), "preset-all"],
                       capture_output=True, text=True, check=True)
        assert _is_enabled(root, DISABLED_BY_POLICY) == "disabled", (
            "preset-all was expected to revert the enablement; if it no longer "
            "does, the defect this cut removes has changed shape and the fix "
            "needs re-deriving"
        )


# --------------------------------------------------------------------------
# The invariant: the shipped hook, fired for real, in both directions.
# --------------------------------------------------------------------------

@pytest.fixture
def sealed_hook(tmp_path):
    return _sealed_post_install(SYSTEMD_BUILD_SH.read_text(), tmp_path)


def test_a_first_install_root_still_resolves_the_full_policy(sealed_hook,
                                                             tmp_path):
    """Nothing about a fresh install changes. The root has no machine-id,
    which is what the installer's package phase presents — it writes the
    target's id later, in its config phase."""
    units = [ENABLED_BY_POLICY, DISABLED_BY_POLICY]
    root = _make_root(tmp_path, units)

    r, calls = _run_hook(sealed_hook, root, tmp_path)
    assert r.returncode == 0, r.stderr
    assert calls == [f"--root {root} preset-all"], calls

    assert _is_enabled(root, ENABLED_BY_POLICY) == "enabled"
    assert _is_enabled(root, DISABLED_BY_POLICY) == "disabled"


def test_the_uninitialized_marker_is_still_a_first_install(sealed_hook,
                                                          tmp_path):
    """build-squashfs.sh writes the literal "uninitialized" into the image, and
    machine-id(5) rule 3 says that is still a first boot. The image must keep
    getting the full pass."""
    root = _make_root(tmp_path, [ENABLED_BY_POLICY],
                      machine_id="uninitialized\n")

    r, calls = _run_hook(sealed_hook, root, tmp_path)
    assert r.returncode == 0, r.stderr
    assert calls == [f"--root {root} preset-all"], calls
    assert _is_enabled(root, ENABLED_BY_POLICY) == "enabled"


def test_a_populated_root_keeps_an_enablement_its_owner_made(sealed_hook,
                                                            tmp_path):
    """The defect, gone. A machine that has booted keeps what its owner turned
    on when this package is upgraded on it."""
    root = _make_root(tmp_path, [DISABLED_BY_POLICY],
                      machine_id=VALID_MACHINE_ID)
    subprocess.run(
        [_REAL_SYSTEMCTL, "--root", str(root), "enable", DISABLED_BY_POLICY],
        capture_output=True, text=True, check=True,
    )
    assert _is_enabled(root, DISABLED_BY_POLICY) == "enabled"

    r, calls = _run_hook(sealed_hook, root, tmp_path)
    assert r.returncode == 0, r.stderr
    assert calls == [], ("the hook presetted a machine that has booted", calls)

    assert _is_enabled(root, DISABLED_BY_POLICY) == "enabled", (
        "upgrading systemd reverted an enablement the machine's owner made"
    )


def test_an_empty_marker_is_not_a_first_install(sealed_hook, tmp_path):
    """machine-id(5) rule 4: an EMPTY /etc/machine-id means the boot is NOT a
    first boot. Read the other way round — the way a plain "is the file empty"
    check would read it — this hook would preset a live system every time
    systemd's own commit step had not yet written the id back to disk."""
    root = _make_root(tmp_path, [DISABLED_BY_POLICY], machine_id="")
    subprocess.run(
        [_REAL_SYSTEMCTL, "--root", str(root), "enable", DISABLED_BY_POLICY],
        capture_output=True, text=True, check=True,
    )

    r, calls = _run_hook(sealed_hook, root, tmp_path)
    assert r.returncode == 0, r.stderr
    assert calls == [], ("an empty marker was read as a first boot", calls)
    assert _is_enabled(root, DISABLED_BY_POLICY) == "enabled"


def test_the_hook_says_which_branch_it_took(sealed_hook, tmp_path):
    """Both branches announce themselves. A hook that leaves a machine's
    enablement alone and says nothing is indistinguishable, to the person
    reading the output, from one that quietly changed it."""
    fresh_dir = tmp_path / "a"
    booted_dir = tmp_path / "b"
    fresh_dir.mkdir()
    booted_dir.mkdir()
    fresh = _make_root(fresh_dir, [ENABLED_BY_POLICY])
    booted = _make_root(booted_dir, [ENABLED_BY_POLICY],
                        machine_id=VALID_MACHINE_ID)

    fresh_out = _run_hook(sealed_hook, fresh, fresh_dir)[0].stdout
    booted_out = _run_hook(sealed_hook, booted, booted_dir)[0].stdout

    assert fresh_out.strip(), "the first-install branch said nothing"
    assert booted_out.strip(), "the populated-root branch said nothing"
    assert fresh_out.strip() != booted_out.strip(), (
        "both branches printed the same line", fresh_out)
    # Each line names the root it decided about, so a reader of a log holding
    # several package operations can tell which root was meant.
    assert str(fresh) in fresh_out, fresh_out
    assert str(booted) in booted_out, booted_out


@pytest.mark.parametrize("root_env", ["/", None])
def test_on_a_machine_that_has_booted_the_hook_issues_nothing(sealed_hook,
                                                              tmp_path,
                                                              root_env):
    """Fired against the real live root of the machine running this suite —
    which has booted and holds a real machine-id — the hook must issue no
    systemctl call at all. This is the case the cut exists for, measured
    against reality rather than against a constructed root.

    Both forms of "the live root" are covered: PKM_PACKAGE_ROOT="/" as pkm
    passes it for a live operation, and PKM_PACKAGE_ROOT absent as the bash
    build drivers leave it when they call post_install inside the chroot.
    """
    live_marker = Path("/etc/machine-id")
    if not live_marker.exists():
        pytest.skip("this host presents as a first boot")
    content = live_marker.read_text().strip()
    if content == "uninitialized" or not content:
        pytest.skip("this host presents as a first boot; the case under test "
                    "is a machine that has already booted")

    r, calls = _run_hook(sealed_hook, None, tmp_path, root_env=root_env)
    assert r.returncode == 0, r.stderr
    assert calls == [], (
        "the hook issued a systemctl call against a machine that has booted",
        calls)
    assert r.stdout.strip(), "it skipped silently"


def test_the_recipe_states_the_predicate_it_uses():
    """The reason lives beside the code. A future reader who finds this branch
    and cannot tell why it exists is the person most likely to remove it."""
    text = SYSTEMD_BUILD_SH.read_text()
    m = re.search(r"(?m)^post_install\(\) \{\n(.*?)^\}\n", text, re.S)
    assert m, "post_install not found in the systemd recipe"
    body = m.group(1)
    assert "machine-id" in body
    assert "PKM_PACKAGE_ROOT" in body
