# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The display adapter's identity is read once, from sysfs, not polled.

The static cache tier re-ran `lspci | grep -i vga` every 300 seconds for the
GPU's identity. An adapter's identity cannot change while the machine runs, so
the poll could only ever return the answer it already held; and `lspci` reads
PCI CONFIGURATION SPACE, which is a device access rather than a kernel-side
read. On intergenos-192-r001-2 (kernel 6.18.10-igos-21, measured 2026-09-16) an
unprivileged read of a runtime-suspended device's configuration space did NOT
resume it — the four suspended devices' runtime_active_time did not move across
`lspci`, `lspci -v` and a direct read of the `config` attribute. That is one
kernel's behaviour under one privilege posture; the identity is read from sysfs
here so the class of access is gone rather than tolerated.

The replacement produces the SAME sentence the pipeline produced, so every
reader of the `gpu_info` cache key is unaffected — pinned below against the
real machine's own `lspci` output where one is available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from intergen import state_cache as sc


PCI_IDS = """\
#
#\tList of PCI IDs
#
8086  Intel Corporation
\t8a56  Iris Plus Graphics G1 (Ice Lake)
\t9999  Some Other Part
10de  NVIDIA Corporation
\t2484  GA104 [GeForce RTX 3070]
"""


def _fake_pci_tree(root: str, devices: dict[str, dict[str, str]]) -> str:
    base = os.path.join(root, "devices")
    for slot, attrs in devices.items():
        d = os.path.join(base, slot)
        os.makedirs(d)
        for name, value in attrs.items():
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(value + "\n")
    return base


class DisplayAdapterIdentityTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        ids = os.path.join(self.tmp, "pci.ids")
        with open(ids, "w", encoding="utf-8") as fh:
            fh.write(PCI_IDS)
        self._orig_dir = sc._PCI_DEVICES_DIR
        self._orig_ids = sc._PCI_IDS_PATHS
        sc._PCI_IDS_PATHS = (ids,)
        self.addCleanup(setattr, sc, "_PCI_DEVICES_DIR", self._orig_dir)
        self.addCleanup(setattr, sc, "_PCI_IDS_PATHS", self._orig_ids)

    def test_a_display_adapter_reads_as_lspci_writes_it(self):
        sc._PCI_DEVICES_DIR = _fake_pci_tree(self.tmp, {
            "0000:00:02.0": {"class": "0x030000", "vendor": "0x8086",
                             "device": "0x8a56", "revision": "0x07"},
            "0000:00:1f.3": {"class": "0x040300", "vendor": "0x8086",
                             "device": "0x34c8", "revision": "0x30"},
        })
        self.assertEqual(
            sc.read_display_adapters(),
            "00:02.0 VGA compatible controller: Intel Corporation "
            "Iris Plus Graphics G1 (Ice Lake) (rev 07)")

    def test_every_display_adapter_is_listed_and_nothing_else_is(self):
        sc._PCI_DEVICES_DIR = _fake_pci_tree(self.tmp, {
            "0000:00:02.0": {"class": "0x030000", "vendor": "0x8086",
                             "device": "0x8a56", "revision": "0x07"},
            "0000:01:00.0": {"class": "0x030200", "vendor": "0x10de",
                             "device": "0x2484", "revision": "0xa1"},
            "0000:00:14.0": {"class": "0x0c0330", "vendor": "0x8086",
                             "device": "0x34ed", "revision": "0x30"},
        })
        lines = sc.read_display_adapters().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("VGA compatible controller: Intel Corporation", lines[0])
        self.assertIn("3D controller: NVIDIA Corporation GA104 "
                      "[GeForce RTX 3070] (rev a1)", lines[1])

    def test_an_unknown_id_reports_the_numbers_rather_than_a_guess(self):
        sc._PCI_DEVICES_DIR = _fake_pci_tree(self.tmp, {
            "0000:00:02.0": {"class": "0x030000", "vendor": "0xbeef",
                             "device": "0xcafe", "revision": "0x00"},
        })
        self.assertEqual(
            sc.read_display_adapters(),
            "00:02.0 VGA compatible controller: beef:cafe")

    def test_a_missing_ids_database_still_answers_with_the_numbers(self):
        sc._PCI_IDS_PATHS = ("/nonexistent/pci.ids",)
        sc._PCI_DEVICES_DIR = _fake_pci_tree(self.tmp, {
            "0000:00:02.0": {"class": "0x030000", "vendor": "0x8086",
                             "device": "0x8a56", "revision": "0x07"},
        })
        self.assertEqual(
            sc.read_display_adapters(),
            "00:02.0 VGA compatible controller: 8086:8a56 (rev 07)")

    def test_an_unreadable_sysfs_tree_returns_nothing_rather_than_raising(self):
        sc._PCI_DEVICES_DIR = "/nonexistent/pci/devices"
        self.assertEqual(sc.read_display_adapters(), "")

    def test_no_subprocess_is_run_and_no_config_attribute_is_opened(self):
        """The point of the change: a kernel-side read, not a device access."""
        sc._PCI_DEVICES_DIR = _fake_pci_tree(self.tmp, {
            "0000:00:02.0": {"class": "0x030000", "vendor": "0x8086",
                             "device": "0x8a56", "revision": "0x07",
                             "config": "not to be read"},
        })
        opened: list[str] = []
        real_open = open
        def watching_open(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)
        import builtins
        self.addCleanup(setattr, builtins, "open", real_open)
        builtins.open = watching_open
        def refuse(*a, **k):
            raise AssertionError("a subprocess was run to read an identity")
        for name in ("run", "Popen", "check_output"):
            self.addCleanup(setattr, subprocess, name,
                            getattr(subprocess, name))
            setattr(subprocess, name, refuse)
        sc.read_display_adapters()
        builtins.open = real_open
        self.assertTrue(any(p.endswith("/vendor") for p in opened))
        self.assertFalse([p for p in opened if p.endswith("/config")])


class IdentityIsNotPolledTests(unittest.TestCase):

    def test_the_static_poll_no_longer_runs_a_pci_listing(self):
        for key, pipeline in sc._STATIC_COMMANDS.items():
            for stage in pipeline:
                self.assertNotIn("lspci", stage,
                                 f"{key} still polls a PCI listing")

    def test_the_identity_key_is_read_by_a_reader_not_a_command(self):
        self.assertIn("gpu_info", sc._IDENTITY_READERS)
        self.assertNotIn("gpu_info", sc._STATIC_COMMANDS)
        # The question words still reach the key, so no consumer changes.
        self.assertIn("gpu_info", sc._QUERY_TO_CACHE)

    def test_starting_the_cache_populates_the_identity_once(self):
        cache = sc.StateCache()
        calls = {"n": 0}
        def reader():
            calls["n"] += 1
            return "00:02.0 VGA compatible controller: Test Adapter"
        orig = dict(sc._IDENTITY_READERS)
        sc._IDENTITY_READERS.clear()
        sc._IDENTITY_READERS["gpu_info"] = reader
        self.addCleanup(lambda: (sc._IDENTITY_READERS.clear(),
                                 sc._IDENTITY_READERS.update(orig)))
        cache._poll_identities()
        cache._poll_identities()
        self.assertEqual(calls["n"], 1)
        self.assertEqual(cache.get("gpu_info"),
                         "00:02.0 VGA compatible controller: Test Adapter")


class AgainstThisMachineTests(unittest.TestCase):
    """The replacement must produce what the pipeline produced, HERE."""

    def test_it_matches_this_machines_own_pci_listing(self):
        if not shutil.which("lspci"):
            self.skipTest("lspci is not installed on this machine")
        if not os.path.isdir("/sys/bus/pci/devices"):
            self.skipTest("this machine exposes no PCI devices in sysfs")
        p = subprocess.run("lspci | grep -i vga", shell=True,
                           capture_output=True, text=True)
        if p.returncode != 0 or not p.stdout.strip():
            self.skipTest("this machine's PCI listing shows no VGA line")
        self.assertEqual(sc.read_display_adapters().strip(), p.stdout.strip())


if __name__ == "__main__":
    unittest.main()
