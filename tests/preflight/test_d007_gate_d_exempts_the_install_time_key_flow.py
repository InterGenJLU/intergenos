"""Gate D of the D-007 gate exempts the installer's key parser by its exact path.

Gate D of scripts/check-d007-compliance.sh refuses any reference to
authorized_keys under packages/, scripts/, installer/ and config/: D-007 forbids
a key baked into a shipped artifact. D-019 permits the keys a person pastes
during the install, and the gate exempts the installer files of that flow by
exact path. installer/backend/sshkeys.py joined the flow as its key parser and
renderer (it reads the pasted keys and renders the lines
installer/backend/users.py writes into the TARGET root's authorized_keys) but
was not in the exemption list, so the gate refused the tree and the image phase
would halt on it.

These tests copy the real gate, byte for byte, into a synthetic tree, as the
Gate F tests beside them do, and read Gate D's own section of its output: the
parser's path passes, the real parser passes, and a reference anywhere else,
including a file whose name only begins like the parser's, is still refused.

Nothing here reads the network, needs privilege, or writes inside the repository.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GATE = REPO / "scripts" / "check-d007-compliance.sh"
PARSER = REPO / "installer" / "backend" / "sshkeys.py"

REFERENCE_LINE = 'AUTH_KEYS = ".ssh/authorized_keys"\n'
PASS_LINE = "PASS — no pre-installed authorized_keys references"
VIOLATION_LINE = "tree contains references to authorized_keys"


def _synthetic_tree(tmp_path: Path) -> Path:
    """A tree shaped like the repository, holding the real gate script."""
    root = tmp_path / "tree"
    for sub in ("scripts", "packages", "installer", "config"):
        (root / sub).mkdir(parents=True)
    shutil.copy2(GATE, root / "scripts" / GATE.name)
    return root


def _gate_d_section(root: Path) -> str:
    """Gate D's own lines of the gate's output, from its header to the next."""
    result = subprocess.run(
        ["bash", str(root / "scripts" / GATE.name)],
        capture_output=True, text=True, timeout=120,
    )
    out = result.stdout + result.stderr
    start = out.index("Gate D")
    end = out.index("=== Gate E", start)
    return out[start:end]


def _plant(root: Path, where: str, text: str = REFERENCE_LINE) -> None:
    planted = root / where
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(text)


def test_gate_script_is_copied_intact(tmp_path: Path) -> None:
    # The premise every other test here rests on: the thing being exercised is
    # the shipped gate, not an edited stand-in.
    root = _synthetic_tree(tmp_path)
    assert (root / "scripts" / GATE.name).read_bytes() == GATE.read_bytes()


def test_the_key_parser_is_exempt_by_its_path(tmp_path: Path) -> None:
    """A reference in installer/backend/sshkeys.py is the install-time flow."""
    root = _synthetic_tree(tmp_path)
    _plant(root, "installer/backend/sshkeys.py")
    section = _gate_d_section(root)
    assert PASS_LINE in section, f"Gate D refused the key parser's path:\n{section}"
    assert VIOLATION_LINE not in section


def test_the_real_key_parser_passes(tmp_path: Path) -> None:
    """The parser as the tree carries it, every one of its references."""
    root = _synthetic_tree(tmp_path)
    target = root / "installer" / "backend" / "sshkeys.py"
    target.parent.mkdir(parents=True)
    shutil.copy2(PARSER, target)
    assert "authorized_keys" in target.read_text()
    section = _gate_d_section(root)
    assert PASS_LINE in section, f"Gate D refused the real key parser:\n{section}"
    assert VIOLATION_LINE not in section


@pytest.mark.parametrize("where", [
    "installer/backend/sshkeys_extra.py",
    "installer/backend/keys.py",
    "installer/frontend/gui/screens/confirm.py",
    "scripts/setup-ssh.sh",
    "packages/extra/kiosk/files/etc/skel/setup-keys.sh",
    "config/ssh-keys.conf",
])
def test_a_reference_anywhere_else_is_refused(tmp_path: Path, where: str) -> None:
    """The exemption is one exact path, not a prefix and not a directory."""
    root = _synthetic_tree(tmp_path)
    _plant(root, where)
    section = _gate_d_section(root)
    assert VIOLATION_LINE in section, (
        f"Gate D did not refuse the reference planted at {where}:\n{section}"
    )
    assert PASS_LINE not in section
    # The reader is told WHERE.
    assert where in section


def test_beside_the_parser_only_the_other_reference_is_named(tmp_path: Path) -> None:
    """With the parser present, a reference beside it is refused alone."""
    root = _synthetic_tree(tmp_path)
    _plant(root, "installer/backend/sshkeys.py")
    _plant(root, "installer/backend/sshkeys_extra.py")
    section = _gate_d_section(root)
    assert VIOLATION_LINE in section
    assert "installer/backend/sshkeys_extra.py:" in section
    assert "installer/backend/sshkeys.py:" not in section, (
        f"Gate D named the exempt key parser among its violations:\n{section}"
    )


def test_the_exemption_names_the_exact_path() -> None:
    """Pin the token. The behavioural tests above would also catch its loss,
    but naming it here says what was dropped and why."""
    source = GATE.read_text()
    assert "|installer/backend/sshkeys\\.py):" in source, (
        "check-d007-compliance.sh Gate D lost the exact-path exemption of "
        "installer/backend/sshkeys.py, the install-time key parser; without it "
        "the gate refuses the tree and the image phase halts"
    )
