# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""What the FDE comments say the code does is what the code does.

WHY THIS EXISTS. Two comments about the same mechanism were wrong, in
opposite directions, and a reader following either one would look in the wrong
place when an encrypted install failed to unlock.

  1. installer/backend/bootloader.py, stage_uki_prereqs, says of the 52-byte
     placeholder it writes to /boot/initramfs.img:

         "LUKS installs replace this stub with the FDE initramfs at the
          kernel hook level"

     They do not. The kernel post-install hook regenerates the unlock
     initramfs at /usr/lib/intergen/fde-initramfs.cpio.gz and passes THAT to
     ukify, so the unlock code travels inside the signed unified kernel image;
     it never touches /boot/initramfs.img, and on an encrypted install that
     file stays the placeholder. Measured on the R001.1 encrypted install,
     2026-08-24: /etc/crypttab carries an active entry, /boot/initramfs.img is
     50 bytes, and the cpio beside it is 9,924,799 bytes. The hook is right
     and the comment is wrong.

  2. packages/core/linux-kernel/hooks/post-install.sh opens by listing
     Phases B, C and D as "(TBD)", and then describes the Phase D activation
     chain in its own body as "now landed" - which it is, and which the same
     file then uses. The other two had landed as well. A header that says a
     mechanism does not exist yet, above a body that runs it, is worse than no
     header: it is a map that sends the reader away from the code.

WHAT THIS MEASURES. The claim in the docstring is checked AGAINST THE HOOK,
not against a remembered string: the hook is parsed for every place the
placeholder path is used, and a write to it would be a real finding rather
than a comment problem. Only once the hook is shown not to write it does the
docstring get held to saying so. The header is checked against the same file's
own body, so a phase cannot be advertised as pending beside the code that
implements it.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "packages" / "core" / "linux-kernel" / "hooks" / "post-install.sh"
BOOTLOADER = REPO_ROOT / "installer" / "backend" / "bootloader.py"

PLACEHOLDER = "/boot/initramfs.img"
FDE_CPIO = "/usr/lib/intergen/fde-initramfs.cpio.gz"


def _code_lines(text):
    """Lines that are shell code, not comments or blanks."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(line)
    return out


def _docstring_of(text, funcname):
    m = re.search(rf'^def {re.escape(funcname)}\([^)]*\):\n\s+"""(.*?)"""',
                  text, re.DOTALL | re.MULTILINE)
    assert m, f"{funcname} has no docstring"
    return m.group(1)


class FdeCommentsMatchTheCode(unittest.TestCase):

    def setUp(self):
        self.hook = HOOK.read_text()
        self.bootloader = BOOTLOADER.read_text()

    # ---------- what the hook actually does ----------

    def test_the_hook_names_the_placeholder_at_all(self):
        """A parser that found nothing would make the next test vacuous."""
        uses = [l for l in _code_lines(self.hook)
                if PLACEHOLDER in l or "$INITRD" in l or "${INITRD}" in l]
        self.assertTrue(uses, "the hook does not mention the placeholder path")

    def test_the_hook_never_writes_the_placeholder(self):
        """If it ever does, the comment is right and this test is the finding."""
        writes = re.compile(
            r'(?:^|\s)(?:cp|mv|install|dd|cat|tee|gzip|truncate)\b[^\n]*'
            r'(?:/boot/initramfs\.img|\$\{?INITRD\}?)'
            r'|>\s*"?(?:/boot/initramfs\.img|\$\{?INITRD\}?)')
        for line in _code_lines(self.hook):
            with self.subTest(line=line.strip()):
                self.assertIsNone(
                    writes.search(line),
                    "the hook writes the placeholder; every comment about "
                    "which file carries the unlock initramfs has to be "
                    "re-read against this line")

    def test_the_hook_bundles_the_separate_cpio_on_an_encrypted_install(self):
        self.assertIn(f'--initrd=$FDE_INITRD_PATH', self.hook,
                      "the hook no longer bundles the FDE cpio into the image")
        self.assertIn(FDE_CPIO, self.hook,
                      "the FDE cpio path is no longer named in the hook")

    # ---------- what the installer says about it ----------

    def test_the_installer_does_not_claim_the_stub_is_replaced(self):
        doc = _docstring_of(self.bootloader, "stage_uki_prereqs")
        collapsed = " ".join(doc.split())
        self.assertNotRegex(
            collapsed, r"LUKS installs replace this stub",
            "stage_uki_prereqs still says encrypted installs replace the "
            "placeholder. The hook never writes it - proven above - so on an "
            "encrypted system that file stays the placeholder and a reader "
            "chasing an unlock failure is sent to the wrong file")

    def test_the_installer_names_where_the_unlock_initramfs_actually_goes(self):
        doc = _docstring_of(self.bootloader, "stage_uki_prereqs")
        collapsed = " ".join(doc.split())
        self.assertIn(
            FDE_CPIO, collapsed,
            "the docstring does not name the file the unlock initramfs is "
            "actually built at, so it says where it is NOT without saying "
            "where it IS")

    # ---------- the hook's own header against its own body ----------

    def test_no_phase_is_advertised_as_pending_beside_the_code_that_runs_it(self):
        pending = set(re.findall(r"^#\s*Phase ([A-Z])\s*\(TBD\)", self.hook,
                                 re.MULTILINE))
        landed = set(re.findall(r"Phase ([A-Z])[^\n]*\(now landed\)", self.hook))
        overlap = sorted(pending & landed)
        self.assertEqual(
            overlap, [],
            f"the header lists Phase(s) {overlap} as still to be done while "
            "this same file describes them as landed and then runs them")

    def test_phase_d_is_not_listed_as_pending(self):
        """Named on its own because it is the one the body demonstrably runs."""
        self.assertNotRegex(
            self.hook, r"(?m)^#\s*Phase D \(TBD\)",
            "Phase D is still marked TBD, above the block that regenerates the "
            "FDE initramfs and bundles it into the unified kernel image")


if __name__ == "__main__":
    unittest.main()
