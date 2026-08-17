"""Post-eviction ELF NEEDED-closure + word-size backstop sweep.

The squashfs seals the post-eviction chroot — the exact file set a live
session and the installer run from. Two properties must hold there and are
verified by nothing else at that point:

  1. WORD-SIZE BACKSTOP: every ELF object on the ISO is ELFCLASS64 unless
     its path sits under an explicitly-passed 32-bit prefix (none today).
     This closes the archive-time audit's bootstrap window (the handful of
     early-Ch8 archives sealed before python3 exists in the chroot) and is
     the ISO-resident backstop the package/deposit-time gates lean on.
  2. NEEDED CLOSURE: every dynamic ELF's DT_NEEDED entries resolve to a
     provider OF THE SAME CLASS somewhere ld.so would actually look — the
     chroot's own ld.so.conf(.d) dirs, the default lib dirs, or the
     binary's own RPATH/RUNPATH (with $ORIGIN expanded). A NEEDED with no
     same-class provider is a runtime failure shipped silently: exactly
     the eviction hazard (a package's libs evicted out from under an
     ISO-resident consumer) this gate exists to kill.

Parsing is stdlib-only (same philosophy as elfaudit.py): ELF header +
program headers + PT_DYNAMIC + strtab, hand-parsed. No readelf, no
pyelftools — the sweep runs anywhere the chroot directory is visible and
its correctness is provable in unit tests with synthetic ELFs plus a
readelf cross-check where readelf exists.

Honesty-first: a sweep that finds ZERO dynamic ELF objects in a chroot did
not audit anything — that is a failure, not a pass (the pi12-sweep rule).
"""

from __future__ import annotations

import os
import struct
import sys
import time
from pathlib import Path

ELF_MAGIC = b"\x7fELF"
ELFCLASS32 = 1
ELFCLASS64 = 2

PT_LOAD = 1
PT_DYNAMIC = 2

DT_NULL = 0
DT_NEEDED = 1
DT_SONAME = 14
DT_RPATH = 15
DT_STRTAB = 5
DT_RUNPATH = 29

# Virtual DSOs the kernel provides — never present on disk, never a miss.
VIRTUAL_DSOS = ("linux-vdso.so", "linux-gate.so")

# Where ld.so looks regardless of config (chroot-relative). /lib and
# /usr/lib64 are symlinks into /usr/lib on InterGenOS; walking through
# them is harmless and keeps the model honest on any layout drift.
DEFAULT_LIB_DIRS = ("/usr/lib", "/usr/lib64", "/lib", "/lib64", "/usr/lib32")

# Top-level chroot entries the sweep NEVER descends into: kernel-virtual
# filesystems (walking a live bind-mounted /proc means reading kcore),
# mutable runtime state, and build-only trees (/sources tarballs, the
# /mnt repo bind) that phase_image/mksquashfs never seal into the ISO.
PRUNE_TOP = ("proc", "sys", "dev", "run", "tmp", "mnt", "sources")

# Chroot-relative subtrees where HOST ELF semantics do not apply and the
# sweep never looks: /usr/lib/firmware holds device firmware images — some
# ARE ELF, for the DEVICE's own processor (e.g. Qualcomm bluetooth .mbn is
# ELFCLASS32 for the QCA chip), never host-executed, never ld.so-loaded.
# Width + NEEDED auditing there would be a category error; firmware
# integrity is owned by verify_paths canaries (incl. the kernel-derived
# nouveau/GSP canary work), not by this gate.
PRUNE_SUBTREES = ("usr/lib/firmware",)

# Directory NAMES pruned anywhere in the walk: `testdata` is the Go-ecosystem
# fixture convention — the go tool itself ignores those dirs, and Go's shipped
# source tree (tier core, ISO-resident) carries deliberately-foreign ELF
# fixtures there (32-bit, ARM, MIPS, PPC relocation-test objects) that are
# reference INPUTS for its test suite, never build or runtime artifacts.
# Placement integrity for such trees is owned by the package manifests, not
# by host width/closure semantics.
PRUNE_DIR_NAMES = ("testdata",)


class ElfInfo:
    __slots__ = ("path", "ei_class", "needed", "runpaths", "is_dynamic")

    def __init__(self, path, ei_class, needed, runpaths, is_dynamic):
        self.path = path
        self.ei_class = ei_class
        self.needed = needed
        self.runpaths = runpaths
        self.is_dynamic = is_dynamic


def parse_elf(path: Path) -> ElfInfo | None:
    """Parse class + DT_NEEDED + RPATH/RUNPATH from an ELF regular file.

    Returns None for non-ELF / non-regular / too-short files. An ELF with
    no PT_DYNAMIC (static binary, relocatable object, firmware) parses as
    is_dynamic=False with empty needed — its class still counts for the
    word-size backstop. Raises OSError on an unreadable regular file (the
    caller fails loud) and ValueError on a malformed/truncated ELF (a file
    that CLAIMS to be ELF but cannot be parsed must halt, not wave through).
    """
    st = os.lstat(path)
    if not os.path.stat.S_ISREG(st.st_mode) or st.st_size < 52:
        return None
    # Header-first: check the magic on a 64-byte read before committing to
    # a full read — the sweep walks whole filesystems and most files are
    # not ELF (and some non-ELF files are enormous).
    with open(path, "rb") as fh:
        head = fh.read(64)
        if head[:4] != ELF_MAGIC:
            return None
        data = head + fh.read()

    ei_class = data[4]
    if ei_class not in (ELFCLASS32, ELFCLASS64):
        raise ValueError(f"unknown EI_CLASS {ei_class}")
    en = "<" if data[5] == 1 else ">"
    try:
        if ei_class == ELFCLASS64:
            e_phoff, = struct.unpack_from(f"{en}Q", data, 0x20)
            e_phentsize, e_phnum = struct.unpack_from(f"{en}HH", data, 0x36)
        else:
            e_phoff, = struct.unpack_from(f"{en}I", data, 0x1C)
            e_phentsize, e_phnum = struct.unpack_from(f"{en}HH", data, 0x2A)

        loads: list[tuple[int, int, int]] = []   # (vaddr, offset, filesz)
        dyn_off = dyn_size = None
        for i in range(e_phnum):
            base = e_phoff + i * e_phentsize
            p_type, = struct.unpack_from(f"{en}I", data, base)
            if ei_class == ELFCLASS64:
                p_offset, p_vaddr = struct.unpack_from(f"{en}QQ", data, base + 0x08)
                p_filesz, = struct.unpack_from(f"{en}Q", data, base + 0x20)
            else:
                p_offset, p_vaddr = struct.unpack_from(f"{en}II", data, base + 0x04)
                p_filesz, = struct.unpack_from(f"{en}I", data, base + 0x10)
            if p_type == PT_LOAD:
                loads.append((p_vaddr, p_offset, p_filesz))
            elif p_type == PT_DYNAMIC:
                dyn_off, dyn_size = p_offset, p_filesz

        if dyn_off is None:
            return ElfInfo(path, ei_class, [], [], False)

        def vaddr_to_off(vaddr: int) -> int | None:
            for v, off, sz in loads:
                if v <= vaddr < v + sz:
                    return off + (vaddr - v)
            return None

        entsize = 16 if ei_class == ELFCLASS64 else 8
        fmt = f"{en}qQ" if ei_class == ELFCLASS64 else f"{en}iI"
        needed_offs: list[int] = []
        path_offs: list[int] = []
        strtab_vaddr = None
        for base in range(dyn_off, dyn_off + dyn_size, entsize):
            if base + entsize > len(data):
                raise ValueError("truncated dynamic section")
            d_tag, d_val = struct.unpack_from(fmt, data, base)
            if d_tag == DT_NULL:
                break
            if d_tag == DT_NEEDED:
                needed_offs.append(d_val)
            elif d_tag in (DT_RPATH, DT_RUNPATH):
                path_offs.append(d_val)
            elif d_tag == DT_STRTAB:
                strtab_vaddr = d_val

        def str_at(stroff: int) -> str:
            if strtab_vaddr is None:
                raise ValueError("dynamic strings without DT_STRTAB")
            off = vaddr_to_off(strtab_vaddr)
            if off is None:
                raise ValueError("DT_STRTAB outside every PT_LOAD")
            end = data.index(b"\x00", off + stroff)
            return data[off + stroff:end].decode("utf-8", "replace")

        needed = [str_at(o) for o in needed_offs]
        runpaths: list[str] = []
        for o in path_offs:
            runpaths.extend(p for p in str_at(o).split(":") if p)
        return ElfInfo(path, ei_class, needed, runpaths, True)
    except (struct.error, IndexError) as exc:
        raise ValueError(f"malformed ELF: {exc}") from exc


def _app_root(abs_path: Path) -> str | None:
    """The consumer's application root for private-lib resolution.

    /usr/lib/<app>/... -> "/usr/lib/<app>"; /opt/<app>/... -> "/opt/<app>";
    anything else -> None (no app-private fallback).
    """
    parts = abs_path.parts
    if len(parts) > 4 and parts[1] == "usr" and parts[2] == "lib":
        return f"/usr/lib/{parts[3]}"
    if len(parts) > 3 and parts[1] == "opt":
        return f"/opt/{parts[2]}"
    return None


def _ld_conf_dirs(chroot: Path) -> list[str]:
    """Chroot-relative extra search dirs from the chroot's own ld.so.conf."""
    dirs: list[str] = []
    seen: set[str] = set()

    def read_conf(conf: Path):
        try:
            lines = conf.read_text().splitlines()
        except OSError:
            return
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("include"):
                pattern = line.split(None, 1)[1]
                base = chroot / pattern.lstrip("/")
                for inc in sorted(base.parent.glob(base.name)):
                    read_conf(inc)
            elif line.startswith("/") and line not in seen:
                seen.add(line)
                dirs.append(line)

    read_conf(chroot / "etc/ld.so.conf")
    return dirs


def audit_chroot(chroot: Path | str, allow32_prefixes: tuple[str, ...] = ()) -> tuple[list[str], int]:
    """Sweep the chroot. Returns (violations, dynamic_elf_count)."""
    chroot = Path(chroot)
    violations: list[str] = []
    if not chroot.is_dir():
        return ([f"{chroot}: chroot missing at audit time — refusing to assume"], 0)

    elfs: list[ElfInfo] = []
    # provider index: filename -> set of ELF classes present in search dirs
    search_dirs = list(DEFAULT_LIB_DIRS) + _ld_conf_dirs(chroot)
    providers: dict[str, set[int]] = {}
    symlink_app_providers: dict[tuple[str, str], set[int]] = {}

    prune_abs = {str(chroot / sub) for sub in PRUNE_SUBTREES}
    # Progress heartbeat (stderr, time-based): this walk examines every file
    # on the chroot and runs silent for tens of minutes on a full image —
    # dead air reads as a hang (progress-indicator candidate #4, 2026-07-18).
    _hb_start = time.monotonic()
    _hb_last = _hb_start
    _hb_scanned = 0
    for root, dirs, files in os.walk(chroot, followlinks=False):
        if Path(root) == chroot:
            dirs[:] = [d for d in dirs if d not in PRUNE_TOP]
        dirs[:] = [d for d in dirs
                   if os.path.join(root, d) not in prune_abs
                   and d not in PRUNE_DIR_NAMES]
        _hb_now = time.monotonic()
        if _hb_now - _hb_last >= 15:
            _hb_last = _hb_now
            print(f"[needclosure] progress: {_hb_scanned} files examined, "
                  f"{len(elfs)} ELF objects so far, "
                  f"elapsed {int(_hb_now - _hb_start)}s", file=sys.stderr, flush=True)
        for fname in files:
            _hb_scanned += 1
            p = Path(root) / fname
            if p.is_symlink():
                # a symlink whose NAME matches a soname is a legitimate
                # provider (libfoo.so.1 -> libfoo.so.1.2.3); classify it by
                # its resolved target when the target parses as ELF.
                try:
                    target = p.resolve(strict=True)
                    info = parse_elf(target)
                except (OSError, RuntimeError, ValueError):
                    continue
                if info is not None:
                    rel = "/" + str(p.parent.relative_to(chroot))
                    if rel in search_dirs:
                        providers.setdefault(fname, set()).add(info.ei_class)
                    approot = _app_root(Path("/") / p.relative_to(chroot))
                    if approot:
                        symlink_app_providers.setdefault(
                            (approot, fname), set()).add(info.ei_class)
                continue
            try:
                info = parse_elf(p)
            except OSError as exc:
                violations.append(f"{p}: unreadable at audit time ({exc}) — refusing to assume")
                continue
            except ValueError as exc:
                violations.append(f"{p}: {exc} — refusing to assume")
                continue
            if info is None:
                continue
            elfs.append(info)
            rel_dir = "/" + str(p.parent.relative_to(chroot))
            if rel_dir in search_dirs:
                providers.setdefault(fname, set()).add(info.ei_class)

    print(f"[needclosure] walk complete: {_hb_scanned} files examined, "
          f"{len(elfs)} ELF objects, {int(time.monotonic() - _hb_start)}s — "
          f"resolving NEEDED closure...", file=sys.stderr, flush=True)

    # App-private provider index: a NEEDED may legitimately resolve inside
    # the consumer's OWN application tree (/usr/lib/<app>/... or
    # /opt/<app>/...) via loader-chain semantics this per-object model
    # cannot see statically — the executable's RPATH/RUNPATH covering the
    # chain (samba's private libs under smbd's runpath) or a launcher-set
    # library path (libreoffice's program tree). The property this gate
    # owns is "the provider SHIPS in the sealed image", and in those
    # conventions it does — so a same-basename provider under the SAME app
    # root resolves. A provider in an UNRELATED app's tree never does
    # (that would mask a genuine eviction).
    app_providers: dict[tuple[str, str], set[int]] = dict(symlink_app_providers)
    for info in elfs:
        approot = _app_root(Path("/") / info.path.relative_to(chroot))
        if approot:
            app_providers.setdefault(
                (approot, info.path.name), set()).add(info.ei_class)

    dynamic_count = 0
    for info in elfs:
        rel = "/" + str(info.path.relative_to(chroot))
        # 1) word-size backstop
        if info.ei_class == ELFCLASS32 and not any(
                rel.startswith(pref) for pref in allow32_prefixes):
            violations.append(
                f"{rel}: ELFCLASS32 on the ISO outside every allowed 32-bit prefix")
        if not info.is_dynamic:
            continue
        dynamic_count += 1
        # 2) NEEDED closure, same-class
        origin = "/" + str(info.path.parent.relative_to(chroot))
        approot = _app_root(Path("/") / info.path.relative_to(chroot))
        for soname in info.needed:
            if soname.startswith(VIRTUAL_DSOS):
                continue
            if info.ei_class in providers.get(soname, set()):
                continue
            if approot and info.ei_class in app_providers.get(
                    (approot, soname), set()):
                continue
            found = False
            for rp in info.runpaths:
                rp = rp.replace("$ORIGIN", origin).replace("${ORIGIN}", origin)
                cand = chroot / rp.lstrip("/") / soname
                try:
                    cand_info = parse_elf(cand.resolve(strict=True))
                except (OSError, RuntimeError, ValueError):
                    continue
                if cand_info is not None and cand_info.ei_class == info.ei_class:
                    found = True
                    break
            if not found:
                cls = "64-bit" if info.ei_class == ELFCLASS64 else "32-bit"
                violations.append(
                    f"{rel}: NEEDED {soname} has no same-class ({cls}) provider "
                    f"in the ld.so search dirs or its own RPATH/RUNPATH")
    return violations, dynamic_count


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="post-eviction ELF NEEDED-closure + word-size backstop")
    ap.add_argument("--chroot", required=True)
    ap.add_argument("--allow32-prefix", action="append", default=[],
                    help="path prefix under which ELFCLASS32 objects are allowed "
                         "(repeatable; the call site declares GRUB's i386-pc tree, "
                         "/usr/lib32, and the chroot-derived gcc/clang/rust 32-bit "
                         "runtime homes — see build-squashfs.sh step 4.75)")
    args = ap.parse_args(argv)

    violations, dynamic_count = audit_chroot(
        Path(args.chroot), tuple(args.allow32_prefix))
    if dynamic_count == 0:
        print("NEEDED-closure sweep: found ZERO dynamic ELF objects — an "
              "empty audit is a failed audit, not a pass", file=sys.stderr)
        return 1
    if violations:
        print(f"NEEDED-closure sweep: {len(violations)} violation(s) "
              f"({dynamic_count} dynamic ELF objects audited):", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print(f"NEEDED-closure sweep: PASS ({dynamic_count} dynamic ELF objects audited)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
