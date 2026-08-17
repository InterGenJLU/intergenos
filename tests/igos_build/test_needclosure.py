"""Wedge tests for the post-eviction NEEDED-closure + word-size backstop
(RT-6, GE gate re-site).

Synthetic dynamic ELFs (both widths) exercise the stdlib parser; a readelf
cross-check on a real system binary pins the parser against the reference
implementation where readelf exists; the closure cases are the
red-before/green-after wedges: an evicted provider REFUSES the sweep,
restoring it passes the identical chroot.
"""

import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

import needclosure  # noqa: E402


def make_elf(ei_class=needclosure.ELFCLASS64, needed=(), runpath=None,
             dynamic=True):
    """Craft a minimal parseable ELF with a real PT_DYNAMIC + strtab."""
    is64 = ei_class == needclosure.ELFCLASS64
    en = "<"
    ehsize = 64 if is64 else 52
    phentsize = 56 if is64 else 32
    phnum = 2 if dynamic else 0
    phoff = ehsize
    dyn_off = phoff + phnum * phentsize

    # string table: \0 + each needed + runpath
    strtab = bytearray(b"\x00")
    offs = {}
    for s in list(needed) + ([runpath] if runpath else []):
        offs[s] = len(strtab)
        strtab += s.encode() + b"\x00"

    # dynamic entries
    entsize = 16 if is64 else 8
    fmt = f"{en}qQ" if is64 else f"{en}iI"
    dyn = bytearray()
    if dynamic:
        for s in needed:
            dyn += struct.pack(fmt, needclosure.DT_NEEDED, offs[s])
        if runpath:
            dyn += struct.pack(fmt, needclosure.DT_RUNPATH, offs[runpath])
        # DT_STRTAB vaddr filled below (identity mapping vaddr == offset)
        strtab_off = dyn_off + (len(dyn) + 2 * entsize)  # + strtab + null
        dyn += struct.pack(fmt, needclosure.DT_STRTAB, strtab_off)
        dyn += struct.pack(fmt, needclosure.DT_NULL, 0)
    total = dyn_off + len(dyn) + len(strtab)

    blob = bytearray(total)
    blob[0:4] = needclosure.ELF_MAGIC
    blob[4] = ei_class
    blob[5] = 1  # little-endian
    if is64:
        struct.pack_into(f"{en}Q", blob, 0x20, phoff)
        struct.pack_into(f"{en}HH", blob, 0x36, phentsize, phnum)
    else:
        struct.pack_into(f"{en}I", blob, 0x1C, phoff)
        struct.pack_into(f"{en}HH", blob, 0x2A, phentsize, phnum)

    if dynamic:
        def phdr(idx, p_type, p_offset, p_vaddr, p_filesz):
            base = phoff + idx * phentsize
            struct.pack_into(f"{en}I", blob, base, p_type)
            if is64:
                struct.pack_into(f"{en}QQ", blob, base + 0x08, p_offset, p_vaddr)
                struct.pack_into(f"{en}Q", blob, base + 0x20, p_filesz)
            else:
                struct.pack_into(f"{en}II", blob, base + 0x04, p_offset, p_vaddr)
                struct.pack_into(f"{en}I", blob, base + 0x10, p_filesz)

        phdr(0, needclosure.PT_LOAD, 0, 0, total)          # identity mapping
        phdr(1, needclosure.PT_DYNAMIC, dyn_off, dyn_off, len(dyn))
        blob[dyn_off:dyn_off + len(dyn)] = dyn
        blob[dyn_off + len(dyn):total] = strtab
    return bytes(blob)


class TestParseElf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, blob):
        p = self.root / name
        p.write_bytes(blob)
        return p

    def test_parse_64_needed_and_runpath(self):
        p = self._write("a", make_elf(needed=["libz.so.1", "libc.so.6"],
                                      runpath="$ORIGIN/lib:/opt/x/lib"))
        info = needclosure.parse_elf(p)
        self.assertTrue(info.is_dynamic)
        self.assertEqual(info.ei_class, needclosure.ELFCLASS64)
        self.assertEqual(info.needed, ["libz.so.1", "libc.so.6"])
        self.assertEqual(info.runpaths, ["$ORIGIN/lib", "/opt/x/lib"])

    def test_parse_32(self):
        p = self._write("b", make_elf(ei_class=needclosure.ELFCLASS32,
                                      needed=["libc.so.6"]))
        info = needclosure.parse_elf(p)
        self.assertEqual(info.ei_class, needclosure.ELFCLASS32)
        self.assertEqual(info.needed, ["libc.so.6"])

    def test_static_elf_not_dynamic(self):
        p = self._write("c", make_elf(dynamic=False))
        info = needclosure.parse_elf(p)
        self.assertFalse(info.is_dynamic)
        self.assertEqual(info.needed, [])

    def test_non_elf_none(self):
        p = self._write("d", b"x" * 100)
        self.assertIsNone(needclosure.parse_elf(p))

    def test_truncated_elf_fails_loud(self):
        blob = bytearray(make_elf(needed=["libc.so.6"]))
        p = self._write("e", bytes(blob[:70]))  # phdrs cut off
        with self.assertRaises(ValueError):
            needclosure.parse_elf(p)

    @unittest.skipUnless(shutil.which("readelf") and Path("/bin/ls").exists(),
                         "readelf or /bin/ls not present")
    def test_readelf_cross_check_real_binary(self):
        info = needclosure.parse_elf(Path("/bin/ls"))
        out = subprocess.run(["readelf", "-d", "/bin/ls"],
                             capture_output=True, text=True).stdout
        ref = {line.split("[")[1].rstrip("]\n ")
               for line in out.splitlines() if "(NEEDED)" in line}
        self.assertEqual(set(info.needed), ref)


class TestAuditChroot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.chroot = Path(self.tmp.name)
        (self.chroot / "usr/lib").mkdir(parents=True)
        (self.chroot / "usr/bin").mkdir(parents=True)
        # a resolvable provider in the default search path
        (self.chroot / "usr/lib/libz.so.1").write_bytes(make_elf(dynamic=False))
        # a dynamic consumer needing it
        (self.chroot / "usr/bin/tool").write_bytes(
            make_elf(needed=["libz.so.1", "linux-vdso.so.1"]))

    def tearDown(self):
        self.tmp.cleanup()

    def test_green_closure_resolves(self):
        violations, n = needclosure.audit_chroot(self.chroot)
        self.assertEqual(violations, [])
        self.assertEqual(n, 1)

    def test_red_evicted_provider_then_green_restored(self):
        provider = self.chroot / "usr/lib/libz.so.1"
        blob = provider.read_bytes()
        provider.unlink()  # the eviction
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(len(violations), 1)
        self.assertIn("libz.so.1", violations[0])
        self.assertIn("no same-class", violations[0])
        provider.write_bytes(blob)  # restore -> identical chroot passes
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(violations, [])

    def test_red_class_mismatch_provider(self):
        # 32-bit consumer under an allowed prefix; provider exists but 64-bit
        lib32 = self.chroot / "usr/lib32"
        lib32.mkdir()
        (lib32 / "consumer32").write_bytes(
            make_elf(ei_class=needclosure.ELFCLASS32, needed=["libz.so.1"]))
        violations, _ = needclosure.audit_chroot(
            self.chroot, allow32_prefixes=("/usr/lib32",))
        self.assertEqual(len(violations), 1)
        self.assertIn("32-bit", violations[0])
        # add the 32-bit provider in the same search dir -> green
        (lib32 / "libz.so.1").write_bytes(
            make_elf(ei_class=needclosure.ELFCLASS32, dynamic=False))
        violations, _ = needclosure.audit_chroot(
            self.chroot, allow32_prefixes=("/usr/lib32",))
        self.assertEqual(violations, [])

    def test_red_class32_outside_allowed_prefix(self):
        (self.chroot / "usr/bin/rogue32").write_bytes(
            make_elf(ei_class=needclosure.ELFCLASS32, dynamic=False))
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(len(violations), 1)
        self.assertIn("ELFCLASS32 on the ISO", violations[0])
        violations, _ = needclosure.audit_chroot(
            self.chroot, allow32_prefixes=("/usr/bin",))
        self.assertEqual(violations, [])

    def test_origin_runpath_resolves(self):
        app = self.chroot / "opt/app"
        (app / "lib").mkdir(parents=True)
        (app / "lib/libpriv.so.1").write_bytes(make_elf(dynamic=False))
        (app / "run").write_bytes(
            make_elf(needed=["libpriv.so.1"], runpath="$ORIGIN/lib"))
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(violations, [])

    def test_ld_so_conf_dir_resolves(self):
        vend = self.chroot / "opt/vendor/lib"
        vend.mkdir(parents=True)
        (vend / "libvend.so.9").write_bytes(make_elf(dynamic=False))
        (self.chroot / "usr/bin/vtool").write_bytes(
            make_elf(needed=["libvend.so.9"]))
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(len(violations), 1)  # not in search path yet
        (self.chroot / "etc").mkdir()
        (self.chroot / "etc/ld.so.conf").write_text("/opt/vendor/lib\n")
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(violations, [])

    def test_symlink_provider_counts(self):
        (self.chroot / "usr/lib/libreal.so.1.2.3").write_bytes(
            make_elf(dynamic=False))
        (self.chroot / "usr/lib/libreal.so.1").symlink_to("libreal.so.1.2.3")
        (self.chroot / "usr/bin/user").write_bytes(
            make_elf(needed=["libreal.so.1"]))
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(violations, [])

    def test_app_root_private_provider_resolves(self):
        # the samba/libreoffice convention: a private lib under the app's
        # own tree, reachable at runtime via loader-chain semantics the
        # per-object model cannot see. The provider SHIPS -> resolves.
        app = self.chroot / "usr/lib/bigapp/program/intl"
        app.mkdir(parents=True)
        (self.chroot / "usr/lib/bigapp/program/libpriv-client.so.2").write_bytes(
            make_elf(dynamic=False))
        (app / "libintlpart.so").write_bytes(
            make_elf(needed=["libpriv-client.so.2"]))
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(violations, [])

    def test_app_root_does_not_resolve_across_apps(self):
        # a provider in an UNRELATED app's tree must NOT resolve — that
        # would mask a genuine eviction.
        (self.chroot / "usr/lib/otherapp").mkdir(parents=True)
        (self.chroot / "usr/lib/otherapp/libelsewhere.so.1").write_bytes(
            make_elf(dynamic=False))
        (self.chroot / "usr/lib/consumerapp").mkdir(parents=True)
        (self.chroot / "usr/lib/consumerapp/needy.so").write_bytes(
            make_elf(needed=["libelsewhere.so.1"]))
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(len(violations), 1)
        self.assertIn("libelsewhere.so.1", violations[0])

    def test_app_root_symlink_provider_resolves(self):
        app = self.chroot / "opt/vendorapp"
        app.mkdir(parents=True)
        (app / "libreal.so.5.1").write_bytes(make_elf(dynamic=False))
        (app / "libreal.so.5").symlink_to("libreal.so.5.1")
        (app / "tool").write_bytes(make_elf(needed=["libreal.so.5"]))
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(violations, [])

    def test_firmware_subtree_excluded(self):
        # device firmware images can be ELF for the DEVICE's own processor
        # (Qualcomm bluetooth .mbn is 32-bit for the QCA chip) — host width
        # semantics are a category error there; never audited.
        fw = self.chroot / "usr/lib/firmware/qca"
        fw.mkdir(parents=True)
        (fw / "devicefw.mbn").write_bytes(
            make_elf(ei_class=needclosure.ELFCLASS32, dynamic=False))
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(violations, [])

    def test_testdata_dirs_pruned_anywhere(self):
        # the Go-ecosystem fixture convention: foreign-arch ELF objects
        # under any testdata/ dir are test INPUTS, never runtime artifacts.
        td = self.chroot / "usr/lib/go/src/debug/elf/testdata"
        td.mkdir(parents=True)
        (td / "relocation-test-arm.obj").write_bytes(
            make_elf(ei_class=needclosure.ELFCLASS32, dynamic=False))
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(violations, [])

    def test_virtual_and_build_top_dirs_pruned(self):
        # a violation planted under /proc, /sources, or /mnt must be
        # invisible — those trees are never sealed into the ISO and a live
        # chroot's /proc is a kernel-virtual filesystem (kcore).
        for top in ("proc", "sources", "mnt"):
            d = self.chroot / top
            d.mkdir(exist_ok=True)
            (d / "rogue32").write_bytes(
                make_elf(ei_class=needclosure.ELFCLASS32, dynamic=False))
        violations, _ = needclosure.audit_chroot(self.chroot)
        self.assertEqual(violations, [])

    def test_missing_chroot_fails_closed(self):
        violations, n = needclosure.audit_chroot(self.chroot / "nope")
        self.assertEqual(len(violations), 1)
        self.assertIn("refusing to assume", violations[0])

    def test_cli_empty_audit_is_failure(self):
        empty = self.chroot / "emptyroot"
        empty.mkdir()
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "igos-build/needclosure.py"),
             "--chroot", str(empty)],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("empty audit is a failed audit", r.stderr)

    def test_cli_green_and_red(self):
        cli = str(REPO_ROOT / "igos-build/needclosure.py")
        r = subprocess.run([sys.executable, cli, "--chroot", str(self.chroot)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PASS", r.stdout)
        (self.chroot / "usr/lib/libz.so.1").unlink()
        r = subprocess.run([sys.executable, cli, "--chroot", str(self.chroot)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("libz.so.1", r.stderr)


if __name__ == "__main__":
    unittest.main()
