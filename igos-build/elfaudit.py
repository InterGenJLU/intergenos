"""Archive-time ELF word-size (class) audit.

Every package archive seals a set of files; the expensive multilib failure
class is a wrong-word-size ELF object riding inside a package that claims
the other width (a 32-bit object in a 64-bit package, or the reverse) —
it packages fine, installs fine, and only fails at dynamic-link time on a
user machine. This audit asserts, at the moment the file set is about to
be archived, that every ELF object in the set matches the package's
declared word size.

Design constraints (deliberate):
  * ZERO tool dependencies. ELF class is byte 4 (EI_CLASS) of the file and
    the machine type is the little-endian u16 at offset 18 (e_machine); we
    read 20 bytes and decide. No readelf, no pyelftools — so the audit can
    run at any point of the build where the filesystem exists, including
    the earliest bootstrap windows, and inside the minimal chroot.
  * ENFORCE class only, REPORT machine. Word size (32 vs 64) is the
    contract this audit owns. e_machine is included in violation messages
    for diagnosis but never enforced here: legitimate 64-bit packages ship
    non-x86 ELF objects (BPF programs, firmware payloads) whose class is
    still the honest signal.
  * Symlinks are never followed; non-ELF and unreadable-short files are
    ignored (a 3-byte text file is not a violation); an unreadable regular
    file that LOOKS like it should be readable raises the violation — an
    audit that cannot see must fail loud, not wave through.

Expected-class vocabulary (package.yml `elf_class:`):
  "64"    — every ELF object must be ELFCLASS64 (the tree-wide default).
  "32"    — every ELF object must be ELFCLASS32 (the lib32-* packages).
  "mixed" — the package legitimately carries both widths (e.g. a multilib
            compiler runtime). Declared explicitly, in the recipe, with a
            comment — never via a hidden allowlist in the audit.

Path-scoped exemptions (package.yml `elf_class_exempt:`, GE-01 launch-gate
L9): some packages ship INERT foreign-width ELF files as data — go installs
its full source tree, whose debug/elf and runtime/pprof test suites carry
deliberate 32-bit x86/ARM/MIPS/PPC fixture objects that are never loaded.
Declaring the whole package `mixed` would waive the width contract across
its thousands of real 64-bit binaries — masking, not verifying. Instead the
recipe declares root-relative glob(s) covering exactly the inert paths:
  * matching is fnmatch (a trailing `/*` also covers nested files — fnmatch
    has no path-separator semantics, which is what we want here);
  * every exempted ELF file is REPORTED loudly (a waived audit is never a
    silent one);
  * a declared glob that exempts NO ELF file is itself a VIOLATION — a
    stale exemption is a false recipe assertion (fail closed, same spirit
    as the unreadable-file rule below);
  * files outside the declared globs are enforced exactly as before.
Like `mixed`, the declaration lives in the recipe with a comment — never a
hidden allowlist in the audit.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Iterable

ELF_MAGIC = b"\x7fELF"
ELFCLASS32 = 1
ELFCLASS64 = 2

VALID_EXPECTED = ("64", "32", "mixed")

# Informational e_machine names for violation messages (diagnosis only).
_MACHINE_NAMES = {
    3: "EM_386",
    40: "EM_ARM",
    62: "EM_X86_64",
    183: "EM_AARCH64",
    243: "EM_RISCV",
    247: "EM_BPF",
}


def read_elf_ident(path: Path) -> tuple[int, int] | None:
    """Return (ei_class, e_machine) for an ELF regular file, else None.

    Never follows symlinks. Returns None for non-files, non-ELF content,
    and files shorter than an ELF ident. Raises OSError for a regular
    file that exists but cannot be read — the caller turns that into a
    violation (fail loud, never wave through).
    """
    st = os.lstat(path)
    if not os.path.stat.S_ISREG(st.st_mode):
        return None
    if st.st_size < 20:
        return None
    with open(path, "rb") as fh:
        head = fh.read(20)
    if len(head) < 20 or head[:4] != ELF_MAGIC:
        return None
    ei_class = head[4]
    # e_machine is a u16 at offset 18, byte order per EI_DATA (byte 5):
    # 1 = little-endian, 2 = big-endian.
    endian = "<" if head[5] == 1 else ">"
    (e_machine,) = struct.unpack_from(f"{endian}H", head, 18)
    return ei_class, e_machine


def _class_name(ei_class: int) -> str:
    return {ELFCLASS32: "ELFCLASS32", ELFCLASS64: "ELFCLASS64"}.get(
        ei_class, f"EI_CLASS={ei_class}"
    )


def _rel_for_match(p: Path, root: Path | None) -> str:
    """Root-relative (or leading-slash-stripped) path string for glob matching."""
    if root is not None:
        try:
            return str(p.relative_to(root))
        except ValueError:
            pass
    return str(p).lstrip("/")


def audit_files(
    paths: Iterable[Path | str],
    expected: str,
    exempt: Iterable[str] = (),
    root: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Audit an explicit file list.

    Returns (violations, exempted): violation strings (empty = pass) and the
    loud per-file record of every ELF object skipped by a declared
    `elf_class_exempt` glob. A declared glob that exempts no ELF file is a
    violation (stale declaration — fail closed).
    """
    import fnmatch

    if expected not in VALID_EXPECTED:
        return [f"invalid expected elf_class '{expected}' (valid: {VALID_EXPECTED})"], []
    if expected == "mixed":
        return [], []
    globs = [g.lstrip("/") for g in exempt if g and g.strip()]
    glob_entries: dict[str, list[str]] = {g: [] for g in globs}
    glob_classes: dict[str, dict[str, int]] = {g: {} for g in globs}
    want = ELFCLASS64 if expected == "64" else ELFCLASS32
    violations: list[str] = []
    exempted: list[str] = []
    for p in paths:
        p = Path(p)
        try:
            ident = read_elf_ident(p)
        except FileNotFoundError:
            # A recorded path that vanished is a different gate's problem
            # (manifest/verify); not an ELF-class violation.
            continue
        except OSError as exc:
            violations.append(f"{p}: unreadable at audit time ({exc}) — refusing to assume")
            continue
        if ident is None:
            continue
        ei_class, e_machine = ident
        if globs:
            rel = _rel_for_match(p, root)
            hit = next((g for g in globs if fnmatch.fnmatch(rel, g)), None)
            if hit is not None:
                mach = _MACHINE_NAMES.get(e_machine, f"e_machine={e_machine}")
                glob_entries[hit].append(
                    f"{p}: {_class_name(ei_class)} ({mach}) — exempted by the "
                    f"recipe's declared elf_class_exempt glob '{hit}'"
                )
                cls = _class_name(ei_class)
                glob_classes[hit][cls] = glob_classes[hit].get(cls, 0) + 1
                continue
        if ei_class != want:
            mach = _MACHINE_NAMES.get(e_machine, f"e_machine={e_machine}")
            violations.append(
                f"{p}: {_class_name(ei_class)} ({mach}) — package declares elf_class={expected}"
            )
    # Reporting scale (L9 calibration, linux-firmware): a waived file is
    # never a silent one, but 200+ per-file lines per build turn the waiver
    # report into wallpaper — the inverse of the L7/L8 invisibility lesson.
    # Per glob: always a summary line with the class breakdown; itemize
    # every file only up to the cap, else the first few + an explicit count.
    ITEMIZE_CAP = 32
    for g in globs:
        entries = glob_entries[g]
        if not entries:
            violations.append(
                f"elf_class_exempt glob '{g}' exempted NO ELF file — stale "
                f"declaration; remove it from the recipe or fix the path"
            )
            continue
        breakdown = " + ".join(
            f"{n}× {cls}" for cls, n in sorted(glob_classes[g].items())
        )
        exempted.append(
            f"exempted {len(entries)} ELF file(s) via declared glob '{g}' ({breakdown})"
        )
        if len(entries) <= ITEMIZE_CAP:
            exempted.extend(entries)
        else:
            exempted.extend(entries[:5])
            exempted.append(
                f"… and {len(entries) - 5} more under '{g}' (itemization "
                f"capped at {ITEMIZE_CAP}; every file remains declared-inert "
                f"by the recipe)"
            )
    return violations, exempted


def audit_tree(
    root: Path | str, expected: str, exempt: Iterable[str] = ()
) -> tuple[list[str], list[str]]:
    """Audit every regular file under a staging tree (symlinks not followed)."""
    root = Path(root)
    if not root.is_dir():
        return [f"{root}: staging tree missing at audit time — refusing to assume"], []
    return audit_files(
        (p for p in root.rglob("*") if not p.is_symlink()),
        expected,
        exempt=exempt,
        root=root,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI for the bash builder path: exit 0 clean, 1 on violations/error.

    Prints one violation per line on stderr. Mirrors the audit the Python
    builder runs in-process, so both builder paths enforce ONE predicate.
    """
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="archive-time ELF class audit")
    ap.add_argument("--root", required=True, help="staging directory to audit")
    ap.add_argument("--expected", default="64", choices=VALID_EXPECTED)
    ap.add_argument("--name", default="", help="package name (message context)")
    ap.add_argument(
        "--exempt", action="append", default=[],
        help="root-relative fnmatch glob whose ELF files are exempt "
             "(repeatable; from the recipe's elf_class_exempt list; each "
             "exempted file is reported; a glob exempting nothing refuses)",
    )
    args = ap.parse_args(argv)
    label = f" [{args.name}]" if args.name else ""

    if args.expected == "mixed":
        # A waived audit is never a silent one (the bash builder path).
        print(f"ELF-class audit{label}: elf_class=mixed — width audit waived "
              f"by the recipe's explicit declaration", file=sys.stderr)
        return 0

    violations, exempted = audit_tree(Path(args.root), args.expected, args.exempt)
    for e in exempted:
        # A waived file is never a silent one.
        print(f"ELF-class audit{label}: {e}", file=sys.stderr)
    if violations:
        print(f"ELF-class audit{label}: {len(violations)} violation(s):", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
