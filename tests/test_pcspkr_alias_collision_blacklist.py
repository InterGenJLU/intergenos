# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""One driver claims the platform:pcspkr alias, and the image says which.

WHY THIS EXISTS. Every boot of the shipped system logs

    input: PC Speaker as /devices/platform/pcspkr/input/input17
    Error: Driver 'pcspkr' is already registered, aborting...

with no module named in the second line, deterministically, on all three boots
examined of the R001.1 install (2026-08-22 onward). It is not a double load of
the PC-speaker input driver. The kernel this image ships builds TWO drivers
that both bind the same platform device and both register under the same bare
platform-driver name `pcspkr`:

    CONFIG_INPUT_PCSPKR=m  ->  pcspkr    (input/misc: the console bell)
    CONFIG_SND_PCSP=m      ->  snd_pcsp  (sound/drivers/pcsp: an ALSA card
                                          that plays through the PC speaker)

so the module index maps one alias to both:

    modules.alias:19246  alias platform:pcspkr pcspkr
    modules.alias:32851  alias platform:pcspkr snd_pcsp

udev requests the alias once; kmod resolves it to both modules and loads them
in index order. pcspkr binds first (7.086s), snd_pcsp's registration is
refused at 7.714s, and snd_pcsp never appears in lsmod. Two drivers cannot
share a platform-driver name, so this is a choice the image has to make
explicitly rather than leave to whichever module the index lists first.

WHAT WAS CHOSEN, AND WHY. snd_pcsp is blacklisted; pcspkr keeps the alias.
pcspkr is the driver that already wins, it is what the input subsystem and the
console bell expect, and it is the one that works. snd_pcsp would publish the
PC speaker as an ALSA sound card - a square-wave beeper appearing in the sound
device list beside the real codec, where it can be selected as an output. It
also drags soundcore, snd, snd-timer and snd-pcm in behind it on every boot,
for a card that then fails to register. `blacklist` rather than
`install ... /bin/false` is deliberate: it suppresses only the ALIAS-driven
autoload, which is the whole mechanism here, and leaves a user who wants the
ALSA beeper able to unload pcspkr and `modprobe snd_pcsp` by name.

WHAT THIS MEASURES. Both halves, with nothing reimplemented:
  * the premise, from the tree - the shipped kernel config fragment still
    builds both drivers as modules, so the collision is still real;
  * the resolution, from kmod itself - the real modprobe, resolving the real
    alias against the running kernel's real module index, with and without the
    shipped blacklist file. The without-case is not decoration: a check that
    only ever saw the blacklisted resolution could not tell a working
    blacklist from a kernel that never had the collision.

The firings are `--dry-run --show-depends`. Nothing is loaded, nothing on the
host is written, and no test here needs privilege.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_FILES = REPO_ROOT / "packages" / "core" / "intergenos-base-files" / "files"
PAYLOAD = BASE_FILES / "etc" / "modprobe.d" / "pcspkr-alias-owner.conf"
KERNEL_FRAGMENTS = REPO_ROOT / "config" / "kernel" / "fragments"

ALIAS = "platform:pcspkr"
KEEPS_THE_ALIAS = "pcspkr"
BLACKLISTED = "snd_pcsp"


def _modprobe(config_dir, alias):
    """Resolve an alias with the real kmod, loading nothing."""
    return subprocess.run(
        ["modprobe", "--dry-run", "--show-depends",
         "--config", str(config_dir), alias],
        capture_output=True, text=True, timeout=120,
    )


def _modules_named(stdout):
    """The module basenames kmod said it would insmod, in order."""
    names = []
    for line in stdout.splitlines():
        if not line.startswith("insmod "):
            continue
        leaf = line.split()[1].rsplit("/", 1)[-1]
        for suffix in (".ko.gz", ".ko.xz", ".ko.zst", ".ko"):
            if leaf.endswith(suffix):
                leaf = leaf[: -len(suffix)]
                break
        names.append(leaf.replace("-", "_"))
    return names


class PcspkrAliasOwner(unittest.TestCase):

    # ---------- the premise, read from the tree ----------

    def test_the_shipped_kernel_still_builds_both_drivers(self):
        fragments = "\n".join(p.read_text() for p in
                              sorted(KERNEL_FRAGMENTS.glob("*.config")))
        self.assertIn("CONFIG_INPUT_PCSPKR=m", fragments,
                      "the console-bell driver is no longer a module")
        self.assertIn("CONFIG_SND_PCSP=m", fragments,
                      "the ALSA PC-speaker driver is no longer a module — if it "
                      "was dropped from the kernel config, the blacklist this "
                      "test guards is no longer needed and should go with it")

    # ---------- the shipped payload ----------

    def test_the_payload_ships(self):
        self.assertTrue(PAYLOAD.is_file(),
                        f"{PAYLOAD.relative_to(REPO_ROOT)} is not in the package")

    def test_the_payload_blacklists_only_the_sound_driver(self):
        directives = [l.split() for l in PAYLOAD.read_text().splitlines()
                      if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(
            directives, [["blacklist", BLACKLISTED]],
            "the file must carry exactly one directive: blacklist the ALSA "
            "PC-speaker driver. Blacklisting the console-bell driver instead "
            "would hand the alias to the driver that cannot keep it, and any "
            "extra directive here is doing something this file does not say.")

    def test_the_payload_explains_itself(self):
        """A user who finds this file must be able to undo it knowingly."""
        text = PAYLOAD.read_text()
        for token in (ALIAS, KEEPS_THE_ALIAS, "already registered"):
            self.assertIn(token, text,
                          f"the file does not mention {token!r}, so it does not "
                          "record which alias is contested or how the conflict "
                          "shows up")

    # ---------- the resolution, measured with kmod ----------

    def _config_dirs(self, want_payload=True):
        """A config dir without the shipped file, and one with it.

        Built per test rather than in setUp so a missing payload fails the
        tests that are ABOUT the payload, instead of erroring out of the
        premise checks above and hiding what they had to say.
        """
        self.assertIsNotNone(shutil.which("modprobe"),
                             "modprobe absent: this suite cannot measure alias "
                             "resolution and must not report the collision gone")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = Path(tmp.name) / "no-blacklist"
        empty.mkdir()
        if not want_payload:
            return empty, None
        shipped = Path(tmp.name) / "with-blacklist"
        shipped.mkdir()
        shutil.copy2(PAYLOAD, shipped / PAYLOAD.name)
        return empty, shipped

    def test_the_collision_is_real_on_the_running_kernel(self):
        """Without the file, one alias request pulls in both drivers."""
        empty, _ = self._config_dirs(want_payload=False)
        proc = _modprobe(empty, ALIAS)
        self.assertEqual(proc.returncode, 0,
                         f"kmod could not resolve {ALIAS}: {proc.stderr}")
        loaded = _modules_named(proc.stdout)
        self.assertIn(KEEPS_THE_ALIAS, loaded, loaded)
        self.assertIn(BLACKLISTED, loaded,
                      "this kernel's module index does not map the alias to "
                      "both drivers, so this instrument cannot show the "
                      "blacklist working; do not read the next test as proof")

    def test_the_blacklist_leaves_one_owner(self):
        _, shipped = self._config_dirs()
        proc = _modprobe(shipped, ALIAS)
        self.assertEqual(proc.returncode, 0,
                         f"kmod could not resolve {ALIAS}: {proc.stderr}")
        loaded = _modules_named(proc.stdout)
        self.assertEqual(
            loaded, [KEEPS_THE_ALIAS],
            f"{ALIAS} still resolves to more than the console-bell driver: "
            f"{loaded}")

    def test_the_blacklist_does_not_block_loading_it_by_name(self):
        """The user keeps the machine: an explicit request still resolves."""
        _, shipped = self._config_dirs()
        proc = _modprobe(shipped, BLACKLISTED)
        self.assertEqual(proc.returncode, 0,
                         f"an explicit request for {BLACKLISTED} was refused, so "
                         "the file takes the choice away from the user: "
                         f"{proc.stderr}")
        self.assertIn(BLACKLISTED, _modules_named(proc.stdout))


if __name__ == "__main__":
    unittest.main()
