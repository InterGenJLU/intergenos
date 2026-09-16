"""A recipe cannot give a program privilege without the declared list saying so.

The setuid/setgid gate's chroot arms fire at the seal — the last moment before
an image is written, and a long way from the commit that introduced the
privilege. This arm asks the same question of the recipe tree at authoring
time, off the SAME declared list (config/setuid-inventory.txt), so a new
privileged program and its declaration land in one commit or not at all.

Every case below runs the real script. The passing case is worth little on its
own: a checker that cannot fail would also print PASS, so the undeclared-program
case proves the instrument detects a true positive, and the empty case proves it
refuses to certify a population it never saw.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "check-setuid-inventory.py"
INVENTORY = REPO / "config" / "setuid-inventory.txt"


def run(packages_dir, inventory=INVENTORY):
    return subprocess.run(
        [sys.executable, str(GATE), "--recipes", str(packages_dir),
         "--inventory", str(inventory)],
        capture_output=True, text=True)


def write_recipe(pkg_dir: Path, name: str, tier: str, body: str, extra=""):
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.yml").write_text(
        f'name: {name}\n'
        f'version: "1.0"\n'
        f'release: 1\n'
        f'description: fixture\n'
        f'license: GPL-3.0-or-later\n'
        f'tier: {tier}\n'
        f'build_style: custom\n'
        f'install_func: do_install\n'
        f'source: []\n'
        f'{extra}'
        f'verify_paths:\n'
        f'  - /usr/bin/{name}\n')
    (pkg_dir / "build.sh").write_text(
        "#!/bin/bash\ndo_install() {\n    set -e\n" + body + "\n}\n")


def test_the_tree_declares_every_privileged_mode_its_shipped_recipes_set():
    r = run(REPO / "packages")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def test_an_undeclared_privileged_program_is_refused(tmp_path):
    """The true-positive control: the instrument must see what it exists for."""
    write_recipe(tmp_path / "base" / "fixture-suid", "fixture-suid", "base",
                 '    chmod 4755 "${DESTDIR}/usr/bin/fixture-suid"')
    r = run(tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "/usr/bin/fixture-suid" in r.stdout
    assert "declares no such path" in r.stdout


@pytest.mark.parametrize("line,shown", [
    ('    chmod 6755 "${DESTDIR}/usr/sbin/fixture-suid"', "/usr/sbin/fixture-suid"),
    ('    chmod 2755 "${DESTDIR}/usr/bin/fixture-suid"', "/usr/bin/fixture-suid"),
    ('    chmod u+s "${DESTDIR}/usr/bin/fixture-suid"', "/usr/bin/fixture-suid"),
    ('    chmod g+s /usr/bin/fixture-suid', "/usr/bin/fixture-suid"),
    ('    install -m 4755 built/prog "${DESTDIR}/usr/bin/fixture-suid"',
     "/usr/bin/fixture-suid"),
])
def test_every_shape_that_grants_privilege_is_seen(tmp_path, line, shown):
    """Octal, symbolic, staged and post-install forms all grant privilege."""
    write_recipe(tmp_path / "base" / "fixture-suid", "fixture-suid", "base", line)
    r = run(tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert shown in r.stdout


def test_an_ordinary_mode_is_not_treated_as_privilege(tmp_path):
    """0755 and 0644 grant nothing; a checker that flags them cries wolf."""
    write_recipe(tmp_path / "base" / "fixture-plain", "fixture-plain", "base",
                 '    chmod 0755 "${DESTDIR}/usr/bin/fixture-plain"\n'
                 '    chmod 0644 "${DESTDIR}/etc/fixture.conf"')
    r = run(tmp_path)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "EMPTY AUDIT" in r.stdout


def test_a_mirror_only_recipe_is_reported_and_does_not_fail_the_shipped_verdict(tmp_path):
    """Split the verdict, never narrow it: the reader still sees the path."""
    write_recipe(tmp_path / "extra" / "fixture-mirror", "fixture-mirror", "extra",
                 '    chmod 4755 "${DESTDIR}/usr/bin/fixture-mirror"')
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "mirror-only privileged modes" in r.stdout
    assert "/usr/bin/fixture-mirror" in r.stdout


def test_a_declared_program_passes(tmp_path):
    """The other half of the control: a declared path must NOT be flagged."""
    inv = tmp_path / "inventory.txt"
    inv.write_text("/usr/bin/fixture-suid 4755 root root\n")
    write_recipe(tmp_path / "base" / "fixture-suid", "fixture-suid", "base",
                 '    chmod 4755 "${DESTDIR}/usr/bin/fixture-suid"')
    r = run(tmp_path, inventory=inv)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def test_a_version_suffixed_program_matches_the_list_glob(tmp_path):
    """A recipe's ${version} and the list's glob must meet, not miss."""
    inv = tmp_path / "inventory.txt"
    inv.write_text("/usr/bin/fixture-suid-* 4755 root root\n")
    write_recipe(tmp_path / "base" / "fixture-suid", "fixture-suid", "base",
                 '    chmod 4755 "${DESTDIR}/usr/bin/fixture-suid-${version}"')
    r = run(tmp_path, inventory=inv)
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_empty_recipe_tree_refuses_rather_than_certifying(tmp_path):
    """A gate that cannot see must halt: exit 3, not a green PASS."""
    (tmp_path / "base").mkdir()
    r = run(tmp_path)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "EMPTY AUDIT" in r.stdout


def test_naming_both_modes_or_neither_is_a_usage_error():
    for args in ([], ["--chroot", "/nonexistent", "--recipes", str(REPO / "packages")]):
        r = subprocess.run([sys.executable, str(GATE)] + args,
                           capture_output=True, text=True)
        assert r.returncode == 2, r.stdout + r.stderr
