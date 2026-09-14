# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The Welcomer's side of the SSH posture: one fragment shape shared with the
installer, an OFF that also closes the legacy inline rule, and a services page
that says which services are already on.

WHY (R001.3 rows 31 + 13). Installs made with the R001.2 installer carried the
install-time SSH opt-in as an inline rule inside the shipped /etc/nftables.conf.
The helper's `disable-ssh` deleted a fragment that did not exist, so the port
stayed open. Now the installer writes the same fragment the helper manages, and
`disable-ssh` additionally strips exactly the legacy three-line block on
machines installed before the change. The services page said "These ship OFF"
on a machine whose owner had turned SSH on during install and where sshd was
listening forty seconds into the first boot; the sentence now names what is on.
"""

import importlib.util
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets/intergen-welcome/intergen-welcome.py"
HELPER = REPO_ROOT / "assets/intergen-welcome/intergen-welcome-privhelper"
LOANER_CONF = REPO_ROOT / "tests/installer/fixtures/nftables.conf.r0012-loaner-install"
SHIPPED_CONF = REPO_ROOT / "packages/core/intergenos-firewall-defaults/nftables.conf"

_spec = importlib.util.spec_from_file_location("intergen_welcome", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)


def _helper_function(name):
    """Extract one shell function's text from the helper (it runs `case "$1"`
    at the bottom, so the whole file cannot be sourced)."""
    src = HELPER.read_text()
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", src, re.S | re.M)
    assert m, f"helper function {name} not found"
    return m.group(0)


class OneFragmentShape(unittest.TestCase):
    def test_helper_fragment_equals_installer_constant(self):
        from installer.backend import users
        src = HELPER.read_text()
        m = re.search(r"cat > \"\$SSH_DROPIN\" <<'NFT'\n(.*?)\nNFT\n", src, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1) + "\n", users.SSH_FIREWALL_FRAGMENT)

    def test_disable_ssh_strips_the_legacy_rule_and_removes_the_fragment(self):
        src = HELPER.read_text()
        arm = re.search(r"    disable-ssh\)\n(.*?)        ;;\n", src, re.S).group(1)
        self.assertIn('rm -f "$SSH_DROPIN"', arm)
        self.assertIn("strip_legacy_ssh_rule /etc/nftables.conf", arm)
        self.assertIn("reload_firewall", arm)


class LegacyRuleStrip(unittest.TestCase):
    """strip_legacy_ssh_rule against the REAL file captured from the loaner's
    R001.2 install, and against the shipped file (a no-op, byte for byte)."""

    def _strip(self, data: bytes) -> bytes:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "nftables.conf"
            f.write_bytes(data)
            script = _helper_function("strip_legacy_ssh_rule") + f'\nstrip_legacy_ssh_rule "{f}"\n'
            r = subprocess.run(["bash", "-euo", "pipefail", "-c", script],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return f.read_bytes()

    def test_loaner_file_becomes_the_shipped_file(self):
        legacy = LOANER_CONF.read_bytes()
        self.assertIn(b"tcp dport 22 accept", legacy)
        self.assertEqual(self._strip(legacy), SHIPPED_CONF.read_bytes())

    def test_shipped_file_is_untouched(self):
        shipped = SHIPPED_CONF.read_bytes()
        self.assertEqual(self._strip(shipped), shipped)

    def test_a_hand_written_rule_is_left_alone(self):
        custom = SHIPPED_CONF.read_text().replace(
            "        # Everything else inbound DROPS",
            "        # my own rule\n        tcp dport 22 accept\n\n        # Everything else inbound DROPS", 1).encode()
        self.assertEqual(self._strip(custom), custom)

    def test_absent_file_is_not_an_error(self):
        script = _helper_function("strip_legacy_ssh_rule") + '\nstrip_legacy_ssh_rule /nonexistent/nftables.conf\n'
        r = subprocess.run(["bash", "-euo", "pipefail", "-c", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class ServicesPageSaysWhatIsOn(unittest.TestCase):
    def test_nothing_on_is_the_shipped_promise(self):
        s = welcome.services_page_subtitle(enabled=set())
        self.assertTrue(s.startswith("These ship OFF for security. Turn on what you need"))
        self.assertNotIn("already on", s)

    def test_ssh_on_names_ssh_and_the_reason(self):
        s = welcome.services_page_subtitle(enabled={"ssh"})
        self.assertIn("The SSH Server is already on because you turned it on during install.", s)
        self.assertTrue(s.endswith("can be turned back off any time."))

    def test_two_on_are_joined_in_page_order(self):
        s = welcome.services_page_subtitle(enabled={"ssh", "printing"})
        self.assertIn("Print Services and the SSH Server are already on", s)

    def test_live_read_uses_the_unit_table(self):
        seen = []
        orig = welcome._service_enabled
        try:
            welcome._service_enabled = lambda unit: seen.append(unit) or unit == "sshd.service"
            s = welcome.services_page_subtitle()
        finally:
            welcome._service_enabled = orig
        self.assertEqual(sorted(seen), sorted(welcome._SERVICE_UNITS.values()))
        self.assertIn("SSH Server is already on", s)


if __name__ == "__main__":
    unittest.main()
