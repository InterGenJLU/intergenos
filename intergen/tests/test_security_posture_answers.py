# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Security posture answered from the machine (class 8 of the assistant battery).

"is the firewall on?", "is Secure Boot on?", "is my disk encrypted?", "any open
ports?", "what's listening on the network?" and "is this machine secure?" are
questions about THIS machine. No route read the machine for them: the turn
reached the model, which answered with a how-to (for Secure Boot, a Windows
how-to), or the explain route taught how encryption is set up instead of saying
whether it is.

Each class is answered by the DIRECT-ANSWER route from an unprivileged reading,
and a reading that cannot be taken declines. The readings are faked here (a fake
tool registry for the two commands, temporary files for the firmware variables,
the kernel's socket tables, AppArmor's switches and pkm's advisory), so the
cases are hermetic.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import intergen.router as router
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher

_GUID = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
_LSBLK = "lsblk -J -o NAME,TYPE,FSTYPE,MOUNTPOINTS"
_FW_ACTIVE = "systemctl is-active nftables.service"
_FW_ENABLED = "systemctl is-enabled nftables.service"

# lsblk -J: a disk, its EFI partition, and a LUKS partition holding the root.
_LSBLK_LUKS_ROOT = json.dumps({"blockdevices": [
    {"name": "nvme0n1", "type": "disk", "fstype": None, "mountpoints": [None],
     "children": [
         {"name": "nvme0n1p1", "type": "part", "fstype": "vfat",
          "mountpoints": ["/boot/efi"]},
         {"name": "nvme0n1p2", "type": "part", "fstype": "crypto_LUKS",
          "mountpoints": [None],
          "children": [{"name": "cryptroot", "type": "crypt", "fstype": "ext4",
                        "mountpoints": ["/"]}]}]}]})
_LSBLK_PLAIN_ROOT = json.dumps({"blockdevices": [
    {"name": "sda", "type": "disk", "fstype": None, "mountpoints": [None],
     "children": [
         {"name": "sda1", "type": "part", "fstype": "vfat",
          "mountpoints": ["/boot/efi"]},
         {"name": "sda2", "type": "part", "fstype": "ext4",
          "mountpoints": ["/"]}]}]})
# LVM on LUKS: the root is a logical volume above the encrypted layer.
_LSBLK_LVM_ON_LUKS = json.dumps({"blockdevices": [
    {"name": "sda", "type": "disk", "fstype": None, "mountpoints": [None],
     "children": [
         {"name": "sda2", "type": "part", "fstype": "crypto_LUKS",
          "mountpoints": [None],
          "children": [{"name": "luks-sda2", "type": "crypt",
                        "fstype": "LVM2_member", "mountpoints": [None],
                        "children": [{"name": "vg-root", "type": "lvm",
                                      "fstype": "ext4",
                                      "mountpoints": ["/"]}]}]}]}]})
# lsblk -J -o NAME,TYPE,FSTYPE,MOUNTPOINTS as recorded on a dual-boot laptop,
# byte for byte (its sha256 is _DUAL_BOOT_SHA256 below):
# the root on a LUKS volume on the second NVMe drive; on the first, another
# system's NTFS partition mounted from the file manager with no encrypted layer;
# an exFAT SD card mounted the same way; the EFI system partition at /boot/efi;
# swap in memory (zram0); the other system's own partitions not mounted.
_LSBLK_DUAL_BOOT = """{
   "blockdevices": [
      {
         "name": "mmcblk0",
         "type": "disk",
         "fstype": null,
         "mountpoints": [],
         "children": [
            {
               "name": "mmcblk0p1",
               "type": "part",
               "fstype": "exfat",
               "mountpoints": [
                   "/run/media/christopher/IGOS-SDTEST"
               ]
            }
         ]
      },{
         "name": "zram0",
         "type": "disk",
         "fstype": "swap",
         "mountpoints": [
             "[SWAP]"
         ]
      },{
         "name": "nvme0n1",
         "type": "disk",
         "fstype": null,
         "mountpoints": [],
         "children": [
            {
               "name": "nvme0n1p1",
               "type": "part",
               "fstype": "vfat",
               "mountpoints": []
            },{
               "name": "nvme0n1p2",
               "type": "part",
               "fstype": null,
               "mountpoints": []
            },{
               "name": "nvme0n1p3",
               "type": "part",
               "fstype": "ntfs",
               "mountpoints": [
                   "/run/media/christopher/OS"
               ]
            },{
               "name": "nvme0n1p4",
               "type": "part",
               "fstype": "ntfs",
               "mountpoints": []
            },{
               "name": "nvme0n1p5",
               "type": "part",
               "fstype": "ntfs",
               "mountpoints": []
            },{
               "name": "nvme0n1p6",
               "type": "part",
               "fstype": "vfat",
               "mountpoints": []
            }
         ]
      },{
         "name": "nvme1n1",
         "type": "disk",
         "fstype": null,
         "mountpoints": [],
         "children": [
            {
               "name": "nvme1n1p1",
               "type": "part",
               "fstype": "vfat",
               "mountpoints": [
                   "/boot/efi"
               ]
            },{
               "name": "nvme1n1p2",
               "type": "part",
               "fstype": "crypto_LUKS",
               "mountpoints": [],
               "children": [
                  {
                     "name": "cryptroot",
                     "type": "crypt",
                     "fstype": null,
                     "mountpoints": [
                         "/"
                     ]
                  }
               ]
            }
         ]
      }
   ]
}
"""
_DUAL_BOOT_SHA256 = "623a32e03fbb5aca0d68b74bd0a770e975198e1078f70b00e9052f45df93c814"
_DUAL_BOOT_ANSWER = (
    "The root filesystem is encrypted: it is on cryptroot, a LUKS-encrypted "
    "volume, so what is stored in it can't be read without its key while the "
    "machine is off. Also mounted, with no encrypted layer beneath them, so disk "
    "encryption does not protect what is stored on them: "
    "/run/media/christopher/IGOS-SDTEST (mmcblk0p1, exfat) and "
    "/run/media/christopher/OS (nvme0n1p3, ntfs). The EFI system partition at "
    "/boot/efi (nvme1n1p1) is not encrypted, by design: the firmware reads it to "
    "start the machine.")
# Each other kind of mount beside an encrypted root: a plain /home and a plain
# USB stick, a second LUKS volume at /srv, the EFI system partition at /efi, a
# btrfs root also mounted at /var/log, a filesystem in memory (zram1) and an ISO
# image on a loop device.
_LSBLK_MOUNT_ROLES = json.dumps({"blockdevices": [
    {"name": "loop0", "type": "loop", "fstype": "iso9660",
     "mountpoints": ["/run/media/user/INSTALLER"]},
    {"name": "zram1", "type": "disk", "fstype": "ext4", "mountpoints": ["/var/tmp"]},
    {"name": "sda", "type": "disk", "fstype": None, "mountpoints": [None],
     "children": [
         {"name": "sda1", "type": "part", "fstype": "vfat", "mountpoints": ["/efi"]},
         {"name": "sda2", "type": "part", "fstype": "crypto_LUKS",
          "mountpoints": [None],
          "children": [{"name": "cryptroot", "type": "crypt", "fstype": "btrfs",
                        "mountpoints": ["/", "/var/log"]}]},
         {"name": "sda3", "type": "part", "fstype": "ext4", "mountpoints": ["/home"]},
         {"name": "sda4", "type": "part", "fstype": "crypto_LUKS",
          "mountpoints": [None],
          "children": [{"name": "cryptsrv", "type": "crypt", "fstype": "xfs",
                        "mountpoints": ["/srv"]}]}]},
    {"name": "sdb", "type": "disk", "fstype": None, "mountpoints": [None],
     "children": [{"name": "sdb1", "type": "part", "fstype": "vfat",
                   "mountpoints": ["/run/media/user/STICK"]}]}]})

_HEADER_TCP = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
               "retrnsmt   uid  timeout inode\n")
_HEADER_UDP = ("   sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
               "retrnsmt   uid  timeout inode ref pointer drops\n")


def _sock(sl, local, remote, state, uid="0"):
    return (f"   {sl}: {local} {remote} {state} 00000000:00000000 00:00000000 "
            f"00000000 {uid:>5} 0 1000{sl} 1 0000000000000000 100 0 0 10 0\n")


# The kernel's tables: ssh on every IPv4 and IPv6 address, CUPS on loopback only,
# one established connection (not a listener), mDNS on every address, the stub
# resolver on loopback, a connected UDP socket (not a listener), and a v4-mapped
# loopback listener on IPv6.
_TCP = (_HEADER_TCP
        + _sock(0, "00000000:0016", "00000000:0000", "0A")
        + _sock(1, "0100007F:0277", "00000000:0000", "0A")
        + _sock(2, "0100007F:A1B2", "0100007F:0016", "01", uid="1000"))
_TCP6 = (_HEADER_TCP
         + _sock(0, "00000000000000000000000000000000:0016",
                 "00000000000000000000000000000000:0000", "0A")
         + _sock(1, "0000000000000000FFFF00000100007F:1F90",
                 "00000000000000000000000000000000:0000", "0A", uid="1000"))
_UDP = (_HEADER_UDP
        + _sock(0, "00000000:14E9", "00000000:0000", "07")
        + _sock(1, "3500007F:0035", "00000000:0000", "07")
        + _sock(2, "0100007F:C350", "0100007F:0035", "01", uid="1000"))
_UDP6 = _HEADER_UDP
# The services database the lookups read: system ports, and three registered
# high ports that another program may hold (8081 was the panel's embedding
# server on the 2B firing, named "sunproxyadmin" from this database).
_SERVICES = {("tcp", 22): "ssh", ("tcp", 631): "ipp", ("udp", 53): "domain",
             ("udp", 5353): "mdns", ("tcp", 8080): "http-alt",
             ("tcp", 8081): "sunproxyadmin"}


def _getservbyport(port, proto):
    try:
        return _SERVICES[(proto, port)]
    except KeyError:
        raise OSError("port/proto not found") from None


class _FakeToolResult:
    def __init__(self, content, success=True, blocked=False):
        self.content = content
        self.success = success
        self.blocked = blocked


class _FakeRegistry:
    """run_command answers from a map of {command: (content, success)}."""

    def __init__(self, names, outputs):
        self._names = list(names)
        self._outputs = outputs
        self.ran = []

    def get_all_names(self):
        return list(self._names)

    def execute(self, call, **kwargs):
        if call.name != "run_command":
            return _FakeToolResult("", success=False)
        cmd = call.arguments.get("command", "")
        self.ran.append(cmd)
        if cmd in self._outputs:
            content, ok = self._outputs[cmd]
            return _FakeToolResult(content, success=ok)
        return _FakeToolResult("", success=False)


def _outputs(*, lsblk=_LSBLK_LUKS_ROOT, active=("active\n", True),
             enabled=("enabled\n", True)):
    return {_LSBLK: (lsblk, True), _FW_ACTIVE: active, _FW_ENABLED: enabled}


def _router(outputs=None, *, web=True, model="TestModel-2B", providers=None):
    names = ["run_command"] + (["web_search"] if web else [])
    r = ConversationRouter.__new__(ConversationRouter)
    r._semantic = SemanticMatcher(embedder=None)
    r._tools = _FakeRegistry(names, outputs if outputs is not None else _outputs())
    r._conversation_history = []
    r._max_history = 20
    r._turn_index = None
    r._last_semantic_score = None
    r._current_query_type = "general"
    r._memory = None
    r._metrics = None
    r._events = None
    r._ingress_tracker = None
    r._trust_state = None
    r._review_callback = None
    r._llm = SimpleNamespace(served_model_name=lambda: model)
    r._escalation = (None if providers is None else SimpleNamespace(
        refusal=lambda: None,
        list_providers=lambda: [SimpleNamespace(name=n) for n in providers]))
    return r


def _ask(r, text):
    normalized = r._semantic._normalize_input(text)
    return r._try_direct_answer(normalized, text, 0.0)


class _MachineFixture(unittest.TestCase):
    """Temporary stand-ins for every file the posture probes read."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.efi = self.tmp / "efi"
        (self.efi / "efivars").mkdir(parents=True)
        self.set_efi(secure_boot=1, setup_mode=0)
        self.net = self.tmp / "net"
        self.net.mkdir()
        for name, body in (("tcp", _TCP), ("tcp6", _TCP6), ("udp", _UDP),
                           ("udp6", _UDP6)):
            (self.net / name).write_text(body)
        self.aa_enabled = self.tmp / "apparmor-enabled"
        self.aa_enabled.write_text("Y\n")
        self.lsm = self.tmp / "lsm"
        self.lsm.write_text("lockdown,capability,landlock,apparmor,bpf\n")
        self.advisory = self.tmp / "available-updates.json"
        self.state = self.tmp / "state"
        self.state.mkdir()
        os.environ["XDG_STATE_HOME"] = str(self.state)
        patches = [
            mock.patch.object(router, "_EFI_DIR", self.efi),
            mock.patch.object(router, "_PROC_NET", self.net),
            mock.patch.object(router, "_APPARMOR_ENABLED", self.aa_enabled),
            mock.patch.object(router, "_KERNEL_LSM", self.lsm),
            mock.patch.object(router, "_PKM_ADVISORY", self.advisory),
            mock.patch.object(router.socket, "getservbyport", _getservbyport),
            mock.patch.object(router.shutil, "which",
                              lambda name: f"/usr/bin/{name}"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        router._readonly_state_requires.cache_clear()
        self.addCleanup(router._readonly_state_requires.cache_clear)

    def set_efi(self, *, secure_boot=None, setup_mode=None):
        for name, value in (("SecureBoot", secure_boot), ("SetupMode", setup_mode)):
            path = self.efi / "efivars" / f"{name}-{_GUID}"
            if value is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(b"\x06\x00\x00\x00" + bytes([value]))

    def write_advisory(self, count, names=(), skipped=0, checked_at=1792540800):
        self.advisory.write_text(json.dumps({
            "timestamp": "2026-10-20T07:20:00Z", "checked_at": checked_at,
            "count": count,
            "packages": [{"name": n, "installed_version": "1.0",
                          "installed_release": 1, "remote_version": "1.1",
                          "remote_release": 1} for n in names],
            "skipped_count": skipped,
            "skipped": [{"name": f"p{i}", "reason": "x"} for i in range(skipped)]}))


# ── one RED/GREEN per class: the class-8 phrasing answers from the machine ──
class EachClassAnswersFromTheMachine(_MachineFixture):

    def assert_direct(self, text, *needles):
        res = _ask(_router(), text)
        self.assertIsNotNone(res, f"{text!r} was not answered from the machine")
        self.assertEqual(res.source, "direct_answer")
        for needle in needles:
            self.assertIn(needle, res.text)
        return res

    def test_secure_boot(self):
        self.assert_direct("is Secure Boot on?", "Secure Boot is on")

    def test_encrypted_root(self):
        self.assert_direct("is my disk encrypted?",
                           "The root filesystem is encrypted", "cryptroot", "LUKS")

    def test_firewall(self):
        for text in ("is the firewall on?", "is the firewall running?"):
            with self.subTest(text=text):
                self.assert_direct(text, "The firewall is on", "nftables")

    def test_listening_sockets(self):
        for text in ("any open ports?", "what's listening on the network?"):
            with self.subTest(text=text):
                self.assert_direct(text, "TCP 22 (ssh)", "TCP 631 (ipp)")

    def test_apparmor(self):
        self.assert_direct("is AppArmor enabled?", "AppArmor is on")

    def test_pending_updates(self):
        self.write_advisory(2, names=("firefox", "openssl"))
        self.assert_direct("are there any pending updates?",
                           "2 updates were pending", "firefox", "openssl")

    def test_the_whole_posture(self):
        self.write_advisory(0)
        res = self.assert_direct("is this machine secure?",
                                 "Secure Boot is on", "encrypted", "The firewall is on",
                                 "TCP 22 (ssh)", "AppArmor is on",
                                 "No updates were pending")
        self.assertNotIn("is secure", res.text.lower().replace("secure boot", ""))
        # Every line is a stated fact; none answers a yes-or-no nobody asked.
        for line in res.text.splitlines()[1:]:
            self.assertFalse(line.startswith(("- Yes", "- No —")), line)


# ── the readings, each state and each failure ───────────────────────────────
class SecureBootReadings(_MachineFixture):

    def answer(self):
        return router._posture_secure_boot()

    def test_on(self):
        text = self.answer()
        self.assertIn("Secure Boot is on", text)
        # The variable shows what the firmware checks, the boot loader; what the
        # boot loader checks in turn is not read, so the kernel is not claimed.
        self.assertIn("boot loader signed with a key it trusts", text)
        self.assertNotIn("kernel", text)

    def test_off(self):
        self.set_efi(secure_boot=0, setup_mode=0)
        self.assertIn("Secure Boot is off", self.answer())

    def test_setup_mode(self):
        self.set_efi(secure_boot=0, setup_mode=1)
        self.assertIn("setup mode", self.answer())

    def test_legacy_boot(self):
        with mock.patch.object(router, "_EFI_DIR", self.tmp / "no-efi"):
            self.assertIn("legacy BIOS", self.answer())

    def test_firmware_without_the_variable(self):
        (self.efi / "efivars" / f"BootOrder-{_GUID}").write_bytes(b"\x07\x00\x00\x00\x01\x00")
        self.set_efi(secure_boot=None, setup_mode=None)
        self.assertIn("doesn't report Secure Boot", self.answer())

    def test_no_variable_visible_is_no_reading(self):
        # efivarfs not mounted, or not visible to the daemon: the directory is
        # empty, the reading cannot be taken, and "not in use" would be a guess.
        self.set_efi(secure_boot=None, setup_mode=None)
        self.assertIsNone(self.answer())
        self.assertIsNone(_ask(_router(), "is Secure Boot on?"))

    def test_a_short_variable_is_no_reading(self):
        (self.efi / "efivars" / f"SecureBoot-{_GUID}").write_bytes(b"\x06\x00")
        self.assertIsNone(self.answer())


class EncryptedRootReadings(unittest.TestCase):

    def render(self, out):
        return router._posture_render_encrypted_root(out)

    def test_luks_root(self):
        text = self.render(_LSBLK_LUKS_ROOT)
        self.assertTrue(text.startswith("The root filesystem is encrypted"))
        self.assertIn("cryptroot", text)
        self.assertIn("LUKS", text)
        # Only the path to / is read: the sentence speaks for the root
        # filesystem, never the whole disk (the EFI system partition is plain).
        self.assertIn("what is stored in it", text)
        self.assertNotIn("disk", text)

    def test_lvm_on_luks_root(self):
        text = self.render(_LSBLK_LVM_ON_LUKS)
        self.assertTrue(text.startswith("The root filesystem is encrypted"))
        self.assertIn("luks-sda2", text)

    def test_plain_root(self):
        text = self.render(_LSBLK_PLAIN_ROOT)
        self.assertTrue(text.startswith("The root filesystem is not encrypted"))
        self.assertIn("sda2", text)

    def test_no_root_or_no_json_is_no_reading(self):
        self.assertIsNone(self.render("not json"))
        self.assertIsNone(self.render(json.dumps({"blockdevices": []})))


class EveryMountedFilesystemIsNamed(_MachineFixture):
    """"is my disk encrypted?" is about every drive mounted, not the root alone:
    beside an encrypted root, a partition or a card can be mounted with no
    encrypted layer at all, and naming the root only is true and partial."""

    def render(self, out):
        return router._posture_render_encrypted_root(out)

    def test_the_recorded_reading_is_the_one_taken(self):
        self.assertEqual(hashlib.sha256(_LSBLK_DUAL_BOOT.encode()).hexdigest(),
                         _DUAL_BOOT_SHA256)

    def test_the_plain_filesystems_beside_an_encrypted_root_are_named(self):
        self.assertEqual(self.render(_LSBLK_DUAL_BOOT), _DUAL_BOOT_ANSWER)

    def test_every_phrasing_gets_the_whole_answer(self):
        r = _router(_outputs(lsblk=_LSBLK_DUAL_BOOT))
        for text in ("is my disk encrypted?", "is the root filesystem encrypted",
                     "do I have full disk encryption"):
            with self.subTest(text=text):
                res = _ask(r, text)
                self.assertIsNotNone(res, f"{text!r} was not answered from the machine")
                self.assertEqual(res.source, "direct_answer")
                self.assertEqual(res.text, _DUAL_BOOT_ANSWER)

    def test_the_posture_list_carries_it_on_one_line(self):
        self.write_advisory(0)
        res = _ask(_router(_outputs(lsblk=_LSBLK_DUAL_BOOT)), "is this machine secure?")
        self.assertIsNotNone(res)
        self.assertIn(f"- {_DUAL_BOOT_ANSWER}", res.text.splitlines())

    def test_swap_in_memory_is_not_counted(self):
        # The absence carries its control: the member is in the reading, and a
        # plain member of the same reading is named.
        self.assertIn('"name": "zram0"', _LSBLK_DUAL_BOOT)
        self.assertIn('"[SWAP]"', _LSBLK_DUAL_BOOT)
        text = self.render(_LSBLK_DUAL_BOOT)
        self.assertNotIn("zram0", text)
        self.assertNotIn("SWAP", text)
        self.assertIn("/run/media/christopher/OS (nvme0n1p3, ntfs)", text)

    def test_each_kind_of_mount_by_its_role(self):
        # The plain ones are named, the encrypted one is named as such, the EFI
        # system partition by its role; the root's own second mount point, the
        # filesystem in memory and the image on a loop device are not counted,
        # though each is in the reading.
        for member in ('"loop0"', '"zram1"', '"/var/log"', '"/var/tmp"',
                       '"/run/media/user/INSTALLER"'):
            self.assertIn(member, _LSBLK_MOUNT_ROLES)
        text = self.render(_LSBLK_MOUNT_ROLES)
        for absent in ("loop0", "zram1", "/var/log", "/var/tmp", "INSTALLER"):
            self.assertNotIn(absent, text)
        self.assertEqual(text, (
            "The root filesystem is encrypted: it is on cryptroot, a LUKS-encrypted "
            "volume, so what is stored in it can't be read without its key while "
            "the machine is off. Also mounted, with no encrypted layer beneath them, "
            "so disk encryption does not protect what is stored on them: /home "
            "(sda3, ext4) and /run/media/user/STICK (sdb1, vfat). Also mounted on an "
            "encrypted layer: /srv (on cryptsrv). The EFI system partition at /efi "
            "(sda1) is not encrypted, by design: the firmware reads it to start the "
            "machine."))

    def test_one_plain_filesystem_beside_a_plain_root(self):
        tree = json.loads(_LSBLK_PLAIN_ROOT)
        tree["blockdevices"][0]["children"].append(
            {"name": "sda3", "type": "part", "fstype": "ext4",
             "mountpoints": ["/home"]})
        self.assertEqual(self.render(json.dumps(tree)), (
            "The root filesystem is not encrypted: it is on sda2 (part), with no "
            "encrypted layer beneath it. Also mounted, with no encrypted layer "
            "beneath it, so disk encryption does not protect what is stored on it: "
            "/home (sda3, ext4). The EFI system partition at /boot/efi (sda1) is not "
            "encrypted, by design: the firmware reads it to start the machine."))

    def test_nothing_else_mounted_is_said(self):
        self.assertTrue(self.render(_LSBLK_LUKS_ROOT).endswith(
            "while the machine is off. The EFI system partition at /boot/efi "
            "(nvme0n1p1) is not encrypted, by design: the firmware reads it to start "
            "the machine. Nothing else is mounted from a drive."))
        self.assertTrue(self.render(_LSBLK_LVM_ON_LUKS).endswith(
            "while the machine is off. Nothing else is mounted from a drive."))


class FirewallReadings(unittest.TestCase):

    def render(self, active, enabled):
        return router._posture_render_firewall(active, enabled)

    def test_on_and_set_for_boot(self):
        text = self.render("active\n", "enabled\n")
        self.assertIn("The firewall is on", text)
        self.assertIn("every start", text)
        self.assertIn("administrator rights", text)

    def test_on_but_not_set_for_boot(self):
        text = self.render("active\n", "disabled\n\n[exit code: 1]")
        self.assertIn("The firewall is on", text)
        self.assertIn("not set to start at boot", text)

    def test_off(self):
        text = self.render("inactive\n\n[exit code: 3]", "disabled\n\n[exit code: 1]")
        self.assertIn("The firewall is off", text)

    def test_failed(self):
        self.assertIn("failed", self.render("failed\n\n[exit code: 3]", "enabled\n"))

    def test_not_installed(self):
        text = self.render("inactive\n\n[exit code: 4]", "not-found\n\n[exit code: 4]")
        self.assertIn("no firewall service", text.lower())

    def test_an_unreadable_state_is_no_reading(self):
        self.assertIsNone(self.render("", "enabled\n"))
        self.assertIsNone(self.render("Command timed out after 30 seconds", "x"))

    def test_an_inactive_firewall_still_answers_through_the_router(self):
        r = _router(_outputs(active=("inactive\n\n[exit code: 3]", False),
                             enabled=("disabled\n\n[exit code: 1]", False)))
        with mock.patch.object(router.shutil, "which", lambda n: f"/usr/bin/{n}"):
            res = _ask(r, "is the firewall on?")
        self.assertIsNotNone(res)
        self.assertIn("The firewall is off", res.text)


class ListeningReadings(_MachineFixture):

    def test_reach_is_split_by_where_the_socket_listens(self):
        text = router._posture_render_listeners(router._read_listeners())
        reachable, _, local = text.partition("only from this machine")
        self.assertIn("TCP 22 (ssh)", reachable)
        self.assertIn("UDP 5353", reachable)
        self.assertIn("TCP 631 (ipp)", local)
        self.assertIn("8080", local)               # the v4-mapped loopback listener
        self.assertIn("UDP 53 (domain)", local)
        self.assertNotIn("41394", text)           # the established connection
        self.assertNotIn("50000", text)           # the connected UDP socket

    def test_only_a_system_port_is_named(self):
        # Any program may hold a port above 1023, so the name the database
        # registers for it may be another program's: 8081 is not
        # "sunproxyadmin" on a machine where the panel's embedding server
        # holds it.
        self.assertEqual(router._service_name(22, "tcp"), "ssh")
        self.assertEqual(router._service_name(53, "udp"), "domain")
        for port, proto in ((8081, "tcp"), (8080, "tcp"), (5353, "udp")):
            with self.subTest(port=port):
                self.assertIsNone(router._service_name(port, proto))
        text = router._posture_render_listeners(router._read_listeners())
        for name in ("(mdns)", "(http-alt)", "(sunproxyadmin)"):
            self.assertNotIn(name, text)

    def test_nothing_reachable(self):
        (self.net / "tcp").write_text(_HEADER_TCP + _sock(0, "0100007F:0277",
                                                          "00000000:0000", "0A"))
        for name in ("tcp6", "udp", "udp6"):
            (self.net / name).write_text(_HEADER_TCP)
        text = router._posture_render_listeners(router._read_listeners())
        self.assertIn("No port accepts connections from other machines", text)

    def test_unreadable_tables_are_no_reading(self):
        with mock.patch.object(router, "_PROC_NET", self.tmp / "no-net"):
            self.assertIsNone(router._read_listeners())


class AppArmorReadings(_MachineFixture):

    def test_on(self):
        self.assertIn("AppArmor is on", router._posture_apparmor())

    def test_off(self):
        self.aa_enabled.write_text("N\n")
        self.lsm.write_text("lockdown,capability,landlock,bpf\n")
        self.assertIn("AppArmor is off", router._posture_apparmor())

    def test_not_in_this_kernel(self):
        self.aa_enabled.unlink()
        self.lsm.write_text("lockdown,capability,landlock,bpf\n")
        self.assertIn("does not include AppArmor", router._posture_apparmor())


class PendingUpdateReadings(_MachineFixture):

    def test_no_check_yet(self):
        text = router._posture_pending_updates()
        self.assertIn("no update check has run", text)

    def test_none_pending(self):
        self.write_advisory(0)
        text = router._posture_pending_updates()
        self.assertIn("No updates were pending", text)
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(1792540800))
        self.assertIn(stamp, text)
        # The check is offline, against the index as last synced, which the
        # advisory does not date: none pending is not "up to date".
        self.assertIn("package index as last synced", text)
        self.assertIn("sudo pkm sync fetches the current index", text)

    def test_some_pending_and_some_unchecked(self):
        names = [f"pkg{i}" for i in range(12)]
        self.write_advisory(12, names=names, skipped=2)
        text = router._posture_pending_updates()
        self.assertIn("12 updates were pending", text)
        self.assertIn("pkg0", text)
        self.assertIn("and 4 more", text)
        self.assertIn("2 installed packages could not be compared", text)
        self.assertIn("package index as last synced", text)

    def test_a_broken_advisory_is_no_reading(self):
        self.advisory.write_text("{not json")
        self.assertIsNone(router._posture_pending_updates())


# ── what must NOT be answered with a state reading ──────────────────────────
class TeachingAndInstructionsAreLeftAlone(_MachineFixture):

    def test_how_to_asks_still_teach(self):
        for text in ("how do I turn on the firewall?",
                     "how do I check if Secure Boot is enabled?",
                     "how do I encrypt my disk?", "what is a firewall?",
                     "how do I see which ports are open?"):
            with self.subTest(text=text):
                self.assertIsNone(_ask(_router(), text))

    def test_instructions_are_not_answered_with_a_reading(self):
        for text in ("disable Secure Boot", "close the open ports",
                     "turn off the firewall", "update the system",
                     "can you close any open ports?"):
            with self.subTest(text=text):
                self.assertIsNone(_ask(_router(), text))


# ── the state map carries every class, and every example routes to it ───────
class TheStateMapCarriesEveryClass(_MachineFixture):

    CLASSES = {
        "secure-boot": "Secure Boot is on",
        "encrypted-root": "the root filesystem is encrypted",
        "firewall": "The firewall is on",
        "listening-sockets": "TCP 22 (ssh)",
        "apparmor": "AppArmor is on",
        "pending-updates": "updates were pending",
    }

    def test_every_class_is_mapped_and_answers_its_examples(self):
        self.write_advisory(2, names=("firefox", "openssl"))
        data = json.loads(router._READONLY_STATE_MAP_PATH.read_text())
        by_id = {c["class"]: c for c in data["classes"]}
        for cid, needle in self.CLASSES.items():
            with self.subTest(cls=cid):
                self.assertIn(cid, by_id)
                cls = by_id[cid]
                self.assertTrue(cls["read_only"])
                self.assertTrue(cls["question_examples"])
                for example in cls["question_examples"]:
                    res = _ask(_router(), example)
                    self.assertIsNotNone(res, f"{cid}: {example!r} not answered")
                    self.assertIn(needle.lower(), res.text.lower(), example)


if __name__ == "__main__":
    unittest.main()
