# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""An install onto a machine with the GL9755 card reader writes the boot
parameter that machine needs, and an install onto anything else does not.

The reader behind a PCIe root port whose power management the kernel drives is
unusable on an installed system: the port is put into D3cold before the driver
arrives and the link never trains again. The remedy is one kernel parameter,
and it has to reach the signed image, which means a fragment under
/etc/kernel/cmdline.d/ written while the target is being configured.

These are behavioural tests. Each runs the real generator against a temporary
target root, with `lspci -n` output injected, and reads the file the generator
did or did not write. A source check would pass on the comment inside the
generator, which is not the claim being made.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.backend import config


class _FakeCompleted:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


# Real `lspci -n` output shape: "<slot> <class>: <vendor>:<device> [(rev nn)]".
# The reader is the storage-class line; the others are here so the test proves
# the generator picks a line out of a real inventory rather than matching any
# output at all.
_LSPCI_WITH_READER = "\n".join([
    "00:00.0 0600: 8086:4601 (rev 04)",
    "00:02.0 0300: 8086:46a8 (rev 0c)",
    "00:1c.6 0604: 8086:51be (rev 01)",
    "01:00.0 0300: 10de:24a0 (rev a1)",
    "2d:00.0 0805: 17a0:9755 (rev 01)",
])

_LSPCI_WITHOUT_READER = "\n".join([
    "00:00.0 0600: 8086:4601 (rev 04)",
    "00:02.0 0300: 8086:46a8 (rev 0c)",
    "01:00.0 0300: 10de:24a0 (rev a1)",
    "02:00.0 0108: 144d:a80c",
])


def _runner_returning(stdout, returncode=0):
    return lambda *a, **kw: _FakeCompleted(stdout, returncode)


def _runner_raising(exc):
    def _run(*a, **kw):
        raise exc
    return _run


FRAGMENT = "etc/kernel/cmdline.d/40-sd-reader-port-power.conf"


class SdReaderCmdlineFragment(unittest.TestCase):

    def test_the_fragment_is_written_when_the_reader_is_present(self):
        with tempfile.TemporaryDirectory() as target:
            config.generate_sd_reader_cmdline_fragment(
                target, lspci_runner=_runner_returning(_LSPCI_WITH_READER))
            path = Path(target) / FRAGMENT
            self.assertTrue(path.is_file(), "no fragment was written")
            text = path.read_text()
            parameters = [ln.strip() for ln in text.splitlines()
                          if ln.strip() and not ln.lstrip().startswith("#")]
            self.assertEqual(
                parameters, ["pcie_port_pm=off"],
                "the fragment must contribute exactly one parameter")

    def test_the_fragment_explains_itself(self):
        """The three shipped fragments each say what the parameter does, why it
        is there, what it costs and how to undo it. A file that a person finds
        on their own machine and cannot account for is the thing being avoided.
        """
        with tempfile.TemporaryDirectory() as target:
            config.generate_sd_reader_cmdline_fragment(
                target, lspci_runner=_runner_returning(_LSPCI_WITH_READER))
            text = (Path(target) / FRAGMENT).read_text()
            comment = "\n".join(ln for ln in text.splitlines()
                                if ln.lstrip().startswith("#")).lower()
            for expected in ("port power", "d3cold", "17a0:9755",
                             "2026-09-22", "delete this file"):
                self.assertIn(expected, comment,
                              "the comment does not mention %r" % expected)

    def test_no_fragment_on_a_machine_without_the_reader(self):
        with tempfile.TemporaryDirectory() as target:
            config.generate_sd_reader_cmdline_fragment(
                target, lspci_runner=_runner_returning(_LSPCI_WITHOUT_READER))
            self.assertFalse(
                (Path(target) / FRAGMENT).exists(),
                "a machine without this reader must not be given the parameter")

    def test_a_near_miss_device_id_does_not_match(self):
        """Same vendor, different device. The gate is on the pair."""
        other = _LSPCI_WITH_READER.replace("17a0:9755", "17a0:9750")
        with tempfile.TemporaryDirectory() as target:
            config.generate_sd_reader_cmdline_fragment(
                target, lspci_runner=_runner_returning(other))
            self.assertFalse((Path(target) / FRAGMENT).exists())

    def test_fail_closed_when_lspci_is_absent(self):
        with tempfile.TemporaryDirectory() as target:
            with self.assertLogs("forge.packages", level="WARNING") as logs:
                config.generate_sd_reader_cmdline_fragment(
                    target,
                    lspci_runner=_runner_raising(FileNotFoundError("lspci")))
            self.assertFalse(
                (Path(target) / FRAGMENT).exists(),
                "an undetected machine must not be guessed at")
            self.assertTrue(
                any("lspci unavailable" in line for line in logs.output),
                "the skip must say why: %r" % (logs.output,))

    def test_fail_closed_when_lspci_fails(self):
        with tempfile.TemporaryDirectory() as target:
            with self.assertLogs("forge.packages", level="WARNING") as logs:
                config.generate_sd_reader_cmdline_fragment(
                    target, lspci_runner=_runner_returning("", returncode=1))
            self.assertFalse((Path(target) / FRAGMENT).exists())
            self.assertTrue(
                any("lspci exited 1" in line for line in logs.output),
                "the skip must say why: %r" % (logs.output,))

    def test_the_base_command_line_is_not_touched(self):
        """The shipped bus-wide ASPM setting is written by a different
        generator and this one must leave it alone."""
        with tempfile.TemporaryDirectory() as target:
            base = Path(target) / "etc" / "kernel" / "cmdline"
            base.parent.mkdir(parents=True, exist_ok=True)
            base.write_text("root=UUID=x rootwait vt.default_utf8=1 pcie_aspm=off\n")
            before = base.read_bytes()
            config.generate_sd_reader_cmdline_fragment(
                target, lspci_runner=_runner_returning(_LSPCI_WITH_READER))
            self.assertEqual(base.read_bytes(), before)


class TheInstallActuallyCallsIt(unittest.TestCase):
    """The generator can be perfectly correct and reach no installed system if
    nothing calls it. Run generate_all with every other generator stubbed and
    watch for the call."""

    _OTHERS = [
        "seed_account_databases",
        "generate_fstab",
        "generate_crypttab",
        "generate_kernel_cmdline",
        "generate_hostname",
        "generate_machine_id",
        "generate_locale",
        "generate_vconsole",
        "set_timezone",
        "generate_network",
        "generate_os_release",
        "generate_branding",
        "generate_grub_defaults",
    ]

    def test_generate_all_writes_the_fragment(self):
        with tempfile.TemporaryDirectory() as target:
            with mock.patch.object(
                config, "generate_sd_reader_cmdline_fragment"
            ) as writer:
                patches = [mock.patch.object(config, name)
                           for name in self._OTHERS]
                for p in patches:
                    p.start()
                self.addCleanup(lambda: [p.stop() for p in patches])
                try:
                    config.generate_all(target, {"root": "/dev/sda2"})
                except Exception:  # noqa: BLE001 — later generators are not under test
                    pass
            self.assertTrue(
                writer.called,
                "generate_all does not write the card-reader fragment")
            self.assertEqual(writer.call_args[0][0], target)


if __name__ == "__main__":
    unittest.main()
