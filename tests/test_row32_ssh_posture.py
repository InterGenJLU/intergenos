"""The remote-login posture ships once and in both copies (R001.3 row 32).

The hardening drop-in exists TWICE in the tree: the recipe writes it during
install, and the same file is shipped as a static file so it reaches every
installed target through the package archive. Nothing checked that the two
agreed, so a correction applied to one of them would ship half a posture — the
running value would depend on which copy landed last.

These tests compare the two copies' SETTINGS (not their comments, which say
different things on purpose), and they pin the values this row changes:
forwarding off, so that authenticating to this machine does not silently grant a
network pivot through it and a reach back into the agent on the machine the
person came from.

The effective values that a running daemon computes from these files are proven
separately, by asking the daemon itself; a file is not a posture until the server
agrees, and that check needs a real sshd and privilege, so it lives in the
delivery's evidence rather than in this suite.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPE = REPO_ROOT / "packages" / "core" / "openssh" / "build.sh"
SHIPPED = (REPO_ROOT / "packages" / "core" / "openssh" / "files" / "etc" / "ssh"
           / "sshd_config.d" / "01-intergenos-hardening.conf")

HEREDOC = re.compile(
    r'cat > "\$\{DESTDIR\}/etc/ssh/sshd_config\.d/01-intergenos-hardening\.conf" '
    r'<< "EOF"\n(.*?)\nEOF\n', re.S)


def _settings(text):
    """The directive lines of a config, without comments or blank lines."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _recipe_copy():
    m = HEREDOC.search(RECIPE.read_text())
    assert m, "the recipe no longer writes the hardening drop-in as a heredoc"
    return m.group(1)


class TestBothCopiesCarryTheSameSettings(unittest.TestCase):
    def test_the_two_copies_agree_line_for_line(self):
        self.assertEqual(_settings(_recipe_copy()), _settings(SHIPPED.read_text()),
                         "the recipe's copy and the shipped file must carry the "
                         "same settings, or which one lands decides the posture")

    def test_neither_copy_is_empty(self):
        self.assertTrue(_settings(SHIPPED.read_text()))
        self.assertTrue(_settings(_recipe_copy()))


class TestTheForwardingPosture(unittest.TestCase):
    def test_both_copies_turn_forwarding_off(self):
        for name, text in (("recipe", _recipe_copy()),
                           ("shipped file", SHIPPED.read_text())):
            settings = _settings(text)
            self.assertIn("AllowTcpForwarding no", settings,
                          f"the {name} must not leave a tunnel through this "
                          f"machine open to anyone who can log in")
            self.assertIn("AllowAgentForwarding no", settings,
                          f"the {name} must not expose the agent on the "
                          f"machine a person connected from")

    def test_the_setting_is_stated_once_per_copy(self):
        for text in (_recipe_copy(), SHIPPED.read_text()):
            settings = _settings(text)
            for directive in ("AllowTcpForwarding", "AllowAgentForwarding"):
                hits = [s for s in settings if s.split()[0] == directive]
                self.assertEqual(len(hits), 1,
                                 f"{directive} must appear once: sshd takes the "
                                 f"first value it reads, so a second line is "
                                 f"either dead or a contradiction")


class TestTheIdleTimeoutIsUnchanged(unittest.TestCase):
    """The row read the keep-alive count of zero as a defect. It is not.

    With `ClientAliveInterval 600` a count of zero means one missed probe ends
    the session, so an idle session is dropped after ten minutes. Raising the
    count would LENGTHEN the window a walked-away-from terminal stays open.
    Pinned here so the row's reading does not get applied by a later reader.
    """

    def test_the_interval_and_count_stay_as_they_are(self):
        settings = _settings(SHIPPED.read_text())
        self.assertIn("ClientAliveInterval 600", settings)
        self.assertIn("ClientAliveCountMax 0", settings)


if __name__ == "__main__":
    unittest.main()
