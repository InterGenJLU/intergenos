"""The D-007 gate can see a tty root-autologin at all.

Gate F of scripts/check-d007-compliance.sh searched for a tty configured to log
in as root. Its pattern begins with two dashes:

    grep -rn -E '--autologin[[:space:]]+root\\b' packages/ scripts/ ...

so grep read the pattern as an OPTION, failed with "unrecognized option", and
exited before opening a single file. The `2>/dev/null` discarded that error, the
hit variable came back empty, and the gate announced PASS. Measured on both
matchers available when this was found — GNU grep 3.12 and ugrep 7.5.0 — each
exiting 2. One of the six things D-007 exists to forbid was undetectable here for
as long as the check existed, and every green run said otherwise.

The fix is the `--` end-of-options separator. These tests plant the violation and
require the gate to refuse it, which is the only way a gate of this shape can be
shown to work: a passing run over a clean tree is exactly what the broken version
produced.

The gate resolves its own repository root and changes into it, so it cannot be
pointed at a synthetic tree by working directory. The real script is COPIED,
byte for byte, into a synthetic tree instead — what runs here is the same bytes
the build runs, never a reimplementation of its logic.

Nothing here reads the network, needs privilege, or writes inside the repository.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GATE = REPO / "scripts" / "check-d007-compliance.sh"

# The exact line an installed system would carry. A systemd getty override with
# this content is the ordinary way the class appears in the wild.
AUTOLOGIN_LINE = "ExecStart=-/sbin/agetty --autologin root --noclear %I $TERM\n"

PASS_LINE = "PASS — no tty root-autologin configuration"
VIOLATION_LINE = "tty root-autologin configuration in tree"


def _synthetic_tree(tmp_path: Path) -> Path:
    """A tree shaped like the repository, holding the real gate script."""
    root = tmp_path / "tree"
    for sub in ("scripts", "packages", "installer", "config"):
        (root / sub).mkdir(parents=True)
    shutil.copy2(GATE, root / "scripts" / GATE.name)
    return root


def _run_gate(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(root / "scripts" / GATE.name)],
        capture_output=True, text=True, timeout=120,
    )


def test_gate_script_is_copied_intact(tmp_path: Path) -> None:
    # The premise every other test here rests on: the thing being exercised is
    # the shipped gate, not an edited stand-in.
    root = _synthetic_tree(tmp_path)
    assert (root / "scripts" / GATE.name).read_bytes() == GATE.read_bytes()


@pytest.mark.parametrize("where", [
    "config/getty-override.conf",
    "packages/extra/kiosk/files/etc/systemd/system/getty.service.d/autologin.conf",
    "installer/init/getty-autologin.conf",
    "scripts/setup-console.sh",
])
def test_planted_autologin_is_refused(tmp_path: Path, where: str) -> None:
    """A root autologin in any searched directory must fail the gate."""
    root = _synthetic_tree(tmp_path)
    planted = root / where
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(AUTOLOGIN_LINE)

    result = _run_gate(root)
    out = result.stdout + result.stderr

    assert VIOLATION_LINE in out, (
        f"gate did not report the autologin planted at {where}; "
        f"Gate F output was:\n{out}"
    )
    assert PASS_LINE not in out, (
        f"gate announced Gate F PASS while {where} configured a root autologin"
    )
    # The reader is told WHERE, not merely that something is wrong.
    assert where in out or Path(where).name in out


def test_clean_tree_still_passes_gate_f(tmp_path: Path) -> None:
    """The other direction, so the fix is not simply 'always fail'."""
    root = _synthetic_tree(tmp_path)
    (root / "config" / "getty-override.conf").write_text(
        "ExecStart=-/sbin/agetty --noclear %I $TERM\n"
    )
    out = _run_gate(root).stdout + _run_gate(root).stderr
    assert PASS_LINE in out
    assert VIOLATION_LINE not in out


def test_autologin_as_another_user_is_not_the_violation(tmp_path: Path) -> None:
    """D-007 forbids autologin as ROOT. The live ISO's own account is allowed,
    so a pattern that fired on any autologin at all would be wrong."""
    root = _synthetic_tree(tmp_path)
    (root / "config" / "getty-override.conf").write_text(
        "ExecStart=-/sbin/agetty --autologin intergenos --noclear %I $TERM\n"
    )
    out = _run_gate(root).stdout
    assert PASS_LINE in out
    assert VIOLATION_LINE not in out


def test_pattern_is_not_read_as_an_option(tmp_path: Path) -> None:
    """The defect at its own level, independent of the gate's plumbing.

    Run the gate's own search form directly and require it to SEARCH — an
    invocation that dies on option parsing exits 2 and prints nothing, which is
    indistinguishable from a clean tree once stderr is discarded.
    """
    hay = tmp_path / "hay.conf"
    hay.write_text(AUTOLOGIN_LINE)
    result = subprocess.run(
        ["grep", "-rn", "-E", "--", r"--autologin[[:space:]]+root\b", str(hay)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"the search form did not run: rc={result.returncode} "
        f"stderr={result.stderr!r}"
    )
    assert "autologin root" in result.stdout


def test_gate_source_carries_the_end_of_options_separator() -> None:
    """Pin the token itself. The behavioural tests above would also catch its
    removal, but naming it here means a future edit that drops it is refused
    with a message that says what was dropped and why."""
    source = GATE.read_text()
    assert "-E -- '--autologin" in source, (
        "check-d007-compliance.sh Gate F lost its `--` end-of-options "
        "separator; without it grep parses the pattern as an option, exits "
        "before reading any file, and the gate reports PASS on a violating tree"
    )
