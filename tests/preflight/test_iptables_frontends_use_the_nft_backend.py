# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The shipped iptables frontends must resolve to a backend the kernel has.

THE DEFECT THIS PINS. This distribution is nftables-only: the kernel fragment
turns the legacy xtables interface off, and the running kernel ships zero
``ip_tables`` modules. The iptables package is described in its own recipe as
the nft compat shim. But upstream's ``make install`` points the UNSUFFIXED
frontends — ``iptables``, ``iptables-restore``, ``iptables-save`` and their IPv6
twins — at the LEGACY multi-call binary, and the recipe never repointed them. So
every program that shells out to ``iptables`` fails at the first call with
``modprobe: FATAL: Module ip_tables not found``.

Measured on four installed machines: the mesh networking daemon's own health
surface reports the exact failing command (``iptables -t filter -N ts-input``)
and none of the chains it needs exist. Container and virtual-machine tooling
call the same binary. Two sibling frontends in the same package,
``arptables`` and ``ebtables``, already resolve to the newer backend — which is
what makes the other six a symlink nobody updated rather than a decision.

WHY THIS TEST RUNS THE RECIPE'S OWN INSTALL STEP. A test that only read the
recipe's text would pass on a comment. This one SOURCES the shipped ``build.sh``
and CALLS ``do_install`` against a throwaway staging directory, with ``make``
replaced by a stub that lays out exactly what upstream's install produces — the
layout measured from a real build of this recipe. The relink under test is the
shipped code path, executed. The stub is the compiler's absence, not the
recipe's.

WHAT IS NOT ASSERTED HERE. That the built image carries the corrected links.
That belongs to the installed-system gate, on a machine installed from an image
built with this recipe.

Nothing here writes outside a temporary directory, reads the network, or needs
privilege.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPE_DIR = REPO_ROOT / "packages" / "desktop" / "iptables"
BUILD_SH = RECIPE_DIR / "build.sh"

NFT_BACKEND = "xtables-nft-multi"
LEGACY_BACKEND = "xtables-legacy-multi"

# The frontends a caller reaches for by their plain names. These are the ones
# every other program on the system shells out to.
DEFAULT_FRONTENDS = ["iptables", "iptables-restore", "iptables-save",
                     "ip6tables", "ip6tables-restore", "ip6tables-save"]
# Frontends whose names SAY legacy. They stay legacy: removing a capability the
# user explicitly asked for by name is a different decision from fixing a
# default, and this branch is not making it.
EXPLICIT_LEGACY = ["iptables-legacy", "iptables-legacy-restore",
                   "iptables-legacy-save", "ip6tables-legacy",
                   "ip6tables-legacy-restore", "ip6tables-legacy-save"]
# Already correct before this change; they must stay correct.
ALREADY_NFT = ["arptables", "ebtables", "iptables-nft", "ip6tables-nft",
               "xtables-monitor"]

# Exactly what a real `make install` of this recipe leaves behind, measured from
# a build of iptables 1.8.12 with the recipe's own configure flags.
UPSTREAM_LAYOUT = {
    "iptables": LEGACY_BACKEND, "iptables-restore": LEGACY_BACKEND,
    "iptables-save": LEGACY_BACKEND, "iptables-legacy": LEGACY_BACKEND,
    "iptables-legacy-restore": LEGACY_BACKEND,
    "iptables-legacy-save": LEGACY_BACKEND,
    "ip6tables": LEGACY_BACKEND, "ip6tables-restore": LEGACY_BACKEND,
    "ip6tables-save": LEGACY_BACKEND, "ip6tables-legacy": LEGACY_BACKEND,
    "ip6tables-legacy-restore": LEGACY_BACKEND,
    "ip6tables-legacy-save": LEGACY_BACKEND,
    "iptables-nft": NFT_BACKEND, "iptables-nft-restore": NFT_BACKEND,
    "iptables-nft-save": NFT_BACKEND, "iptables-translate": NFT_BACKEND,
    "iptables-restore-translate": NFT_BACKEND,
    "ip6tables-nft": NFT_BACKEND, "ip6tables-nft-restore": NFT_BACKEND,
    "ip6tables-nft-save": NFT_BACKEND, "ip6tables-translate": NFT_BACKEND,
    "ip6tables-restore-translate": NFT_BACKEND,
    "arptables": NFT_BACKEND, "arptables-restore": NFT_BACKEND,
    "arptables-save": NFT_BACKEND, "arptables-nft": NFT_BACKEND,
    "ebtables": NFT_BACKEND, "ebtables-restore": NFT_BACKEND,
    "ebtables-save": NFT_BACKEND, "ebtables-nft": NFT_BACKEND,
    "xtables-monitor": NFT_BACKEND,
}


def _make_stub(bin_dir: Path) -> None:
    """A ``make`` that lays out what upstream's install produces, and nothing else."""
    layout = " ".join(f'"{name}:{target}"'
                      for name, target in sorted(UPSTREAM_LAYOUT.items()))
    script = f'''#!/bin/bash
# Stands in for the compiler, not for the recipe. It reproduces exactly the
# staging layout a real `make DESTDIR=... install` of iptables 1.8.12 leaves.
set -e
dest=""
for arg in "$@"; do
    case "$arg" in DESTDIR=*) dest="${{arg#DESTDIR=}}" ;; esac
done
[ -n "$dest" ] || exit 0
case " $* " in *" install "*) ;; *) exit 0 ;; esac
mkdir -p "$dest/usr/sbin"
for real in {NFT_BACKEND} {LEGACY_BACKEND}; do
    printf '#!/bin/true\\n' > "$dest/usr/sbin/$real"
    chmod 755 "$dest/usr/sbin/$real"
done
for pair in {layout}; do
    name="${{pair%%:*}}"; target="${{pair#*:}}"
    ln -sfn "$target" "$dest/usr/sbin/$name"
done
'''
    path = bin_dir / "make"
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def staged(tmp_path):
    """Run the SHIPPED ``do_install`` against a throwaway staging directory."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub(bin_dir)
    dest = tmp_path / "destdir"
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["DESTDIR"] = str(dest)
    env["IGOS_JOBS"] = "1"
    proc = subprocess.run(
        ["bash", "-c", f'set -e; source "{BUILD_SH}"; do_install'],
        env=env, capture_output=True, text=True, timeout=120, cwd=str(tmp_path))
    return dest, proc


def _target(dest: Path, name: str) -> str:
    link = dest / "usr" / "sbin" / name
    if not link.is_symlink():
        return "<not a symlink>" if link.exists() else "<absent>"
    return os.readlink(link)


def test_the_recipes_install_step_succeeds(staged):
    dest, proc = staged
    assert proc.returncode == 0, (
        f"the recipe's do_install exited {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}")


def test_the_default_frontends_point_at_the_nft_backend(staged):
    dest, proc = staged
    wrong = {name: _target(dest, name) for name in DEFAULT_FRONTENDS
             if _target(dest, name) != NFT_BACKEND}
    assert not wrong, (
        "\nThe frontends every other program shells out to do not resolve to the "
        "backend this kernel supports:\n"
        + "\n".join(f"  /usr/sbin/{n:24} -> {t}" for n, t in sorted(wrong.items()))
        + f"\nThe kernel fragment turns the legacy xtables interface off "
          f"({LEGACY_BACKEND} has no kernel modules to talk to), so each of these "
          "fails at the first call with 'Module ip_tables not found'.\n"
          f"stderr from the install step: {proc.stderr}")


def test_the_explicitly_named_legacy_frontends_are_left_alone(staged):
    """Fixing a default is not the same decision as removing a named capability."""
    dest, _proc = staged
    moved = {name: _target(dest, name) for name in EXPLICIT_LEGACY
             if _target(dest, name) != LEGACY_BACKEND}
    assert not moved, (
        "\nFrontends whose names say legacy were repointed:\n"
        + "\n".join(f"  /usr/sbin/{n:28} -> {t}" for n, t in sorted(moved.items()))
        + "\nA user who types iptables-legacy asked for that binary by name.")


def test_the_frontends_that_were_already_correct_stay_correct(staged):
    dest, _proc = staged
    wrong = {name: _target(dest, name) for name in ALREADY_NFT
             if _target(dest, name) != NFT_BACKEND}
    assert not wrong, (
        "\nFrontends that already resolved to the newer backend no longer do:\n"
        + "\n".join(f"  /usr/sbin/{n:24} -> {t}" for n, t in sorted(wrong.items())))


def test_every_default_frontend_agrees_with_every_other(staged):
    """The four plain names must name one backend; a split is the defect's shape."""
    dest, _proc = staged
    plain = ["iptables", "ip6tables", "arptables", "ebtables"]
    targets = {name: _target(dest, name) for name in plain}
    assert len(set(targets.values())) == 1, (
        "\nThe plain-named frontends do not agree on a backend:\n"
        + "\n".join(f"  /usr/sbin/{n:12} -> {t}" for n, t in targets.items()))


def test_the_recipe_states_the_backend_it_ships_without_claiming_more(staged):
    """The recipe's own text must not claim the legacy binary is absent.

    It said the legacy backend "is NOT built because the kernel disables
    CONFIG_NETFILTER_XTABLES_LEGACY". A real build of this recipe produces
    /usr/sbin/xtables-legacy-multi and installs it, and the installed image
    carries it. A claim a build contradicts is worse than no claim, because it
    is the reason nobody looked at the symlinks for a release.
    """
    import re
    # The claim is wrapped across comment lines in the file, so the comparison
    # is made on the flattened text. Checking the raw string would pass on the
    # wrap and certify nothing.
    text = (RECIPE_DIR / "package.yml").read_text(encoding="utf-8")
    flat = re.sub(r"[\s#]+", " ", text)
    assert "backend is NOT built" not in flat, (
        "packages/desktop/iptables/package.yml still says the legacy backend is "
        "not built. A real build of this recipe produces /usr/sbin/"
        f"{LEGACY_BACKEND} and installs it, and the released image carries it.")


# ── negative control ─────────────────────────────────────────────────────────

def test_control_the_check_detects_the_upstream_default(tmp_path):
    """Control: staged WITHOUT the recipe's relink, the check must fail.

    Proves the assertions above are reading the staging directory rather than
    passing on an empty one — the layout here is upstream's untouched output.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub(bin_dir)
    dest = tmp_path / "untouched"
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    subprocess.run(["make", f"DESTDIR={dest}", "install"], env=env, check=True,
                   timeout=60, cwd=str(tmp_path))
    assert _target(dest, "iptables") == LEGACY_BACKEND
    assert _target(dest, "arptables") == NFT_BACKEND
    wrong = [n for n in DEFAULT_FRONTENDS if _target(dest, n) != NFT_BACKEND]
    assert len(wrong) == len(DEFAULT_FRONTENDS), (
        "the control layout is supposed to have every default frontend on the "
        f"legacy backend; these were already correct: "
        f"{sorted(set(DEFAULT_FRONTENDS) - set(wrong))}")


def test_control_the_assertions_read_the_staging_directory_not_the_live_system(staged):
    """Control: what is being inspected is the temporary tree, not /usr/sbin.

    Without this, every assertion above would pass on a machine whose real
    /usr/sbin happened to be correct, while saying nothing about the recipe.
    """
    dest, _proc = staged
    assert not str(dest).startswith("/usr"), "the fixture is staging into /usr"
    assert (dest / "usr" / "sbin" / "iptables").is_symlink(), (
        "the staging directory holds no iptables symlink, so the assertions "
        "above were reading nothing")
    live = Path("/usr/sbin/iptables")
    if live.is_symlink():
        # The live link is allowed to be anything; the point is only that the
        # inspected path is NOT it.
        assert (dest / "usr" / "sbin" / "iptables").resolve() != live.resolve()
