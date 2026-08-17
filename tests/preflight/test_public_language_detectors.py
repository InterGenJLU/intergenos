"""The public-content scanner can see the three language classes it missed.

scripts/check-public-content.py passed CLEAN on a tree that carried 192 lines
attributing decisions to a person or project role, 28 citations of a repository
that does not exist publicly, and 39 development-host shorthands. None of its
tiers described those shapes, so enforcement for that half of the
public-language rule was human attention — and human attention does not scale
to 1,150 recipes. That is why the count reached 192 before anyone counted.

Three tiers were added, and the order mattered: each was proven against a
fixture, then the tree was swept, and only then was the tier armed. A
fail-closed gate armed over a tree still carrying hits blocks every push on day
one, which teaches people to route around the gate rather than fix the content.

These tests drive the REAL scanner over fixture trees via its own --dir entry
point. They assert both directions for every tier: the violating shape is
refused, and the legitimate neighbouring shape is NOT — a detector that fires
on ordinary technical writing gets exempted into uselessness within a week, so
the false-positive direction is as load-bearing as the true-positive one.

Nothing here reads the network, needs privilege, or writes inside the tree.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCANNER = REPO / "scripts" / "check-public-content.py"


def _scan(tmp_path: Path, filename: str, content: str) -> subprocess.CompletedProcess:
    tree = tmp_path / "tree"
    target = tree / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return subprocess.run(
        ["python3", str(SCANNER), "--dir", str(tree)],
        capture_output=True, text=True, timeout=180,
    )


def _blocked(result: subprocess.CompletedProcess, category: str) -> bool:
    return f"[{category}]" in result.stdout + result.stderr


# --------------------------------------------------------------------------
# 1. Attribution of a decision to a person or role.
# --------------------------------------------------------------------------

# The fixture strings are ASSEMBLED rather than written out. They have to be
# the real violating shapes for the tests to mean anything, and writing those
# shapes as literals would put banned tokens into the public tree — the very
# thing the detector under test exists to stop. Joining the fragments at run
# time keeps the fixture exact and the file clean.
_ROLE = "oper" + "ator"
_OWNER = "own" + "er"
_PRIVATE_REPO = "intergenos-" + "private"

CREDITED = [
    f"# {_ROLE}-ratified 2026-05-21: the default wallpaper ships here.\n",
    f"# {_OWNER.capitalize()}-approved 2026-05-11 — completes the bootstrap chain.\n",
    f"# The {_ROLE} ruled that this stays disabled.\n",
    f"# Kept per the {_ROLE}'s ruling of 2026-07-08.\n",
    f"# Enabled per {_ROLE} directive.\n",
    f"# {_ROLE}-directed gate wave, 2026-07-12.\n",
    "# coordi" + "nator-approved shape.\n",
    f"# {_ROLE}-greenlit Q8 design — never auto-upgrades.\n",
]


@pytest.mark.parametrize("line", CREDITED)
def test_decision_credited_to_a_person_is_refused(tmp_path: Path, line: str) -> None:
    result = _scan(tmp_path, "packages/core/demo/build.sh", line)
    assert _blocked(result, "PERSONA-ATTRIBUTION"), (
        f"not detected: {line!r}\n{result.stdout}")
    assert result.returncode == 1


# A human PERFORMING A STEP is ordinary technical writing and must stay legal.
PERFORMING = [
    f"# The {_ROLE} inserts the smartcard when prompted.\n",
    f"# Timestamps stay: the {_ROLE} reads them during the ceremony.\n",
    f"# The {_OWNER} of the machine chooses a passphrase here.\n",
    f"# Wait for the {_ROLE} to confirm the fingerprint on the card.\n",
    f"# This runs as the {_ROLE}, never as root.\n",
]


@pytest.mark.parametrize("line", PERFORMING)
def test_person_performing_a_step_is_not_refused(tmp_path: Path, line: str) -> None:
    result = _scan(tmp_path, "packages/core/demo/build.sh", line)
    assert not _blocked(result, "PERSONA-ATTRIBUTION"), (
        f"false positive on ordinary technical writing: {line!r}\n{result.stdout}")


def test_neutral_decision_record_is_the_accepted_form(tmp_path: Path) -> None:
    """The replacement the rule asks for must itself pass."""
    result = _scan(
        tmp_path, "packages/core/demo/build.sh",
        "# Decided 2026-05-21: the default wallpaper ships here because the\n"
        "# alternative needed a codec that is not in the base set.\n")
    assert result.returncode == 0, result.stdout


# --------------------------------------------------------------------------
# 2. Citations of the repository that is not published.
# --------------------------------------------------------------------------

def test_private_repository_citation_is_refused(tmp_path: Path) -> None:
    result = _scan(
        tmp_path, "packages/desktop/demo/package.yml",
        "# Research dossier:\n"
        f"#   {_PRIVATE_REPO}:research/2026-05-28-demo/30-notes.md\n")
    assert _blocked(result, "PRIVATE-REPO-PATH"), result.stdout
    assert result.returncode == 1


def test_functional_locator_paths_stay_legal(tmp_path: Path) -> None:
    """The audit tooling must LOCATE that repository to do its work. Those
    paths are functional, not citations, and are exempt by path."""
    for rel in ("scripts/anchor-tracker.sh", "scripts/audit-package.py",
                ".githooks/pre-push"):
        result = _scan(
            tmp_path / rel.replace("/", "_"), rel,
            'PRIVATE_REPO="${INTERGENOS_PRIVATE_REPO:-../' + _PRIVATE_REPO + '}"\n')
        assert not _blocked(result, "PRIVATE-REPO-PATH"), (
            f"{rel} must stay able to find the repository it audits\n{result.stdout}")


# --------------------------------------------------------------------------
# 3. Development-host shorthand.
# --------------------------------------------------------------------------

SHORTHAND = [
    "# machine (observed on .241, 2026-06-10 — see G3-12).\n",
    "# The CPU 2B .192 box: report-only, no false-fail.\n",
    "# Grounded on the .218 trace.\n",
    '"""signature seen on .241."""\n',   # sentence-final period, missed once
]


@pytest.mark.parametrize("line", SHORTHAND)
def test_host_shorthand_is_refused(tmp_path: Path, line: str) -> None:
    result = _scan(tmp_path, "intergen/demo.py", line)
    assert _blocked(result, "HOST-SHORTHAND"), (
        f"not detected: {line!r}\n{result.stdout}")


NOT_SHORTHAND = [
    "# Requires LFS 13.0 host requirements to be met.\n",
    "# Examined on Ubuntu 25.04 host, BLFS 13.0 documentation.\n",
    "    path = 'M 12 .5 L 13 .75 Z'\n",       # SVG coordinates
    "    scale = .5\n",                         # a bare float
    "# Compared release 0.2.0 against the 3.14 interpreter.\n",
    "# The build VM is 192.168.122.249 (libvirt NAT).\n",
]


@pytest.mark.parametrize("line", NOT_SHORTHAND)
def test_numbers_that_are_not_hosts_are_not_refused(tmp_path: Path, line: str) -> None:
    result = _scan(tmp_path, "intergen/demo.py", line)
    assert not _blocked(result, "HOST-SHORTHAND"), (
        f"false positive: {line!r}\n{result.stdout}")


# --------------------------------------------------------------------------
# The tiers are actually wired in, not merely defined.
# --------------------------------------------------------------------------

def test_all_three_tiers_are_armed() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("_cpc", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    armed = {cat for cat, _ in mod.BLOCK_PATTERNS}
    for tier in ("PERSONA-ATTRIBUTION", "PRIVATE-REPO-PATH", "HOST-SHORTHAND"):
        assert tier in armed, f"{tier} is defined but not in BLOCK_PATTERNS"


def test_every_exempt_path_is_a_real_file() -> None:
    """An exemption for a path that no longer exists is a hole nobody can see:
    it suppresses nothing today and silently covers whatever takes that name."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_cpc", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("PERSONA_ATTRIBUTION_EXEMPT_PATHS",
                 "PRIVATE_REPO_PATH_EXEMPT_PATHS"):
        for rel in getattr(mod, name):
            assert (REPO / rel).exists(), f"{name} lists a path that does not exist: {rel}"
