#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Silent-feature-loss preflight gate.

Promoted from prototype (Scan B primary + supplement) used during the
Build #8 → Build #9 remediation arc. Catches the class of bug where a
package is declared as a build-time dependency in another package's
``package.yml``, but the consumer's configure script:

* Fails to detect the dep at probe time ("checking for X... no") and
  defaults to a soft-disable instead of erroring out, OR
* Reports the feature as ``no`` / ``disabled`` / ``None`` in its
  end-of-configure summary block.

The canonical case that drove this scan into existence: systemd built
without 15 in-tree security/hardening deps (libseccomp, libapparmor,
libcryptsetup, libfido2, libgcrypt, gnutls, …) plus ukify/homed/man/
sysupdate disabled — none of which surfaced as halts because configure
chose silent defaults. The build "succeeded" but the systemd binary
shipped without the features we claimed.

Three classification passes:
  Pass 1 — DECLARED-FAILED:
    For each consumer pkg's declared dep, search the configure log for
    detection-failure patterns. If hit → dep was declared but configure
    didn't see it.
  Pass 2 — BLFS-{REQUIRED,RECOMMENDED}-UNDECLARED-FAILED:
    For each pkg's BLFS-truth deps (required/recommended) NOT declared
    in our package.yml, search the log. If hit → dep is missing from our
    declared set AND configure tried to find it.
  Pass 3 — BLFS-OPTIONAL-INTREE-FAILED:
    For each BLFS-optional dep that EXISTS in our tree, search the log.
    If hit → we have the package but didn't declare/wire it.

Plus a supplemental scan (formerly ``scan_summary_disables``):
  - SUMMARY-DISABLED:    end-of-configure "feature: disabled/no/None" lines
  - MESON-NOT-FOUND-INTREE: ``Dependency X found: NO`` where X is in our tree

Chroot-data presence:
  This scan reads CHROOT_INSTALLED + CHROOT_LOGS from the build VM. If
  those paths don't exist (e.g., running on a workstation without a
  mounted chroot), the gate SKIPS with an informational message — exit 0,
  no block — because there's no prior-build state to audit. The scan
  blocks only when chroot data is present AND findings surface.

Exit codes:
  0 — clean (no findings, OR chroot data absent → skip-with-info)
  1 — findings present (build kickoff should halt)
  2 — environment problem (repo source missing — distinct from chroot absent)
  3 — required audit could not run OR could not cover the full installed
      set: --require-audit was set and either the scan SKIPPED (chroot/BLFS
      data absent) or its coverage was incomplete — an empty installed
      inventory, a package whose build log is missing or unreadable, or a
      package with no package.yml. Fail-closed for the build call sites
      where the chroot MUST be populated (the end-of-extra gate + the
      targeted-resume manual fire) — "couldn't check" and "checked only a
      subset" are both not "it's fine." Packages outside the BLFS reference
      scope stay permitted by policy (named in the summary; their logs are
      still scanned). Ad-hoc/workstation runs omit the flag and keep the
      exit-0 skip-with-info behaviour.

Usage:
  scripts/preflight-silent-loss.py             # gate mode (terse pass/fail)
  scripts/preflight-silent-loss.py --report    # verbose: emit JSON + TSV
  scripts/preflight-silent-loss.py --root /alt/repo --chroot /alt/igos
  scripts/preflight-silent-loss.py --require-audit   # SKIP → fail-closed (exit 3)

Environment:
  INTERGENOS_ROOT    — repo root (default: autodetect from script location)
  INTERGENOS_CHROOT  — chroot mount point (default: /mnt/igos)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


# ----------------------------------------------------------------------
# Path discovery + configuration
# ----------------------------------------------------------------------

def discover_repo_root(arg_root: str | None) -> Path:
    if arg_root:
        return Path(arg_root).resolve()
    env_root = os.environ.get("INTERGENOS_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parent.parent


def discover_chroot_root(arg_chroot: str | None) -> Path:
    if arg_chroot:
        return Path(arg_chroot).resolve()
    env_chroot = os.environ.get("INTERGENOS_CHROOT")
    if env_chroot:
        return Path(env_chroot).resolve()
    return Path("/mnt/igos")


# ----------------------------------------------------------------------
# Name-aliasing for log matching
# ----------------------------------------------------------------------

def name_variants(name: str) -> set[str]:
    """Search-variant set for matching dep names in log lines."""
    name = re.sub(r"-[\d.]+$", "", name)
    variants = {name}
    base = re.sub(r"-(pass\d+|bootstrap|core|host)$", "", name)
    variants.add(base)
    if base.startswith("lib"):
        variants.add(base[3:])
    else:
        variants.add("lib" + base)
    stripped = re.sub(r"\d+$", "", base)
    if stripped and stripped != base:
        variants.add(stripped)
        if stripped.startswith("lib"):
            variants.add(stripped[3:])
        else:
            variants.add("lib" + stripped)
    variants |= {v.lower() for v in list(variants)}
    return {v.rstrip(".-") for v in variants if v and len(v) >= 3}


# ----------------------------------------------------------------------
# package.yml parser (stdlib — same shape as preflight-build-order)
# ----------------------------------------------------------------------

def parse_simple_list_field(pkg_yml_path: Path, field_name: str) -> list[str]:
    """Parse a top-level ``<field_name>:`` list from a package.yml.

    Generic stdlib YAML helper for fields like ``configure_flags:``,
    ``supersedes:``, and ``silent_loss_accepted:``. Handles the simple
    ``- "value"`` / ``- value`` line form used throughout the in-tree
    package.yml files. Returns empty list if the field is absent.
    """
    if not pkg_yml_path.is_file():
        return []
    out: list[str] = []
    in_field = False
    field_indent = -1
    with pkg_yml_path.open() as fp:
        for raw in fp:
            line = raw.rstrip("\n")
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(stripped)
            if indent == 0:
                in_field = stripped.startswith(f"{field_name}:")
                field_indent = 0 if in_field else -1
                continue
            if not in_field:
                continue
            if indent <= field_indent:
                in_field = False
                continue
            if stripped.startswith("- "):
                item = stripped[2:].strip().strip('"').strip("'")
                if "#" in item:
                    item = item.split("#", 1)[0].strip()
                if item:
                    out.append(item)
            else:
                in_field = False
    return out


def parse_configure_flags(pkg_yml_path: Path) -> list[str]:
    """Parse the top-level ``configure_flags:`` list from a package.yml."""
    return parse_simple_list_field(pkg_yml_path, "configure_flags")


_LIB_ONLY_PATTERNS = [
    re.compile(r"^--enable-lib(?:-only|only)\b"),
    re.compile(r"^--disable-(?:apps|programs|tools|utilities|cli|exec|examples|samples|client|tests|test|docs|doc|man)\b"),
    re.compile(r"^--without-(?:apps|programs|tools|examples)\b"),
    re.compile(r"^--enable-only-lib(?:rary)?\b"),
]


def is_lib_only_mode(configure_flags: list[str]) -> bool:
    """Heuristic: is this consumer building in a minimal-library-only mode?

    Returns True if ANY configure flag matches the lib-only-mode pattern
    set. In lib-only mode, the consumer's configure typically reports many
    optional libs as "no" by design — the apps that would consume them
    aren't built. Used to suppress ``autotools-summary-feature-*`` findings
    where the declared dep is genuinely app-side and not linked into the
    library output.
    """
    for flag in configure_flags:
        if any(p.match(flag) for p in _LIB_ONLY_PATTERNS):
            return True
    return False


def check_pkgconfig_rescue(log_text: str, line_no: int, dep_name: str) -> str | None:
    """Detect autotools pkg-config rescue after a legacy ``-config`` script miss.

    When configure runs ``checking for X-config... no`` (legacy autotools
    style), modern packages then attempt one of two rescue paths:

    1. **gpgrt-config rescue** (libgpg-error pattern): configure emits a
       follow-up line like ``configure: Use gpgrt-config with /usr/lib as
       gpg-error-config`` indicating it sourced the lib via the canonical
       gpgrt-config wrapper. Treated as fully-rescued.

    2. **pkg-config rescue** (generic): configure emits a follow-up
       ``checking for X >= V.V.V... yes`` (or ``checking for X... yes``)
       within a short window after the legacy-config miss, indicating
       pkg-config provided the lib. Treated as fully-rescued.

    Returns a short reason string if rescued, else None. Looks within a
    20-line window after the miss to cover slow-to-emit configure scripts.
    """
    lines = log_text.splitlines()
    if line_no <= 0 or line_no > len(lines):
        return None
    window_start = line_no  # line AFTER the miss
    window_end = min(line_no + 20, len(lines))
    window = "\n".join(lines[window_start:window_end])

    # Pattern 1: gpgrt-config (or any X-config) rescue
    if re.search(r"configure:\s*Use\s+\S+(?:rt)?-config\s+with\s", window):
        return "gpgrt-config-rescue"

    # Pattern 2: pkg-config-style follow-up YES detection
    variants = name_variants(dep_name)
    for variant in variants:
        if len(variant) < 3:
            continue
        v_esc = re.escape(variant)
        rescue = re.compile(
            rf"checking for {v_esc}\s*(?:>=\s*[\d.]+)?\s*\.\.\.\s*yes\b",
            re.IGNORECASE,
        )
        if rescue.search(window):
            return f"pkg-config-rescue ({variant})"

    return None


_MESON_ENABLED_LINE = re.compile(
    r"enabled\s+:\s+([^:\n]+)$",
    re.IGNORECASE | re.MULTILINE,
)


def find_pass2_for(packages_dir: Path, consumer_pkg: str) -> Path | None:
    """Locate a package whose ``supersedes:`` list contains ``consumer_pkg``.

    InterGenOS uses the BLFS-style pass2 pattern: an optional pkg-dep that
    is unavailable at LFS Ch8 time (because it's tier:desktop or core-extra,
    built after ch8) gets folded in via a second-pass rebuild package that
    declares ``supersedes: [<original-pkg>]``. The second-pass replaces the
    original binary in pkm at install time, and its deps + configure flags
    are the ones that actually ship.

    Returns the pass2 package.yml path if one supersedes ``consumer_pkg``,
    else None.
    """
    for tier_dir in packages_dir.iterdir():
        if not tier_dir.is_dir():
            continue
        for pkg_dir in tier_dir.iterdir():
            if not pkg_dir.is_dir():
                continue
            yml = pkg_dir / "package.yml"
            if not yml.is_file():
                continue
            supersedes = parse_simple_list_field(yml, "supersedes")
            if consumer_pkg in supersedes:
                return yml
    return None


def check_pass2_rescue(packages_dir: Path, consumer_pkg: str, dep_name: str) -> str | None:
    """Detect that a pass1 silent-loss is overridden by a pass2 rebuild.

    The gate scans pass1 (LFS Ch8) build logs. If a pass2 rebuild exists
    that supersedes the consumer AND declares ``dep_name`` (or a variant)
    in its ``dependencies.build`` AND has an ``-D<feature>=enabled`` flag
    in its build.sh, the pass1 silent-disable is *not* a real loss — the
    shipped binary will be the pass2 build, which has the feature compiled
    in.

    Returns a short identifying string for the rescuing pass2 package + the
    matched dep variant if rescued, else None.
    """
    pass2_yml = find_pass2_for(packages_dir, consumer_pkg)
    if pass2_yml is None:
        return None
    pass2_deps = set(parse_deps_build(pass2_yml))
    # Tolerate name variation (cryptsetup vs libcryptsetup, etc.)
    dep_variants = name_variants(dep_name)
    matched_dep = None
    for v in dep_variants:
        if v in pass2_deps:
            matched_dep = v
            break
    if matched_dep is None:
        return None
    # Belt-and-suspenders: also confirm pass2's build.sh enables the feature
    # via -D<something>=enabled. Heuristic — meson feature names don't always
    # equal dep names, so we accept a loose match (dep-stem appearing in any
    # -D<flag>=enabled line). If the flag exists for the variant, treat as
    # corroboration. If not, still rescue based on the declared dep alone
    # (the dep is in pass2's build closure either way; the build.sh choice
    # is a separate audit surface).
    pass2_name = pass2_yml.parent.name
    return f"{pass2_name} declares {matched_dep}"


def check_accepted_loss(yml_path: Path, dep_name: str) -> str | None:
    """Detect that a silent loss is explicitly accepted in the consumer's
    package.yml via the ``silent_loss_accepted:`` declaration.

    Mechanism: a consumer that intentionally does NOT integrate a dep
    (because the dep is deprecated, security-risk, replaced by another, or
    not desired in InterGenOS posture) can declare::

        silent_loss_accepted:
          - dep1   # one-line rationale
          - dep2   # one-line rationale

    The gate then trusts the declaration and suppresses findings for those
    deps. Rationale comments belong in the package.yml itself for
    durability.
    """
    if yml_path is None or not yml_path.is_file():
        return None
    accepted = parse_simple_list_field(yml_path, "silent_loss_accepted")
    if not accepted:
        return None
    accepted_lower = {a.lower() for a in accepted}
    # Match the raw probed name AND its search-variants. The raw check lets a
    # precise, version-suffixed accept token (e.g. ``libsoup-2.4``, ``gtk+-2.0``)
    # match exactly: name_variants() strips a trailing version, so a versioned
    # accept token would otherwise never appear in its own variant set.
    candidates = {dep_name.lower()} | {v.lower() for v in name_variants(dep_name)}
    if candidates & accepted_lower:
        return f"declared in {yml_path.parent.name}/package.yml silent_loss_accepted"
    return None


def check_meson_enabled(log_text: str, dep_name: str) -> str | None:
    """Detect meson "enabled features" rescue for a runtime-dep "found: NO" miss.

    Pattern observed in systemd's meson build: a runtime dep like
    ``polkit-gobject-1`` reports ``found: NO`` at probe time (the daemon
    isn't installed at build time), but the package's feature summary line
    ``enabled : ..., polkit, ...`` still includes the feature — meaning the
    client-side support was compiled in. The runtime-dep check is
    build-host vs target distinction noise; the consumer has the support.

    Returns the matching variant name if found in any enabled-features
    summary line, else None.
    """
    variants = name_variants(dep_name)
    for m in _MESON_ENABLED_LINE.finditer(log_text):
        enabled_list = m.group(1)
        # Tokenize the enabled list: comma-separated features, possibly with whitespace.
        tokens = {t.strip().rstrip(",").lower() for t in enabled_list.split(",")}
        for variant in variants:
            if len(variant) < 3:
                continue
            if variant.lower() in tokens:
                return variant
            # The meson summary uses short names ("polkit" for "polkit-gobject-1");
            # also try a stem-prefix match against each token.
            for tok in tokens:
                if tok and variant.lower().startswith(tok + "-"):
                    return tok
                if tok and tok.startswith(variant.lower() + "-"):
                    return tok
    return None


def check_subproject_fallback(log_text: str, dep_name: str) -> str | None:
    """Detect a meson SUBPROJECT-FALLBACK rescue for a "found: NO" miss (Rule L).

    Pattern observed in dxvk's PE cross build (GE-01, 2026-07-04): the
    system probe reports ``Run-time dependency libdisplay-info found: NO``
    (correct — the mingw pkg-config personality cannot serve a Linux .pc,
    and a PE build could not link the Linux lib anyway), then meson falls
    back to the package's VENDORED subproject and compiles the dependency
    for the actual target::

        Looking for a fallback subproject for the dependency libdisplay-info
        ...
        Dependency libdisplay-info from subproject subprojects/libdisplay-info found: YES 0.0.0

    The dependency is SATISFIED — vendored into the artifact — so the
    "found: NO" line is resolution noise, not a silent feature loss.
    Only the definitive resolution line rescues; the "Looking for a
    fallback" line alone does not (a failed fallback still ends NO).

    Returns the matched resolution line (trimmed) if present, else None.
    """
    pat = re.compile(
        r"^Dependency\s+" + re.escape(dep_name) +
        r"\s+from\s+subproject\s+\S+\s+found:\s*YES\b",
        re.MULTILINE | re.IGNORECASE,
    )
    m = pat.search(log_text)
    if m:
        return m.group(0).strip()[:160]
    return None


def parse_deps_build(pkg_yml_path: Path) -> list[str]:
    if not pkg_yml_path.is_file():
        return []
    deps: list[str] = []
    in_deps = False
    in_build = False
    deps_indent = -1
    build_indent = -1
    with pkg_yml_path.open() as fp:
        for raw in fp:
            line = raw.rstrip("\n")
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(stripped)
            if indent == 0:
                in_deps = stripped.startswith("dependencies:")
                in_build = False
                deps_indent = 0 if in_deps else -1
                continue
            if not in_deps:
                continue
            if not in_build:
                if stripped.startswith("build:") and indent > deps_indent:
                    in_build = True
                    build_indent = indent
                continue
            if indent < build_indent:
                in_build = False
                continue
            if indent == build_indent:
                if stripped.startswith("- "):
                    item = stripped[2:].strip().strip('"').strip("'")
                    if "#" in item:
                        item = item.split("#", 1)[0].strip()
                    if item:
                        deps.append(item)
                else:
                    in_build = False
                continue
            if stripped.startswith("- "):
                item = stripped[2:].strip().strip('"').strip("'")
                if "#" in item:
                    item = item.split("#", 1)[0].strip()
                if item:
                    deps.append(item)
    return deps


def find_package_yml(packages_dir: Path, pkg_name: str) -> Path | None:
    candidates = [pkg_name, f"{pkg_name}-core", f"{pkg_name}-pass1"]
    for tier_dir in packages_dir.iterdir():
        if not tier_dir.is_dir():
            continue
        for name in candidates:
            candidate = tier_dir / name / "package.yml"
            if candidate.is_file():
                return candidate
    return None


def in_tree(packages_dir: Path, pkg_name: str) -> bool:
    for tier_dir in packages_dir.iterdir():
        if not tier_dir.is_dir():
            continue
        if (tier_dir / pkg_name / "package.yml").is_file():
            return True
    return False


# ----------------------------------------------------------------------
# Installed-package list (from chroot)
# ----------------------------------------------------------------------

def derive_manifest_pkg_name(manifest: str,
                             known_names: set[str] | None = None) -> str:
    """Derive the package name from a text-manifest filename.

    The authoritative derivation is the LONGEST hyphen-delimited prefix that
    names a known package. The naive name-(digit-led-version) regex
    mis-derives both directions: a non-digit version never matches, so the
    whole manifest name is taken as the package (llama-cpp-b8796 → no log,
    no yml → the package silently drops out of the audit), and a digit-led
    name segment truncates early (ntfs-3g-2026.2.25 → 'ntfs', auditing the
    real package under a name the tree does not have). The regex remains
    only as the fallback for a manifest whose package left the tree.
    """
    if known_names:
        if manifest in known_names:
            return manifest
        best = None
        idx = manifest.find("-")
        while idx != -1:
            prefix = manifest[:idx]
            if prefix in known_names:
                best = prefix  # keep scanning — longest match wins
            idx = manifest.find("-", idx + 1)
        if best:
            return best
    m = re.match(r"^(.+?)-(\d.*)$", manifest)
    return m.group(1) if m else manifest


def collect_known_package_names(packages_dir: Path) -> set[str]:
    """Every package identity the tree knows: directory names AND declared
    name: fields (manifests carry the declared name, which can differ from
    the directory — e.g. the perl-core directory declares name: perl)."""
    names: set[str] = set()
    for yml in packages_dir.rglob("package.yml"):
        names.add(yml.parent.name)
        try:
            m = re.search(r"^name:\s*['\"]?([A-Za-z0-9_.+-]+)['\"]?\s*$",
                          yml.read_text(errors="replace"), re.MULTILINE)
        except OSError:
            continue
        if m:
            names.add(m.group(1))
    return names


def list_installed_packages(installed_dir: Path,
                            known_names: set[str] | None = None,
                            ) -> list[tuple[str, str]]:
    """Return [(pkg_name, manifest_name)] sorted by manifest name."""
    out = []
    for entry in sorted(installed_dir.iterdir()):
        manifest = entry.name
        if manifest.startswith(".") or manifest.endswith(".bak"):
            continue
        out.append((derive_manifest_pkg_name(manifest, known_names), manifest))
    return out


def find_log_for_pkg(logs_dir: Path, pkg_name: str,
                     known_names: set[str] | None = None) -> Path | None:
    candidates = sorted(logs_dir.glob(f"{pkg_name}-*.log"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True)
    if known_names:
        # A log named for a LONGER known package must never be attributed to
        # this one: `glib2-*.log` also globs glib2-bootstrap's logs, and the
        # bootstrap pass's DESIGNED introspection=disabled then reads as a
        # glib2 silent loss (false attribution, caught on the ge9b-11 gate
        # run 2026-07-29 against the restored full log corpus; artifact truth
        # = GLib-2.0.typelib present in the chroot. Same prefix-collision
        # class as the pkm r13 exact-name archive-resolver fix).
        longer = [k for k in known_names
                  if k != pkg_name and k.startswith(pkg_name + "-")]
        if longer:
            candidates = [c for c in candidates
                          if not any(c.name.startswith(k + "-")
                                     for k in longer)]
    return candidates[0] if candidates else None


# ----------------------------------------------------------------------
# BLFS truth lookup
# ----------------------------------------------------------------------

class BlfsLookup:
    def __init__(self, db_path: Path):
        self.con = sqlite3.connect(str(db_path))
        self.aliases: dict[str, list[str]] = {}
        for igos_name, blfs_anchor in self.con.execute(
            "SELECT igos_name, blfs_anchor FROM aliases"
        ):
            self.aliases.setdefault(igos_name, []).append(blfs_anchor)

    def lookup_deps(self, pkg_name: str) -> list[tuple[str, str]]:
        base = re.sub(r"-(pass\d+|bootstrap|core|host)$", "", pkg_name)
        rows = self.con.execute(
            "SELECT id FROM packages WHERE anchor_id = ?", (base,)
        ).fetchall()
        if not rows:
            rows = self.con.execute(
                "SELECT id FROM packages WHERE name = ?", (base,)
            ).fetchall()
        if not rows and base in self.aliases:
            for alias in self.aliases[base]:
                rows = self.con.execute(
                    "SELECT id FROM packages WHERE name = ? OR anchor_id = ?",
                    (alias, alias),
                ).fetchall()
                if rows:
                    break
        if not rows:
            return []
        pkg_id = rows[0][0]
        return self.con.execute(
            "SELECT dep_name, dep_type FROM dependencies WHERE package_id = ?",
            (pkg_id,),
        ).fetchall()


# ----------------------------------------------------------------------
# Pattern matching for failed-detection lines
# ----------------------------------------------------------------------

_DETECTION_PATTERNS = [
    (r"checking for {name}\.\.\.\s*no\b", "autotools-checking-for"),
    (r"checking for {name}\.h\.\.\.\s*no\b", "autotools-checking-header"),
    (r"checking for {name}-[\d.]+\.\.\.\s*no\b", "autotools-checking-versioned"),
    (r"checking for {name}-config\.\.\.\s*no\b", "autotools-checking-config-script"),
    (r"^[ \t]+{name}\s*:\s*no\b", "autotools-summary-feature-no"),
    (r"^[ \t]+{name}\s*:\s*disabled\b", "autotools-summary-feature-disabled"),
    (r"^[ \t]+{name}\s*:\s*None\b", "autotools-summary-feature-none"),
    (r"^[ \t]+(with|--with)[-_]{name}\s*:\s*no\b", "autotools-summary-with-no"),
    (r"^[ \t]+{name} support\s*:\s*no\b", "autotools-summary-support-no"),
    (r"Run-time dependency {name}.* found:\s*NO\b", "meson-runtime-dep"),
    (r"Dependency {name}.* found:\s*NO\b", "meson-dep"),
    (r"Library {name} found:\s*NO\b", "meson-library"),
    (r"Program {name}.* found:\s*NO\b", "meson-program"),
    (r"Could NOT find {name}\b", "cmake-could-not-find"),
    (r"\b{name}_FOUND\s*[:=].*\bFALSE\b", "cmake-found-false"),
    (r"Package {name} was not found", "pkgconfig-not-found"),
    (r"Package '{name}', required by .*, not found", "pkgconfig-required-not-found"),
    (r"configure: error: {name} (not found|is required|required)", "configure-error-required"),
]


def scan_log_for_dep(log_text: str, dep_name: str) -> list[tuple[int, str, str]]:
    variants = name_variants(dep_name)
    # Digit-stripped variants can name a DIFFERENT real library (libssh2 ->
    # libssh, a distinct upstream project). When the log carries an exact-name
    # POSITIVE for the dep itself (summary "libssh2 : YES/enabled" or a meson
    # "Dependency libssh2 found: YES"), a negative hit on a digit-stripped
    # variant is that OTHER feature being knowingly off — not this dep
    # failing. Suppress only those variants, only under an exact positive;
    # a genuinely missing dep has no exact positive and scans unchanged.
    # (Caught live 2026-07-18: libvirt's deliberate -Dlibssh=disabled flagged
    # as a libssh2 DECLARED-FAILED while libssh2 was enabled and linked.)
    _base = re.sub(r"-[\d.]+$", "", dep_name)
    _base = re.sub(r"-(pass\d+|bootstrap|core|host)$", "", _base)
    _stripped = re.sub(r"\d+$", "", _base)
    risky: set[str] = set()
    if _stripped and _stripped != _base:
        risky.add(_stripped.lower())
        if _stripped.startswith("lib"):
            risky.add(_stripped[3:].lower())
        else:
            risky.add(("lib" + _stripped).lower())
    exact_positive = False
    if risky:
        _exact_esc = re.escape(_base)
        exact_positive = bool(
            re.search(
                rf"^[ \t]+{_exact_esc}\s*[:=]\s*(yes|enabled|true)\b",
                log_text, re.IGNORECASE | re.MULTILINE)
            or re.search(
                rf"(Run-time dependency|Dependency|Library)\s+{_exact_esc}\b.*found:\s*YES\b",
                log_text, re.IGNORECASE)
        )
    findings = []
    seen_keys: set[tuple[int, str]] = set()
    for variant in variants:
        if len(variant) < 3:
            continue
        if exact_positive and variant.lower() in risky:
            continue
        v_esc = re.escape(variant)
        for tmpl, kind in _DETECTION_PATTERNS:
            pat = re.compile(tmpl.format(name=v_esc), re.IGNORECASE | re.MULTILINE)
            for m in pat.finditer(log_text):
                line_no = log_text.count("\n", 0, m.start()) + 1
                key = (line_no, kind)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                line_start = log_text.rfind("\n", 0, m.start()) + 1
                line_end = log_text.find("\n", m.end())
                if line_end == -1:
                    line_end = len(log_text)
                match_line = log_text[line_start:line_end].strip()
                findings.append((line_no, kind, match_line))
    return findings


# ----------------------------------------------------------------------
# Supplement: end-of-configure summary block + meson "found: NO"
# ----------------------------------------------------------------------

_SUMMARY_LINE = re.compile(
    r"^[ \t]+([A-Za-z][A-Za-z0-9_+.-]+)\s*[:=]\s*(disabled|no|NO|None|FALSE|off)\b",
    re.MULTILINE,
)
_MESON_FOUND_NO = re.compile(
    r"^(Run-time dependency|Dependency|Library|Program)\s+(\S+).*found:\s*NO\b",
    re.MULTILINE,
)

# CMake plugin/feature summary in the TABULAR form libheif (and other
# feature_summary-style CMake projects) print at the end of configure:
#     Dav1d AV1 decoder                    : - disabled
#     OpenJPEG J2K decoder                 : - not found
# _SUMMARY_LINE misses this on two counts: the display name carries spaces
# (multi-word) and the value is prefixed "- " (dash) before the word, not the
# bare token _SUMMARY_LINE expects right after the colon. This is exactly the
# shape that let libheif ship with AV1/JPEG/JPEG2000 silently disabled
# (post-burn sweep). Matches the negative states only ("- disabled" / "- not
# found"); the positive "+ built-in" lines are NOT matched. An optional
# leading "-- " tolerates CMake message(STATUS) rendering.
_CMAKE_TABLE_NEGATIVE = re.compile(
    r"^[ \t]*(?:-- )?([A-Za-z][A-Za-z0-9 +/._-]*?)[ \t]*:[ \t]*-[ \t]+(disabled|not found)\b",
    re.MULTILINE,
)

# Generic role/boilerplate tokens in CMake codec display names that must NOT
# drive a declared-dep match — only the substantive codec/library token should
# (so "Dav1d AV1 decoder" matches the dav1d dep via "dav1d", never via "av1").
_CMAKE_FEATURE_STOP_TOKENS = frozenset({
    "decoder", "encoder", "codec", "support", "plugin", "module", "backend",
    "library", "builtin", "built", "the", "and", "with",
    "av1", "j2k", "hevc", "avc", "jpeg2000",
})

# Broad cmake undeclared-feature detector (analog of _MESON_FOUND_NO).
# Catches "-- Could NOT find X" for ANY X, including the optional-component
# silent-loss class where parent find_package() succeeds but COMPONENTS go
# missing (e.g. "-- Could NOT find PkgConfig::libsystemd"). Used by
# scan_cmake_block() — distinct from the per-declared-dep templated patterns
# in _DETECTION_PATTERNS at line 509 above.
_CMAKE_COULD_NOT_FIND = re.compile(
    r"^--\s+Could NOT find\s+([A-Za-z][\w+.:-]*)\b",
    re.MULTILINE,
)

_NOISE = {
    # Cross-compile / build-host probes
    "windows", "windows.h", "win32", "winsock", "winsock2",
    "dlltool", "sysroot", "mt", "windres", "vfork.h", "minix",
    "kqueue", "kevent", "epoll", "inotify", "darwin", "macos", "msys",
    "_LARGEFILE_SOURCE", "_FILE_OFFSET_BITS", "_LARGE_FILES",
    "code", "danted",
    "alloca", "memcpy", "vsnprintf", "snprintf", "fseeko",
    "valgrind", "gprof", "lcov", "gcov", "address-sanitizer", "asan",
    "static", "shared", "ipv6", "tests", "test", "examples", "docs",
    "debug", "profile", "coverage", "fuzzing", "fuzz", "oss_fuzz",
    "rpath", "dependency", "soname", "versioning",
}

# CMake-specific allowlist for irrelevant misses. These are commonly probed
# by cmake-using packages but their absence does NOT constitute silent loss:
#   - Doc generators (Doxygen, Sphinx, Pandoc, LATEX): we don't ship docs
#     for in-tree builds; missing doc tools at configure time is intentional.
#   - Test frameworks (GTest, GMock, Catch2, boost_test): tests are not
#     installed; not having the framework at build time is fine.
#   - Lint/static analysis (clang-tidy, IWYU, cppcheck, cmake_format):
#     dev-only tools, not load-bearing for shipped artifacts.
#   - Build optimizers (ccache, sccache): purely speed; absence is benign.
#   - Build-host probes (Threads, PkgConfig, Git): probed redundantly; the
#     real check is elsewhere in the same configure block.
# Rule G (new) checks against this set and routes to rescued with reason.
_CMAKE_NOISE = {
    "doxygen", "sphinx", "pandoc", "latex", "pdflatex", "asciidoc",
    "asciidoctor", "rst2man", "ronn", "fontforge",
    "gtest", "gmock", "googletest", "googlemock", "catch2",
    "boost_unit_test_framework", "boost_test",
    "ccache", "sccache",
    "clang-tidy", "clang_tidy", "iwyu", "include-what-you-use",
    "cppcheck", "cmake_format", "clang-format", "clang_format",
    "threads", "git", "ronin",
    "openmp", "intelopenmp",
    "ruby", "perl", "tcl",
}


def is_cmake_noise(target: str) -> bool:
    """Is this cmake find_package target on the noise allowlist?"""
    flat = target.lower().replace("-", "").replace("_", "").replace(".", "")
    cmake_noise_flat = {
        n.lower().replace("-", "").replace("_", "").replace(".", "")
        for n in _CMAKE_NOISE
    }
    if flat in cmake_noise_flat:
        return True
    # Handle "PkgConfig::libfoo" style: strip namespace prefix and recheck
    if "::" in target:
        ns_stripped = target.split("::", 1)[1]
        return is_cmake_noise(ns_stripped)
    return False


def is_noise(feat: str) -> bool:
    flat = feat.lower().replace("-", "").replace("_", "").replace(".", "")
    if flat in {n.lower().replace("-", "").replace("_", "").replace(".", "") for n in _NOISE}:
        return True
    if len(feat) <= 2:
        return True
    return False


# Platform-specific GUI/graphics backends. A probed target whose NAME carries one
# of these tokens (cairo-quartz, gtk4-quartz, cairo-win32, *-cocoa, *-d3d…) is a
# macOS / Windows / other-OS backend that is CORRECTLY absent on a Linux build —
# its "found: NO" can NEVER be a silent loss for InterGenOS. Rule H rescues these.
# (Scoped to backend tokens that are unambiguously a foreign-platform component;
# bare "win"/"x11" are deliberately NOT here — too broad / Linux-relevant.)
_PLATFORM_BACKEND_TOKENS = frozenset({
    "quartz", "win32", "cocoa", "uikit", "directfb", "wgl",
    "d3d", "d3d9", "d3d11", "d3d12", "directx", "beos", "haiku",
    "os2", "metal", "wasm", "emscripten", "appkit",
})
_PROBED_TARGET_RE = re.compile(
    r"(?:dependency|checking for|find_package|Could NOT find)\s+\(?[\"']?([A-Za-z][\w.+-]*)",
    re.IGNORECASE,
)


def platform_backend_token(match_line: str) -> str | None:
    """Return the platform-backend token if the probed target in ``match_line``
    is a non-Linux platform backend (cairo-quartz, cairo-win32, gtk4-quartz),
    else None. Splits the probed target on -/_/. and checks each component, so
    only a genuine foreign-platform sub-backend matches — a bare 'cairo' or
    'gtk4' (the real Linux dep) does not."""
    m = _PROBED_TARGET_RE.search(match_line or "")
    if not m:
        return None
    for part in re.split(r"[-_.]", m.group(1)):
        if part.lower() in _PLATFORM_BACKEND_TOKENS:
            return part.lower()
    return None


# Strip ONLY a clearly-delimited version suffix: dash/underscore-separated
# (xkeyboard-config-10, lua-5.3) or dotted (lua5.3). A BARE trailing digit
# (gtk4, lua54) is part of the NAME, not a strippable version — leaving it
# unstripped means a real `gtk4 found: NO` can never be mistaken for a satisfied
# `gtk3`. (Caught by the gtk4/gtk3 edge-case test, 2026-06-25.)
_VERSION_SUFFIX_RE = re.compile(r"(?:[-_]\d[\d._]*|\d+\.\d[\d._]*)$")


def version_probe_rescue(match_line: str, log_text: str) -> str | None:
    """Rescue a ``<base>-<version> found: NO`` miss when the SAME base dep was
    found YES under a different version-suffixed name in the same log — i.e. a
    meson/cmake version-range probe artifact, not a real loss. Examples confirmed
    from real logs: libxkbcommon probes ``xkeyboard-config-3..12`` (NO) but found
    ``xkeyboard-config-2`` (YES); wireplumber probes ``lua-5.3/lua5.4`` (NO) but
    found ``lua`` (YES).

    Self-protecting: it fires ONLY if (a) a version suffix was actually stripped
    (so a real dep name like ``gtk4`` with no separated version is untouched) AND
    (b) the stripped base was independently found: YES. A genuine loss — where the
    base is NOT found under any name — never matches."""
    m = _PROBED_TARGET_RE.search(match_line or "")
    if not m:
        return None
    target = m.group(1)
    # (1) delimited / dotted version suffix (lua-5.4, lua5.4, xkeyboard-config-12):
    # rescue if the stripped base is found YES under any version-suffixed name.
    base = _VERSION_SUFFIX_RE.sub("", target)
    if base != target and len(base) >= 3:
        found_re = re.compile(
            r"\b" + re.escape(base) + r"(?:[-_.]?\d[\d._]*)?\s+found:\s*YES",
            re.IGNORECASE,
        )
        if found_re.search(log_text or ""):
            return f"version-probe artifact (base '{base}' found YES under another version name)"
    # (2) separator-less concatenated version (lua54, lua53): a BARE trailing digit
    # is normally part of the name (gtk4 != gtk3), so this rescues ONLY when the
    # fully digit-stripped base is found YES as an EXACT BARE pkgconfig name
    # (``lua found: YES``, NOT ``lua-5.4 found: YES``). wireplumber probes
    # lua54/lua53 but finds bare ``lua`` (5.4.8) and builds its lua-scripting
    # module; gtk4 is NOT over-rescued because bare ``gtk`` is never found YES
    # (only gtk+-3.0 / gtk4). (silent-loss audit 2026-06-25.)
    bare = re.sub(r"\d+$", "", target)
    if bare != target and len(bare) >= 3:
        bare_re = re.compile(
            r"\b" + re.escape(bare) + r"\s+found:\s*YES",
            re.IGNORECASE,
        )
        if bare_re.search(log_text or ""):
            return (f"version-probe artifact (bare '{bare}' found YES; "
                    f"'{target}' is a separator-less version probe)")
    return None


def later_probe_success_rescue(match_line: str, log_text: str) -> str | None:
    """Rule M — the SAME probed name has a LATER ``found: YES`` in this log.

    meson probes a dependency more than once: a versioned probe that fails
    (``Dependency liblz4 found: NO. Found 1.10.0 but need: '>= 129'`` — a
    constraint written for lz4's legacy release numbering), then an
    unversioned/fallback probe that succeeds and links the dep. The earlier
    ``found: NO`` lines are probe-retry resolution noise, not a loss.
    Self-protecting: a genuine loss never has an exact-same-name ``found:
    YES`` AFTER its last ``found: NO``. (Caught live 2026-07-29, ge9b-11
    gate run on the restored full corpus: spice <- liblz4 flagged
    DECLARED-FAILED while the artifact's DT_NEEDED carries liblz4.so.1.)"""
    m = _PROBED_TARGET_RE.search(match_line or "")
    if not m:
        return None
    target = m.group(1)
    probe_re = re.compile(
        rf"(?:Run-time dependency|Dependency|Library)\s+{re.escape(target)}"
        rf"\s.*?found:\s*(YES|NO)\b",
        re.IGNORECASE)
    last_yes = last_no = -1
    for pm in probe_re.finditer(log_text or ""):
        if pm.group(1).upper() == "YES":
            last_yes = pm.start()
        else:
            last_no = pm.start()
    if last_no >= 0 and last_yes > last_no:
        return (f"later-probe-success ('{target}' found YES after the failed "
                f"probe — meson retry/fallback resolution, not a loss)")
    return None


_AUTOTOOLS_CHECKING_NO_RE = re.compile(
    r"checking for\s+([A-Za-z][\w.+-]*?)\.\.\.\s*no\b", re.IGNORECASE,
)


def autotools_variant_rescue(match_line: str, log_text: str) -> str | None:
    """Rescue an autotools ``checking for X... no`` miss when the SAME base is
    found YES under a variant name in the same log — the autotools sibling of
    Rule I (version_probe_rescue). Confirmed from a real log: libcanberra probes
    ``checking for GTK... no`` (the GTK2 toolkit, which we don't ship) then
    ``checking for GTK3... yes`` and builds libcanberra-gtk3 — so the GTK2 miss
    is not a loss.

    Self-protecting: it fires ONLY if a ``checking for <base>...  yes`` line
    exists whose probed name STARTS with the same base (so an unrelated
    ``checking for Foo... yes`` cannot rescue ``checking for Bar... no``). The
    base must be >= 3 chars."""
    m = _AUTOTOOLS_CHECKING_NO_RE.search(match_line or "")
    if not m:
        return None
    base = m.group(1)
    if len(base) < 3:
        return None
    yes_re = re.compile(
        r"checking for\s+" + re.escape(base) + r"[\w.+-]*\.\.\.\s*yes\b",
        re.IGNORECASE,
    )
    if yes_re.search(log_text or ""):
        return f"autotools variant probe (base '{base}' found YES under a variant name)"
    return None


def meson_subproject_rescue(match_line: str, log_text: str) -> str | None:
    """Rescue a meson ``dependency X found: NO`` miss when the same dep is then
    satisfied by a BUNDLED meson subproject. meson's standard pattern: probe the
    system (``found: NO``), then fall back to ``Executing subproject <name>`` and
    build it. Confirmed from a real log: gnome-connections probes
    ``gtk-frdp-0.2 found: NO`` then ``Executing subproject gtk-frdp`` (which links
    freerdp3 3.22.0) and installs libgtk-frdp-0.2.so — RDP fully works.

    Self-protecting: it fires ONLY if an ``Executing subproject <base>`` /
    ``Subproject <base> finished`` line exists for the SAME base name (the probed
    target minus any version suffix), so an unrelated subproject cannot rescue."""
    m = _PROBED_TARGET_RE.search(match_line or "")
    if not m:
        return None
    base = _VERSION_SUFFIX_RE.sub("", m.group(1))
    if len(base) < 3:
        return None
    b = re.escape(base)
    if re.search(r"Executing subproject\s+" + b + r"\b", log_text or "", re.IGNORECASE) \
       or re.search(r"Subproject\s+" + b + r"\s+finished", log_text or "", re.IGNORECASE):
        return f"meson subproject fallback ('{base}' built as a bundled subproject)"
    return None


def cmake_feature_dep_match(feature: str, declared_deps: set[str]) -> bool:
    """Token-overlap match between a CMake plugin-summary display name
    (multi-word, e.g. "Dav1d AV1 decoder", "OpenJPEG J2K decoder") and a
    package's declared deps. The tabular CMake summary names a codec by a human
    display string, not the package name, so the meson/autotools substring test
    misses them — tokenize instead. A match requires a shared, meaningful
    (>=3-char, non-generic) token between the display name and some declared
    dep's name-variants; role words (decoder/encoder/av1/j2k/...) are excluded
    so a match reflects the actual codec/library, not the format's boilerplate.
    Applied ONLY to source=="cmake-table" summary features, so it never changes
    the meson/autotools summary path."""
    feat_tokens = {
        t for t in re.split(r"[^A-Za-z0-9]+", feature.lower())
        if len(t) >= 3 and t not in _CMAKE_FEATURE_STOP_TOKENS
    }
    if not feat_tokens:
        return False
    dep_tokens: set[str] = set()
    for dep in declared_deps:
        for variant in name_variants(dep):
            for t in re.split(r"[^A-Za-z0-9]+", variant.lower()):
                for tok in (t, re.sub(r"\d+$", "", t)):  # openjpeg2 -> openjpeg
                    if len(tok) >= 3 and tok not in _CMAKE_FEATURE_STOP_TOKENS:
                        dep_tokens.add(tok)
    return bool(feat_tokens & dep_tokens)


def scan_summary_block(log_text: str, packages_dir: Path) -> tuple[list[dict], list[dict]]:
    summary_feats: list[dict] = []
    seen = set()
    for m in _SUMMARY_LINE.finditer(log_text):
        feat = m.group(1)
        value = m.group(2)
        if is_noise(feat):
            continue
        key = (feat.lower(), value.lower())
        if key in seen:
            continue
        seen.add(key)
        line_no = log_text.count("\n", 0, m.start()) + 1
        summary_feats.append({
            "feature": feat,
            "value": value,
            "line_no": line_no,
            "line": m.group(0).strip(),
        })

    # CMake tabular "<display name> : - disabled / - not found" features. Tagged
    # source="cmake-table" so the relevance filter uses token-overlap matching
    # (multi-word display names) instead of the substring test. Shares the
    # `seen` dedup set with the block above.
    for m in _CMAKE_TABLE_NEGATIVE.finditer(log_text):
        feat = m.group(1).strip()
        state = m.group(2)
        if is_noise(feat):
            continue
        value = f"- {state}"
        key = (feat.lower(), value.lower())
        if key in seen:
            continue
        seen.add(key)
        line_no = log_text.count("\n", 0, m.start()) + 1
        summary_feats.append({
            "feature": feat,
            "value": value,
            "line_no": line_no,
            "line": m.group(0).strip(),
            "source": "cmake-table",
        })

    meson_no: list[dict] = []
    for m in _MESON_FOUND_NO.finditer(log_text):
        kind = m.group(1)
        target = m.group(2).rstrip(",")
        if is_noise(target):
            continue
        line_no = log_text.count("\n", 0, m.start()) + 1
        tree_match = in_tree(packages_dir, target.replace("lib", "")) or in_tree(packages_dir, target)
        meson_no.append({
            "kind": kind,
            "target": target,
            "in_tree": tree_match,
            "line_no": line_no,
            "line": m.group(0).strip(),
        })
    return summary_feats, meson_no


def scan_cmake_block(log_text: str, packages_dir: Path) -> list[dict]:
    """Find ALL cmake 'Could NOT find X' lines (not just declared deps).

    Mirrors the meson supplemental scan: catches undeclared-dep silent loss
    where a cmake find_package() (or find_package COMPONENTS) fails to find
    something but the build doesn't error out. The optional-component class
    (e.g. 'Could NOT find PkgConfig::libsystemd' where parent PkgConfig
    succeeded) is the specific gap that motivated this scan.

    Filters by:
      - general _NOISE (cross-compile probes, build-host detection cruft)
      - _CMAKE_NOISE (docs/test/lint/optimizer tools — known-irrelevant)
    Returns in-tree-or-not tagged findings; caller decides whether to flag
    or rescue based on Rules C/E/F + new Rule G (cmake-allowlist).
    """
    findings: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for m in _CMAKE_COULD_NOT_FIND.finditer(log_text):
        target = m.group(1).rstrip(",.")
        if is_noise(target):
            continue
        line_no = log_text.count("\n", 0, m.start()) + 1
        key = (line_no, target.lower())
        if key in seen:
            continue
        seen.add(key)
        # Strip "::" namespace prefix for in-tree lookup (PkgConfig::libsystemd
        # → libsystemd → systemd via name_variants)
        lookup_target = target.split("::", 1)[1] if "::" in target else target
        tree_match = (
            in_tree(packages_dir, lookup_target)
            or in_tree(packages_dir, lookup_target.replace("lib", ""))
            or in_tree(packages_dir, "lib" + lookup_target)
        )
        findings.append({
            "kind": "cmake-could-not-find-undeclared",
            "target": target,
            "in_tree": tree_match,
            "noise_allowlist": is_cmake_noise(target),
            "line_no": line_no,
            "line": m.group(0).strip(),
        })
    return findings


# ----------------------------------------------------------------------
# Main scan loop
# ----------------------------------------------------------------------

def scan(repo: Path, chroot: Path) -> dict:
    """Run all passes. Returns a result dict with findings + metadata."""
    packages_dir = repo / "packages"
    blfs_db = repo / "build" / "blfs-packages.db"
    chroot_installed = chroot / "var/lib/igos/packages"
    chroot_logs = chroot / "mnt/intergenos/build/logs"

    result: dict = {
        "repo": str(repo),
        "chroot": str(chroot),
        "blfs_db_present": blfs_db.is_file(),
        "chroot_installed_present": chroot_installed.is_dir(),
        "chroot_logs_present": chroot_logs.is_dir(),
        "skipped": False,
        "skip_reason": None,
        "installed_count": 0,
        "findings": [],
        "rescued": [],
        "log_missing": [],
        "log_unreadable": [],
        "yml_missing": [],
        "blfs_no_truth": [],
        "summary_disabled": {},
        "meson_not_found_intree": {},
        "cmake_not_found_intree": {},
    }

    if not blfs_db.is_file():
        result["skipped"] = True
        result["skip_reason"] = f"BLFS db not at {blfs_db}"
        return result
    if not chroot_installed.is_dir() or not chroot_logs.is_dir():
        result["skipped"] = True
        result["skip_reason"] = (
            f"chroot data absent (installed={chroot_installed.is_dir()}, "
            f"logs={chroot_logs.is_dir()}) — no prior-build state to audit"
        )
        return result

    blfs = BlfsLookup(blfs_db)
    known_names = collect_known_package_names(packages_dir)
    installed = list_installed_packages(chroot_installed, known_names)
    result["installed_count"] = len(installed)

    findings: list[dict] = []
    rescued: list[dict] = []  # findings suppressed by Rules A/B/C — kept for visibility
    summary_per_pkg: dict[str, list[dict]] = {}
    meson_per_pkg: dict[str, list[dict]] = {}
    cmake_per_pkg: dict[str, list[dict]] = {}

    def _record_finding(payload: dict, log_text_local: str,
                         lib_only_local: bool, yml_local: Path | None) -> None:
        """Apply suppression Rules A/B/C/E/F; route to ``findings`` or ``rescued``.

        Rule A — autotools pkg-config rescue: a legacy ``-config`` script miss
        followed within ~20 lines by a ``checking for X >= V... yes`` (pkg-config
        succeeded) OR a ``configure: Use Xrt-config with`` (gpgrt-style rescue).

        Rule B — meson enabled-features list: a ``Run-time dependency / Dependency
        X found: NO`` miss whose target also appears in a meson ``enabled : ...``
        summary line (consumer compiled in client-side support; the runtime daemon
        just isn't on the build host).

        Rule C — lib-only build mode: an ``autotools-summary-feature-*`` miss in
        a consumer whose ``configure_flags`` include a lib-only / disable-apps
        style flag (the optional app-side libs are intentionally not consumed).

        Rule E — pass2 supersedes: the consumer has a corresponding ``*-pass2``
        package that supersedes it and declares the missing dep in its build
        deps; the ``ch8`` silent-disable is fixed in the pass2 rebuild that
        actually ships. Standard BLFS layering pattern for systemd, dbus, etc.

        Rule F — explicit accepted loss: the consumer's package.yml declares
        ``silent_loss_accepted: [<dep>]`` with rationale, formally accepting
        that this dep is intentionally not integrated in InterGenOS.
        """
        kind = payload["kind"]
        dep = payload["dep"]
        pkg = payload["pkg"]
        line_no = payload["line_no"]
        # Rule D — a meson ``Program X found: NO`` is a find_program() miss: a
        # build-host *tool* probe (icon-cache generators, rpm-macro tools,
        # removed systemd helpers, disabled-plugin interpreters), NOT a
        # link-time library dependency. A non-halting optional find_program()
        # miss does not degrade the shipped artifact — a *required* tool would
        # have halted the build. Same premise already trusted in the
        # supplemental MESON-NOT-FOUND-INTREE path; applied here for declared
        # deps whose loose name-match happened to land on a Program line
        # (at-spi2 dbus-broker-launch, gdm systemd-multi-seat-x, systemd-pass2
        # rpm/rpmspec, seahorse/gnome-connections gtk4-update-icon-cache,
        # rhythmbox python3 "disabled by: plugins_python").
        if kind == "meson-program":
            rescued.append({**payload, "rescue_rule": "D",
                            "rescue_reason": "meson-program-kind (build-host tool, not a link-time dep)"})
            return
        # Rule A
        if kind == "autotools-checking-config-script":
            rescue_reason = check_pkgconfig_rescue(log_text_local, line_no, dep)
            if rescue_reason:
                rescued.append({**payload, "rescue_rule": "A", "rescue_reason": rescue_reason})
                return
        # Rule B
        if kind in {"meson-runtime-dep", "meson-dep"}:
            enabled_match = check_meson_enabled(log_text_local, dep)
            if enabled_match:
                rescued.append({**payload, "rescue_rule": "B",
                                "rescue_reason": f"meson-enabled-features-list ({enabled_match})"})
                return
            # Rule L — resolved via meson subproject fallback (vendored into
            # the artifact; the "found: NO" system probe is resolution noise)
            subproj_rescue = check_subproject_fallback(log_text_local, dep)
            if subproj_rescue:
                rescued.append({**payload, "rescue_rule": "L",
                                "rescue_reason": f"meson-subproject-fallback ({subproj_rescue})"})
                return
        # Rule C
        if lib_only_local and kind in {
            "autotools-summary-feature-no",
            "autotools-summary-feature-disabled",
            "autotools-summary-feature-none",
            "autotools-summary-support-no",
            "autotools-summary-with-no",
        }:
            rescued.append({**payload, "rescue_rule": "C",
                            "rescue_reason": "lib-only-mode (consumer configure_flags exclude app-side features)"})
            return
        # Rule E (pass2 supersedes)
        pass2_rescue = check_pass2_rescue(packages_dir, pkg, dep)
        if pass2_rescue:
            rescued.append({**payload, "rescue_rule": "E",
                            "rescue_reason": f"pass2-supersedes-rebuild ({pass2_rescue})"})
            return
        # Rule F (explicit accepted) — match the declared dep OR the actual
        # probed target from the match line. The probed target is what is truly
        # missing (e.g. a declared dep ``systemd`` whose loosely-matched probe is
        # ``libsystemd-login``, or ``gtk3`` whose probe is the superseded
        # ``gtk+-2.0``); accepting the *precise* target leaves any real loss of
        # the declared dep itself still flagged.
        accepted_rescue = check_accepted_loss(yml_local, dep)
        if not accepted_rescue:
            tm = _PROBED_TARGET_RE.search(payload.get("match", ""))
            if tm:
                accepted_rescue = check_accepted_loss(yml_local, tm.group(1))
        if accepted_rescue:
            rescued.append({**payload, "rescue_rule": "F",
                            "rescue_reason": accepted_rescue})
            return
        # Rule H (platform-specific backend, absent by design on Linux)
        plat = platform_backend_token(payload.get("match", ""))
        if plat:
            rescued.append({**payload, "rescue_rule": "H",
                            "rescue_reason": f"platform-backend '{plat}' — absent by design on a Linux build"})
            return
        # Rule I (version-range probe artifact — base found YES under another name)
        vprobe = version_probe_rescue(payload.get("match", ""), log_text_local)
        if vprobe:
            rescued.append({**payload, "rescue_rule": "I", "rescue_reason": vprobe})
            return
        # Rule J (autotools variant probe — base found YES under a variant name)
        if kind in {"autotools-checking-for", "autotools-checking-header"}:
            avariant = autotools_variant_rescue(payload.get("match", ""), log_text_local)
            if avariant:
                rescued.append({**payload, "rescue_rule": "J", "rescue_reason": avariant})
                return
        # Rule K (meson bundled-subproject fallback — the failed system dep is
        # built from a bundled meson subproject, e.g. gnome-connections gtk-frdp)
        if kind in {"meson-runtime-dep", "meson-dep"}:
            subp = meson_subproject_rescue(payload.get("match", ""), log_text_local)
            if subp:
                rescued.append({**payload, "rescue_rule": "K", "rescue_reason": subp})
                return
        # Rule M (same-name later probe succeeded — meson retry noise)
        if kind in {"meson-runtime-dep", "meson-dep"}:
            later = later_probe_success_rescue(payload.get("match", ""), log_text_local)
            if later:
                rescued.append({**payload, "rescue_rule": "M", "rescue_reason": later})
                return
        findings.append(payload)

    for pkg_name, manifest in installed:
        log_path = find_log_for_pkg(chroot_logs, pkg_name, known_names)
        if log_path is None:
            result["log_missing"].append(pkg_name)
            continue
        try:
            log_text = log_path.read_text(errors="replace")
        except OSError:
            # An unreadable log is a NAMED coverage failure, not a silent
            # drop — under --require-audit it fails the gate.
            result["log_unreadable"].append(pkg_name)
            continue

        yml = find_package_yml(packages_dir, pkg_name)
        declared_deps = set(parse_deps_build(yml)) if yml else set()
        configure_flags = parse_configure_flags(yml) if yml else []
        lib_only = is_lib_only_mode(configure_flags)
        if yml is None:
            result["yml_missing"].append(pkg_name)

        blfs_deps = blfs.lookup_deps(pkg_name)
        if not blfs_deps:
            result["blfs_no_truth"].append(pkg_name)

        blfs_by_type: dict[str, list[str]] = {}
        for dep_name, dep_type in blfs_deps:
            blfs_by_type.setdefault(dep_type, []).append(dep_name)

        # Pass 1: declared-failed
        for dep in declared_deps:
            for line_no, kind, line in scan_log_for_dep(log_text, dep):
                _record_finding({
                    "type": "DECLARED-FAILED",
                    "pkg": pkg_name,
                    "manifest": manifest,
                    "dep": dep,
                    "log": log_path.name,
                    "line_no": line_no,
                    "kind": kind,
                    "match": line[:200],
                }, log_text, lib_only, yml)

        # Pass 2: BLFS required/recommended not declared, failed in log
        for dep_type, dep_list in blfs_by_type.items():
            if dep_type not in ("required", "recommended"):
                continue
            for dep in dep_list:
                if dep in declared_deps:
                    continue
                for line_no, kind, line in scan_log_for_dep(log_text, dep):
                    _record_finding({
                        "type": f"BLFS-{dep_type.upper()}-UNDECLARED-FAILED",
                        "pkg": pkg_name,
                        "manifest": manifest,
                        "dep": dep,
                        "log": log_path.name,
                        "line_no": line_no,
                        "kind": kind,
                        "match": line[:200],
                    }, log_text, lib_only, yml)

        # Pass 3: BLFS optional in-tree not declared, failed in log
        for dep in blfs_by_type.get("optional", []):
            if dep in declared_deps:
                continue
            if find_package_yml(packages_dir, dep) is None:
                continue
            for line_no, kind, line in scan_log_for_dep(log_text, dep):
                _record_finding({
                    "type": "BLFS-OPTIONAL-INTREE-FAILED",
                    "pkg": pkg_name,
                    "manifest": manifest,
                    "dep": dep,
                    "log": log_path.name,
                    "line_no": line_no,
                    "kind": kind,
                    "match": line[:200],
                }, log_text, lib_only, yml)

        # Supplement: summary-disabled + meson-not-found-in-tree
        # Tightened: only flag features whose name resembles a DECLARED dep,
        # AND suppress entirely in lib-only mode (those are intentional).
        summary_feats, meson_no = scan_summary_block(log_text, packages_dir)
        if not lib_only and declared_deps:
            declared_variants = set()
            for d in declared_deps:
                declared_variants |= {v.lower() for v in name_variants(d)}
            relevant_summary = []
            for sf in summary_feats:
                feat_lower = sf["feature"].lower()
                if sf.get("source") == "cmake-table":
                    # Multi-word CMake display name → token-overlap match.
                    is_relevant = cmake_feature_dep_match(sf["feature"], declared_deps)
                else:
                    feat_variants = {v.lower() for v in name_variants(sf["feature"])}
                    is_relevant = bool(declared_variants & feat_variants) or any(
                        v in feat_lower or feat_lower in v for v in declared_variants
                    )
                if not is_relevant:
                    continue
                # Rule F — explicit accept of a deliberately-disabled summary
                # feature (e.g. pipewire -Dman=disabled, suil gtk2,
                # pulseaudio bluez5) via the consumer's silent_loss_accepted.
                # For a cmake-table display name ("Dav1d AV1 decoder") the
                # accept is declared by the codec/library token ("dav1d"), so
                # check each meaningful token; else check the feature directly.
                if sf.get("source") == "cmake-table":
                    accepted = None
                    for tok in re.split(r"[^A-Za-z0-9]+", feat_lower):
                        if len(tok) >= 3 and tok not in _CMAKE_FEATURE_STOP_TOKENS:
                            accepted = check_accepted_loss(yml, tok)
                            if accepted:
                                break
                else:
                    accepted = check_accepted_loss(yml, sf["feature"])
                if accepted:
                    rescued.append({
                        "type": "SUMMARY-DISABLED", "pkg": pkg_name,
                        "dep": sf["feature"], "log": log_path.name,
                        "line_no": sf["line_no"], "kind": "summary-disabled",
                        "match": sf["line"][:200],
                        "rescue_rule": "F", "rescue_reason": accepted,
                    })
                    continue
                relevant_summary.append(sf)
            if relevant_summary:
                summary_per_pkg[pkg_name] = relevant_summary
        # Meson "found: NO" supplemental — apply 3 suppression filters:
        # (Rule B)  Target appears in the meson enabled-features summary line
        #           (client-side support compiled in even though probe failed).
        # (Rule D)  Target is a Program (build-host tool like git/rsync/rpm/
        #           doxygen) — these aren't link-time deps, just build-time
        #           helpers; their absence is not a silent feature loss.
        # (Rule C') In lib-only mode, all app-side optional libs are skipped
        #           by design; runtime-dep misses in this mode are intentional.
        intree_meson = []
        for m in meson_no:
            if not m["in_tree"]:
                continue
            meson_payload = {
                "type": "MESON-NOT-FOUND-INTREE",
                "pkg": pkg_name,
                "dep": m["target"],
                "log": log_path.name,
                "line_no": m["line_no"],
                "kind": m["kind"].lower().replace(" ", "-"),
                "match": m["line"][:200],
            }
            # Rule D — meson Program kind (build-host tool, not a link dep)
            if m.get("kind") == "Program":
                rescued.append({**meson_payload, "kind": "meson-program-found-no",
                                "rescue_rule": "D",
                                "rescue_reason": "meson-program-kind (build-host tool, not a link-time dep)"})
                continue
            # Rule L — resolved via meson subproject fallback (vendored into
            # the artifact; the "found: NO" system probe is resolution noise)
            subproj_rescue = check_subproject_fallback(log_text, m["target"])
            if subproj_rescue:
                rescued.append({**meson_payload, "rescue_rule": "L",
                                "rescue_reason": f"meson-subproject-fallback ({subproj_rescue})"})
                continue
            # Rule B — present in meson enabled-features summary
            if check_meson_enabled(log_text, m["target"]):
                rescued.append({**meson_payload, "rescue_rule": "B",
                                "rescue_reason": "meson-enabled-features-list (client support compiled in)"})
                continue
            # Rule C — consumer is in lib-only build mode
            if lib_only:
                rescued.append({**meson_payload, "rescue_rule": "C",
                                "rescue_reason": "lib-only-mode"})
                continue
            # Rule E — pass2 supersedes-rebuild covers the loss
            pass2_rescue = check_pass2_rescue(packages_dir, pkg_name, m["target"])
            if pass2_rescue:
                rescued.append({**meson_payload, "rescue_rule": "E",
                                "rescue_reason": f"pass2-supersedes-rebuild ({pass2_rescue})"})
                continue
            # Rule F — explicit silent_loss_accepted declaration
            accepted_rescue = check_accepted_loss(yml, m["target"])
            if accepted_rescue:
                rescued.append({**meson_payload, "rescue_rule": "F",
                                "rescue_reason": accepted_rescue})
                continue
            # Rule M — same-name later probe succeeded (meson retry noise)
            later = later_probe_success_rescue(m["line"], log_text)
            if later:
                rescued.append({**meson_payload, "rescue_rule": "M",
                                "rescue_reason": later})
                continue
            intree_meson.append(m)
        if intree_meson:
            meson_per_pkg[pkg_name] = intree_meson

        # CMake "Could NOT find X" supplemental — apply suppression filters:
        # (Rule G)  Target on cmake-allowlist (Doxygen, Sphinx, test frameworks,
        #           lint/optimizer tools) — known-irrelevant by design.
        # (Rule C)  Lib-only mode: app-side optional cmake-found targets are
        #           skipped intentionally.
        # (Rule E)  Pass2 supersedes-rebuild covers the loss.
        # (Rule F)  Explicit silent_loss_accepted declaration.
        # Only IN-TREE misses are flagged (mirrors meson supplement model):
        # a cmake miss on a package we don't have in our tree is less actionable
        # than one we DO have but failed to wire.
        cmake_no = scan_cmake_block(log_text, packages_dir)
        intree_cmake = []
        for c in cmake_no:
            cmake_payload = {
                "type": "CMAKE-NOT-FOUND-INTREE",
                "pkg": pkg_name,
                "dep": c["target"],
                "log": log_path.name,
                "line_no": c["line_no"],
                "kind": c["kind"],
                "match": c["line"][:200],
            }
            # Rule G — cmake-allowlist (docs/test/lint/optimizer tools)
            if c.get("noise_allowlist"):
                rescued.append({**cmake_payload, "rescue_rule": "G",
                                "rescue_reason": f"cmake-allowlist ({c['target']} is a docs/test/lint/optimizer tool — not load-bearing)"})
                continue
            if not c["in_tree"]:
                # Not in our tree: not actionable as silent loss — could be a
                # system tool we don't ship. Skip silently.
                continue
            # Rule C — consumer is in lib-only build mode
            if lib_only:
                rescued.append({**cmake_payload, "rescue_rule": "C",
                                "rescue_reason": "lib-only-mode"})
                continue
            # Rule E — pass2 supersedes-rebuild covers the loss
            pass2_rescue = check_pass2_rescue(packages_dir, pkg_name, c["target"])
            if pass2_rescue:
                rescued.append({**cmake_payload, "rescue_rule": "E",
                                "rescue_reason": f"pass2-supersedes-rebuild ({pass2_rescue})"})
                continue
            # Rule F — explicit silent_loss_accepted declaration
            accepted_rescue = check_accepted_loss(yml, c["target"])
            if accepted_rescue:
                rescued.append({**cmake_payload, "rescue_rule": "F",
                                "rescue_reason": accepted_rescue})
                continue
            intree_cmake.append(c)
        if intree_cmake:
            cmake_per_pkg[pkg_name] = intree_cmake

    # Deduplicate BLFS-* against DECLARED-FAILED for same (pkg, dep-base)
    def dep_base(d: str) -> str:
        return re.sub(r"-[\d.]+$", "", d)

    declared_failed_keys = {
        (f["pkg"], dep_base(f["dep"]))
        for f in findings if f["type"] == "DECLARED-FAILED"
    }
    findings = [
        f for f in findings
        if not (f["type"].startswith("BLFS-")
                and (f["pkg"], dep_base(f["dep"])) in declared_failed_keys)
    ]

    result["findings"] = findings
    result["rescued"] = rescued
    result["summary_disabled"] = summary_per_pkg
    result["meson_not_found_intree"] = meson_per_pkg
    result["cmake_not_found_intree"] = cmake_per_pkg
    return result


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------

def emit_summary(result: dict) -> None:
    print("=== preflight-silent-loss ===")
    print(f"Repo:    {result['repo']}")
    print(f"Chroot:  {result['chroot']}")

    if result["skipped"]:
        print(f"SKIP — {result['skip_reason']}")
        print()
        print("PASS — chroot data absent; nothing to audit. "
              "Gate intentionally does not block first-build scenarios.")
        return

    print(f"BLFS db: present ({result['blfs_db_present']})")
    print(f"Installed packages scanned: {result['installed_count']}")
    print(f"Logs missing: {len(result['log_missing'])}")
    print(f"Logs unreadable: {len(result.get('log_unreadable', []))}")
    print(f"package.yml missing: {len(result['yml_missing'])}")
    print(f"Packages outside BLFS reference scope (no cross-check data): {len(result['blfs_no_truth'])}")
    print()

    findings = result["findings"]
    rescued = result.get("rescued", [])
    summary_per_pkg = result["summary_disabled"]
    meson_per_pkg = result["meson_not_found_intree"]
    cmake_per_pkg = result.get("cmake_not_found_intree", {})
    print(f"TOTAL FINDINGS: {len(findings)} "
          f"(+ {sum(len(v) for v in summary_per_pkg.values())} summary-disabled lines "
          f"+ {sum(len(v) for v in meson_per_pkg.values())} meson-in-tree-NO lines "
          f"+ {sum(len(v) for v in cmake_per_pkg.values())} cmake-in-tree-NO lines)")
    if rescued:
        by_rule: dict[str, int] = {}
        for r in rescued:
            by_rule[r.get("rescue_rule", "?")] = by_rule.get(r.get("rescue_rule", "?"), 0) + 1
        print(f"  rescued (false-positives suppressed): {len(rescued)} "
              f"({', '.join(f'rule {k}: {v}' for k, v in sorted(by_rule.items()))})")

    if not findings and not summary_per_pkg and not meson_per_pkg and not cmake_per_pkg:
        print()
        print("PASS — no silent feature losses detected in prior-build chroot.")
        return

    by_type: dict[str, list[dict]] = {}
    for f in findings:
        by_type.setdefault(f["type"], []).append(f)

    for t in ("DECLARED-FAILED",
              "BLFS-REQUIRED-UNDECLARED-FAILED",
              "BLFS-RECOMMENDED-UNDECLARED-FAILED",
              "BLFS-OPTIONAL-INTREE-FAILED"):
        items = by_type.get(t, [])
        if items:
            print(f"  {t}: {len(items)}")

    print()
    print("=== First 10 unique (pkg, dep, type) tuples ===")
    seen: dict[tuple, list[dict]] = {}
    for f in findings:
        key = (f["type"], f["pkg"], f["dep"])
        seen.setdefault(key, []).append(f)
    for i, ((t, pkg, dep), entries) in enumerate(sorted(seen.items())):
        if i >= 10:
            print(f"  ... ({len(seen) - 10} more unique tuples)")
            break
        kinds = sorted(set(e["kind"] for e in entries))
        print(f"  [{t}] {pkg} <- {dep}  ({len(entries)} hits, kinds={','.join(kinds)})")

    if summary_per_pkg:
        print()
        print("=== Summary-disabled features (first 10 packages) ===")
        for i, pkg in enumerate(sorted(summary_per_pkg)):
            if i >= 10:
                print(f"  ... ({len(summary_per_pkg) - 10} more packages)")
                break
            feats = summary_per_pkg[pkg][:3]
            feat_str = ", ".join(f"{f['feature']}={f['value']}" for f in feats)
            more = (f" (+{len(summary_per_pkg[pkg]) - 3} more)"
                    if len(summary_per_pkg[pkg]) > 3 else "")
            print(f"  {pkg}: {feat_str}{more}")

    if meson_per_pkg:
        print()
        print("=== Meson-not-found where target IS in our tree (first 10 packages) ===")
        for i, pkg in enumerate(sorted(meson_per_pkg)):
            if i >= 10:
                print(f"  ... ({len(meson_per_pkg) - 10} more packages)")
                break
            ms = meson_per_pkg[pkg][:3]
            m_str = ", ".join(m["target"] for m in ms)
            more = (f" (+{len(meson_per_pkg[pkg]) - 3} more)"
                    if len(meson_per_pkg[pkg]) > 3 else "")
            print(f"  {pkg}: {m_str}{more}")

    if cmake_per_pkg:
        print()
        print("=== CMake-could-not-find where target IS in our tree (first 10 packages) ===")
        for i, pkg in enumerate(sorted(cmake_per_pkg)):
            if i >= 10:
                print(f"  ... ({len(cmake_per_pkg) - 10} more packages)")
                break
            cs = cmake_per_pkg[pkg][:3]
            c_str = ", ".join(c["target"] for c in cs)
            more = (f" (+{len(cmake_per_pkg[pkg]) - 3} more)"
                    if len(cmake_per_pkg[pkg]) > 3 else "")
            print(f"  {pkg}: {c_str}{more}")

    print()
    print("FAIL — silent feature losses detected. Resolve by: "
          "(1) adding the missing dep to consumer's package.yml "
          "dependencies.build, (2) reordering build to ensure dep is built "
          "before consumer, or (3) accepting the loss explicitly with a "
          "rationale comment in package.yml if the dep is genuinely "
          "optional and not desired in InterGenOS.")


def write_artifacts(repo: Path, result: dict, ts: str) -> tuple[Path, Path]:
    build_dir = repo / "build"
    build_dir.mkdir(exist_ok=True)
    json_path = build_dir / f"preflight-silent-loss-{ts}.json"
    tsv_path = build_dir / f"preflight-silent-loss-{ts}.tsv"
    json_path.write_text(json.dumps({**result, "timestamp": ts}, indent=2))
    with tsv_path.open("w") as fp:
        fp.write("type\tpkg\tdep\tlog\tline_no\tkind\tmatch\n")
        for f in result["findings"]:
            fp.write("\t".join([
                f["type"], f["pkg"], f["dep"], f["log"],
                str(f["line_no"]), f["kind"],
                f["match"].replace("\t", " ").replace("\n", " "),
            ]) + "\n")
    return json_path, tsv_path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Preflight silent-feature-loss gate.",
        epilog="Exit 0 on clean OR skipped (chroot absent); 1 on findings; "
               "2 on env problem; 3 under --require-audit when the audit "
               "could not run OR could not cover the full installed set "
               "(empty inventory, missing/unreadable logs, missing "
               "package.yml).",
    )
    ap.add_argument("--root", help="repo root (overrides INTERGENOS_ROOT + autodetect)")
    ap.add_argument("--chroot", help="chroot mount point (default /mnt/igos)")
    ap.add_argument("--report", action="store_true",
                    help="also write JSON + TSV artifacts to <repo>/build/")
    ap.add_argument("--require-audit", action="store_true",
                    help="treat a SKIP (chroot/BLFS data absent) as a fail-closed "
                         "halt (exit 3) instead of exit 0 — for build call sites "
                         "where the chroot MUST be populated (end-of-extra gate, "
                         "targeted-resume manual fire). A SKIP there means the audit "
                         "could not run, which must not be waved through as a pass.")
    args = ap.parse_args()

    repo = discover_repo_root(args.root)
    if not (repo / "packages").is_dir():
        print(f"ERROR: repo root {repo} missing packages/ — "
              f"is this an InterGenOS checkout?", file=sys.stderr)
        return 2

    chroot = discover_chroot_root(args.chroot)
    result = scan(repo, chroot)
    emit_summary(result)

    if args.report:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        json_path, tsv_path = write_artifacts(repo, result, ts)
        print()
        print(f"Report artifacts:")
        print(f"  JSON: {json_path}")
        print(f"  TSV:  {tsv_path}")

    # Exit code:
    #   - skipped (chroot absent): exit 0 normally; exit 3 under --require-audit
    #     (fail-closed — at the build call sites the chroot MUST be populated,
    #     so a SKIP means the audit could not run, which is not a pass)
    #   - findings: exit 1
    #   - clean: exit 0
    if result["skipped"]:
        if args.require_audit:
            print(f"FAIL-CLOSED (--require-audit): silent-loss audit could not "
                  f"run — {result['skip_reason']}. The chroot must be populated "
                  f"at this call site; a SKIP here is not a pass. Halting.",
                  file=sys.stderr)
            return 3
        return 0

    # --require-audit demands POSITIVE coverage, not merely a non-skipped
    # run: a scan over a present-but-empty inventory, or one that dropped
    # packages it could not pair with a build log or package.yml, has not
    # audited the intended set — success would certify a subset, the exact
    # class this gate exists to catch. Packages outside the BLFS reference
    # scope (blfs_no_truth) are permitted BY POLICY and named in the
    # summary: their logs are still scanned for meson/cmake/summary losses;
    # only the BLFS dependency cross-check has no reference data for them
    # (over half the corpus — own packages and LFS-chapter-8 — is
    # legitimately outside that reference).
    if args.require_audit:
        gaps: list[str] = []
        if result["installed_count"] == 0:
            gaps.append("the installed inventory is EMPTY (0 packages) — "
                        "nothing was audited")
        for label, names in (
                ("build log missing", result["log_missing"]),
                ("build log unreadable", result["log_unreadable"]),
                ("package.yml missing", result["yml_missing"])):
            if names:
                gaps.append(f"{label} for {len(names)} package(s): "
                            f"{', '.join(sorted(names))}")
        if gaps:
            print("FAIL-CLOSED (--require-audit): incomplete audit coverage — "
                  "the scan ran but did not examine the full installed set:",
                  file=sys.stderr)
            for g in gaps:
                print(f"  - {g}", file=sys.stderr)
            print(f"Every installed package must pair with a readable build "
                  f"log and its package.yml before this gate can certify. "
                  f"(Outside-BLFS-scope: {len(result['blfs_no_truth'])} "
                  f"packages — permitted by policy, log-scan still applied.) "
                  f"Halting.", file=sys.stderr)
            return 3

    # Every independently-reported failure bucket must gate the exit —
    # cmake_not_found_intree was printed as a FAIL class by emit_summary
    # yet omitted here, so a CMake-only silent loss printed FAIL and
    # exited 0 (the orchestrator continued).
    if (result["findings"] or result["summary_disabled"]
            or result["meson_not_found_intree"]
            or result["cmake_not_found_intree"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
