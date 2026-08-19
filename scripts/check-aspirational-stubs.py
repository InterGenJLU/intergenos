#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Rule 21 aspirational-stub detection gate.

Authored against the project's build rulebook Rule 21 ("No stubs") and
audit finding
M-009 (USA-1 audit B17 closure). Rule 21's detection layer cites this
script by name as the canonical code-stub audit:

    "Code-stub audit (scripts/check-aspirational-stubs.py, scheduled) —
     greps init.sh / *.service / *.desktop / tmpfiles.d / sysusers.d /
     polkit rules / dbus configs for path references, cross-checks each
     against the packages-yml-derived install manifests."

Prior to this script's existence, the rulebook itself was reflexively
violating Rule 21: it cited an aspirational path. This closure makes
the citation real.

What this gate does
-------------------
1. Walks the repository for the surfaces enumerated in Rule 21:
     - systemd unit files (*.service)
     - desktop autostart files (*.desktop)
     - polkit policy + JS rule files (*.policy, *.rules)
     - D-Bus service activation files (*.service under dbus-1 paths)
     - tmpfiles.d / sysusers.d drop-ins (*.conf in those paths)
     - installer init scripts (installer/init/*.sh)
2. Extracts every absolute filesystem path that those surfaces *reference*
   (the "claimed-paths" set) using a set of source-type-aware regexes.
3. Builds the "produced-paths" set from every `verify_paths:` block in
   packages/*/*/package.yml. The verify_paths field is the human-curated
   install-manifest declaration per Rule 20.
4. Cross-checks. Every claimed-path that is not:
     - declared in any package's verify_paths block,
     - covered by a known-system path prefix (FHS standard locations that
       are owned by the base system rather than a single package),
     - covered by an --allowlist entry,
   is flagged ASPIRATIONAL-STUB.

A stub flag is not always a defect — some references are intentionally
forward-pointing (e.g. /etc/intergenos/first-boot-completed is written
at runtime, not built into the package). The tool surfaces every gap;
operators review and either fix the gap, extend verify_paths, or add a
documented allowlist entry.

Exit codes
----------
  0 — no ASPIRATIONAL-STUB entries (or all are allowlisted)
  1 — one or more ASPIRATIONAL-STUB entries surfaced (BUILD BLOCKER)
  2 — script invocation error (missing project root, etc.)
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Source-surface scan globs.
# Restricted to repository content. The gate intentionally does NOT scan an
# installed-system /etc or /usr — that's the runtime audit (Rule 20). This
# gate catches stubs at authoring time, before the build runs.
# ---------------------------------------------------------------------------
SURFACE_GLOBS: list[tuple[str, str]] = [
    # (glob-pattern, surface-kind-label)
    ("packages/*/*/*.service", "systemd"),
    ("packages/*/*/*.desktop", "desktop"),
    ("packages/*/*/*.policy", "polkit"),
    ("packages/*/*/*.rules", "polkit-rules"),
    ("packages/*/*/*.conf", "conf"),
    ("intergen/data/*.service", "systemd"),
    ("intergen/data/*.policy", "polkit"),
    ("intergen/data/*.rules", "polkit-rules"),
    ("installer/data/*.service", "systemd"),
    ("installer/data/*.policy", "polkit"),
    ("installer/data/*.rules", "polkit-rules"),
    ("installer/init/*.sh", "init"),
    ("installer/init/init.sh", "init"),
]

# ---------------------------------------------------------------------------
# Path-extraction regexes per surface kind.
# Each captures the absolute path operand of a directive that "claims" the
# path exists. Capture group 1 is the path.
# ---------------------------------------------------------------------------

# systemd unit directives that name a binary or path.
# ExecStart=/usr/bin/foo --flag    => /usr/bin/foo
# ExecStart=-/usr/bin/foo          => /usr/bin/foo  (leading '-' = ignore-failure)
# ExecStart=@/usr/bin/foo          => /usr/bin/foo  (leading '@' = argv[0] override)
# ExecStart=+/usr/bin/foo          => /usr/bin/foo  (leading '+' = full privileges)
# EnvironmentFile=-PATH            => skipped (see extract_from_systemd):
#                                     the leading '-' on EnvironmentFile=
#                                     declares the file as intentionally
#                                     optional per systemd semantics. A
#                                     missing optional env file is by
#                                     design, not a stub.
SYSTEMD_DIRECTIVES = (
    "ExecStart", "ExecStartPre", "ExecStartPost",
    "ExecReload", "ExecStop", "ExecStopPost", "ExecCondition",
    "ConditionPathExists", "ConditionPathIsDirectory",
    "ConditionPathIsReadWrite", "ConditionFileNotEmpty",
    "AssertPathExists", "AssertPathIsDirectory",
    "WorkingDirectory", "RootDirectory", "BindReadOnlyPaths",
    "ReadOnlyPaths", "ReadWritePaths", "InaccessiblePaths",
    "EnvironmentFile",
)
SYSTEMD_PATTERN = re.compile(
    r"^\s*(" + "|".join(SYSTEMD_DIRECTIVES) + r")\s*=\s*([-@+!]?)(/[^\s%$\n]+)",
    re.MULTILINE,
)

# Freedesktop .desktop directives.
# Exec=/usr/bin/foo --arg  /  TryExec=/usr/bin/foo  /  Path=/usr/share/foo  /
# Icon=/usr/share/icons/...
DESKTOP_PATTERN = re.compile(
    r"^\s*(?:Exec|TryExec|Path|Icon)\s*=\s*(/[^\s%\n]+)",
    re.MULTILINE,
)

# Polkit .policy XML — the path-bearing key is the exec annotation:
#   <annotate key="org.freedesktop.policykit.exec.path">/usr/bin/foo</annotate>
POLKIT_POLICY_PATTERN = re.compile(
    r">\s*(/[^<\s]+)\s*</annotate>",
)

# tmpfiles.d / sysusers.d directives.
# Format: "<type> <path> <mode> <uid> <gid> <age> <arg>"
# type is a single letter or letter+modifier. We anchor on type-then-space-then-/.
TMPFILES_PATTERN = re.compile(
    r"^\s*[a-zA-Z][!+=^-]*\s+(/[^\s\n]+)",
    re.MULTILINE,
)

# D-Bus .service file activation:
#   Exec=/usr/bin/python3 -m intergen.dbus_daemon
# Same pattern as desktop Exec= — DESKTOP_PATTERN handles it.

# Shell-script path-bearing literals — *very* conservative scope to avoid
# matching every /var/log or /tmp string in shell. We only flag absolute
# paths that appear on the right side of:
#   cat > /path  /  > /path  /  >> /path  /  install -m ... /path
# This is intentionally narrow: init.sh writes files at runtime that don't
# need to be in any verify_paths claim, so we lean toward false-negative over
# false-positive on shell. Stronger checks would belong in a separate gate.
SHELL_WRITE_PATTERN = re.compile(
    r"(?:^|\s)(?:>|>>|cat\s*>|cat\s*>>)\s*(/[^\s\n<&|;]+)",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Known-system path prefixes.
# These are FHS-standard locations owned by glibc / coreutils / systemd /
# the base system rather than a single InterGenOS package. References to
# paths under these prefixes are not aspirational-stub candidates unless
# the specific filename is suspicious; the gate trusts the base-system
# coverage here.
#
# Trade-off: a reference like /usr/bin/foo-nonexistent will be marked OK
# if /usr/bin/ is a known-system prefix. The trade-off is intentional —
# every package that ships a /usr/bin/* binary already declares it in
# verify_paths (Rule 20), so the cross-check against verify_paths catches
# the real cases. Known-system prefixes catch "kernel-side", "always-there"
# paths that no single package owns.
# ---------------------------------------------------------------------------
KNOWN_SYSTEM_PATHS: set[str] = {
    # Kernel + /proc + /sys + /dev — kernel-managed, never package-shipped.
    "/proc", "/sys", "/dev", "/run", "/tmp", "/var/tmp", "/var/run",
    # Runtime-created state directories — typically created by tmpfiles.d
    # or by the unit's own setup; not package-shipped.
    "/var/lib", "/var/log", "/var/cache", "/var/spool",
    # Boot partition — populated by bootloader/kernel install, declared
    # in linux kernel package's verify_paths but as version-suffixed paths.
    "/boot",
    # Mount points + chroot conventions.
    "/mnt", "/media", "/srv", "/opt", "/home", "/root",
    # /etc subtrees that are runtime-populated (drop-ins, user config).
    "/etc/intergenos", "/etc/sudoers.d", "/etc/cron.d", "/etc/cron.daily",
    "/etc/cron.hourly", "/etc/cron.weekly", "/etc/cron.monthly",
    "/etc/profile.d", "/etc/skel", "/etc/xdg",
    # systemd runtime locations not owned by any single package.
    "/etc/systemd/system", "/run/systemd",
    # /var/lib/igos — InterGenOS runtime state directory.
    "/var/lib/igos", "/usr/share/intergenos",
    # /usr/libexec — sub-binaries called by primary binaries; many are
    # declared in verify_paths but the directory itself is FHS-standard.
    "/usr/libexec",
    # /newroot — runtime initramfs mountpoint for the target filesystem.
    # installer/init/init.sh writes to /newroot/etc/* + /newroot/home/*
    # AFTER mounting the installed-system filesystem there; these are
    # runtime writes to a mounted target, not aspirational references to
    # paths in the build tree. Initramfs init switches root to /newroot
    # before handing off to systemd. Sibling /sysroot is the systemd-
    # standard equivalent path used by other initramfs flavors.
    "/newroot", "/sysroot",
}

# Base-system binaries shipped by core packages (bash-core, coreutils-core,
# procps-ng) but whose verify_paths hand-curates only 2-3 identity-signal
# paths per Rule 20 (e.g., procps-ng declares /usr/bin/free + libproc2.so,
# not every CLI it ships). Service files across the distribution routinely invoke
# these by basename in ExecStart/ExecReload/ExecStop directives without
# the citing package owning them. Comprehensive enumeration of these
# binaries is captured at pkm-manifest layer (pkg-functions.sh:pkg_manifest
# walks the full staging tree at build time), not at verify_paths layer.
# Added per USA-1 B17 walk (2026-05-22) — closes 6 false-positives of the
# /bin/kill and /bin/sh classes.
KNOWN_SYSTEM_BINARIES: set[str] = {
    "/bin/sh", "/usr/bin/sh",       # bash-core
    "/bin/bash", "/usr/bin/bash",   # bash-core
    "/bin/kill", "/usr/bin/kill",   # procps-ng
    "/bin/true", "/usr/bin/true",   # coreutils-core
    "/bin/false", "/usr/bin/false", # coreutils-core
    "/bin/cat", "/usr/bin/cat",     # coreutils-core
    "/bin/echo", "/usr/bin/echo",   # coreutils-core
}

# Non-packages surface ownership map. Maps a top-level prefix outside
# packages/ to the (tier, pkg_name) tuple that owns the package which
# ships those surfaces. Used by is_citing_package_owned() for surfaces
# like intergen/data/*.policy whose owning package lives at packages/ai/
# intergen/ rather than at packages/intergen/data/.
NON_PACKAGES_OWNERSHIP: dict[str, tuple[str, str]] = {
    "intergen/data": ("ai", "intergen"),
    # installer/data and installer/init have no packages/ entry — the
    # installer is a top-level non-packaged tree. Surfaces there fall
    # through to the standard verify_paths cross-check.
}


# ---------------------------------------------------------------------------
# UsrMerge sibling-prefix mapping.
# InterGenOS / LFS use UsrMerge: /bin -> /usr/bin, /sbin -> /usr/sbin, and
# /lib -> /usr/lib are top-level symlinks (see LFS Ch 7 + packages/core/
# filesystem). A package declaring /usr/bin/kill in verify_paths satisfies
# any reference to either /bin/kill or /usr/bin/kill — same file.
#
# This handles a recurrent false-positive class: service files commonly
# write "ExecStop=/bin/kill -s HUP $MAINPID" while the owning package
# declares /usr/bin/kill (the canonical post-UsrMerge location).
# ---------------------------------------------------------------------------
USRMERGE_PREFIX_MAP: dict[str, str] = {
    "/bin/": "/usr/bin/",
    "/sbin/": "/usr/sbin/",
    "/lib/": "/usr/lib/",
    "/lib64/": "/usr/lib/",
}


def usrmerge_siblings(path: str) -> list[str]:
    """Return alternate path forms that a package may have declared.

    Handles UsrMerge symlinks in both directions: a reference to /bin/kill
    is satisfied by /usr/bin/kill declared in verify_paths AND vice versa.
    """
    siblings = [path]
    for legacy, canonical in USRMERGE_PREFIX_MAP.items():
        if path.startswith(legacy):
            siblings.append(canonical + path[len(legacy):])
        elif path.startswith(canonical):
            siblings.append(legacy + path[len(canonical):])
    return siblings


@dataclass
class ClaimedPath:
    """A path reference extracted from a surface file."""
    path: str
    source_file: Path
    line_no: int
    surface_kind: str

    def __hash__(self) -> int:  # pragma: no cover — dataclass-eq satisfies set
        return hash((self.path, str(self.source_file), self.line_no))


# ---------------------------------------------------------------------------
# Path normalization. Strips trailing slashes, query-fragments, and trailing
# punctuation that regex over-captured. Returns a canonical form for set
# membership.
# ---------------------------------------------------------------------------
TRAILING_TRASH_RE = re.compile(r"[,;:'\"<>\)\]]+$")


def normalize_path(raw: str) -> str:
    """Return canonical path form. Strips trailing punctuation + slash."""
    p = raw.strip()
    p = TRAILING_TRASH_RE.sub("", p)
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p


def is_known_system(path: str) -> bool:
    """Return True if the path is under a known-system prefix or matches
    a known-system binary."""
    if path in KNOWN_SYSTEM_BINARIES:
        return True
    for prefix in KNOWN_SYSTEM_PATHS:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def is_citing_package_owned(
    claimed_path: str,
    source_file: Path,
    project_root: Path,
) -> bool:
    """Return True if the cited path is shipped by the package that owns
    the citing surface file.

    A surface at packages/<tier>/<pkg>/* is owned by <pkg>. Surfaces at
    intergen/data/* map to ai/intergen via NON_PACKAGES_OWNERSHIP. The
    cited path is treated as shipped by that package if any of:
      (a) basename(path) and pkg-name share a substring (catches
          /usr/sbin/nft from nftables-owned surface; /usr/sbin/mariadbd
          from mariadb-owned; /usr/bin/intergen-privileged-runner from
          intergen-owned).
      (b) the path appears literally in the citing package's build.sh
          (catches /etc/mysql from mariadb.service via mariadb/build.sh's
          explicit `install -d "$DESTDIR/etc/mysql/conf.d"` line, and
          /etc/sysconfig/httpd from apache-httpd/build.sh's heredoc-staged
          sysconfig file).

    Rationale (per USA-1 B17 walk, 2026-05-22): verify_paths in package.yml
    is the hand-curated 2-3-path identity-signal assertion per Rule 20;
    the pkm SQLite manifest (captured by pkg-functions.sh:pkg_manifest
    via `find . -mindepth 1` over the full staging tree) is the
    comprehensive file enumeration that catches missing files via
    `pkm verify <pkg>`. A cited path is pipeline-tracked when the citing
    package's build.sh either installs it explicitly (class b) or its
    autotools/cmake/meson make-install would put it at the cited location
    (class a basename heuristic catches the autotools case where
    `make install` does not literally appear with the path string but
    the binary basename strongly correlates with the package name).
    """
    try:
        rel = source_file.resolve().relative_to(project_root.resolve())
    except ValueError:
        return False
    parts = rel.parts

    tier: str | None = None
    pkg_name: str | None = None
    if len(parts) >= 3 and parts[0] == "packages":
        tier, pkg_name = parts[1], parts[2]
    else:
        rel_str = str(rel)
        for prefix, (mapped_tier, mapped_pkg) in NON_PACKAGES_OWNERSHIP.items():
            if rel_str.startswith(prefix + "/"):
                tier, pkg_name = mapped_tier, mapped_pkg
                break

    if not pkg_name:
        return False

    basename = claimed_path.rsplit("/", 1)[-1]

    # (a) basename ↔ package name substring match.
    if basename and (basename in pkg_name or pkg_name in basename):
        return True

    # (b) literal path appearance in build.sh.
    if not tier:
        return False
    build_sh = project_root / "packages" / tier / pkg_name / "build.sh"
    if build_sh.exists():
        try:
            content = build_sh.read_text(errors="replace")
            if claimed_path in content:
                return True
        except OSError:
            pass
    return False


# ---------------------------------------------------------------------------
# Per-file extractors.
# Each returns a list of (path, line_no, surface_kind). The line_no is
# 1-indexed; computed by counting newlines up to the match offset (cheap
# vs. building a line-number index).
# ---------------------------------------------------------------------------

def offset_to_line(text: str, offset: int) -> int:
    """Convert a string offset to a 1-indexed line number."""
    return text.count("\n", 0, offset) + 1


def extract_from_systemd(text: str) -> list[tuple[str, int]]:
    """Extract every (path, line_no) referenced by systemd directives.

    Skips `EnvironmentFile=-PATH` per the systemd `-` prefix semantic:
    the leading `-` declares the file as intentionally optional (operator-
    provided env override). A missing optional env file is by design, not
    a stub. Added per USA-1 B17 walk (2026-05-22) — closes the
    /etc/sysconfig/haproxy false-positive class.

    Skips negated conditions `Condition...=!PATH` / `Assert...=!PATH`: the
    leading `!` asserts the path is ABSENT — a runtime-generated or
    must-not-exist reference, the opposite of a claim that the package ships
    it. The `!` modifier only appears on Condition*/Assert* directives (Exec*
    use `-@+`), so this never suppresses a real Exec claim. (F43: the
    per-service TLS keygen units gate on `ConditionPathExists=!<cert>`, the
    cert being generated on the target at first service start.)
    """
    results: list[tuple[str, int]] = []
    for m in SYSTEMD_PATTERN.finditer(text):
        directive, prefix, raw_path = m.group(1), m.group(2), m.group(3)
        if directive == "EnvironmentFile" and prefix == "-":
            continue
        if prefix == "!":
            continue
        path = normalize_path(raw_path)
        if path:
            results.append((path, offset_to_line(text, m.start())))
    return results


def extract_from_desktop(text: str) -> list[tuple[str, int]]:
    results: list[tuple[str, int]] = []
    for m in DESKTOP_PATTERN.finditer(text):
        path = normalize_path(m.group(1))
        if path:
            results.append((path, offset_to_line(text, m.start())))
    return results


def extract_from_policy(text: str) -> list[tuple[str, int]]:
    results: list[tuple[str, int]] = []
    for m in POLKIT_POLICY_PATTERN.finditer(text):
        path = normalize_path(m.group(1))
        if path:
            results.append((path, offset_to_line(text, m.start())))
    return results


def extract_from_polkit_rules(text: str) -> list[tuple[str, int]]:
    # .rules files are JavaScript. They reference action IDs (not paths) and
    # very rarely reference filesystem paths directly. We scan for any quoted
    # absolute-path string as a conservative measure.
    results: list[tuple[str, int]] = []
    for m in re.finditer(r"['\"](/[^'\"\s]+)['\"]", text):
        path = normalize_path(m.group(1))
        if path and "/" in path[1:]:  # require at least two segments
            results.append((path, offset_to_line(text, m.start())))
    return results


def extract_from_tmpfiles(text: str, file_path: Path) -> list[tuple[str, int]]:
    """Extract tmpfiles.d / sysusers.d path operands.

    The filename or its parent directory must contain 'tmpfiles' or
    'sysusers' for this extractor to apply. We honor that by checking the
    caller's file_path before invoking.
    """
    name = file_path.name
    parent = file_path.parent.name
    if not (
        "tmpfiles" in name or "sysusers" in name
        or "tmpfiles" in parent or "sysusers" in parent
    ):
        return []
    results: list[tuple[str, int]] = []
    for m in TMPFILES_PATTERN.finditer(text):
        # Skip comments — comment lines start with '#' which the regex's
        # type-letter class excludes already, but a leading-whitespace
        # comment "  # ..." needs explicit handling.
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_text = text[line_start:m.start()]
        if line_text.strip().startswith("#"):
            continue
        path = normalize_path(m.group(1))
        if path:
            results.append((path, offset_to_line(text, m.start())))
    return results


def extract_from_init_shell(text: str) -> list[tuple[str, int]]:
    """Extract write-target paths from shell scripts.

    Narrow scope per the SHELL_WRITE_PATTERN trade-off above: we surface
    paths that the script claims to write, not every path string that
    appears in the script. This catches the highest-value pattern
    (script writes a file that no package produces) while accepting
    false-negatives on conditional paths.
    """
    results: list[tuple[str, int]] = []
    for m in SHELL_WRITE_PATTERN.finditer(text):
        path = normalize_path(m.group(1))
        if path:
            results.append((path, offset_to_line(text, m.start())))
    return results


# ---------------------------------------------------------------------------
# verify_paths harvester (matches check-installer-runtime-deps.py shape).
# ---------------------------------------------------------------------------

def collect_verify_paths(project_root: Path) -> dict[str, str]:
    """Map every verify_paths entry to the package that declares it.

    Simple line-based YAML parse — no PyYAML dep so this gate runs in any
    environment with stock Python. Mirror of check-installer-runtime-deps.py
    behavior for consistency.
    """
    owners: dict[str, str] = {}
    pkg_root = project_root / "packages"
    if not pkg_root.exists():
        return owners
    for yml in pkg_root.rglob("package.yml"):
        try:
            text = yml.read_text(errors="replace")
        except OSError:
            continue
        pkg_name = yml.parent.name
        in_block = False
        for raw in text.splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if line.startswith("verify_paths:"):
                in_block = True
                continue
            if in_block:
                if line and not line[0].isspace() and not stripped.startswith("-"):
                    in_block = False
                    continue
                if stripped.startswith("- "):
                    path = stripped[2:].strip().strip('"').strip("'")
                    # Strip inline YAML comments.
                    if "#" in path:
                        path = path.split("#", 1)[0].strip()
                    if path:
                        owners[path] = pkg_name
    return owners


def path_resolves(
    path: str,
    source_file: Path,
    owners: dict[str, str],
    allowlist: set[str],
    project_root: Path,
) -> tuple[bool, str]:
    """Return (resolves, reason). reason is the matched owner / system-prefix
    / allowlist / citing-package tag."""
    if path in allowlist:
        return (True, "allowlist")
    # Direct + UsrMerge sibling matches.
    for candidate in usrmerge_siblings(path):
        if candidate in owners:
            return (True, owners[candidate])
    # Match a verify_paths entry that is a directory ancestor of path.
    # Some verify_paths declarations name a directory (e.g. /usr/lib/firmware/amdgpu);
    # files under that directory are considered owned. Check UsrMerge siblings
    # for the parent-dir case too.
    for candidate in usrmerge_siblings(path):
        for owned, pkg in owners.items():
            if candidate.startswith(owned + "/"):
                return (True, f"{pkg} (parent dir)")
    if is_known_system(path):
        return (True, "system-path")
    # Citing-package-owned check (USA-1 B17 walk, 2026-05-22): a cited
    # path is pipeline-tracked when the citing package's own build.sh
    # ships it, even if verify_paths (hand-curated identity-signal) does
    # not list it. The comprehensive enumeration lives at the pkm manifest
    # layer, captured from the full staging tree at build time.
    if is_citing_package_owned(path, source_file, project_root):
        return (True, "citing-package")
    return (False, "ASPIRATIONAL-STUB")


# ---------------------------------------------------------------------------
# Allowlist loader. Format: one path per line; '#' begins a comment.
# ---------------------------------------------------------------------------

def load_allowlist(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    result: set[str] = set()
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Drop trailing inline comments.
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        if line.startswith("/"):
            result.add(normalize_path(line))
    return result


# ---------------------------------------------------------------------------
# Main scan.
# ---------------------------------------------------------------------------

def scan_surfaces(project_root: Path) -> list[ClaimedPath]:
    """Walk every surface glob and harvest all claimed-paths."""
    claims: list[ClaimedPath] = []
    seen_files: set[Path] = set()
    for glob, kind in SURFACE_GLOBS:
        for file_path in project_root.glob(glob):
            if file_path in seen_files:
                continue
            seen_files.add(file_path)
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(errors="replace")
            except OSError:
                continue

            # Per-surface-kind extraction.
            if kind == "systemd":
                raw_hits = extract_from_systemd(text)
            elif kind == "desktop":
                raw_hits = extract_from_desktop(text)
            elif kind == "polkit":
                raw_hits = extract_from_policy(text)
            elif kind == "polkit-rules":
                raw_hits = extract_from_polkit_rules(text)
            elif kind == "init":
                raw_hits = extract_from_init_shell(text)
            elif kind == "conf":
                # .conf files are catch-all: tmpfiles.d, sysusers.d, and
                # systemd drop-ins all share the .conf extension. Try
                # tmpfiles extraction first (filename-gated); fall back to
                # systemd extraction for drop-ins.
                raw_hits = extract_from_tmpfiles(text, file_path)
                if not raw_hits:
                    raw_hits = extract_from_systemd(text)
            else:
                raw_hits = []

            for path, line_no in raw_hits:
                claims.append(ClaimedPath(
                    path=path,
                    source_file=file_path,
                    line_no=line_no,
                    surface_kind=kind,
                ))
    return claims


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rule 21 aspirational-stub detection gate (M-009 closure)",
    )
    parser.add_argument(
        "--project",
        default=str(PROJECT_ROOT),
        help=f"Path to project root (default: {PROJECT_ROOT})",
    )
    parser.add_argument(
        "--allowlist",
        default=None,
        help="Path to allowlist file (one absolute path per line; # comments OK)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print every resolved claim (not just stubs)",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only the summary counts; suppress per-stub table",
    )
    args = parser.parse_args()

    project = Path(args.project)
    if not project.exists():
        print(f"[Rule 21] FATAL: project root {project} does not exist", file=sys.stderr)
        return 2

    allowlist_path = Path(args.allowlist) if args.allowlist else None
    allowlist = load_allowlist(allowlist_path)

    owners = collect_verify_paths(project)
    claims = scan_surfaces(project)

    stubs: list[tuple[ClaimedPath, str]] = []
    ok_count = 0
    by_reason: dict[str, int] = {}

    for claim in claims:
        ok, reason = path_resolves(claim.path, claim.source_file, owners, allowlist, project)
        if ok:
            ok_count += 1
            by_reason[reason] = by_reason.get(reason, 0) + 1
            if args.verbose:
                rel = claim.source_file.relative_to(project)
                print(f"[Rule 21] OK    {rel}:{claim.line_no}: {claim.path}  -> {reason}")
        else:
            stubs.append((claim, reason))

    if stubs and not args.summary_only:
        print()
        print("ASPIRATIONAL-STUB findings (Rule 21 violations):")
        print()
        print(f"  {'FILE:LINE':<60} {'REFERENCE':<50} STATUS")
        print(f"  {'-' * 60} {'-' * 50} {'-' * 20}")
        for claim, reason in stubs:
            rel = claim.source_file.relative_to(project)
            loc = f"{rel}:{claim.line_no}"
            if len(loc) > 60:
                loc = "..." + loc[-57:]
            ref = claim.path
            if len(ref) > 50:
                ref = ref[:47] + "..."
            print(f"  {loc:<60} {ref:<50} {reason}")

    print()
    print(
        f"[Rule 21] Summary: {len(claims)} total claimed-paths scanned, "
        f"{ok_count} resolved, {len(stubs)} ASPIRATIONAL-STUB"
    )
    if args.verbose and by_reason:
        print()
        print("Resolution breakdown:")
        for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {count:5d}  {reason}")

    if stubs:
        print()
        print(
            "[Rule 21] BUILD BLOCKER — aspirational-stub references found. "
            "Either declare the path in the owning package's verify_paths, "
            "fix the reference, or document an --allowlist entry with a "
            "reason comment.",
            file=sys.stderr,
        )
        return 1

    print("[Rule 21] PASS — zero aspirational-stub references detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
