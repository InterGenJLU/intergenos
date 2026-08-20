"""The Network Discovery toggle must leave the machine able to RESOLVE the
names it discovers, not merely to see them.

Measured on a real install, 2026-08-19, against a real IPP printer on the link:
with both the printing and the discovery toggle on, the printer was discovered
(CUPS listed it, its queue address was the printer's .local name) and could not
be reached — `getent hosts` returned nothing and printing failed. The cause is
the order of the shipped name-service-switch hosts line: `resolve
[!UNAVAIL=return]` answers first and returns to the caller on anything except
"resolver unavailable", so the mdns4_minimal entry after it is never consulted,
and systemd-resolved's own multicast DNS is off because Avahi is the responder
on this system. Moving that entry ahead of resolve fixed it: resolution in 3 ms
and a working IPP conversation with the printer.

These tests pin the mechanism, not the measurement:

  (a) the line the privileged helper expects to find is the line the system
      actually ships, so the two cannot drift apart silently;
  (b) the helper's transformation only MOVES an entry — it adds nothing and
      removes nothing;
  (c) a hosts line the helper does not recognise is refused, not guessed at;
  (d) the discovery verbs are the ones wired to it, and only those.

Every test runs the helper's unprivileged `mdns-hosts-line` verb, which reads a
name-service-switch file on standard input and writes nothing at all, so this
file never touches the machine's own resolver configuration.
"""

import hashlib
import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PRIVHELPER = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome-privhelper"
GLIBC_BUILD = REPO_ROOT / "packages" / "core" / "glibc-core" / "build.sh"

SHIPPED_OFF = ("hosts: mymachines resolve [!UNAVAIL=return] files myhostname "
               "dns mdns4_minimal [NOTFOUND=return]")
EXPECTED_ON = ("hosts: mymachines mdns4_minimal [NOTFOUND=return] resolve "
               "[!UNAVAIL=return] files myhostname dns")


def _nsswitch(hosts_line):
    return ("# Begin /etc/nsswitch.conf\n"
            "passwd: files systemd\n"
            f"{hosts_line}\n"
            "networks: files\n"
            "# End /etc/nsswitch.conf\n")


def _run(want, text):
    return subprocess.run(["bash", str(PRIVHELPER), "mdns-hosts-line", want],
                          input=text, capture_output=True, text=True, timeout=30)


class ShippedLineIsTheLineTheHelperKnows(unittest.TestCase):
    """(a) The helper edits one exact line. If the shipped default is reworded,
    the helper would silently stop recognising it and the toggle would go back
    to leaving discovery half-working. This test fails instead."""

    def test_glibc_core_ships_the_line_the_helper_expects(self):
        shipped = GLIBC_BUILD.read_text(encoding="utf-8")
        self.assertIn(SHIPPED_OFF, shipped,
                      "the hosts line glibc-core ships is no longer the line "
                      "the Welcomer's privileged helper knows how to move")

    def test_helper_carries_both_forms_verbatim(self):
        helper = PRIVHELPER.read_text(encoding="utf-8")
        self.assertIn(f"HOSTS_LINE_OFF='{SHIPPED_OFF}'", helper)
        self.assertIn(f"HOSTS_LINE_ON='{EXPECTED_ON}'", helper)


class TheChangeOnlyMovesAnEntry(unittest.TestCase):
    """(b) Nothing is added to the name-service switch and nothing is taken
    away — a reordering is the whole change, and that is checkable."""

    @staticmethod
    def _entries(line):
        return sorted(re.findall(r"\[[^\]]+\]|[^\s\[\]]+", line.split(":", 1)[1]))

    def test_both_forms_hold_exactly_the_same_entries(self):
        self.assertEqual(self._entries(SHIPPED_OFF), self._entries(EXPECTED_ON))

    def test_on_puts_mdns_before_resolve_and_off_puts_it_after(self):
        self.assertLess(EXPECTED_ON.index("mdns4_minimal"), EXPECTED_ON.index("resolve"))
        self.assertGreater(SHIPPED_OFF.index("mdns4_minimal"), SHIPPED_OFF.index("resolve"))


class TheVerbAnswersForEveryKnownState(unittest.TestCase):
    def test_off_file_asked_for_on(self):
        r = _run("on", _nsswitch(SHIPPED_OFF))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), EXPECTED_ON)

    def test_on_file_asked_for_on_is_unchanged(self):
        r = _run("on", _nsswitch(EXPECTED_ON))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), EXPECTED_ON)

    def test_on_file_asked_for_off_goes_back_to_the_shipped_line(self):
        r = _run("off", _nsswitch(EXPECTED_ON))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), SHIPPED_OFF)

    def test_off_file_asked_for_off_is_unchanged(self):
        r = _run("off", _nsswitch(SHIPPED_OFF))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), SHIPPED_OFF)


class AnUnknownLineIsRefusedRatherThanGuessedAt(unittest.TestCase):
    """(c) A machine whose owner has edited their own hosts line must get that
    line back untouched. The helper reports and declines; it never rewrites a
    line it did not write or ship."""

    def test_a_hand_written_hosts_line_is_refused(self):
        r = _run("on", _nsswitch("hosts: files dns"))
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout, "")

    def test_a_file_with_no_hosts_line_at_all_is_refused(self):
        r = _run("on", "passwd: files systemd\nnetworks: files\n")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout, "")

    def test_a_state_that_is_neither_on_nor_off_is_refused(self):
        r = _run("sideways", _nsswitch(SHIPPED_OFF))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")

    def test_the_verb_writes_nothing(self):
        text = _nsswitch(SHIPPED_OFF)
        before = hashlib.sha256(PRIVHELPER.read_bytes()).hexdigest()
        _run("on", text)
        _run("off", text)
        self.assertEqual(hashlib.sha256(PRIVHELPER.read_bytes()).hexdigest(), before)


class OnlyTheDiscoveryVerbsAreWired(unittest.TestCase):
    """(d) Printing on its own must not rearrange name resolution, and the
    discovery verb must set the order in both directions."""

    def setUp(self):
        self.helper = PRIVHELPER.read_text(encoding="utf-8")

    def _verb_body(self, verb):
        body = self.helper.split(f"    {verb})", 1)[1]
        return body.split(";;", 1)[0]

    def test_enable_discovery_sets_the_order_on(self):
        self.assertIn("set_mdns_hosts_order on", self._verb_body("enable-discovery"))

    def test_disable_discovery_sets_the_order_off(self):
        self.assertIn("set_mdns_hosts_order off", self._verb_body("disable-discovery"))

    def test_the_printing_verbs_do_not_touch_it(self):
        for verb in ("enable-printing", "disable-printing"):
            self.assertNotIn("set_mdns_hosts_order", self._verb_body(verb))

    def test_the_ssh_verbs_do_not_touch_it(self):
        for verb in ("enable-ssh", "disable-ssh"):
            self.assertNotIn("set_mdns_hosts_order", self._verb_body(verb))


if __name__ == "__main__":
    unittest.main()
