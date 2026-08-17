"""Wedge tests for the archive-time ELF word-size audit (RT-1, GE gate re-site).

Covers all three enforcement points:
  * elfaudit.py itself (audit_tree / audit_files / read_elf_ident / CLI) —
    the shared predicate both builder paths run;
  * parser.py's `elf_class:` field (default, valid values, closed vocabulary);
  * intergenos-helper-lib's deposit-time check in igos_helper_record_file.

The load-bearing wedge is red-before/green-after: a planted wrong-width ELF
object REFUSES the audit; removing the plant passes the identical tree.
"""

import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

import elfaudit  # noqa: E402  (path manipulation must precede import)
from parser import TemplateError, parse_template  # noqa: E402

HELPER_LIB = REPO_ROOT / "packages/core/intergenos-helper-lib/helper-lib.sh"
ELFAUDIT_CLI = REPO_ROOT / "igos-build/elfaudit.py"

EM_X86_64 = 62
EM_386 = 3


def _elf(ei_class: int, machine: int = EM_X86_64, endian: int = 1) -> bytes:
    """Craft a minimal ELF header prefix: magic + class + data + e_machine."""
    head = bytearray(24)
    head[0:4] = b"\x7fELF"
    head[4] = ei_class
    head[5] = endian
    fmt = "<H" if endian == 1 else ">H"
    struct.pack_into(fmt, head, 18, machine)
    return bytes(head)


ELF64 = _elf(elfaudit.ELFCLASS64, EM_X86_64)
ELF32 = _elf(elfaudit.ELFCLASS32, EM_386)


class TestReadElfIdent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data):
        p = self.root / name
        p.write_bytes(data)
        return p

    def test_elf64_identified(self):
        p = self._write("a.so", ELF64)
        self.assertEqual(elfaudit.read_elf_ident(p),
                         (elfaudit.ELFCLASS64, EM_X86_64))

    def test_elf32_identified(self):
        p = self._write("b.so", ELF32)
        self.assertEqual(elfaudit.read_elf_ident(p),
                         (elfaudit.ELFCLASS32, EM_386))

    def test_big_endian_machine_parse(self):
        p = self._write("be.o", _elf(elfaudit.ELFCLASS64, EM_X86_64, endian=2))
        self.assertEqual(elfaudit.read_elf_ident(p),
                         (elfaudit.ELFCLASS64, EM_X86_64))

    def test_non_elf_is_none(self):
        p = self._write("t.txt", b"#!/bin/sh\necho hello, definitely not an elf\n")
        self.assertIsNone(elfaudit.read_elf_ident(p))

    def test_short_file_is_none(self):
        p = self._write("tiny", b"\x7fEL")
        self.assertIsNone(elfaudit.read_elf_ident(p))

    def test_symlink_not_followed(self):
        target = self._write("real.so", ELF32)
        link = self.root / "link.so"
        link.symlink_to(target)
        self.assertIsNone(elfaudit.read_elf_ident(link))


class TestAuditTree(unittest.TestCase):
    """The red-before/green-after planted-violation wedge."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "usr/bin").mkdir(parents=True)
        (self.root / "usr/bin/tool").write_bytes(ELF64)
        (self.root / "usr/share").mkdir(parents=True)
        (self.root / "usr/share/doc.txt").write_bytes(b"documentation, not elf")

    def tearDown(self):
        self.tmp.cleanup()

    def test_green_clean_64_tree(self):
        self.assertEqual(elfaudit.audit_tree(self.root, "64"), ([], []))

    def test_red_planted_32_in_64_tree(self):
        plant = self.root / "usr/bin/sneaky32.so"
        plant.write_bytes(ELF32)
        violations, exempted = elfaudit.audit_tree(self.root, "64")
        self.assertEqual(len(violations), 1)
        self.assertEqual(exempted, [])
        self.assertIn("sneaky32.so", violations[0])
        self.assertIn("ELFCLASS32", violations[0])
        self.assertIn("EM_386", violations[0])
        # green-after: remove the plant, identical tree passes
        plant.unlink()
        self.assertEqual(elfaudit.audit_tree(self.root, "64"), ([], []))

    def test_red_planted_64_in_32_tree(self):
        lib32 = Path(self.tmp.name) / "lib32tree"
        (lib32 / "usr/lib32").mkdir(parents=True)
        (lib32 / "usr/lib32/libc.so").write_bytes(ELF32)
        self.assertEqual(elfaudit.audit_tree(lib32, "32"), ([], []))
        plant = lib32 / "usr/lib32/wrong64.so"
        plant.write_bytes(ELF64)
        violations, exempted = elfaudit.audit_tree(lib32, "32")
        self.assertEqual(len(violations), 1)
        self.assertEqual(exempted, [])
        self.assertIn("ELFCLASS64", violations[0])

    def test_mixed_accepts_both(self):
        (self.root / "usr/bin/runtime32.so").write_bytes(ELF32)
        self.assertEqual(elfaudit.audit_tree(self.root, "mixed"), ([], []))

    def test_missing_tree_fails_closed(self):
        violations, exempted = elfaudit.audit_tree(self.root / "nope", "64")
        self.assertEqual(len(violations), 1)
        self.assertEqual(exempted, [])
        self.assertIn("refusing to assume", violations[0])

    def test_invalid_expected_fails_closed(self):
        violations, exempted = elfaudit.audit_tree(self.root, "31337")
        self.assertEqual(len(violations), 1)
        self.assertEqual(exempted, [])
        self.assertIn("invalid expected", violations[0])

    def test_bpf_class64_object_passes_64(self):
        # e_machine is reported, never enforced: a 64-bit BPF object in a
        # 64-bit package is legitimate (systemd ships them).
        (self.root / "usr/bin/prog.bpf.o").write_bytes(
            _elf(elfaudit.ELFCLASS64, 247))
        self.assertEqual(elfaudit.audit_tree(self.root, "64"), ([], []))


class TestAuditFiles(unittest.TestCase):
    def test_vanished_path_ignored(self):
        # A recorded path that no longer exists is the manifest/verify
        # gates' problem, not a width violation.
        self.assertEqual(
            elfaudit.audit_files(["/nonexistent/definitely/not/here"], "64"),
            ([], []))


class TestCli(unittest.TestCase):
    """The bash builder path invokes elfaudit.py as a standalone script."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "ok.so").write_bytes(ELF64)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(ELFAUDIT_CLI), *args],
            capture_output=True, text=True)

    def test_cli_green(self):
        r = self._run("--root", str(self.root), "--expected", "64")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cli_red_names_the_plant(self):
        (self.root / "plant32.so").write_bytes(ELF32)
        r = self._run("--root", str(self.root), "--expected", "64",
                      "--name", "victim-pkg")
        self.assertEqual(r.returncode, 1)
        self.assertIn("victim-pkg", r.stderr)
        self.assertIn("plant32.so", r.stderr)


MINIMAL_YAML_TEMPLATE = """\
name: {name}
version: "1.0"
release: 1
description: "test package"
license: "MIT"
build_style: make
source:
  - url: "https://example.com/{name}-1.0.tar.gz"
    sha256: "0000000000000000000000000000000000000000000000000000000000000000"
{extra}
"""


class TestParserElfClass(unittest.TestCase):
    def _parse(self, extra_yaml=""):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "package.yml"
            path.write_text(
                MINIMAL_YAML_TEMPLATE.format(name="elfy", extra=extra_yaml))
            return parse_template(path)

    def test_default_is_64(self):
        self.assertEqual(self._parse().elf_class, "64")

    def test_explicit_32(self):
        self.assertEqual(self._parse('elf_class: "32"').elf_class, "32")

    def test_bare_int_64_normalized(self):
        self.assertEqual(self._parse("elf_class: 64").elf_class, "64")

    def test_mixed(self):
        self.assertEqual(self._parse("elf_class: mixed").elf_class, "mixed")

    def test_closed_vocabulary(self):
        with self.assertRaises(TemplateError):
            self._parse('elf_class: "both"')


@unittest.skipUnless(HELPER_LIB.is_file(), "helper-lib.sh not present")
class TestHelperLibDepositCheck(unittest.TestCase):
    """Deposit-time enforcement in igos_helper_record_file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.staging = self.root / "staging"
        self.staging.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _record(self, deposit_bytes, elf_class_env=None):
        deposit = self.root / "usr_opt_payload.bin"
        deposit.write_bytes(deposit_bytes)
        env_line = (
            f'export IGOS_HELPER_ELF_CLASS="{elf_class_env}"; '
            if elf_class_env else "")
        script = (
            f'set -u; source "{HELPER_LIB}"; '
            f'export IGOS_HELPER_STAGING="{self.staging}"; '
            f'{env_line}'
            f'igos_helper_record_file "{deposit}"')
        return subprocess.run(["bash", "-c", script],
                              capture_output=True, text=True)

    def test_green_64bit_deposit_recorded(self):
        r = self._record(ELF64)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("usr_opt_payload.bin",
                      (self.staging / "files").read_text())

    def test_red_32bit_deposit_refused(self):
        r = self._record(ELF32)
        self.assertNotEqual(r.returncode, 0)
        # igos_helper_emit folds long messages at terminal width, so assert
        # on tokens that cannot be split by a fold (no internal spaces).
        out = r.stdout + r.stderr
        self.assertIn("32-bit", out)
        self.assertIn("elf_class=64", out)
        self.assertFalse((self.staging / "files").exists())

    def test_mixed_declaration_records_32bit(self):
        r = self._record(ELF32, elf_class_env="mixed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_non_elf_deposit_recorded(self):
        r = self._record(b"just a config file, nothing binary")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def _run_script(self, body, elf_class_env=None):
        env_line = (
            f'export IGOS_HELPER_ELF_CLASS="{elf_class_env}"; '
            if elf_class_env else "")
        script = (
            f'set -u; source "{HELPER_LIB}"; '
            f'export IGOS_HELPER_STAGING="{self.staging}"; '
            f'{env_line}{body}')
        return subprocess.run(["bash", "-c", script],
                              capture_output=True, text=True)

    def test_red_record_before_deposit_refused(self):
        # F-1(a): recording a path that does not exist yet is an audit
        # bypass — mechanically refused now.
        r = self._run_script(
            f'igos_helper_record_file "{self.root}/not_deposited_yet.bin"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("BEFORE", r.stdout + r.stderr)
        self.assertFalse((self.staging / "files").exists())

    def test_red_directory_refused(self):
        d = self.root / "a_directory"
        d.mkdir()
        r = self._run_script(f'igos_helper_record_file "{d}"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("directory", r.stdout + r.stderr)

    def test_red_symlink_to_wrong_width_refused(self):
        # F-1(b): a symlink must not launder a wrong-width target.
        target = self.root / "real32.bin"
        target.write_bytes(ELF32)
        link = self.root / "innocent-link"
        link.symlink_to(target)
        r = self._run_script(f'igos_helper_record_file "{link}"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("32-bit", r.stdout + r.stderr)

    def test_green_symlink_to_right_width_recorded(self):
        target = self.root / "real64.bin"
        target.write_bytes(ELF64)
        link = self.root / "good-link"
        link.symlink_to(target)
        r = self._run_script(f'igos_helper_record_file "{link}"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("good-link", (self.staging / "files").read_text())

    def test_green_dangling_symlink_recorded(self):
        # no bytes to audit at record time; the pkm wire-up re-audit owns
        # the appeared-later case.
        link = self.root / "dangling-link"
        link.symlink_to(self.root / "never-exists")
        r = self._run_script(f'igos_helper_record_file "{link}"')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_red_contract_flip_mid_run_refused(self):
        f64 = self.root / "a64.bin"
        f64.write_bytes(ELF64)
        f32 = self.root / "b32.bin"
        f32.write_bytes(ELF32)
        r = self._run_script(
            f'igos_helper_record_file "{f64}" && '
            f'export IGOS_HELPER_ELF_CLASS=mixed && '
            f'igos_helper_record_file "{f32}"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("mid-run", r.stdout + r.stderr)

    def test_mixed_declaration_is_recorded_and_not_printed(self):
        # BEHAVIOUR CHANGED 2026-08-05, and this test changed with it. A
        # helper declaring mixed 32/64-bit deposits used to print a notice
        # that its per-file width check was waived, once per run, and this
        # test asserted that notice appeared exactly once.
        #
        # The notice was removed: it printed on every successful install by
        # such a helper, and it described this project's internal audit
        # machinery to somebody who was installing software and could do
        # nothing with it. Nothing about the waiver became silent — the
        # declaration is written to staging here and igos_helper_commit
        # carries it into the package's recorded manifest, which is where a
        # question about it can still be answered afterwards. That durable
        # record is what this test now asserts, together with the terminal
        # staying quiet.
        f32 = self.root / "m32.bin"
        f32.write_bytes(ELF32)
        f64 = self.root / "m64.bin"
        f64.write_bytes(ELF64)
        r = self._run_script(
            f'igos_helper_record_file "{f32}" && '
            f'igos_helper_record_file "{f64}"',
            elf_class_env="mixed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertEqual(out.count("waived"), 0,
                         "the waiver notice must not reach the terminal")
        self.assertEqual(out.strip(), "",
                         "a successful mixed-width record prints nothing")
        self.assertEqual((self.staging / "elf_class").read_text().strip(),
                         "mixed",
                         "the declaration must still be recorded for the "
                         "manifest — that is where the waiver stays visible")

    def test_contract_written_to_staging_for_manifest(self):
        f64 = self.root / "c64.bin"
        f64.write_bytes(ELF64)
        r = self._run_script(f'igos_helper_record_file "{f64}"')
        self.assertEqual(r.returncode, 0)
        self.assertEqual((self.staging / "elf_class").read_text().strip(),
                         "64")

    def test_red_invalid_contract_refused(self):
        f64 = self.root / "d64.bin"
        f64.write_bytes(ELF64)
        r = self._run_script(f'igos_helper_record_file "{f64}"',
                             elf_class_env="both")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
