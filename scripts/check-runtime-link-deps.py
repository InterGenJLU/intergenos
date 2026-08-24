#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Runtime-linkage dependency gate: a shipped library a package links must be
pulled in by that package's own declared runtime dependencies.

THE HOLE THIS CLOSES, measured on a real install before it was written
(2026-08-24, R001.1 machine installed 2026-08-22).

`igos-build/needclosure.py` already asserts that every DT_NEEDED entry of every
ELF object resolves to a provider — but it audits THE SEALED CHROOT, the file
set the ISO carries. A package that is not on the ISO and is fetched from the
mirror after install is never examined by it. The ROCm inference engine is
exactly that package class ("opt-in, mirror-only"), and on the measured install
every one of its 80 binaries failed to start:

    /opt/rocm/bin/llama-server: error while loading shared libraries:
    librocprofiler-register.so.0: cannot open shared object file

Two sonames had no provider on the machine — librocprofiler-register.so.0 and
libroctx64.so.4. Both are shipped by packages that exist on the mirror and are
installable. Nothing pulled them, because no installed package DECLARES them:
libamdhip64 and libhsa-runtime64 link librocprofiler-register.so.0 while their
recipes (rocm-hip, rocr-runtime) list neither, and librocsparse links
libroctx64.so.4 while rocsparse lists neither. Declaration, not availability.

WHAT THIS GATE ASSERTS, over a real root (an installed system or a staged
install tree) plus the recipe tree that produced it:

  1. RESOLUTION — every DT_NEEDED of every ELF under the scanned prefixes has a
     same-class provider somewhere ld.so would actually look: the root's own
     ld.so.conf(.d) dirs, the default lib dirs, the object's RPATH/RUNPATH with
     $ORIGIN expanded, or its own application tree. This is the property that
     was false on the measured install.

  2. DECLARATION — a resolved provider owned by a DIFFERENT package must lie
     inside the consumer package's declared runtime-dependency closure, read
     from the recipe tree. This is the property whose falseness ALLOWED (1) to
     be false: a library present only because some unrelated package happened
     to pull it is one `pkm autoremove` away from the same failure.

The declaration half separates two outcomes. A provider installed only as
another package's dependency is FAILED: `pkm autoremove` may take it, and the
machine lands in exactly the unresolved state above. A provider that is part of
the base install (the package manager records it as manually installed) is
REPORTED and not failed — the recipe still under-declares, and a minimal system
built from that recipe alone would break, but nothing on this root removes it.
Both classes print; only the first sets the exit code.

Ownership comes from the package database of the scanned root; the declared
closure comes from the recipes. That split is deliberate — ownership is a fact
of the root, declaration is a fact of the tree, and the author fixes the tree.

Honesty-first, the pi12-sweep rule: a run that examines ZERO dynamic ELF objects
audited nothing and exits non-zero. Where the package database is unavailable
the declaration half is REPORTED AS NOT PERFORMED rather than silently skipped.

Exit codes:
  0 — every scanned object resolves, and every cross-package edge is declared
  1 — one or more violations, or nothing was examined
  2 — environment error (root, prefix or recipe tree missing)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

import needclosure  # noqa: E402
from parser import load_all_packages  # noqa: E402

# The prefixes this gate is wired to scan by default. The ROCm tree is the
# measured defect's home and the only tree whose consumers are mirror-only, so
# it is the default rather than the whole filesystem: a root-wide sweep is a
# different, much larger finding set and belongs to its own decided step.
DEFAULT_PREFIXES = ("/opt/rocm",)

PKM_DB_DEFAULT = "/var/lib/igos/pkm.db"


class Finding:
    """One violation, with the sentence that names its fix.

    `fatal` separates what is unsafe NOW from what is under-declared but held
    up by the base install. Both are reported — a gate that hides the second
    class teaches nobody — but only the first fails the run.
    """

    __slots__ = ("kind", "obj", "soname", "detail", "fatal")

    def __init__(self, kind: str, obj: str, soname: str, detail: str,
                 fatal: bool = True):
        self.kind = kind
        self.obj = obj
        self.soname = soname
        self.detail = detail
        self.fatal = fatal

    def __str__(self) -> str:
        return f"{self.kind}  {self.obj}  NEEDED {self.soname}\n      {self.detail}"


def soname_providers_from_recipes(packages_dir: Path) -> dict[str, str]:
    """basename declared in a recipe's verify_paths -> the recipe's name.

    A recipe naming /opt/rocm/lib/libroctx64.so in verify_paths is the tree's
    own statement that it ships that library, so a soname of libroctx64.so.4
    resolves to it by stem. Derived from the recipes rather than restated in a
    table here, so it cannot drift away from what the recipes say.

    Read straight from package.yml: `verify_paths` is one of the keys the
    template parser deliberately leaves to external readers (KNOWN_FIELDS in
    igos-build/parser.py names this consumer class), so it is not carried on
    the parsed Package object.
    """
    import yaml

    out: dict[str, str] = {}
    for yml in sorted(packages_dir.glob("*/*/package.yml")):
        try:
            raw = yaml.safe_load(yml.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        name = raw.get("name")
        if not name:
            continue
        for vp in raw.get("verify_paths") or []:
            base = os.path.basename(str(vp))
            if ".so" not in base:
                continue
            out.setdefault(base, name)
    return out


def owning_recipe_for_soname(soname: str, stem_map: dict[str, str]) -> str | None:
    """Which recipe ships this soname, by exact name then by .so stem."""
    if soname in stem_map:
        return stem_map[soname]
    stem = soname.split(".so")[0] + ".so"
    return stem_map.get(stem)


def declared_runtime_closure(packages) -> dict[str, set[str]]:
    """package name -> every package name reachable through runtime deps.

    Runtime dependencies are written in the SHIPPED namespace (a recipe may
    ship under a different name via ships_as), so ship names are resolved back
    to the recipe that provides them before the walk — the same namespace split
    igos-build/graph.py resolves for the build order.
    """
    by_name = {p.name: p for p in packages}
    ship_to_recipe = {}
    for p in packages:
        sa = getattr(p, "ships_as", None)
        if sa:
            ship_to_recipe[sa] = p.name

    def deps_of(name: str) -> list[str]:
        pkg = by_name.get(name)
        if pkg is None:
            return []
        out = []
        for d in pkg.dependencies.runtime:
            out.append(ship_to_recipe.get(d, d))
        return out

    closure: dict[str, set[str]] = {}
    for name in by_name:
        seen: set[str] = set()
        stack = list(deps_of(name))
        while stack:
            d = stack.pop()
            if d in seen:
                continue
            seen.add(d)
            stack.extend(deps_of(d))
        closure[name] = seen
    return closure


def build_provider_index(root: Path) -> tuple[dict[str, set[int]], list[str]]:
    """Every library ld.so could find from this root: basename -> ELF classes."""
    search_dirs = list(needclosure.DEFAULT_LIB_DIRS) + needclosure._ld_conf_dirs(root)
    providers: dict[str, set[int]] = {}
    for d in search_dirs:
        real = root / d.lstrip("/")
        if not real.is_dir():
            continue
        for entry in os.scandir(real):
            try:
                target = Path(entry.path).resolve(strict=True) if entry.is_symlink() \
                    else Path(entry.path)
                info = needclosure.parse_elf(target)
            except (OSError, RuntimeError, ValueError):
                continue
            if info is not None:
                providers.setdefault(entry.name, set()).add(info.ei_class)
    return providers, search_dirs


def _app_providers_for(root: Path, approot: str) -> dict[str, set[int]]:
    """Libraries inside one application tree, for RUNPATH-chain resolution."""
    out: dict[str, set[int]] = {}
    base = root / approot.lstrip("/")
    if not base.is_dir():
        return out
    for dirpath, _dirs, files in os.walk(base, followlinks=False):
        for fname in files:
            p = Path(dirpath) / fname
            try:
                target = p.resolve(strict=True) if p.is_symlink() else p
                info = needclosure.parse_elf(target)
            except (OSError, RuntimeError, ValueError):
                continue
            if info is not None:
                out.setdefault(fname, set()).add(info.ei_class)
    return out


def open_owner_lookup(pkgdb: Path):
    """Return owner(abs_path)->package name, or None when no database is there.

    Uses the package manager's own database layer rather than a second copy of
    its schema, and opens it read-only: this gate reads, it never writes.
    """
    if not pkgdb.exists():
        return None
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from pkm.database import PackageDB
    except ImportError:
        return None
    import sqlite3
    # read_only=True is the package manager's own non-root inspection path:
    # it opens the root-owned database immutable, touching neither it nor its
    # WAL sidecars. A gate that had to WRITE to read would need root, and a
    # gate needing root to answer a read-only question is a gate people skip.
    db = PackageDB(db_path=str(pkgdb), read_only=True)

    reasons = {}
    try:
        for row in db.list_installed():
            reasons[row["name"]] = row.get("install_reason") or "manual"
    except sqlite3.Error:
        pass

    def owner(abs_path: str) -> str | None:
        try:
            row = db.find_owner(abs_path)
        except sqlite3.Error:
            return None
        return row["name"] if row else None

    owner.install_reason = reasons.get  # type: ignore[attr-defined]
    return owner


def scan(root: Path, prefixes: tuple[str, ...], packages, owner,
         packages_dir: Path) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    providers, _search_dirs = build_provider_index(root)
    stem_map = soname_providers_from_recipes(packages_dir)
    closure = declared_runtime_closure(packages)
    app_cache: dict[str, dict[str, set[int]]] = {}
    dynamic_count = 0

    for prefix in prefixes:
        base = root / prefix.lstrip("/")
        if not base.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(base, followlinks=False):
            for fname in files:
                p = Path(dirpath) / fname
                if p.is_symlink():
                    continue
                try:
                    info = needclosure.parse_elf(p)
                except (OSError, ValueError) as exc:
                    findings.append(Finding(
                        "UNREADABLE", str(p), "-",
                        f"{exc} — refusing to assume it is sound"))
                    continue
                if info is None or not info.is_dynamic:
                    continue
                dynamic_count += 1
                abs_rel = "/" + str(p.relative_to(root))
                origin = os.path.dirname(abs_rel)
                approot = needclosure._app_root(Path(abs_rel))
                consumer = owner(abs_rel) if owner else None

                for soname in info.needed:
                    if soname.startswith(needclosure.VIRTUAL_DSOS):
                        continue
                    provider_path = _resolve(root, info, soname, providers,
                                             origin, approot, app_cache)
                    if provider_path is None:
                        ships = owning_recipe_for_soname(soname, stem_map)
                        fix = (f"no provider on this root; the recipe that ships it is "
                               f"'{ships}' — declare it as a runtime dependency of "
                               f"'{consumer or 'the owning recipe'}'"
                               if ships else
                               "no provider on this root and no recipe in the tree "
                               "declares a verify_paths entry shipping it")
                        findings.append(Finding("UNRESOLVED", abs_rel, soname, fix))
                        continue
                    if owner is None or consumer is None:
                        continue
                    provider_pkg = owner(provider_path)
                    if provider_pkg is None or provider_pkg == consumer:
                        continue
                    if provider_pkg in closure.get(consumer, set()):
                        continue
                    reason = getattr(owner, "install_reason", lambda _n: None)(provider_pkg)
                    if reason == "dependency":
                        findings.append(Finding(
                            "UNDECLARED", abs_rel, soname,
                            f"resolved to {provider_path} owned by '{provider_pkg}', which "
                            f"is not in the declared runtime closure of '{consumer}' and is "
                            f"installed only as another package's dependency — one "
                            f"`pkm autoremove` away from the unresolved case above",
                            fatal=True))
                    else:
                        findings.append(Finding(
                            "UNDECLARED-BASE", abs_rel, soname,
                            f"resolved to {provider_path} owned by '{provider_pkg}', which "
                            f"is not in the declared runtime closure of '{consumer}'. It is "
                            f"installed as a base/manual package, so nothing removes it "
                            f"today and this is reported, not failed",
                            fatal=False))
    return findings, dynamic_count


def _resolve(root, info, soname, providers, origin, approot, app_cache) -> str | None:
    """The path ld.so would load for this soname, or None. Same-class only."""
    if info.ei_class in providers.get(soname, set()):
        return _first_hit(root, soname)
    for rp in info.runpaths:
        rp = rp.replace("$ORIGIN", origin).replace("${ORIGIN}", origin)
        cand = root / rp.lstrip("/") / soname
        try:
            cand_info = needclosure.parse_elf(cand.resolve(strict=True))
        except (OSError, RuntimeError, ValueError):
            continue
        if cand_info is not None and cand_info.ei_class == info.ei_class:
            return _root_relative(root, cand, f"{rp.rstrip('/')}/{soname}")
    if approot:
        if approot not in app_cache:
            app_cache[approot] = _app_providers_for(root, approot)
        if info.ei_class in app_cache[approot].get(soname, set()):
            hit = _first_hit(root, soname, under=approot)
            if hit:
                return hit
    return None


def _root_relative(root: Path, candidate: Path, logical: str) -> str:
    """The path to report for a hit, expressed inside the scanned root.

    A root assembled for a test can reach real system directories through
    symlinks, so a fully resolved candidate may land outside it. The logical
    path — the search directory the loader used, plus the soname — is the
    honest answer in that case, and it is the name ld.so itself would use.
    """
    try:
        return "/" + str(candidate.resolve().relative_to(root))
    except (OSError, ValueError):
        return logical


def _first_hit(root: Path, soname: str, under: str | None = None) -> str | None:
    dirs = ([under] if under else
            list(needclosure.DEFAULT_LIB_DIRS) + needclosure._ld_conf_dirs(root))
    for d in dirs:
        cand = root / d.lstrip("/") / soname
        if cand.exists():
            return _root_relative(root, cand, f"{d.rstrip('/')}/{soname}")
        if under:
            for dirpath, _sub, files in os.walk(root / d.lstrip("/"), followlinks=False):
                if soname in files:
                    hit = Path(dirpath) / soname
                    logical = "/" + str(hit.relative_to(root)) \
                        if hit.is_relative_to(root) else str(hit)
                    return _root_relative(root, hit, logical)
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="assert a package's linked libraries are pulled by its own "
                    "declared runtime dependencies")
    ap.add_argument("--repo", default=str(REPO_ROOT),
                    help="recipe tree whose package.yml files declare the closure")
    ap.add_argument("--root", default="/",
                    help="filesystem root to scan (an installed system or a staged tree)")
    ap.add_argument("--prefix", action="append", default=[],
                    help=f"path prefix to scan, repeatable (default: {' '.join(DEFAULT_PREFIXES)})")
    ap.add_argument("--pkgdb", default=PKM_DB_DEFAULT,
                    help="package database of the scanned root, for file ownership")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    root = Path(args.root).resolve()
    prefixes = tuple(args.prefix) or DEFAULT_PREFIXES

    if not (repo / "packages").is_dir():
        print(f"runtime-link-deps gate: no recipe tree at {repo}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"runtime-link-deps gate: no root at {root}", file=sys.stderr)
        return 2

    packages = load_all_packages(repo / "packages")
    owner = open_owner_lookup(Path(args.pkgdb))
    if owner is None:
        print(f"runtime-link-deps gate: NO PACKAGE DATABASE at {args.pkgdb} — the "
              f"declaration half was NOT PERFORMED; resolution is still enforced",
              file=sys.stderr)

    findings, dynamic_count = scan(root, prefixes, packages, owner,
                                   repo / "packages")

    if dynamic_count == 0:
        print(f"runtime-link-deps gate: found ZERO dynamic ELF objects under "
              f"{' '.join(prefixes)} on {root} — an empty audit is a failed audit, "
              f"not a pass", file=sys.stderr)
        return 1

    fatal = [f for f in findings if f.fatal]
    advisory = [f for f in findings if not f.fatal]

    if findings:
        print(f"runtime-link-deps gate: {len(fatal)} failing violation(s) and "
              f"{len(advisory)} reported-not-failed ({dynamic_count} dynamic ELF "
              f"objects audited under {' '.join(prefixes)}):", file=sys.stderr)
        for f in fatal + advisory:
            print(f"  {f}", file=sys.stderr)

    if fatal:
        return 1

    half = "resolution only" if owner is None else "resolution + declaration"
    print(f"runtime-link-deps gate: PASS ({dynamic_count} dynamic ELF objects "
          f"audited under {' '.join(prefixes)}; {half}; "
          f"{len(advisory)} reported-not-failed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
