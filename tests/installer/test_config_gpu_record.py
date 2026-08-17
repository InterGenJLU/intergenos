# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The display-controller record is actually written by the install.

`installer/backend/gpu_detect.py` can be perfectly correct and still reach no
installed system if nothing calls it — and the whole first-boot offer is
silent when the record is absent, so that failure would look exactly like
hardware with nothing to add. This asserts the call HAPPENS, by running
generate_all with every other generator stubbed out and watching what the
writer is given.

Deliberately behavioural rather than a grep of the source: a source check
passes on its own comment, and "the file mentions write_detection_record" is
not the claim being made.
"""

import unittest
from unittest import mock

from installer.backend import config


# Everything generate_all calls except the writer under test. Stubbed so the
# call can be observed without a real target root, a chroot, or a disk.
_OTHER_GENERATORS = [
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


class TestGenerateAllWritesTheRecord(unittest.TestCase):
    def _run_generate_all(self, target="/target"):
        patches = {name: mock.DEFAULT for name in _OTHER_GENERATORS}
        with mock.patch.multiple(config, **patches):
            with mock.patch.object(config, "write_detection_record") as writer:
                config.generate_all(target, partitions={})
        return writer

    def test_the_writer_is_called(self):
        self.assertTrue(self._run_generate_all().called)

    def test_it_is_given_the_install_target(self):
        writer = self._run_generate_all("/mnt/target")
        writer.assert_called_once_with("/mnt/target")

    def test_it_is_called_once_not_per_partition(self):
        self.assertEqual(self._run_generate_all().call_count, 1)

    def test_the_writer_is_the_real_one_by_default(self):
        # The name generate_all reaches must be the module under test, not a
        # look-alike left behind by a refactor.
        from installer.backend import gpu_detect
        self.assertIs(config.write_detection_record,
                      gpu_detect.write_detection_record)


if __name__ == "__main__":
    unittest.main()
