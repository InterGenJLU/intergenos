#!/usr/bin/env python3
"""Target enumeration: the classify_candidate matrix — POSIX viability, the
same-disk/system disqualifiers, the addendum directory-class + cap, and the
non-POSIX remediation matrix incl. exFAT and gparted-absent (spec §4, addendum).
Pure — no lsblk, no I/O."""

import unittest

from chronicle import enumerate as _enum

_GB = 1024 ** 3


def _cand(**kw):
    base = {"name": "/dev/sdb1", "fstype": "ext4", "size_bytes": 500 * _GB,
            "free_bytes": 400 * _GB, "parent_disk": "/dev/sdb",
            "mountpoint": "/run/media/u/backup", "is_system": False}
    base.update(kw)
    return base


class ClassifyTest(unittest.TestCase):
    def _classify(self, cand, gparted=True):
        return _enum.classify_candidate(
            cand, system_disk="/dev/sda", home_estimate_bytes=50 * _GB,
            floor_bytes=1 * _GB, gparted_present=gparted,
            cap_default_bytes=256 * _GB)

    def test_posix_separate_disk_offers_both_target_classes(self):
        r = self._classify(_cand())
        self.assertIsNone(r["disqualified"])
        self.assertEqual(r["protection_label"], _enum.LABEL_SEPARATE_DISK)
        modes = {t["mode"] for t in r["supported_targets"]}
        self.assertEqual(modes, {"whole-volume", "directory"})
        directory = next(t for t in r["supported_targets"] if t["mode"] == "directory")
        self.assertEqual(directory["cap_bytes"], 256 * _GB)
        self.assertIn("ChronicleBackups", directory["subtree"])

    def test_same_disk_is_labelled_but_still_usable(self):
        r = self._classify(_cand(parent_disk="/dev/sda"))
        self.assertEqual(r["protection_label"], _enum.LABEL_SAME_DISK)
        self.assertIsNone(r["disqualified"], "same-disk is labelled, not refused")

    def test_system_volume_refused(self):
        r = self._classify(_cand(is_system=True))
        self.assertEqual(r["protection_label"], _enum.LABEL_SYSTEM_REFUSED)
        self.assertIsNotNone(r["disqualified"])

    def test_posix_too_small_disqualified(self):
        r = self._classify(_cand(free_bytes=1 * _GB))  # < 50 GiB home estimate
        self.assertIn("not enough free space", r["disqualified"])

    def test_non_posix_ntfs_with_gparted_offers_guided_shrink(self):
        r = self._classify(_cand(fstype="ntfs"), gparted=True)
        self.assertIsNotNone(r["disqualified"])
        self.assertFalse(r["posix"])
        actions = {o["action"] for o in r["remediation"]["options"]}
        self.assertIn("gparted-guided", actions)
        self.assertIn("reformat-whole-drive", actions)

    def test_non_posix_ntfs_without_gparted_names_the_package(self):
        r = self._classify(_cand(fstype="ntfs"), gparted=False)
        opts = r["remediation"]["options"]
        install = next(o for o in opts if o["action"] == "install-gparted")
        self.assertEqual(install["package"], "gparted")

    def test_exfat_is_not_shrinkable(self):
        r = self._classify(_cand(fstype="exfat"), gparted=True)
        self.assertFalse(r["remediation"]["shrinkable"])
        actions = {o["action"] for o in r["remediation"]["options"]}
        self.assertIn("not-resizable", actions)
        self.assertNotIn("gparted-guided", actions)

    def test_sort_by_protection_puts_separate_disk_first(self):
        a = self._classify(_cand(name="/dev/sda2", parent_disk="/dev/sda"))
        b = self._classify(_cand(name="/dev/sdb1", parent_disk="/dev/sdb"))
        ordered = _enum.sort_by_protection([a, b])
        self.assertEqual(ordered[0]["protection_label"], _enum.LABEL_SEPARATE_DISK)


# The fail-closed unknown-topology class (measured live on an installed
# ge9b-12 system): a sandboxed scan environment that cannot see the
# device-mapper chain finds no "/" mount (system_disk None) — the classifier
# must refuse every candidate rather than default to separate-disk, because
# one of those "candidates" was the partition holding the encrypted root.
class UnknownTopologyTest(unittest.TestCase):
    def _classify_no_system_disk(self, cand):
        return _enum.classify_candidate(
            cand, system_disk=None, home_estimate_bytes=50 * _GB,
            floor_bytes=1 * _GB, gparted_present=True,
            cap_default_bytes=256 * _GB)

    def test_no_system_disk_never_claims_separate_disk(self):
        r = self._classify_no_system_disk(_cand())
        self.assertEqual(r["protection_label"], _enum.LABEL_UNKNOWN)
        self.assertIsNotNone(r["disqualified"])
        self.assertEqual(r["supported_targets"], [])
        self.assertIsNone(r["remediation"], "no destructive flow may be "
                          "offered over an unresolved topology")

    def test_no_parent_disk_never_claims_separate_disk(self):
        r = _enum.classify_candidate(
            _cand(parent_disk=None), system_disk="/dev/sda",
            home_estimate_bytes=50 * _GB, floor_bytes=1 * _GB,
            gparted_present=True, cap_default_bytes=256 * _GB)
        self.assertEqual(r["protection_label"], _enum.LABEL_UNKNOWN)
        self.assertIsNotNone(r["disqualified"])

    def test_unknown_ranks_below_same_disk_in_sort(self):
        known = _enum.classify_candidate(
            _cand(parent_disk="/dev/sda"), system_disk="/dev/sda",
            home_estimate_bytes=50 * _GB, floor_bytes=1 * _GB,
            gparted_present=True)
        unknown = self._classify_no_system_disk(_cand(name="/dev/sdc1"))
        ordered = _enum.sort_by_protection([unknown, known])
        self.assertEqual(ordered[0]["protection_label"], _enum.LABEL_SAME_DISK)


# The scan() integration shape over real lsblk JSON: an encrypted-root
# single-NVMe laptop (the measured ge9b-12 install layout). The LUKS holder
# chain must resolve the system disk, the ESP must be refused as a system
# volume (not offered partition surgery), and the plugged USB stick is the
# only separate-disk candidate.
_LUKS_LAPTOP_LSBLK = {"blockdevices": [
    {"name": "sda", "path": "/dev/sda", "pkname": None, "type": "disk",
     "fstype": None, "size": 15728640000, "fsavail": None, "mountpoint": None,
     "children": [
         {"name": "sda1", "path": "/dev/sda1", "pkname": "sda", "type": "part",
          "fstype": "ext4", "size": 15727591424, "fsavail": 15000000000,
          "mountpoint": "/run/media/u/stick"}]},
    {"name": "nvme0n1", "path": "/dev/nvme0n1", "pkname": None, "type": "disk",
     "fstype": None, "size": 512110190592, "fsavail": None, "mountpoint": None,
     "children": [
         {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "pkname": "nvme0n1",
          "type": "part", "fstype": "vfat", "size": 1073741824,
          "fsavail": 995213312, "mountpoint": "/boot/efi"},
         {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "pkname": "nvme0n1",
          "type": "part", "fstype": "crypto_LUKS", "size": 511035047936,
          "fsavail": None, "mountpoint": None,
          "children": [
              {"name": "cryptroot", "path": "/dev/mapper/cryptroot",
               "pkname": "nvme0n1p2", "type": "crypt", "fstype": None,
               "size": 511018270720, "fsavail": 457586327552,
               "mountpoint": "/"}]}]},
]}

# The same laptop as the blinded sandbox saw it: no mapper node, no "/"
# mount anywhere, the LUKS partition exposed as a childless leaf.
_BLINDED_LSBLK = {"blockdevices": [
    {"name": "sda", "path": "/dev/sda", "pkname": None, "type": "disk",
     "fstype": None, "size": 15728640000, "fsavail": None, "mountpoint": None,
     "children": [
         {"name": "sda1", "path": "/dev/sda1", "pkname": "sda", "type": "part",
          "fstype": "ext4", "size": 15727591424, "fsavail": None,
          "mountpoint": None}]},
    {"name": "nvme0n1", "path": "/dev/nvme0n1", "pkname": None, "type": "disk",
     "fstype": None, "size": 512110190592, "fsavail": None, "mountpoint": None,
     "children": [
         {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "pkname": "nvme0n1",
          "type": "part", "fstype": "vfat", "size": 1073741824,
          "fsavail": 995213312, "mountpoint": "/boot/efi"},
         {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "pkname": "nvme0n1",
          "type": "part", "fstype": "crypto_LUKS", "size": 511035047936,
          "fsavail": None, "mountpoint": None}]},
]}


class ScanTopologyTest(unittest.TestCase):
    def _scan(self, data):
        return _enum.scan(1 * _GB, 1 * _GB, gparted_present=True,
                          _lsblk_json=data)

    def test_luks_chain_resolves_and_esp_is_refused_as_system(self):
        by_dev = {r["device"]: r for r in self._scan(_LUKS_LAPTOP_LSBLK)}
        self.assertEqual(by_dev["/dev/nvme0n1p1"]["protection_label"],
                         _enum.LABEL_SYSTEM_REFUSED)
        self.assertEqual(by_dev["/dev/mapper/cryptroot"]["protection_label"],
                         _enum.LABEL_SYSTEM_REFUSED)
        self.assertEqual(by_dev["/dev/sda1"]["protection_label"],
                         _enum.LABEL_SEPARATE_DISK)

    def test_blinded_scan_refuses_every_candidate(self):
        results = self._scan(_BLINDED_LSBLK)
        self.assertTrue(results)
        for r in results:
            self.assertEqual(r["protection_label"], _enum.LABEL_UNKNOWN, r)
            self.assertIsNotNone(r["disqualified"], r)
            self.assertEqual(r["supported_targets"], [], r)
            self.assertIsNone(r["remediation"], r)


if __name__ == "__main__":
    unittest.main()
