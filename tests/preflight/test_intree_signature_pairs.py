# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every in-tree <artifact> + <artifact>.asc pair verifies against the keyring.

The defect class this pins, measured on this tree before writing it: a change
set swapped the tier-2 model pin in intergen/data/models-manifest.json without
re-signing it, and the tree carried a signature that verified BAD through the
branch's own gates, a composed landing, and one independent review — corrected
only because a signing ceremony happened to be scheduled. Nothing verified an
in-tree signed artifact against its sidecar; the runtime verifiers check
installed roots, not the repository.

Discovery rule: a ``*.asc`` file is a detached-signature sidecar iff the
sibling path (the name minus ``.asc``) exists in the tree. Vendor keyrings
(``*-keyring.asc``) and the exported public key (``docs/signing-key.asc``)
have no sibling and are correctly out of scope.

The verifying key is the tree's own pinned keyring
(packages/core/intergenos-keyring/trusted.gpg) via ``gpgv`` — the same
fail-closed primitive the installed system uses.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_KEYRING = _REPO / "packages" / "core" / "intergenos-keyring" / "trusted.gpg"

# Pairs that must exist today; discovery finding fewer than these is itself a
# failure, so an accidentally-emptied discovery can never pass vacuously.
_KNOWN_PAIRS = {
    "intergen/data/models-manifest.json",
    "intergen/data/destructive-policy-manifest.json",
    "packages/desktop/intergenos-wiki/pages-manifest.json",
}


def _discover_pairs() -> list[tuple[Path, Path]]:
    pairs = []
    for asc in _REPO.rglob("*.asc"):
        if ".git" in asc.parts:
            continue
        artifact = asc.with_suffix("")  # strip the trailing .asc
        if artifact.exists() and artifact.is_file():
            pairs.append((artifact, asc))
    return sorted(pairs)


def _gpgv(artifact: Path, asc: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gpgv", "--keyring", str(_KEYRING), str(asc), str(artifact)],
        capture_output=True, text=True)


class InTreeSignaturePairTests(unittest.TestCase):

    def test_discovery_finds_the_known_pairs(self):
        found = {str(a.relative_to(_REPO)) for a, _ in _discover_pairs()}
        missing = _KNOWN_PAIRS - found
        self.assertFalse(
            missing,
            f"signature-pair discovery no longer finds {sorted(missing)} — "
            f"either the pair moved (update _KNOWN_PAIRS) or discovery broke")

    def test_every_pair_verifies_against_the_pinned_keyring(self):
        self.assertTrue(_KEYRING.is_file(), f"keyring missing: {_KEYRING}")
        for artifact, asc in _discover_pairs():
            with self.subTest(artifact=str(artifact.relative_to(_REPO))):
                r = _gpgv(artifact, asc)
                self.assertEqual(
                    0, r.returncode,
                    f"{artifact.relative_to(_REPO)} does not verify against "
                    f"its .asc — the artifact changed without a re-sign, or "
                    f"the signature is foreign:\n{r.stderr}")

    def test_the_instrument_can_fail(self):
        """Positive control: a tampered artifact must verify BAD."""
        import tempfile
        artifact, asc = _discover_pairs()[0]
        data = bytearray(artifact.read_bytes())
        data[len(data) // 2] ^= 0xFF
        with tempfile.NamedTemporaryFile(suffix=artifact.suffix) as tampered:
            tampered.write(bytes(data))
            tampered.flush()
            r = _gpgv(Path(tampered.name), asc)
            self.assertNotEqual(
                0, r.returncode,
                "gpgv accepted a tampered artifact — the instrument cannot "
                "be trusted to fail")


if __name__ == "__main__":
    unittest.main()
