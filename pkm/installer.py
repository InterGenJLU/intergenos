# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""pkm installer — Archive extraction and file deployment.

Implements supersede-aware install per RFC §4 (Supersedes Primitive +
Content-Hash Manifest). The install path:

  1. Read the package's staged manifest (SUPERSEDES header, file list, optional
     per-file sha256 hashes).
  2. Validate predecessors named in SUPERSEDES — warn on missing or
     already-superseded entries (RFC §11) but proceed.
  3. Deploy archive contents to the target filesystem.
  4. Open a SQLite transaction wrapping the DB-side updates: register the
     package, register its files, transfer file ownership from each
     predecessor, mark predecessors superseded, log the operation. Commit
     only after deploy succeeded — gate-3 retirement timing per RFC §4a.
  5. Write a text manifest reflecting the post-install ownership state, with
     SHA-256 hash columns per file.

If the staged manifest lacks per-file hashes (legacy archive built before
Phase 2 tracker write-through), the installer computes them by hashing files
from the staged tree before deploy. Per RFC v2 §2g and the §8 OQ4 resolution
(DS PASS-with-nits): every install produces hashes regardless of archive
generation, so the content-hash property has no NULL-checksum holes.
"""

import json
import os
import re
import sqlite3
import stat
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# Forensic-trace shim — defensive import.
try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    _trace = None
    _TRACE_AVAILABLE = False

from .database import (
    PackageDB,
    _sha256,
    _parse_manifest_line,
    _parse_manifest,
)
from . import rootpaths
from .repo import _read_package_meta, ArchiveReadError
from .hooks import (
    CANONICAL_HOOKS_PRE,
    archive_lifecycle_hook_path,
    run_canonical_hooks,
    run_archive_lifecycle_hook,
    format_hook_summary,
)
from .hookrecord import (
    fs_snapshot,
    diff_snapshots,
    claimable,
    format_record_summary,
)
from .configprotect import (
    prepare_config_protection,
    materialize_pkmnew_sidecars,
    ratchet_baselines,
    summary_lines,
)

# H-008: archive-bundled metadata files (provenance + key=value pkginfo) are
# read at install time for the DB metadata population, but must NOT be
# tracked as installed files on the target filesystem.
_ARCHIVE_METADATA_FILES = frozenset({".PKGINFO", "package.yml"})

# Archive-bundled metadata DIRECTORIES — same class, one level up. `.scripts/`
# holds the sealed lifecycle hooks that run_archive_lifecycle_hook fires out of
# the extracted staging dir; they are executed FROM staging and never belong on
# the target. Without this, every package carrying a sealed hook would deploy
# and register a `/.scripts/post_install.sh`, putting an unowned script at the
# filesystem root of every install — the exact class the seam exists to close,
# reintroduced by the seam itself.
_ARCHIVE_METADATA_DIRS = frozenset({".scripts"})


def _is_archive_metadata(rel: str) -> bool:
    """True when a staging-relative path is archive metadata, not payload.

    Takes the path with or without a trailing slash, so the same predicate
    answers for the directory entry and for the files beneath it.
    """
    stripped = rel.rstrip("/")
    if stripped in _ARCHIVE_METADATA_FILES:
        return True
    head = stripped.split("/", 1)[0]
    return head in _ARCHIVE_METADATA_DIRS


# Environment allowlist for install-helper subprocess execution (H-024).
# Default-deny per the security-only-alignment posture. Anything outside this
# set is dropped before exec to prevent inherited-variable attacks via e.g.
# LD_PRELOAD / LD_LIBRARY_PATH / LD_AUDIT (library injection), *_PROXY (MitM
# of helper-time upstream downloads), PYTHONPATH (Python-module hijack).
#
# SUDO_USER is the one identity-metadata entry allowed through: it is inert
# (a username string — NOT a library path, proxy URL, or module search path),
# so it is not an injection vector, and helpers need it to drop from root back
# to the invoking user for per-user work. Concretely, `code --install-extension`
# run as root is refused by VS Code's super-user guard, so the claude-code
# helper must drop to ${SUDO_USER} to install the extension into that user's
# profile; with SUDO_USER stripped the helper's drop-to-user branch never fired
# and the extension install failed on the first bare-metal run. SUDO_UID /
# SUDO_GID are deliberately excluded — no helper consumes them, and default-deny
# widens only for a demonstrated need. claude-code is currently the sole helper
# reading SUDO_USER; any helper needing per-user context relies on this same
# entry (there is ONE allowlist — do not fork a second).
HELPER_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM",
    "TMPDIR", "SHELL",
    "SUDO_USER",
})


# H-007: install-helper manifest schema for footprint tracking.
#
# Helpers write /var/lib/igos/helpers/<name>.manifest as JSON via the
# /usr/share/igos/helpers/helper-lib.sh API (packages/core/intergenos-
# helper-lib). pkm reads the manifest on helper success and threads the
# file list through PackageDB.add_files / add_depends so pkm
# files/verify/remove work for helper-installed packages.
#
# See docs/architecture/helper-manifest-spec-v1.md for the contract.
HELPER_MANIFEST_DIR = Path("/var/lib/igos/helpers")
HELPER_MANIFEST_SCHEMA_VERSION = 1

# Where the proprietary-download install-helper binaries live on the installed
# system (one per app: /usr/bin/igos-install-<name>). build.sh for each helper
# package installs it here.
HELPER_BIN_DIR = Path("/usr/bin")


def is_download_helper(name, root=None):
    """True if <name> is a proprietary-download helper package.

    Detection is the on-disk presence of its install-helper binary
    /usr/bin/igos-install-<name>. The installed DB has no payload_license
    column, so the helper binary — laid down by the helper package, which
    ships in the default install set — is the reliable signal that <name> is
    a "run this to download the real proprietary app" package rather than a
    normally-installed one.

    `root` names which filesystem is being asked. It defaults to the running
    system, which is what every caller before the install-root option meant.
    Asking the LIVE machine about a package being installed into another root
    would answer for the wrong filesystem — the live machine's helper set has
    nothing to do with the target's.
    """
    base = HELPER_BIN_DIR if root is None else Path(root) / "usr" / "bin"
    return (base / f"igos-install-{name}").is_file()


def payload_installed(name, root=None):
    """True if the REAL proprietary app for <name> has actually been
    downloaded and installed.

    The signal is the helper footprint manifest /var/lib/igos/helpers/
    <name>.manifest, which helper-lib.sh writes only on a successful
    download + EULA acceptance. The helper merely shipping by default does
    NOT create it — so this is what separates "the app is installed" from
    "only the helper that can install it is present." (PRIME DIRECTIVE: the
    system must never report the app as installed when it is not.)

    `root` names which filesystem is being asked; see is_download_helper.
    """
    base = HELPER_MANIFEST_DIR if root is None else rootpaths.helper_manifest_dir(root)
    return (Path(base) / f"{name}.manifest").is_file()


# Where a download-helper records that a human read the vendor licence and
# typed the exact acceptance phrase. The helper owns these files; pkm only
# reads them. The name carries the acceptance schema the helper wrote against
# (`<package>-<schema>-accepted.json`), so the presence test globs the schema
# rather than pinning one — a helper that revises its acceptance record to
# schema 2.0 must not silently look un-accepted.
ACCEPTANCE_RECORD_DIR = Path("/var/lib/intergen/legal")


def acceptance_record_exists(name, record_dir=None):
    """True if this machine already holds an acceptance record for <name>.

    Read by the non-interactive gate on proprietary-payload installs. The rule
    that gate enforces is "pkm never accepts a vendor licence on the user's
    behalf" — and a recorded acceptance IS the user having accepted, on this
    machine, at a time the record itself carries. Refusing anyway meant the
    only way to update an already-accepted payload without a human at the
    keyboard was to run the helper directly, which is the path that leaves the
    package database describing the previous payload.

    A machine with no record still refuses exactly as before: this function
    returns False and the caller stops.
    """
    directory = Path(record_dir) if record_dir is not None else ACCEPTANCE_RECORD_DIR
    try:
        return any(directory.glob(f"{name}-*-accepted.json"))
    except OSError:
        # An unreadable record directory is not evidence of acceptance.
        return False


# pkm 2b item 4: an install helper exits with this code when the user DECLINES
# the vendor EULA (the "I ACCEPT" prompt), as distinct from exit 1 = a genuine
# failure (download error, signature mismatch, etc.). A decline is a user
# choice, not an error; pkm surfaces it as a clean cancellation (exit 0) rather
# than an ERROR. All bundled igos-install-<app> helpers honor this convention.
HELPER_DECLINE_RC = 10

# EULA install-helper hybrid-model (decided 2026-05-28).
#
# Packages that bundle proprietary userspace under a EULA (the first
# instance is nvidia for the NVIDIA proprietary userspace) declare
# `eula_helper: <name>` in their package.yml. parser.py reads it,
# tracker.py emits it into .PKGINFO as `eula_helper=<name>`, and
# pkm.installer.install (below) checks for a system-wide acceptance
# marker BEFORE the package install proceeds. Missing marker -> the
# helper at EULA_HELPER_DIR/<name> is run; on accept the helper writes
# the marker + verbatim transcript and exits 0. On decline or fetch
# failure the helper exits non-zero and pkm.installer aborts the
# install with a clear message.
#
# Marker convention: /var/lib/intergen/eula/<helper-name-suffix>.accepted
# where the suffix is derived from the helper name (e.g. helper
# `nvidia-eula` -> marker `nvidia-userspace.accepted`). The helper
# owns its marker filename; pkm just checks the helper's exit code +
# delegates "is this accepted?" to the helper itself (the helper has
# the canonical knowledge of what marker it writes).
EULA_HELPER_DIR = Path("/usr/lib/intergen/eula-helpers")

# Path-prefix allowlist: files in the helper manifest MUST live under
# one of these directories. The allowlist defends against a buggy or
# malicious helper that claims ownership of system-critical paths
# outside its territory (e.g. /etc/passwd, /boot/vmlinuz). Allowlist
# violation refuses the wire-up + warns but does NOT remove the
# deposited files — operator triages.
HELPER_PATH_ALLOWLIST_PREFIXES = ("/usr/", "/opt/", "/etc/", "/var/lib/")

# Reasonable upper bound on manifest entries (files + symlinks combined)
# to defend against a runaway helper recording millions of paths.
HELPER_MANIFEST_MAX_ENTRIES = 10000

# Merged-/usr root aliases. On a merged-usr system these top-level dirs are
# symlinks INTO /usr (e.g. /lib -> usr/lib, /sbin -> usr/sbin), so a file
# deposited under one is really under /usr and squarely within the allowlist
# — but a helper records the alias path it copied to. _normalize_merged_usr_root
# rewrites the LEADING root component (only) to its canonical /usr/... target
# before the allowlist check + before the manifest reader hands paths to the
# DB, so the alias is accepted (never a widening of the 4 accepted prefixes —
# /lib normalizes INTO /usr) and pkm files/verify/remove track the real on-disk
# path. Resolving only the top-level root — never a deeper component — keeps a
# legitimately-symlinked install subdir from being over-canonicalized, and uses
# the LIVE symlink target so distro layout variance (/sbin -> usr/bin vs
# usr/sbin) is handled without hardcoding. Surfaced by Steam's lib/udev/rules.d
# payload: /lib/udev/... was refused though /lib -> usr/lib.
_MERGED_USR_ROOT_NAMES = ("lib", "lib64", "bin", "sbin")


def _normalize_merged_usr_root(path):
    """Rewrite a leading merged-usr root alias to its canonical /usr/... target.

    Returns ``path`` unchanged unless it is absolute, its first component is a
    known merged-usr root name, and that root is a live symlink — in which case
    only the root component is resolved and re-prefixed.
    """
    if not isinstance(path, str) or not path.startswith("/"):
        return path
    parts = path.split("/", 2)  # ["", "<root>", "<rest>"]
    if len(parts) < 3 or parts[1] not in _MERGED_USR_ROOT_NAMES:
        return path
    root = "/" + parts[1]
    try:
        if os.path.islink(root):
            target = os.path.normpath(os.path.join("/", os.readlink(root)))
            return target + "/" + parts[2]
    except OSError:
        pass
    return path


def _has_traversal_segment(path):
    """True when a path string carries a ``..`` segment.

    A ``..`` segment can prefix-satisfy the allowlist while resolving outside
    it (``/usr/../root/x`` startswith ``/usr/`` but lands in ``/root``) — and
    every downstream consumer (DB, verify, remove, the ELF re-audit's
    realpath) would then operate on a laundered location. A first-party
    helper never legitimately records one, so the reader refuses loudly
    (verify, don't normalize)."""
    return isinstance(path, str) and ".." in path.split("/")


def _read_partial_manifest_summary(name):
    """Return summary dict for the helper's partial-manifest sidecar.

    H-007 Decision D, 2026-05-19: when a helper crashes between
    igos_helper_init + igos_helper_commit, the EXIT trap installed by
    init writes a `<name>.manifest.partial` JSON sidecar at
    HELPER_MANIFEST_DIR. _run_helper surfaces the orphan file list to
    the user in the install-failed error message via this helper.

    Returns None when no sidecar exists or it cannot be parsed.
    On success returns: {"path": Path, "count": int, "sample": [str],
    "version_installed": str}.
    """
    partial_path = HELPER_MANIFEST_DIR / f"{name}.manifest.partial"
    if not partial_path.is_file():
        return None
    try:
        with open(str(partial_path), "r", encoding="utf-8") as f:
            partial = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(partial, dict):
        return None
    files = partial.get("files", [])
    if not isinstance(files, list):
        files = []
    symlink_paths = []
    for s in (partial.get("symlinks", []) or []):
        if isinstance(s, dict) and isinstance(s.get("path"), str):
            symlink_paths.append(s["path"])
    paths = list(files) + symlink_paths
    return {
        "path": partial_path,
        "count": len(paths),
        "sample": paths[:10],
        "version_installed": partial.get("version_installed", ""),
    }


def _read_helper_manifest(name, root=None):
    """Read + validate the helper manifest produced by helper-lib.sh.

    `root` names the filesystem being asked (see payload_installed); None is
    the running system, which is what every caller before the install-root
    option meant.

    H-007. Returns (manifest_dict, None) on success or
    (None, error_message) on absent/malformed/disallowed manifest.
    Phase A grace-period semantics: a missing manifest is NOT an
    error from pkm's perspective — _run_helper falls back to the
    legacy "register only add_installed + log_operation" behavior
    and warns once on the operation log. Phase B flips the missing-
    manifest case to a hard failure once all bundled helpers have
    migrated to the helper-lib API.
    """
    base = HELPER_MANIFEST_DIR if root is None else Path(rootpaths.helper_manifest_dir(root))
    manifest_path = base / f"{name}.manifest"
    if not manifest_path.is_file():
        return None, f"no manifest at {manifest_path}"

    try:
        with open(str(manifest_path), "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return None, f"manifest unreadable: {e}"

    # Schema-version envelope + required fields.
    if not isinstance(manifest, dict):
        return None, "manifest root is not a JSON object"
    if manifest.get("version") != HELPER_MANIFEST_SCHEMA_VERSION:
        return None, (
            f"unsupported schema version {manifest.get('version')!r}; "
            f"expected {HELPER_MANIFEST_SCHEMA_VERSION}"
        )
    if not isinstance(manifest.get("name"), str) or not manifest["name"]:
        return None, "missing or non-string `name` field"
    if not isinstance(manifest.get("files", []), list):
        return None, "`files` is not a list"
    if not isinstance(manifest.get("symlinks", []), list):
        return None, "`symlinks` is not a list"
    if not isinstance(manifest.get("depends", []), list):
        return None, "`depends` is not a list"

    files = manifest.get("files", [])
    symlinks = manifest.get("symlinks", [])

    # Normalize merged-/usr root aliases to canonical /usr/... paths BEFORE the
    # DoS cap, the allowlist check, the ELF re-audit, and the caller's DB
    # ingestion all read these lists — so the alias is accepted and every
    # downstream consumer sees the real on-disk path (see
    # _normalize_merged_usr_root). Rewritten in place on the manifest dict.
    files = [_normalize_merged_usr_root(p) for p in files]
    for _s in symlinks:
        if isinstance(_s, dict) and isinstance(_s.get("path"), str):
            _s["path"] = _normalize_merged_usr_root(_s["path"])
    manifest["files"] = files
    manifest["symlinks"] = symlinks

    # DoS cap: combined files + symlinks count.
    total_entries = len(files) + len(symlinks)
    if total_entries > HELPER_MANIFEST_MAX_ENTRIES:
        return None, (
            f"manifest entry count {total_entries} exceeds DoS cap "
            f"{HELPER_MANIFEST_MAX_ENTRIES}"
        )

    # Path-prefix allowlist: every tracked path must live under one of
    # the accepted prefixes. Collect violations (up to 5 for the
    # operator-facing error) and bail on first failure.
    bad_paths = []
    for path in files:
        if (not isinstance(path, str)) or _has_traversal_segment(path) or (
            not any(path.startswith(p) for p in HELPER_PATH_ALLOWLIST_PREFIXES)
        ):
            bad_paths.append(path)
            if len(bad_paths) >= 5:
                break
    for entry in symlinks:
        if not isinstance(entry, dict) or "path" not in entry:
            return None, f"symlink entry malformed: {entry!r}"
        link = entry.get("path", "")
        if not isinstance(link, str) or _has_traversal_segment(link) or not any(
            link.startswith(p) for p in HELPER_PATH_ALLOWLIST_PREFIXES
        ):
            bad_paths.append(link)
            if len(bad_paths) >= 5:
                break
    if bad_paths:
        return None, (
            "path(s) outside helper-manifest allowlist or carrying `..` "
            "traversal segments (accepts only "
            f"{', '.join(HELPER_PATH_ALLOWLIST_PREFIXES)}): {bad_paths!r}"
        )

    # ELF word-size re-audit at wire-up time (RT-1 deposit gate, second
    # look). The helper library audits each file as it is RECORDED, but a
    # record-then-swap (or a dangling link whose target appeared after the
    # record) would ship un-audited width — so every tracked path is
    # re-read HERE, after the helper exited and before pkm wires it up.
    # Manifests without the field (pre-field helpers) default to the
    # tree-wide 64-bit contract; "mixed" waives the width check by
    # explicit declaration. A mismatch refuses the wire-up (files stay on
    # disk for debugging — the same posture as an allowlist violation).
    elf_class = manifest.get("elf_class", "64")
    if elf_class not in ("64", "32", "mixed"):
        return None, f"invalid elf_class {elf_class!r} (valid: 64, 32, mixed)"
    if elf_class != "mixed":
        want = 2 if elf_class == "64" else 1
        elf_bad = []
        for path in files:
            if not isinstance(path, str):
                continue
            try:
                with open(os.path.realpath(path), "rb") as fh:
                    head = fh.read(5)
            except OSError:
                continue  # absent/unreadable at wire-up: not a width violation
            if head[:4] == b"\x7fELF" and head[4] != want:
                found = "32" if head[4] == 1 else "64"
                elf_bad.append(f"{path} ({found}-bit)")
                if len(elf_bad) >= 5:
                    break
        if elf_bad:
            return None, (
                f"ELF word-size mismatch vs the helper's elf_class={elf_class} "
                f"contract at wire-up time: {', '.join(elf_bad)}"
            )

    return manifest, None


def _unlink_existing_regular_file(target):
    """Unlink ``target`` iff it is an existing REGULAR file (not a directory or
    symlink), so a subsequent in-place write lands on a fresh inode.

    This is the ETXTBSY guard for deploy-over-a-running-binary: opening a
    currently-executing binary for writing raises OSError ETXTBSY, so we remove
    the old inode first (the running process keeps its own reference to it).
    Deliberately narrow — only regular files, so directories and symlinks keep
    tarfile's existing extract semantics. Silently tolerates the file vanishing
    between the lstat and the unlink (a concurrent-removal race); a genuine
    write failure still surfaces from the extractall that follows.
    """
    try:
        st = os.lstat(target)
    except OSError:
        return
    if stat.S_ISREG(st.st_mode):
        try:
            os.unlink(target)
        except OSError:
            pass


def _safe_extract_tar(archive_path, dest, exclude_paths=None, usrmerge_root=None):
    """Extract a .tar.gz archive to dest using the PEP 706 'data' filter
    with two pkm-specific relaxations applied via a member-rewriting wrapper.

    H-022: path-traversal hardening for archive extraction.
    Replaces the legacy subprocess `tar -xzf` invocation which had no built-in
    protection against `../` members, absolute-path members, or escape-via-
    symlink. The 'data' filter (PEP 706, available since Python 3.12 and
    default in 3.14+) blocks:
      - members with absolute paths (after leading-slash strip)
      - members whose resolved path escapes dest
      - hard/symbolic links targeting outside dest
      - device/character/block/fifo special files
      - setuid/setgid/sticky bits (caller restores selectively where the
        original archive metadata indicates the bit is intentional)
      - uid/gid/uname/gname (set to None — matches the legacy
        --no-same-owner GNU tar flag)
      - non-rw group/other permissions on regular files (matches the
        legacy --no-same-permissions GNU tar flag)

    Pkm-specific relaxations (added 2026-05-26 after install attempt #10
    surfaced 27 packages failing extraction in two classes):

    1. Absolute-path symlinks are REWRITTEN to relative-equivalent BEFORE
       the data_filter sees them. Many packages ship symlinks like
       `./var/lib/dbus/machine-id -> /etc/machine-id` (absolute target).
       The data_filter rejects these as path-traversal attempts because at
       extraction time the symlink would resolve to the LIVE system's
       /etc/machine-id, not the target's. But semantically, when the target
       is BOOTED (becomes /), the absolute target resolves to the target's
       own /etc/machine-id — correct behavior. Rewriting to relative form
       (e.g., `../../../etc/machine-id`) preserves the runtime semantics
       (same resolved path when booted) while satisfying data_filter
       (the symlink resolves WITHIN dest). 24-of-27 install attempt #10
       failures had this exact shape.

    2. Top-level dirs colliding with target symlinks are REMAPPED to the
       symlink's target prefix. Packages can ship `./lib/*` as a real dir
       when they were built against a chroot that had /lib as a real dir;
       on a target with `/lib -> usr/lib` (the UsrMerge convention bash's
       archive establishes), extracting `./lib/foo.so` would overwrite the
       /lib symlink with a real dir + drop foo.so in it. The
       previously-extracted /usr/lib contents become invisible. Solution:
       transparently remap `./lib/*` → `./usr/lib/*` (and same for lib64,
       bin, sbin) so the file lands at the symlinked location. 3-of-27
       install attempt #10 failures had this shape.

    Both relaxations PRESERVE the original H-022 invariant: after rewrite,
    every member's resolved path still falls WITHIN dest, so data_filter's
    other checks (device files, setuid, group perms, etc.) continue to
    protect the extraction. The relaxations are NOT a security regression;
    they correct two cases where PEP 706 'data' was too conservative for
    OS-distribution packaging workloads (it's tuned for "untrusted download
    from internet", not "our own build pipeline's signed binary archives").

    Fail-closed: any FilterError still aborts extraction and returns
    (False, message). The on-disk state at failure may be partially-
    extracted; the caller is responsible for cleanup (typically the staging
    tmpdir which gets rmtree'd in the finally clause of install()).

    Args:
        archive_path: Path to .tar.gz archive.
        dest: Path to extraction root (must exist).
        exclude_paths: Optional iterable of archive-relative member names
            to skip. Used by the deploy-extract path to drop .PKGINFO +
            package.yml (H-008 archive-metadata files) and Q4
            config-protected /etc/* paths.

    Returns:
        (success: bool, message: str). On success, message is empty. On
        FilterError, message names the offending member; on other
        tar/OS errors, message includes the underlying error text.
    """
    exclude_set = frozenset(exclude_paths or ())
    # UsrMerge detection: when usrmerge_root is provided (caller is the
    # staging extract, which extracts to a tmpdir but needs to remap based
    # on the INSTALL target's symlinks), use it. Otherwise (e.g., the
    # deploy extract that goes straight to self.root), dest IS the target
    # — use it.
    usrmerge_check_root = Path(usrmerge_root) if usrmerge_root is not None else Path(dest)

    # Detect UsrMerge symlinks so we can remap archive members from
    # <dir>/* to <symlink-target>/*. Only top-level dirs that are
    # CONVENTIONALLY remapped under FHS UsrMerge are considered. If the
    # check-root's /<entry> doesn't exist or isn't a symlink, no remap
    # happens for that prefix.
    usrmerge_remaps = {}
    for entry in ("lib", "lib64", "bin", "sbin"):
        link = usrmerge_check_root / entry
        if link.is_symlink():
            target = os.readlink(str(link))
            # Conventional UsrMerge targets are relative (e.g., "usr/lib").
            # Absolute targets would point outside dest at extraction time;
            # only accept relative for safety.
            if not target.startswith("/"):
                # Strip trailing slash if any; normalize.
                usrmerge_remaps[entry] = target.rstrip("/")

    def _strip_dot_slash(name):
        """Strip leading './' from tar member names so prefix logic is uniform."""
        return name[2:] if name.startswith("./") else name

    def _abs_symlink_to_relative(member_name, link_target):
        """Convert an absolute symlink target to a relative target that
        resolves to the same SYSTEM-rooted path when the target is booted.

        Example: member at './var/lib/dbus/machine-id' with target
        '/etc/machine-id' becomes '../../../etc/machine-id', which resolves
        to '<dest>/etc/machine-id' at extraction time AND resolves to
        '/etc/machine-id' once the target is booted as root.
        """
        member_stripped = _strip_dot_slash(member_name)
        member_dir = os.path.dirname(member_stripped)
        # link_target is absolute like '/etc/machine-id'; strip leading /
        target_stripped = link_target.lstrip("/")
        # Compute relative path from member's directory to the target.
        # os.path.relpath handles the ../ chain correctly.
        return os.path.relpath(target_stripped, member_dir or ".")

    def member_filter(member, path):
        # Normalize the member name the same way the usrmerge/symlink logic
        # below does BEFORE testing exclusion. Real archives are built
        # `tar -C <dest> -czf <archive> .` (pkg-functions.sh pkg_archive), so
        # every member is stored `./`-prefixed (`./.PKGINFO`, `./etc/foo.conf`)
        # while exclude_set holds un-prefixed names (_ARCHIVE_METADATA_FILES +
        # the file_list-derived config-protect paths, both `os.path.relpath`
        # bare). A raw `member.name in exclude_set` test therefore MISSED every
        # real-archive member — leaking .PKGINFO/package.yml onto the root
        # (work-plan 1.20) and clobbering the exact user-edited /etc files the
        # Q4 config-protection plan meant to --exclude. Strip './' first so the
        # test matches both archive forms (the bare form still normalizes to
        # itself, so no regression for arcname-without-'./' archives).
        _rel = _strip_dot_slash(member.name)
        if _rel in exclude_set:
            return None
        # An excluded DIRECTORY excludes its subtree. exclude_set holds exact
        # names, which is right for .PKGINFO and for config-protect paths, but
        # `.scripts/post_install.sh` would sail past an exact-name test and land
        # on the root — the same leak class the './'-stripping above closed, one
        # directory level up.
        #
        # Driven off exclude_set rather than off a module constant, and that
        # distinction is load-bearing: this filter serves BOTH the staging
        # extract and the deploy extract. Staging is exactly where pkm reads
        # .PKGINFO and fires .scripts/<event>.sh from, so a filter that dropped
        # metadata unconditionally would silently stop every sealed hook from
        # ever running. The caller says what to drop; only the deploy call drops
        # metadata.
        _parts = _rel.rstrip("/").split("/")
        for _i in range(1, len(_parts)):
            if "/".join(_parts[:_i]) in exclude_set:
                return None

        # Relaxation #2: UsrMerge top-level-dir remap. Apply BEFORE
        # absolute-symlink rewrite so the rewritten member.name is
        # consistent throughout the rest of the filter.
        #
        # Both member.name AND member.linkname need remapping. The name
        # tells tar where to WRITE the entry; linkname tells tar what
        # path the entry POINTS AT (for hardlinks and relative symlinks).
        # gdk-pixbuf-pass2 surfaced the case: a hardlink at
        # `usr/lib/gdk-pixbuf-2.0/.../foo.so` pointing at
        # `lib/gdk-pixbuf-2.0/.../foo.so` (the real-file member). If we
        # only remap the real file's name (lib/* -> usr/lib/*) and not
        # the hardlink's linkname, tar's hardlink-resolution looks up
        # the pre-remap path "lib/..." and raises KeyError.
        def _remap(path):
            stripped = _strip_dot_slash(path)
            for prefix, redirect in usrmerge_remaps.items():
                if stripped == prefix or stripped == prefix + "/":
                    return None  # caller drops the entry
                if stripped.startswith(prefix + "/"):
                    rest = stripped[len(prefix) + 1:]
                    return redirect + "/" + rest
            return stripped  # unchanged (no prefix matched)

        if usrmerge_remaps:
            remapped_name = _remap(member.name)
            if remapped_name is None:
                return None  # top-level dir entry itself; skip
            member.name = remapped_name

            # Hardlinks + same-tree relative symlinks: remap linkname too.
            # Absolute-target symlinks are handled separately below
            # (Relaxation #1).
            if member.linkname and not member.linkname.startswith("/"):
                remapped_link = _remap(member.linkname)
                if remapped_link is not None:
                    member.linkname = remapped_link

        # Relaxation #1: absolute-symlink rewrite. data_filter rejects
        # absolute linkname targets because at extraction time they
        # would resolve OUTSIDE dest. We rewrite to relative form that
        # resolves WITHIN dest while preserving runtime semantics (same
        # resolved path once the target is booted as root).
        if member.issym() and member.linkname.startswith("/"):
            member.linkname = _abs_symlink_to_relative(
                member.name, member.linkname,
            )

        result = tarfile.data_filter(member, path)
        # Unlink-before-write (ETXTBSY guard). data_filter has now confirmed
        # this member resolves WITHIN dest, so it is safe to touch the on-disk
        # target. If a REGULAR FILE already exists there, unlink it first so
        # extractall creates a FRESH inode instead of open()-ing the existing
        # file for writing in place. Overwriting a currently-RUNNING binary in
        # place raises OSError ETXTBSY ("Text file busy") — the redeploy-over-a-
        # live-binary case that stranded the llama-cpp r2 deploy (2026-07-07).
        # Unlinking a running executable is safe: the kernel keeps the old inode
        # alive for the running process while the new content lands at a new
        # inode. Every mainstream package manager unlinks first for this reason.
        # Scoped to regular files — a running executable is the only ETXTBSY
        # case, and leaving symlinks/dirs to tarfile's existing handling keeps
        # the change behavior-neutral everywhere else. Best-effort: a genuine
        # write error still surfaces from extractall.
        if result is not None and result.isreg():
            _unlink_existing_regular_file(os.path.join(path, result.name))
        return result

    try:
        with tarfile.open(str(archive_path), "r:gz") as tf:
            # Capture the member objects BEFORE extractall so the correction
            # pass below sees the same (filter-remapped) names extractall used.
            members = tf.getmembers()
            tf.extractall(path=str(dest), filter=member_filter)
            _restore_symlink_target_modes(members, dest)
        return True, ""
    except tarfile.FilterError as e:
        offending_member = getattr(e, "member", None)
        offending = offending_member.name if offending_member is not None else "<unknown>"
        return False, (
            f"Archive contains unsafe member ({offending}): {e}. "
            "Refusing to extract — archive may be malicious or corrupt."
        )
    except (tarfile.TarError, OSError) as e:
        return False, f"Failed to extract archive: {e}"


def _purge_stale_bytecode(root, file_list):
    """Delete cached bytecode for every Python module in ``file_list``.

    CPython keeps a module's compiled form at
    ``<dir>/__pycache__/<stem>.<tag>.pyc``. It normally invalidates that
    cache by comparing the source's recorded modification time and size —
    but a deployed file's timestamp comes from the archive, not from the
    moment it landed, so a replaced module can present the same (mtime,
    size) pair the stale cache was built against and the interpreter loads
    the OLD code with nothing to indicate it did. That was measured on a
    installed system 2026-08-06: an upgraded package executed pre-upgrade code
    twelve seconds after its own deploy.

    Removing the compiled copy makes the question moot: with no cache entry
    the interpreter must read the source that is actually on disk. This is
    the one mechanism that does not depend on getting cache invalidation
    right, which is why it is the one chosen.

    Nothing here is allowed to fail an install. A cache file that cannot be
    removed is reported, not fatal: the package's own files are correct, the
    condition worth surfacing is that a stale compiled copy of one of them
    may still be executed.

    Args:
        root: install root (Path or str) the file_list paths are relative to.
        file_list: tracked entries, POSIX-relative, '/'-suffixed for dirs.

    Returns:
        list[str] — absolute paths of the cache files actually removed.
    """
    removed = []
    problems = []
    root = Path(root)
    seen_dirs = set()
    for entry in file_list:
        if entry.endswith("/") or not entry.endswith(".py"):
            continue
        rel = entry.lstrip("/")
        try:
            src = root / rel
        except (TypeError, ValueError):
            continue
        cache_dir = src.parent / "__pycache__"
        # One directory listing per __pycache__, however many modules of the
        # package live in it.
        key = str(cache_dir)
        stem = src.stem
        if key not in seen_dirs:
            seen_dirs.add(key)
            if not cache_dir.is_dir():
                continue
        elif not cache_dir.is_dir():
            continue
        # Every tag CPython may have written for this module: the plain
        # `<stem>.cpython-NNN.pyc` and the optimized `.opt-N` variants. A
        # glob on the stem covers all of them without this code needing to
        # know which interpreter or optimization level wrote them.
        try:
            candidates = list(cache_dir.glob(f"{stem}.*.pyc"))
        except OSError as e:
            problems.append(f"{cache_dir}: {e}")
            continue
        for pyc in candidates:
            try:
                pyc.unlink()
                removed.append(str(pyc))
            except FileNotFoundError:
                pass          # already gone — the desired state
            except OSError as e:
                problems.append(f"{pyc}: {e}")
    if problems:
        print(
            f"  WARNING: {len(problems)} stale Python bytecode file(s) could "
            f"not be removed after deploy; an old compiled copy may still be "
            f"executed until they are: " + "; ".join(problems[:5]),
            file=sys.stderr,
        )
    return removed


def _read_target_ids(root):
    """name→id maps from the INSTALL TARGET's passwd/group databases (PI-Z11).

    Ownership restore must resolve archive uname/gname against the system
    being installed (self.root), never the host running pkm — a Forge install
    runs from the live ISO whose ids need not match the target's. Malformed
    lines are skipped; a missing file yields an empty table (every lookup
    then misses and the caller warns loudly per member).
    """
    users, groups = {}, {}
    for rel, table in (("etc/passwd", users), ("etc/group", groups)):
        try:
            text = (Path(root) / rel).read_text(encoding="utf-8",
                                                errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[0]:
                try:
                    table[parts[0]] = int(parts[2])
                except ValueError:
                    continue
    return users, groups


def _restore_symlink_target_modes(members, dest):
    """Undo CPython tarfile's symlink-follow chmod clobber.

    tarfile.extractall() applies os.chmod() to symlink members, and on Linux
    os.chmod() has no lchmod equivalent so it FOLLOWS the link. A relative
    symlink such as `sysinit.target.wants/cryptsetup.target -> ../cryptsetup.target`
    therefore chmods the REAL cryptsetup.target to the symlink's data_filter-
    clamped mode (0o777 & ~0o022 = 0o755) — silently making any unit file that
    is the target of an enable-symlink executable on every install. On the
    GBC002.6 install this turned exactly the 3 .target units with a
    sysinit.target.wants/ symlink (cryptsetup, imports, integritysetup) into
    0755, which systemd then warns about as "marked executable" (GBC003 G3-8B).

    Fix: after extraction, re-assert the intended mode (the mode the data
    filter assigned to that file's OWN member) on every real file that is the
    target of an in-archive relative symlink. Surgical — only files actually
    pointed at by a shipped symlink are touched; everything else is left exactly
    as extracted.

    `members` are the post-extractall member objects (names already UsrMerge-
    remapped by member_filter); `dest` is the extraction root.
    """
    dest = Path(dest)
    try:
        dest_real = dest.resolve()
    except OSError:
        return

    # Dest-relative paths that some in-archive relative symlink points AT.
    targets = set()
    for m in members:
        if not m.issym():
            continue
        link = m.linkname or ""
        if not link or link.startswith("/"):
            continue  # absolute links are rewritten relative earlier; skip residual
        resolved = os.path.normpath(
            os.path.join(os.path.dirname(m.name), link)
        ).lstrip("/")
        if resolved and not resolved.startswith(".."):
            targets.add(resolved)
    if not targets:
        return

    for m in members:
        if not m.isfile():
            continue
        name = m.name.lstrip("/")
        if name not in targets:
            continue
        deployed = dest / name
        # Containment: the resolved deployed path must stay within dest.
        try:
            deployed.resolve().relative_to(dest_real)
        except (ValueError, OSError):
            continue
        if not deployed.exists() or deployed.is_symlink():
            continue
        try:
            intended = tarfile.data_filter(m, str(dest)).mode
        except Exception:  # noqa: BLE001 — fall back to the raw archive mode
            intended = m.mode & 0o7777
        try:
            os.chmod(deployed, intended)
        except OSError:
            continue


class PackageInstaller:
    """Install packages from pre-built archives."""

    def __init__(self, db: PackageDB, root="/"):
        self.db = db
        self.root = Path(root)

    def install(self, name, archive_path=None, queue=None, expected_sha256=None,
                install_reason="manual", reporter=None, sidecars_out=None):
        """Install a package from its .igos.tar.gz archive.

        Args:
            name: Package name.
            archive_path: Path to archive, or None to search ARCHIVE_DIR.
            queue: Optional iterable of package names representing the full
                   install queue this install is part of. When provided,
                   the installer enforces the install-order invariant: a
                   package P that declares supersedes:[Q] must install
                   AFTER Q if Q is also in the queue. Forge passes this
                   from `installer/backend/packages.py`. Ad-hoc invocations
                   (`pkm install foo-pass2` from the CLI) leave it None
                   and fall back to a warn-and-proceed posture.
            expected_sha256: When provided, re-verify the archive sha256
                   inside install() AFTER path validation but BEFORE any
                   tar extract (L-021 TOCTOU defense). Caller computed
                   the hash at download/verify time; we re-compute here
                   so a local attacker who swaps the cached archive
                   between caller's verify and our extract fails the
                   second hash check. Mismatch → fail-closed return.
                   None means no expected hash (legacy / archive-trust=
                   loose path); install proceeds without the gate.
            install_reason: Q9 install_reason field — 'manual' (user-
                   requested install) or 'dependency' (dep-resolution-
                   pulled). Default 'manual'. cmd_install threads
                   'dependency' for each non-target dep in the resolved
                   queue; explicit single-package invocations stay
                   'manual'. `pkm autoremove` removes only 'dependency'
                   rows with no live reverse-deps.

        Returns:
            (success: bool, message: str)
        """
        existing = self.db.get_installed(name)
        if existing and not existing.get("superseded_by"):
            # ROUTING BY RELEASE, not a bare "already installed, use
            # reinstall". That line stated something true and gave advice that
            # was reasonable in general — and it is the line that invited a
            # silent downgrade, because it never said which side was newer.
            # The caller (cmd_install) has the resolved index entry and
            # produces the full comparison; this message is the floor for
            # every other caller, and it at least names what IS installed,
            # with its release.
            from . import txn as _txn
            return False, (
                f"{_txn.describe_subject(name, existing)} is already "
                f"installed. `pkm install` does not replace an installed "
                f"package. Use `pkm upgrade {name}` to move it forward, or "
                f"`pkm reinstall {name}` to re-deploy the resolved build — "
                f"which refuses by default if that build is OLDER than this "
                f"one."
            )
        if existing and existing.get("superseded_by"):
            return False, (
                f"{name} {existing['version']} was superseded by "
                f"{existing['superseded_by']} on {existing.get('superseded_at')}. "
                f"To revert, run 'pkm reinstall {name}' explicitly — this will "
                f"un-retire {name} and is not recommended without a clear reason."
            )

        resolved_locally = False
        if not archive_path:
            archive_path = self._find_archive(name)
            resolved_locally = archive_path is not None
        if not archive_path:
            helper = self._find_helper(name)
            if helper:
                return False, (
                    f"No local archive for '{name}', but an install helper exists.\n"
                    f"         Run: pkm install-helper {name}"
                )
            return False, (f"No archive found for '{name}' in "
                           f"{rootpaths.archive_dir(self.root)}")

        archive_path = Path(archive_path)
        if not archive_path.exists():
            return False, f"Archive not found: {archive_path}"

        # S5-1 fail-closed chokepoint (security-review 2026-07-01): an archive pkm
        # resolved ITSELF from ARCHIVE_DIR (caller passed archive_path=None) must
        # carry a verification reference (expected_sha256, which the CLI threads
        # from the signed index). Without one it is an unverified local file —
        # refuse rather than extract it as root. A caller-PROVIDED archive_path is
        # the caller's own trust decision (Forge's install-integrity manifest, an
        # explicit `pkm install --archive`), so this guards ONLY the implicit
        # local-resolution path — the exact seam where the integrity gate was
        # previously skippable by which code path reached install().
        if resolved_locally and not expected_sha256:
            return False, (
                f"refusing to install '{name}' from the local archive "
                f"{archive_path.name}: no signed-index verification reference. "
                f"Run `pkm sync` then retry, or install a local archive "
                f"deliberately with `pkm install --archive {archive_path} "
                f"--archive-trust loose`."
            )

        # L-021: re-hash archive immediately before any tar extract.
        # Defends against TOCTOU between the caller's sha256 verification
        # (in repo.download_package or cmd_install --archive path) and
        # the subprocess tar invocations below. The narrow window between
        # caller-verify and our hash here is the only remaining attack
        # surface for the cached-archive-swap scenario; subprocess tar
        # then runs on the freshly-verified content.
        if expected_sha256:
            actual = _sha256(str(archive_path))
            if actual != expected_sha256:
                return False, (
                    f"Archive integrity check FAILED at install time for "
                    f"{name}: expected sha256 {expected_sha256[:16]}..., "
                    f"got {actual[:16]}.... Cached file may have been "
                    f"swapped between download/verify and install. "
                    f"Cache cleared; retry with `pkm install {name}`."
                )

        # EULA install-helper gate (hybrid-model, 2026-05-28). Read
        # .PKGINFO ahead of the staging extract so we can run the EULA
        # helper BEFORE the user's filesystem sees a single byte of the
        # archive. The helper checks its own marker file; if accepted,
        # exit 0 + we proceed. If declined / fetch-failed / non-tty /
        # write-failure, exit non-zero + we abort with the helper's
        # message. The .PKGINFO read is cheap (single tar -O extract);
        # the result is reused below as `pkginfo` after staging so we
        # avoid double-reading.
        try:
            early_pkginfo = _read_package_meta(archive_path) or {}
        except ArchiveReadError as e:
            # PKM-A13: a corrupt/truncated archive must abort the install
            # fail-closed, not read as "no metadata" and proceed to deploy
            # with empty metadata + release=1. The second read below is then
            # unreachable for this archive (it only fires when this read
            # returned an empty dict, i.e. the archive opened cleanly).
            return False, (
                f"Cannot read package archive for {name}: {e}. The archive "
                f"may be corrupt or truncated — re-fetch with "
                f"`pkm install {name}`."
            )
        eula_helper_name = early_pkginfo.get("eula_helper")
        if eula_helper_name:
            # PI-Z6: pass the archive so the gate can fall back to the copy
            # of the helper that ships INSIDE the package being gated — on a
            # first install (fresh Forge target, or `pkm install nvidia` on a
            # box that never had it) the helper cannot already be on the
            # filesystem, which made the gate unrunnable-by-construction.
            eula_ok, eula_msg = self._run_eula_helper(
                name, eula_helper_name, archive_path=archive_path)
            if not eula_ok:
                return False, eula_msg

        staging = Path(tempfile.mkdtemp(prefix=f"pkm-{name}-"))
        try:
            # H-022: extract archive to staging via Python
            # tarfile + PEP 706 'data' filter. Path-traversal, absolute-path,
            # escape-via-symlink, and device-file members are rejected; the
            # filter also strips uid/gid/uname/gname and setuid/setgid/sticky
            # bits (the latter are selectively restored further down based on
            # original archive metadata). Fail-closed: any FilterError aborts
            # the install with a message naming the offending member.
            # Pass usrmerge_root=self.root so the filter remaps top-level
            # dirs (lib, lib64, bin, sbin) based on the install TARGET's
            # symlinks, not the empty staging tmpdir's. Without this, the
            # staging tree would carry the unremapped layout and the
            # downstream file_list / hash map / deploy step would all
            # see paths that don't match what actually lands on disk.
            # Say what is happening. On a multi-gigabyte package this extract
            # runs for a long time with no other output between the download
            # line and the completion line, and silence that long is
            # indistinguishable from a hang (pkm/progress.py states the rule;
            # this path did not follow it).
            if reporter:
                reporter.phase("Extract", f"unpacking {name}")
            ok, err = _safe_extract_tar(
                archive_path, staging, usrmerge_root=self.root,
            )
            if not ok:
                return False, err

            # Read staged manifest (if present): SUPERSEDES + file list + hashes.
            supersedes_decl, manifest_files, manifest_hashes = self._read_staged_manifest(staging, name)

            # H-008: read canonical .PKGINFO key=value for tier/description/
            # license/build_date population at add_installed below. Falls back
            # to empty dict for archives built before .PKGINFO ratification.
            # The EULA gate above already read .PKGINFO from this archive;
            # reuse the result rather than re-running the tar extract.
            pkginfo = early_pkginfo or _read_package_meta(archive_path) or {}

            # Build canonical file list from the staged tree itself; the
            # manifest's file list is a transparency artifact, not the
            # authoritative ownership record. Skip archive-level metadata
            # files (.PKGINFO + package.yml) — they're provenance/metadata,
            # not installed-system payload.
            file_list = []
            for root, dirs, files in os.walk(staging):
                for d in sorted(dirs):
                    rel = os.path.relpath(os.path.join(root, d), staging)
                    if _is_archive_metadata(rel):
                        continue
                    if not os.path.islink(os.path.join(root, d)):
                        file_list.append(rel + "/")
                for f in sorted(files):
                    rel = os.path.relpath(os.path.join(root, f), staging)
                    if _is_archive_metadata(rel):
                        continue
                    file_list.append(rel)

            # Predecessor validation. Missing or already-superseded predecessors
            # surface a warning but do not block install (RFC §11).
            predecessors_to_supersede = self._validate_predecessors(
                name, supersedes_decl or [], queue
            )
            if predecessors_to_supersede is None:
                return False, (
                    f"install-order invariant violated: {name} declares "
                    f"supersedes for a predecessor that is later in the install "
                    f"queue. Reorder the queue so the predecessor installs first."
                )

            # Compute or carry forward content hashes.
            # Primary path: hashes embedded in the staged manifest (Phase 2).
            # Fallback path: hash from the staged tree (legacy archive — RFC v2 §2g).
            hashes_for_db = self._build_hash_map(staging, file_list, manifest_hashes)

            # Safety check on root-level symlinks (e.g., UsrMerge /lib ->
            # usr/lib) — historically rejected installs with top-level
            # `lib/` real-dir entries colliding with the symlink. Now
            # handled at extraction time by _safe_extract_tar's
            # usrmerge_remaps (the member-filter rewrites e.g. `./lib/foo`
            # to `./usr/lib/foo` before tar writes it), so the staging
            # tree never has the colliding top-level dir. Defense-in-depth
            # assertion: if we DO see a top-level dir colliding with a
            # root symlink despite the remap, something failed silently
            # (e.g., the symlink target was absolute and we skipped the
            # remap for safety) — fail-closed so the symptom is loud.
            dangerous = []
            for entry in ("lib", "lib64", "bin", "sbin"):
                staged = staging / entry
                root_path = self.root / entry
                if staged.is_dir() and not staged.is_symlink() and root_path.is_symlink():
                    dangerous.append(entry)
            if dangerous:
                return False, (
                    f"DANGEROUS: Archive contains top-level dirs that would "
                    f"collide with root symlinks despite UsrMerge remap: "
                    f"{' '.join(dangerous)}. Check _safe_extract_tar's "
                    f"usrmerge_remaps detection — the target's "
                    f"/{dangerous[0]} symlink target may be absolute "
                    f"(remap only handles relative targets for safety)."
                )

            # Q4 (O-006 + O-021) config protection: classify each archive
            # /etc/* path against the recorded baseline + live content. Three
            # buckets: first-install (no live; deploys normally), unedited
            # (live matches baseline → deploys; baseline ratchets after
            # COMMIT), user-edited (live diverged → exclude from tar deploy,
            # write .pkmnew sidecar after deploy, baseline NOT ratcheted so
            # subsequent upgrades continue to detect the edit). Runs against
            # the staging tree before the tar deploy so we know what to
            # --exclude up front.
            config_plan = prepare_config_protection(
                staging, file_list, self.root, self.db,
            )

            # Deploy to target filesystem. This is the gate-3 line: if the
            # extract succeeds, the supersede transaction below records the
            # ownership transfer; if it fails, no DB changes happen and
            # predecessors keep their records.
            #
            # H-022: tarfile + PEP 706 'data' filter (path-traversal /
            # absolute-path / escape-via-symlink hardening). The exclude
            # set covers (a) H-008 archive-metadata files which are
            # provenance only and not deployed to the target, and (b) Q4
            # config-protected /etc/* paths the user has diverged from
            # baseline (handled via .pkmnew sidecars after deploy).
            #
            # Behavioral note on legacy --no-overwrite-dir and
            # --keep-directory-symlink: the 'data' filter sets directory
            # mode to None so existing directories are not chmod-overwritten
            # (matches --no-overwrite-dir); tarfile.makedir catches
            # FileExistsError silently so an existing directory-symlink
            # (e.g. /lib → /usr/lib for usr-merge) is preserved and
            # subsequent file members traverse through it (matches
            # --keep-directory-symlink). The top-level usr-merge collision
            # safety check above remains the strict-fail line for
            # archive-vs-host filesystem-shape mismatches.
            deploy_excludes = (
                _ARCHIVE_METADATA_FILES | _ARCHIVE_METADATA_DIRS
                | set(config_plan["protect"])
            )
            # Drop stale compiled bytecode for every Python module this
            # deploy is about to replace — BEFORE the archive lands.
            #
            # An upgrade that replaces a .py file leaves the __pycache__
            # entry compiled from the PREVIOUS source sitting beside it, and
            # a subsequent invocation can execute that old bytecode instead
            # of the code that was just installed. Measured on an installed
            # system 2026-08-06: pkm's own upgrade deployed at 11:45:17 and
            # the next invocation twelve seconds later ran a code path that
            # had been removed from the source on disk, with the recompile
            # not happening until 11:48.
            #
            # This is a silent-failure class defect and not a cosmetic one: a
            # security fix that is installed but not EXECUTED is a fix the
            # user has been told they have and does not. Deleting the
            # compiled copy is safe under every condition — CPython
            # regenerates it on the next import, an unwritable cache
            # directory simply means it was never ours to manage, and no
            # package records or installed source files are touched.
            #
            # ORDER IS LOAD-BEARING. This call used to sit AFTER the extract,
            # where it deleted the compiled files the archive had just
            # deployed — the package's own, recorded, checksummed .pyc files.
            # Every install of a Python package then failed `pkm verify` with
            # each of those files reported missing: thousands of entries on a
            # fresh installation, on every machine (measured on two installs
            # 2026-09-02). Purging first removes only what the previous
            # version left behind; the archive then lays down its own
            # compiled files and they stay.
            _purge_stale_bytecode(self.root, file_list)

            # The second long part, and the one that writes to the live
            # filesystem — named for the same reason as the extract above.
            if reporter:
                reporter.phase("Deploy", f"writing {name} into place")
            ok, err = _safe_extract_tar(
                archive_path, self.root, exclude_paths=deploy_excludes,
            )
            if not ok:
                return False, f"Failed to deploy: {err}"

            # Restore setuid/setgid/sticky bits that hardened-tar dropped.
            try:
                with tarfile.open(str(archive_path)) as tf:
                    _special_members = tf.getmembers()
            except (OSError, tarfile.TarError) as e:
                # Can't read the archive to learn which bits to restore. The
                # deploy extract above already read it, so this is unexpected —
                # fail closed rather than silently skip privilege-bit restore.
                return False, (
                    f"setuid restore: cannot re-read archive for {name}: {e}"
                )
            # PI-Z11 (2026-07-06, Zephyrus): the extract filter deploys every
            # file root:root, and this loop used to restore ONLY the s-bits —
            # so a member the archive records as e.g. `wall 2755 root:tty`
            # landed setgid-ROOT on the installed system (a privilege-surface
            # regression; installer-side twin of the L29 archiver chown bug).
            # Restore non-root ownership recorded in the archive, resolving
            # names against the INSTALL TARGET's passwd/group databases
            # (never the host's). Ordering is load-bearing: the kernel clears
            # setuid/setgid on ANY chown of a regular file, so ownership is
            # restored FIRST and the mode is (re)applied after. A name the
            # target cannot resolve is warned LOUDLY and left root:root — the
            # packages whose own post-install hooks create their users (at,
            # fcron, dbus: the already-ledgered hook-chown class) own that
            # window; failing the whole install here would deadlock them.
            _ids = None  # lazy: (users{name:uid}, groups{name:gid})
            for member in _special_members:
                if not (member.isfile() or member.isdir()):
                    continue
                special = member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
                uname = (member.uname or "").strip()
                gname = (member.gname or "").strip()
                wants_owner = (uname not in ("", "root")) or (gname not in ("", "root"))
                if not special and not wants_owner:
                    continue
                deployed = (self.root / member.name.lstrip("/")).resolve()
                try:
                    deployed.relative_to(self.root.resolve())
                except ValueError:
                    continue  # path escapes install root
                if not deployed.exists():
                    continue
                if wants_owner:
                    if _ids is None:
                        _ids = _read_target_ids(self.root)
                    uid = _ids[0].get(uname, 0) if uname not in ("", "root") else 0
                    gid = _ids[1].get(gname, 0) if gname not in ("", "root") else 0
                    unresolved = [
                        n for n, table in ((uname, _ids[0]), (gname, _ids[1]))
                        if n not in ("", "root") and n not in table
                    ]
                    if unresolved:
                        print(
                            f"  WARNING: archive records "
                            f"/{member.name.lstrip('/')} as "
                            f"{uname or 'root'}:{gname or 'root'} but the "
                            f"target does not define {', '.join(unresolved)} "
                            f"— left root-owned; the owning package's hook "
                            f"must correct it.",
                            file=sys.stderr,
                        )
                    else:
                        try:
                            os.chown(str(deployed), uid, gid)
                        except OSError as e:
                            if special & (stat.S_ISUID | stat.S_ISGID):
                                return False, (
                                    f"Failed to restore ownership "
                                    f"{uname}:{gname} on "
                                    f"/{member.name.lstrip('/')} for {name}: "
                                    f"{e}. Refusing to leave a privileged "
                                    f"file wrongly owned — install aborted."
                                )
                            print(
                                f"  WARNING: could not restore ownership "
                                f"{uname}:{gname} on "
                                f"/{member.name.lstrip('/')} for {name}: {e}",
                                file=sys.stderr,
                            )
                if not member.isfile() or not special:
                    # Ownership-only member (or a dir): the chown above (if
                    # any) cleared no file s-bits worth re-applying here; dir
                    # modes were set at extract time.
                    continue
                try:
                    deployed.chmod(member.mode)
                except OSError as e:
                    if _TRACE_AVAILABLE:
                        try:
                            _trace.trace_event(
                                "pkm_setuid_restore_failed",
                                pkg=name, member=member.name, err=str(e),
                            )
                        except Exception:
                            pass
                    # PKM-A24: a setuid/setgid bit that could NOT be restored is
                    # a security-relevant MASKED failure — the privileged binary
                    # lands non-setuid SILENTLY. Fail closed for the privilege
                    # bits (the old code WARN-and-continued). A sticky-only
                    # failure (e.g. on a dir) is not a privilege surface, so it
                    # stays a warning.
                    if member.mode & (stat.S_ISUID | stat.S_ISGID):
                        return False, (
                            f"Failed to restore the setuid/setgid bit on "
                            f"/{member.name.lstrip('/')} for {name}: {e}. "
                            f"Refusing to leave a privileged binary non-setuid "
                            f"silently — install aborted."
                        )
                    print(
                        f"  WARNING: could not restore sticky bit on "
                        f"/{member.name.lstrip('/')} for {name}: {e}",
                        file=sys.stderr,
                    )

            version = self._version_from_archive(name, archive_path.name)

            # Atomic supersede transaction. add_installed + add_files +
            # mark_superseded + transfer_file_ownership + log_operation all
            # ride inside one BEGIN/COMMIT, so any failure rolls back to a
            # consistent DB state. Filesystem deploy already happened — the
            # FS-level mv window is the documented atomicity tradeoff
            # (RFC §4c). If the DB transaction fails, recovery is
            # `pkm install <name>` re-run; idempotent because INSERT OR
            # REPLACE handles re-registration.
            self.db.conn.execute("BEGIN")
            try:
                # H-008: populate tier/description/license/build_date from
                # archive .PKGINFO. pkm._parse_pkginfo returned an empty dict
                # for pre-H-008 archives so this stays backward-compatible.
                # PKM-A02: capture the package release from .PKGINFO. Without
                # this, every install recorded release=1 (the add_installed
                # default), so a same-version release bump on the mirror showed
                # as a phantom upgrade against a perpetual local "release 1"
                # (23 phantom upgrades on a real install once the index started
                # carrying release). _parse_pkginfo already parsed pkgrel into
                # pkginfo["release"]; coerce defensively here — a malformed
                # release falls back to 1 rather than failing the install, since
                # the build-side .PKGINFO gate is the authoritative guard.
                try:
                    _release = int(pkginfo.get("release", 1))
                except (TypeError, ValueError):
                    _release = 1
                pkg_id = self.db.add_installed(
                    name=name, version=version, release=_release,
                    install_method="archive",
                    archive_path=str(archive_path),
                    tier=pkginfo.get("tier"),
                    description=pkginfo.get("description"),
                    license_=pkginfo.get("license"),
                    # _parse_pkginfo renames the on-disk `builddate` key
                    # to `build_date` (pkm/repo.py:593-595). Look up the
                    # post-rename key.
                    build_date=pkginfo.get("build_date"),
                    # Q9: caller threads 'manual' (user-requested) or
                    # 'dependency' (dep-resolution-pulled) per the
                    # install_reason kwarg on PackageInstaller.install.
                    install_reason=install_reason,
                    # 3.0-F28: persist the declared activation semantics so the
                    # post-transaction reboot banner + restart classification
                    # read it back off the installed row. Absent key => 0.
                    reboot_required=1 if pkginfo.get("reboot_required") else 0,
                    commit=False,
                    # Declared per the add_installed destructive contract: a
                    # reinstall or upgrade of an installed name cascades its
                    # files and depends away here, and both are re-added
                    # immediately below inside this same transaction.
                    replace_existing=True,
                )
                self.db.add_files(pkg_id, file_list, hashes=hashes_for_db, commit=False)

                # H-004: persist runtime deps so `pkm depends` works + `pkm
                # remove` reverse-dep safety check has data to trip on. Deps
                # come from .PKGINFO depend=X lines (per-entry, repeated).
                runtime_deps = pkginfo.get("depends", [])
                if runtime_deps:
                    self.db.add_depends(
                        pkg_id,
                        [(d, "runtime") for d in runtime_deps],
                        commit=False,
                    )

                superseded_names = []
                for pred in predecessors_to_supersede:
                    overlap = self._paths_owned_by(pred["name"], file_list)
                    if overlap:
                        self.db.transfer_file_ownership(
                            pred["name"], pkg_id, overlap, hashes=hashes_for_db
                        )
                    self.db.mark_superseded(pred["name"], name)
                    superseded_names.append(pred["name"])

                self.db.log_operation(
                    "install", name, new_version=version, method="archive",
                    commit=False,
                )
                # Q4 ratchet: for unedited /etc/* paths that deployed
                # normally, advance the recorded baseline to the new stock
                # so subsequent upgrades treat new stock as the comparison
                # surface. Rides this BEGIN/COMMIT transaction (commit=False)
                # so any DB-side failure rolls back the ratchets too.
                ratchet_baselines(
                    self.db, config_plan["update_baselines"], commit=False,
                )
                self.db.conn.execute("COMMIT")
            except Exception as e:
                self.db.conn.execute("ROLLBACK")
                return False, (
                    f"Install of {name} {version} FAILED at DB transaction "
                    f"after deploy. DB rolled back; filesystem may have partial "
                    f"changes. Re-run 'pkm install {name}' to recover. Error: {e}"
                )

            # Q4 materialize: copy each staging→<live>.pkmnew for the
            # protected paths. Runs AFTER the DB commit because (a) it is
            # purely filesystem-side and not transactional, (b) an orphan
            # .pkmnew written when the DB transaction subsequently rolled
            # back would mislead the user; placing it after COMMIT means
            # the sidecar only exists when the package row really landed.
            pkmnew_written = materialize_pkmnew_sidecars(
                config_plan["pkmnew_writes"],
            )
            # Hand the caller the sidecar paths as DATA, not as prose buried
            # in the return message. The upgrade loop discards that message
            # on success, which is exactly how sidecars written during a
            # upgrade round were never printed on any of the four systems that
            # reported it: they existed, the install knew about them, and
            # nothing the user saw said so. A list the caller owns cannot be
            # discarded by accident the way a substring of a message can.
            if sidecars_out is not None:
                try:
                    sidecars_out.extend(pkmnew_written)
                except Exception:
                    pass

            # Generate text manifest reflecting the supersede outcome, then
            # record its sha256 on the row. Without that stamp the row's
            # manifest_sha256 stays NULL, which import_manifests reads as
            # "provenance unproven" and re-registers on its first pass — so
            # every installed package took a needless re-register from the
            # moment it was installed.
            manifest_sha256 = self._write_manifest(
                name, version, file_list,
                hashes=hashes_for_db,
                supersedes=superseded_names,
                # S-3: the archive's own description, so a later re-register
                # cannot overwrite the row's real description with a
                # placeholder this writer invented.
                description=pkginfo.get("description"),
                # Release honesty: emit exactly what the committed row records
                # (from .PKGINFO pkgrel above), read back rather than re-derived,
                # so the manifest and the DB cannot disagree. A header-less
                # manifest is what let a later corpus-wide `pkm import` re-register
                # this row at the schema default and erase the true release.
                release=(self.db.get_installed(name) or {}).get("release"),
            )
            self.db.set_manifest_sha256(name, manifest_sha256)

            # D-005 Phase A: fire per-package post-install runtime hook if
            # shipped. Hook lives at <root>/var/lib/pkm/hooks/<name>/post-install
            # (executable). Used by linux-kernel to rebuild + sign the UKI on
            # every install/upgrade per D-005 Option A. Pre-Q2-framework path;
            # kept for backward compatibility with linux-kernel's existing wiring.
            self._run_post_install_hook(name, version)

            # Q2 hook framework (approved 2026-05-19) +
            # canonical pre/post split (2026-05-27 sysusers migration).
            # Order:
            #   1. canonical_pre — sysusers + tmpfiles. Mirrors Arch's
            #      systemd-sysusers.hook / systemd-tmpfiles.hook pacman
            #      hooks. Creates users + runtime dirs declared by the
            #      package's own sysusers.d/tmpfiles.d files so the
            #      archive lifecycle hook can chown to them without
            #      pre-creating them in build.sh useradd/groupadd (which
            #      ran on the BUILD VM at the wrong layer).
            #   2. archive_lifecycle — per-package .scripts/post_install.sh
            #      (opt-in, bespoke packages). Now safe to chown to the
            #      sysusers-declared user because step 1 created it.
            #   3. canonical_post — depmod/ldconfig/glib-compile-schemas/
            #      apparmor-reload/icon-cache/etc. Fired AFTER per-package
            #      lifecycle so any files written by step 2 are seen.
            canonical_pre_result = run_canonical_hooks(
                self.root, file_list, name, version, "install",
                hooks=CANONICAL_HOOKS_PRE,
            )
            # D-9 (hook-output recording): a sealed lifecycle hook writes
            # files that no manifest declares, and an unrecorded write is an
            # unowned file — `pkm provides` denies it, `pkm remove` strands
            # it, and the squashfs ownership gate refuses the image or ships
            # it unaccounted. Bracket the hook with a filesystem snapshot so
            # what it wrote is measured rather than predicted. The whole-tree
            # walk is affordable because this branch is only taken when the
            # archive actually SHIPS the hook; every other package skips it
            # entirely and pays nothing.
            hook_present = archive_lifecycle_hook_path(
                staging, "post_install",
            ) is not None
            snap_before = (
                fs_snapshot(self.root) if hook_present else None
            )
            archive_hook_result = run_archive_lifecycle_hook(
                staging, "post_install", name, version, self.root,
            )
            record_messages = []
            generated_paths = []
            own_modified = []
            if hook_present:
                (generated_paths, own_modified,
                 record_messages) = self._record_hook_outputs(
                    name, snap_before,
                )
            canonical_result = run_canonical_hooks(
                self.root, file_list, name, version, "install",
            )
            hook_summary = format_hook_summary(
                canonical_pre_result, archive_hook_result, canonical_result,
            )
            if record_messages:
                hook_summary = (
                    (hook_summary + "\n") if hook_summary else ""
                ) + "\n".join(record_messages)

            # The text manifest is the transparency mirror of the rows, and
            # `pkm import` rebuilds those rows FROM it — add_installed's
            # INSERT OR REPLACE cascades the old file rows away first. A
            # generated path recorded in the database but absent from the
            # manifest therefore survives exactly until the next corpus-wide
            # import, which runs after every bash-tier package build. Re-emit
            # the manifest from the committed rows so the two agree, and
            # re-stamp its hash so provenance still matches the bytes.
            #
            # The re-emit also fires when the hook only REWROTE the package's
            # own payload and created nothing (D-9b). Those paths are already
            # in file_list, so the file set does not change — what changes is
            # that the manifest now STATES their classification in
            # HOOK-MANAGED headers. Without the re-emit the reclassification
            # existed only in the SQLite row, and a from-scratch import, which
            # has no row to carry it from, registered the file as ordinary
            # owned content for the metadata-sync gate to byte-compare against
            # the pre-hook archive bytes.
            if generated_paths or own_modified:
                manifest_sha256 = self._write_manifest(
                    name, version,
                    file_list + generated_paths,
                    hashes=self.db.get_file_checksums(name),
                    supersedes=superseded_names,
                    description=pkginfo.get("description"),
                    release=(self.db.get_installed(name) or {}).get("release"),
                    generated=generated_paths + own_modified,
                )
                self.db.set_manifest_sha256(name, manifest_sha256)

            # PKM-E post-install reconcile: hooks (and the legacy UKI
            # signing hook above) may rewrite files AFTER their archive
            # hashes were recorded in add_files — re-record this package's
            # non-config file hashes from the live tree so `verify` checks
            # reality, not the pre-hook archive bytes. Scoped to the
            # just-deployed file list: those files came from a verified
            # archive this transaction, so re-recording them is safe; an
            # unscoped reconcile would re-bless unrelated files.
            self.db.reconcile_checksums_from_live(paths=file_list)

            file_count = len([f for f in file_list if not f.endswith("/")])
            extra = ""
            if superseded_names:
                extra = f" — superseded {', '.join(superseded_names)}"

            # Transparency layer (opt-in): list the deployed files WITH their
            # target paths (≤50 inline, else a per-dir breakdown + a pointer
            # to the full list), then the hook/config-merge summary. None
            # keeps the legacy silent (ok, msg) contract for Forge + tests.
            if reporter:
                reporter.file_list(
                    file_list, action="Deploy", pkg=name, root=self.root,
                )

            critical_hook_ids = (
                canonical_pre_result.critical_failures
                + archive_hook_result.critical_failures
                + canonical_result.critical_failures
            )
            if critical_hook_ids:
                # Critical hook failure surfaces install as failed-with-rollback-
                # required. Deploy + DB commit already happened; the caller
                # (cmd_install / cmd_upgrade) decides whether to invoke the Q1
                # rollback flow. Hook summary is included so the user can see
                # which hook failed and why before deciding.
                # PKM-A25: the package IS fully registered (DB+FS) — returning
                # False alone made it masquerade as a generic failure while
                # `pkm list`/`verify` showed it as a normal install. Record a
                # DURABLE degraded marker on the installed row so the degraded
                # state is visible until a successful reinstall clears it.
                self.db.mark_degraded(name, ", ".join(critical_hook_ids))
                if reporter and hook_summary:
                    for line in hook_summary.splitlines():
                        reporter.info(line)
                # The sidecars were written BEFORE the hooks ran, so they
                # exist whether or not a critical hook then failed — and
                # this early return used to skip the block that names them,
                # which is how a degraded install swallowed its own pending
                # config-merge work. A user whose install went wrong needs
                # that list MORE than one whose install went right, not
                # less.
                degraded_pkmnew = summary_lines(pkmnew_written)
                if reporter and degraded_pkmnew:
                    for line in degraded_pkmnew.splitlines():
                        reporter.info(line)
                return False, (
                    f"Installed {name} {version} ({file_count} files){extra}, "
                    f"but critical post-install hook(s) FAILED: "
                    f"{', '.join(critical_hook_ids)}. Live system state may "
                    f"diverge from package metadata. Package marked DEGRADED "
                    f"(see `pkm list`/`pkm verify`); reinstall to retry. "
                    f"Rollback recommended.\n"
                    f"{hook_summary}"
                    + (f"\n{degraded_pkmnew}" if degraded_pkmnew else "")
                )

            msg = f"Installed {name} {version} ({file_count} files){extra}"
            if hook_summary:
                msg = msg + "\n" + hook_summary
            # PKM-A25: the systemd daemon-reload hook is intentionally
            # non-critical (a stale unit cache is recoverable, not broken
            # state), but a SILENT failure means systemd keeps running stale
            # unit definitions. If it failed, emit a SPECIFIC remediation so the
            # user knows the exact fix instead of guessing from a generic warn.
            daemon_reload_failed = any(
                "systemd-daemon-reload" in r.cosmetic_failures
                for r in (canonical_pre_result, archive_hook_result,
                          canonical_result)
            )
            daemon_reload_hint = None
            if daemon_reload_failed:
                daemon_reload_hint = (
                    "  NOTE: `systemctl daemon-reload` did not run — systemd is "
                    "still using the OLD unit definitions for this package. Run "
                    "`sudo systemctl daemon-reload` so the new/updated units "
                    "take effect."
                )
                msg = msg + "\n" + daemon_reload_hint
            # Q4: surface .pkmnew sidecars in the success message so the
            # operator sees pending config-merge work without scrolling
            # back through per-package output.
            pkmnew_summary = summary_lines(pkmnew_written)
            if pkmnew_summary:
                msg = msg + "\n" + pkmnew_summary

            if reporter:
                if hook_summary:
                    for line in hook_summary.splitlines():
                        reporter.info(line)
                if daemon_reload_hint:
                    reporter.warn(daemon_reload_hint.strip())  # PKM-A25
                if pkmnew_summary:
                    for line in pkmnew_summary.splitlines():
                        reporter.info(line)
                # RELEASE-BEARING COMPLETION LINE. The old line printed the
                # version alone, so `forge 1.0.0` read identically whether it
                # had just replaced release 133 with 110 or the reverse. The
                # release is read back off the row that was just written,
                # which is the authority for what is now installed — not the
                # archive metadata, which the ingestion may have adjusted.
                _row = self.db.get_installed(name) or {}
                _vr = f"{version}-{_row.get('release', 1)}"
                _payload = _row.get("payload_version")
                if _payload:
                    _vr += f" (payload {_payload})"
                reporter.installed(name, f"{_vr}{extra}")
                reporter.blank()
            return True, msg

        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _run_post_install_hook(self, name, version):
        """Fire per-package post-install runtime hook if shipped.

        Pkm-runtime hook surface for packages that need to do work on the
        live system after deploy (UKI rebuild per D-005, cache regeneration,
        depmod, etc.). Hook lives at
        <root>/var/lib/pkm/hooks/<name>/post-install (executable).

        Environment provided to the hook:
            PKM_PACKAGE_NAME      — package name
            PKM_PACKAGE_VERSION   — package version
            PKM_PACKAGE_ROOT      — install root (e.g. "/" or a chroot)
                                    — from the HOOK's perspective. When pkm
                                    chroots before running the hook, this
                                    is "/" because the hook now sees the
                                    target as its own root.

        Chroot-aware execution: when self.root != "/" (Forge install or
        any other chroot-target deploy), the hook is executed under
        chroot(self.root). Without this, hooks that reference filesystem-
        rooted paths like /boot/vmlinuz-*, /boot/efi/EFI/Linux/,
        /etc/kernel/cmdline, /var/log/* resolve to the HOST install
        (live ISO + workstation) rather than the target install — the
        linux-kernel:post-install hook then silently fails to write the
        UKI to the target's ESP because /boot/efi inside the host context
        isn't a mountpoint for the target's /dev/sda1.
        Surfaced 2026-05-26 install #18 first-boot: no UKI on ESP, GRUB
        chainload only, missing initramfs, "@" symbol console artifact
        in the early-boot window before systemd-vconsole-setup runs.

        The fix mirrors the chroot-context guards we land in canonical
        hooks (pkm/hooks.py:_apparmor_parser_cmd, _systemctl_daemon_reload_cmd)
        but in the opposite direction: canonical hooks SKIP for chroot;
        per-package hooks RUN UNDER CHROOT for chroot. That's because
        canonical hooks touch the running kernel's LSM/init (which the
        target doesn't have yet), but per-package hooks like UKI rebuild
        operate on the TARGET filesystem and want target-rooted paths.

        Failure is non-fatal: log + continue. The deploy + DB transaction
        already committed; the hook is a side-channel that can be re-run
        manually if it fails.
        """
        hook = self.root / "var" / "lib" / "pkm" / "hooks" / name / "post-install"
        if not hook.is_file() or not os.access(str(hook), os.X_OK):
            return
        # H-024 (same vulnerability class as _run_helper): strip env to
        # HELPER_ENV_ALLOWLIST. Hook executes as the install process; inherited
        # LD_PRELOAD / *_PROXY / PYTHONPATH would let an attacker who can set
        # parent-env vars compromise hook execution.
        env = {k: v for k, v in os.environ.items() if k in HELPER_ENV_ALLOWLIST}
        env["PKM_PACKAGE_NAME"] = name
        env["PKM_PACKAGE_VERSION"] = version

        if str(self.root) == "/":
            # Live-system install: run hook directly. PKM_PACKAGE_ROOT="/"
            # is the natural value the hook would compute itself.
            env["PKM_PACKAGE_ROOT"] = "/"
            cmd = [str(hook)]
        else:
            # Chroot install: execute hook under chroot(self.root) so all
            # filesystem-rooted paths inside the hook (/boot, /boot/efi,
            # /etc, /var, /usr) resolve to the target instead of the host.
            # The bind-mounts of /dev /proc /sys are set up upstream by
            # the install pipeline at PHASE_VIRTUAL_FS
            # (installer/backend/hooks.py:mount_virtual_fs), so chroot
            # has everything it needs. PKM_PACKAGE_ROOT="/" because inside
            # the chroot, the target IS the root.
            env["PKM_PACKAGE_ROOT"] = "/"
            hook_in_chroot = "/" + str(hook.relative_to(self.root))
            cmd = ["chroot", str(self.root), hook_in_chroot]

        try:
            if _TRACE_AVAILABLE:
                _trace.trace_event(
                    "pkm_hook_fire", pkg=name, hook="post_install",
                    script_path=str(hook), argv=cmd,
                )
                # Use traced_run to capture argv + rc + stdout + stderr.
                result = _trace.traced_run(
                    cmd, env=env, phase="pkm_post_install",
                    intent=f"post_install hook for {name}", pkg=name,
                )
                _trace.trace_event(
                    "pkm_hook_done", pkg=name, hook="post_install",
                    rc=result.returncode,
                )
            else:
                result = subprocess.run(cmd, env=env)  # trace-coverage: allow — _trace shim unavailable fallback
            if result.returncode != 0:
                print(
                    f"  WARNING: post-install hook for {name} exited "
                    f"{result.returncode}; install proceeds (hook is "
                    f"non-fatal). Re-run manually: {hook}",
                    file=sys.stderr,
                )
        except (OSError, subprocess.SubprocessError) as e:
            if _TRACE_AVAILABLE:
                try:
                    _trace.trace_event(
                        "pkm_hook_failed", pkg=name, hook="post_install",
                        err=str(e),
                    )
                except Exception:
                    pass
            print(
                f"  WARNING: post-install hook for {name} could not "
                f"execute: {e}; install proceeds (hook is non-fatal).",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # Helpers — manifest reading + predecessor validation + hash building
    # ------------------------------------------------------------------

    def _read_staged_manifest(self, staging, name):
        """Read SUPERSEDES + file list + per-file hashes from the staged manifest.

        Returns (supersedes_list_or_None, file_list, hashes_dict).
        Tolerates absent manifest, missing SUPERSEDES line, and absent
        sha256 columns (legacy format) — the install path falls back
        gracefully in each case.
        """
        manifest_dir = staging / "var" / "lib" / "igos" / "packages"
        if not manifest_dir.exists():
            return None, [], {}
        candidates = sorted(manifest_dir.glob(f"{name}-*"))
        if not candidates:
            return None, [], {}
        try:
            content = candidates[0].read_text()
        except (OSError, UnicodeDecodeError):
            return None, [], {}

        supersedes = None
        files = []
        hashes = {}
        in_files = False
        for line in content.splitlines():
            if line.startswith("SUPERSEDES:"):
                value = line.split(":", 1)[1].strip()
                parsed = [s.strip() for s in value.split(",") if s.strip()]
                supersedes = parsed if parsed else None
            elif line.strip() == "FILE LIST:":
                in_files = True
            elif in_files and line.strip():
                # _parse_manifest_line handles paths with whitespace correctly
                # (anchors hash suffix at end-of-line via regex).
                path, h = _parse_manifest_line(line)
                files.append(path)
                if h is not None:
                    hashes[path.rstrip("/")] = h
        return supersedes, files, hashes

    def _validate_predecessors(self, name, supersedes_decl, queue):
        """Validate each predecessor named in supersedes_decl.

        Returns:
            list of installed-record dicts for predecessors to supersede, OR
            None if the install-order invariant was violated (predecessor
            named appears LATER than this package in the install queue —
            manifest inversion would result, regardless of whether the
            predecessor is currently installed from a prior batch).

        Missing-predecessor and already-superseded predecessor cases
        surface warnings on stderr and are skipped (RFC §11).
        """
        predecessors = []
        queue_list = list(queue) if queue is not None else None

        for pred_name in supersedes_decl:
            if (
                queue_list is not None
                and name in queue_list
                and pred_name in queue_list
            ):
                my_pos = queue_list.index(name)
                pred_pos = queue_list.index(pred_name)
                if pred_pos > my_pos:
                    return None

            pred = self.db.get_installed(pred_name)
            if pred is None:
                if queue_list is not None and pred_name in queue_list:
                    print(
                        f"  WARNING: {pred_name} is earlier in install queue "
                        f"than {name} but is not yet registered. Proceeding "
                        f"with missing-supersedee semantics for this entry.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"  WARNING: {name} declares supersedes:[{pred_name}] but "
                        f"{pred_name} is not installed. Proceeding as a standard "
                        f"install (no ownership transfer).",
                        file=sys.stderr,
                    )
                continue
            if pred.get("superseded_by"):
                print(
                    f"  WARNING: {name} declares supersedes:[{pred_name}] but "
                    f"{pred_name} was already superseded by "
                    f"{pred['superseded_by']}. Skipping ownership transfer "
                    f"for this predecessor.",
                    file=sys.stderr,
                )
                continue
            predecessors.append(pred)
        return predecessors

    def _build_hash_map(self, staging, file_list, manifest_hashes):
        """Return a dict {path: sha256_hex} for every regular file in file_list.

        Primary source: the per-file hashes carried in the staged manifest
        (Phase 2 tracker write-through).
        Fallback source: hash the staged tree directly (RFC v2 §2g — applies
        to legacy archives produced before Phase 2). Either way, every
        regular file ends up with a hash recorded in pkm SQLite, closing
        the NULL-checksum hole.
        """
        hashes = {}
        for entry in file_list:
            if entry.endswith("/"):
                continue  # directories don't carry hashes
            normalized = entry.rstrip("/")
            existing = manifest_hashes.get(normalized)
            if existing:
                hashes[normalized] = existing
                continue
            staged_file = staging / normalized
            if not staged_file.is_file() or staged_file.is_symlink():
                continue
            try:
                hashes[normalized] = _sha256(str(staged_file))
            except (OSError, PermissionError):
                # Best-effort — failed-to-read leaves checksum NULL for this
                # path, surfaced later by `pkm verify --strict`.
                pass
        return hashes

    def _paths_owned_by(self, predecessor_name, file_list):
        """Return the subset of file_list paths currently owned by predecessor.

        Used during atomic supersede to scope the file-ownership transfer to
        only the paths the successor actually overwrote (per RFC §3b — the
        successor's manifest must not annex predecessor paths the successor
        didn't touch).
        """
        pred = self.db.get_installed(predecessor_name)
        if not pred:
            return []
        rows = self.db.conn.execute(
            "SELECT path FROM files WHERE package_id = ?",
            (pred["id"],),
        ).fetchall()
        owned = {r[0] for r in rows}
        overlap = []
        for entry in file_list:
            if entry.endswith("/"):
                continue
            normalized = entry.rstrip("/")
            if normalized in owned:
                overlap.append(normalized)
        return overlap

    # ------------------------------------------------------------------
    # Helpers — archive discovery + version parsing
    # ------------------------------------------------------------------

    def _find_archive(self, name):
        """Return the newest ARCHIVE_DIR archive whose package name is EXACTLY `name`.

        S5-2 (security-review 2026-07-01): the prior matcher used
        `f.name.startswith(f"{name}-")` over a reverse-SORTED listing, which was
        wrong two ways — (a) it matched a DIFFERENT package by name prefix
        (`bash` -> `bash-completion-2.11`, `go` -> `go-md2man-2.0.5`), and (b) it
        returned the lexically-greatest filename, so a planted
        `bash-9.9.9.igos.tar.gz` shadowed the real `bash-5.2.37`. This parses
        `<name>-<version>.igos.tar.gz` and keeps a candidate ONLY when the token
        after `<name>-` begins a version (a digit) — so a name that is a prefix of
        a longer package name never matches — then selects the highest version by
        pkm's own version.compare, never a lexical sort.

        The directory searched is the INSTALL ROOT's archive directory, not the
        running system's: resolving a package for a target out of the live
        machine's archives would install whatever that machine happens to hold.
        With the default root the path is unchanged.
        """
        archive_dir = rootpaths.archive_dir(self.root)
        if not archive_dir.exists():
            return None
        from .version import compare as _vcompare, VersionParseError
        prefix = f"{name}-"
        suffix = ".igos.tar.gz"
        best = None
        best_ver = None
        for f in archive_dir.iterdir():
            n = f.name
            if not (n.startswith(prefix) and n.endswith(suffix)):
                continue
            ver = n[len(prefix):-len(suffix)]
            # Exact-name guard: a real version starts with a digit, so `bash`
            # cannot match `bash-completion-*` (the char after `bash-` is a
            # letter). This is the S5-2 name/version-confusion close.
            if not ver or not ver[0].isdigit():
                continue
            if best is None:
                best, best_ver = f, ver
                continue
            try:
                # version.compare takes (version, release)-bearing entries, not
                # bare strings — a bare string ALWAYS raises VersionParseError,
                # which the fail-safe below swallowed, silently degrading
                # "highest version" to readdir order (the planted-archive
                # shadowing this matcher exists to prevent, reopened by a type
                # mismatch). Archive names carry no release; a fixed "0" makes
                # the comparison purely version-ordered.
                newer = _vcompare((ver, "0"), (best_ver, "0")) > 0
            except VersionParseError:
                # Unparseable version string — never let it win by fallback;
                # keep the already-validated best. (A malformed planted name
                # cannot displace a real version.)
                newer = False
            if newer:
                best, best_ver = f, ver
        return best

    def _version_from_archive(self, name, archive_name):
        """Extract version from archive filename like 'bash-5.2.37.igos.tar.gz'."""
        stem = archive_name.replace(".igos.tar.gz", "")
        if stem.startswith(f"{name}-"):
            return stem[len(f"{name}-"):]
        return "unknown"

    def _find_helper(self, name):
        """Check if an install helper script exists for this package.

        Proprietary software (Chrome, VS Code, Claude Code) can't be
        pre-built into archives. Instead, helper scripts handle the
        download and installation. pkm runs them transparently so the
        user doesn't need to remember separate commands.
        """
        helper = Path(f"/usr/bin/igos-install-{name}")
        if helper.exists() and os.access(str(helper), os.X_OK):
            return helper
        return None

    def _find_eula_helper(self, helper_name):
        """Locate an EULA install-helper by name under EULA_HELPER_DIR.

        Hybrid-model wiring (2026-05-28). Packages declaring
        `eula_helper: <name>` in their package.yml carry the helper
        name through .PKGINFO; pkm.installer.install resolves the
        name to an executable path under
        /usr/lib/intergen/eula-helpers/<name> (the package's build.sh
        is responsible for installing the helper there + making it
        executable).

        Returns the Path if the helper exists + is executable; None
        otherwise. None means the EULA gate cannot fire — the caller
        treats that as a hard install-failure since the package's own
        metadata says EULA acceptance is required.
        """
        helper = EULA_HELPER_DIR / helper_name
        if helper.is_file() and os.access(str(helper), os.X_OK):
            return helper
        return None

    def _extract_eula_helper_from_archive(self, archive_path, helper_name):
        """First-install fallback (PI-Z6): pull the EULA helper out of the
        archive being gated.

        The helper ships inside the very package it gates (nvidia's build.sh
        installs it under /usr/lib/intergen/eula-helpers/), so on a machine
        that never had the package there is no filesystem copy for
        _find_eula_helper to resolve — the gate was unrunnable-by-construction
        on every first install (caught on the first NVIDIA-hardware Forge
        install, Zephyrus 2026-07-06). The archive is the same
        signature-verified artifact the install is about to deploy, so running
        its bundled helper crosses no trust boundary the install itself does
        not already cross.

        Extracts the whole eula-helpers/ subtree (the helper reads sidecar
        files like banner.txt relative to its own path) into a private
        tempdir via the same PEP 706 'data' filter as every other pkm
        extraction. Returns (tempdir, helper_path); the CALLER removes
        tempdir. Returns (None, None) if the archive carries no such helper.
        """
        subtree = "usr/lib/intergen/eula-helpers/"
        tmpdir = tempfile.mkdtemp(prefix=f"pkm-eula-{helper_name}-")
        try:
            with tarfile.open(str(archive_path)) as tf:
                members = [
                    m for m in tf.getmembers()
                    if m.name.lstrip("./").startswith(subtree)
                ]
                if not members:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    return None, None
                tf.extractall(path=tmpdir, members=members, filter="data")
        except (OSError, tarfile.TarError) as e:
            shutil.rmtree(tmpdir, ignore_errors=True)
            print(
                f"  WARNING: could not extract EULA helper "
                f"'{helper_name}' from {archive_path.name}: {e}",
                file=sys.stderr,
            )
            return None, None
        helper = Path(tmpdir) / subtree / helper_name
        if not helper.is_file():
            shutil.rmtree(tmpdir, ignore_errors=True)
            return None, None
        helper.chmod(0o755)
        return tmpdir, helper

    def _run_eula_helper(self, package_name, helper_name, archive_path=None):
        """Run the EULA install-helper for package_name.

        Hybrid-model gate (decided 2026-05-28). Fires from
        PackageInstaller.install BEFORE any staging extract or
        filesystem deploy, so a declined EULA aborts the install with
        no on-disk side effects.

        Subprocess env is stripped to HELPER_ENV_ALLOWLIST (same H-024
        defense the regular install-helper path applies). The helper
        inherits stdin / stdout / stderr unchanged so the prompt_toolkit
        pager renders correctly + the user sees the banner + license
        text + ACCEPT/DECLINE buttons.

        Helper exit-code contract (matches the documented contract in
        packages/extra/nvidia/eula-helper/nvidia-eula.py):

          0 - marker present OR newly accepted; proceed with install.
          1 - user declined the EULA in the pager.
          2 - could not read the EULA text bundled with the package
              (missing/empty sidecar - corrupted install media; PI-Z15
              superseded the old live-fetch design).
          3 - could not write the marker / transcript (filesystem error).
          4 - interactive TTY required (helper invoked from cron /
              scripted install / non-interactive shell).

        Any non-zero code surfaces a clear plain-English message to
        the caller (cmd_install) which prints it to stderr + exits 1.

        Returns (True, "") on success; (False, error_message) on any
        failure path.
        """
        helper_tmpdir = None
        helper_path = self._find_eula_helper(helper_name)
        if helper_path is None and archive_path is not None:
            # First-install fallback (PI-Z6): the helper ships inside the
            # package being gated, so on a fresh system it cannot already be
            # on disk — run the copy bundled in the signature-verified
            # archive itself.
            helper_tmpdir, helper_path = \
                self._extract_eula_helper_from_archive(archive_path, helper_name)
        if helper_path is None:
            return False, (
                f"EULA gate for '{package_name}' refused: the package "
                f"declares eula_helper={helper_name!r} but no executable "
                f"helper exists at {EULA_HELPER_DIR / helper_name} and the "
                f"package archive carries none under "
                f"usr/lib/intergen/eula-helpers/. This points at a broken "
                f"or tampered package. Re-fetch the package, or remove the "
                f"eula_helper declaration if it was set in error."
            )

        env = {k: v for k, v in os.environ.items() if k in HELPER_ENV_ALLOWLIST}
        env["PKM_PACKAGE_NAME"] = package_name
        env["PKM_EULA_HELPER_NAME"] = helper_name

        try:
            result = subprocess.run([str(helper_path)], env=env)  # trace-coverage: allow — EULA helper is interactive (banner + prompt UI); tracing would capture stdout/stderr + break the user-facing accept/decline flow
        except (OSError, subprocess.SubprocessError) as e:
            return False, (
                f"EULA helper '{helper_name}' could not be executed: {e}. "
                f"Cannot proceed with install of '{package_name}' without "
                f"running the EULA gate."
            )
        finally:
            if helper_tmpdir is not None:
                shutil.rmtree(helper_tmpdir, ignore_errors=True)

        if result.returncode == 0:
            return True, ""

        # Map documented exit codes to operator-facing messages. The
        # helper itself prints diagnostics to stdout/stderr (banner,
        # decline-message, fetch-failure details, etc.); these
        # messages are pkm's summary of why the install is aborting.
        rc = result.returncode
        if rc == 1:
            msg = (
                f"EULA for '{package_name}' was declined. The package "
                f"install is aborted; no files were deployed."
            )
        elif rc == 2:
            msg = (
                f"EULA helper '{helper_name}' could not obtain the EULA "
                f"text to present. The text ships bundled with the package "
                f"itself, so this usually means an incomplete or corrupted "
                f"archive; see the helper's stderr above for the exact "
                f"error. The package install is aborted."
            )
        elif rc == 3:
            msg = (
                f"EULA helper '{helper_name}' could not write the "
                f"acceptance marker (filesystem error). The package "
                f"install is aborted; re-run as root after the underlying "
                f"issue is resolved."
            )
        elif rc == 4:
            msg = (
                f"EULA helper '{helper_name}' requires an interactive "
                f"TTY to drive the ACCEPT/DECLINE buttons. The package "
                f"install is aborted; re-run from an interactive shell."
            )
        else:
            msg = (
                f"EULA helper '{helper_name}' exited with unexpected code "
                f"{rc}. The package install is aborted; see the helper's "
                f"output above for diagnostics."
            )
        return False, msg

    def _run_helper(self, name, helper_path):
        """Run an install helper script with transparent output.

        The user sees exactly what the helper is doing — no hidden steps.
        Subprocess env is stripped to HELPER_ENV_ALLOWLIST (H-024) so
        inherited variables like LD_PRELOAD / LD_LIBRARY_PATH / *_PROXY
        / PYTHONPATH cannot redirect the helper's execution or downloads.

        H-007: on helper success, read the manifest that the helper-lib
        igos_helper_commit wrote at /var/lib/igos/helpers/<name>.manifest
        and thread its files[] + symlinks[] + depends[] through
        PackageDB.add_files / add_depends so pkm files/verify/remove
        work for helper-installed packages. Phase A grace period: a
        missing manifest WARNs but does not fail the install (legacy
        helpers that have not yet migrated to the helper-lib API still
        function, just without footprint tracking). Phase B (next
        commit cluster) flips missing-manifest to a hard failure once
        all bundled helpers have migrated.
        """
        print(f"  No local archive for '{name}' — using install helper")
        print(f"  Running: {helper_path}")
        print(f"  {'-' * 50}")
        # Flush our buffered banner BEFORE the helper runs. The helper inherits
        # stdio and writes straight to the terminal fd; without this flush our
        # print() buffer drains only at process exit, so the banner lands AFTER
        # the helper's own output — making the order read backwards.
        sys.stdout.flush()

        helper_env = {k: v for k, v in os.environ.items() if k in HELPER_ENV_ALLOWLIST}
        # Tell the helper it is running UNDER pkm. A helper run any other way
        # deposits files that pkm never ingests, so helper-lib prints an
        # advisory saying so — and stays quiet here, where the advisory would
        # be false. The variable is not in HELPER_ENV_ALLOWLIST, so a value
        # inherited from the caller's environment cannot reach a helper
        # through this path and silence the advisory for a direct run.
        helper_env["PKM_HELPER_INVOCATION"] = "1"

        # Interactive-helper fix (2026-06-14): run the helper with INHERITED
        # stdio — NOT _trace.traced_run, which captures stdout/stderr into
        # pipes. Install helpers are interactive (the vscode helper prints the
        # license + prompts "Type 'I ACCEPT'" and blocks on `read`); capturing
        # their output made that prompt INVISIBLE while the helper blocked on
        # stdin forever. That is the operator's ~1h vscode "hang": pkm had
        # swallowed the prompt the user was supposed to answer. The EULA-helper
        # path (_run_eula_helper above) already inherits stdio for exactly this
        # reason. We still record the invocation + outcome as trace events; only
        # the live stdio stays attached to the user's terminal.
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event(
                    "pkm_helper_invoke", pkg=name, helper_path=str(helper_path),
                )
            except Exception:
                pass
        result = subprocess.run([str(helper_path)], env=helper_env)  # trace-coverage: allow — interactive helper (license prompt + stdin read); capturing would deadlock the accept/decline flow
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event(
                    "pkm_helper_done", pkg=name, rc=result.returncode,
                )
            except Exception:
                pass

        print(f"  {'-' * 50}")

        if result.returncode == HELPER_DECLINE_RC:
            # Item 4: the user declined the vendor EULA. Not a failure — a
            # choice. Return declined=True so the caller cancels cleanly
            # (exit 0), and nothing is recorded as installed.
            return False, "the vendor license was not accepted", True

        if result.returncode != 0:
            # Decision D (2026-05-19): if the helper crashed between
            # igos_helper_init + igos_helper_commit, the EXIT trap
            # wrote a `<name>.manifest.partial` sidecar. Surface the
            # orphan-file list so the user knows what was deposited
            # but never tracked.
            summary = _read_partial_manifest_summary(name)
            if summary is not None:
                return False, (
                    f"Install helper '{name}' aborted with exit "
                    f"{result.returncode} after depositing "
                    f"{summary['count']} file(s) on disk that pkm has "
                    f"NOT tracked. Partial manifest sidecar at "
                    f"{summary['path']}.\n"
                    f"  Recorded paths (first 10): {summary['sample']}\n"
                    f"  Remove the orphans manually or fix the helper "
                    f"+ re-run (a successful re-run supersedes the "
                    f"partial state)."
                ), False
            return False, f"Install helper failed (exit {result.returncode})", False

        # H-007 Phase B: read + validate the manifest the helper-lib
        # wrote. Phase A's WARN-continue grace period is now closed —
        # all bundled extra-tier helpers (chrome, vscode, edge, brave,
        # discord, spotify, claude-code) source
        # /usr/share/igos/helpers/helper-lib.sh and call the API. A
        # missing or invalid manifest indicates either a third-party
        # helper that has not migrated, a helper script that aborted
        # before igos_helper_commit, or a tampered manifest — all of
        # which warrant fail-closed handling so the user does not end
        # up with on-disk files pkm cannot track or remove.
        # Decision D defensive cleanup: if a stale partial sidecar
        # exists alongside a successful run, the helper-lib commit
        # path already tried to unlink it -- but a permission edge
        # case could leave it behind. Clean up best-effort so pkm
        # verify doesn't keep surfacing a phantom partial-state warning.
        stale_partial = HELPER_MANIFEST_DIR / f"{name}.manifest.partial"
        if stale_partial.is_file():
            try:
                stale_partial.unlink()
            except OSError:
                pass

        manifest, err = _read_helper_manifest(name)
        if manifest is None:
            return False, (
                f"Install helper '{name}' did not write a trackable "
                f"manifest ({err}). Refusing to register the package "
                f"without file tracking — pkm files/verify/remove "
                f"would be broken for this install. If you authored a "
                f"third-party helper, source "
                f"/usr/share/igos/helpers/helper-lib.sh and call "
                f"igos_helper_init + record_file + commit (see "
                f"docs/architecture/helper-manifest-spec-v1.md). The "
                f"helper-deposited files remain on disk; remove them "
                f"manually if you want a clean slate before re-running."
            ), False

        return self._register_helper_footprint(name, manifest, helper_path)

    def reattach_helper_payload(self, name):
        """Re-record a download helper's application on a freshly installed row.

        The upgrade of a download-helper package removes the package record
        and the archive's own files but leaves the application the helper
        fetched on disk, together with the helper's footprint manifest (see
        PackageRemover.remove, keep_helper_payload). Once the new archive is
        installed this reads that manifest and records the application on
        the new row exactly as a fresh helper run would: file rows labelled
        as the helper's, the recorded dependencies, install_method 'helper',
        the payload version, and the text manifest. Nothing is downloaded and
        nothing on disk changes.

        Returns (ok, message).
        """
        manifest, err = _read_helper_manifest(
            name, root=None if str(self.root) == "/" else str(self.root))
        if manifest is None:
            return False, (
                f"the footprint manifest for {name} could not be read "
                f"({err}). The application's files are still on disk but "
                f"are no longer recorded — run `sudo pkm install {name}` "
                f"to record them again."
            )
        helper_path = self.root / "usr" / "bin" / f"igos-install-{name}"
        ok, msg, _declined = self._register_helper_footprint(
            name, manifest, helper_path, verb="Re-recorded")
        return ok, msg

    def _register_helper_footprint(self, name, manifest, helper_path,
                                   verb="Installed"):
        """Record a validated helper footprint manifest on the package row.

        Shared by the helper run (a fresh download) and by the upgrade path
        (the application kept across a package upgrade). Returns the
        (ok, message, declined) triple _run_helper returns.
        """
        # Manifest present + validated: wire files + depends through the
        # DB inside a single BEGIN/COMMIT so the install record stays
        # atomic with the file/depend rows.
        # PKM-A26: the installer used to fabricate an opaque "latest" sentinel
        # here (`or "latest"`), which defeats is_upgradable (the proprietary
        # app could NEVER be detected as upgradable) and lies about what is
        # installed. Require the helper to report a version
        # (igos_helper_set_version always writes at least "unknown"); fail
        # closed only if it reported genuinely nothing.
        version = (manifest.get("version_installed") or "").strip()
        if not version:
            return False, (
                f"Install helper '{name}' did not report an installed version. "
                f"Refusing to register a package with no version — pkm could "
                f"not track its upgrades. The helper must call "
                f"igos_helper_set_version with the upstream version."
            ), False
        # PKM-A26: thread release on the helper path too (parity with the
        # archive path's A02 fix). The manifest carries no release today, so
        # this defaults to 1 but is forward-compatible if a future manifest
        # records release_installed.
        try:
            release = int(manifest.get("release_installed", 1))
        except (TypeError, ValueError):
            release = 1
        # A helper manifest that carries no release_installed says nothing
        # about the release; the archive install that preceded this merge
        # recorded the .PKGINFO release, and that value must survive. Measured
        # 2026-09-03: cuda-toolkit r5 was rewritten to r1 here, and
        # `pkm list upgradable` then offered a phantom 13.3.1-1 -> 13.3.1-5.
        if "release_installed" not in manifest:
            prior = self.db.get_installed(name)
            if prior is not None:
                try:
                    release = max(release, int(prior["release"] or 1))
                except (TypeError, ValueError, KeyError):
                    pass
        manifest_files = manifest.get("files", [])
        manifest_symlinks = manifest.get("symlinks", [])
        manifest_depends = manifest.get("depends", [])
        action_log = manifest.get("post_install_actions_log", [])

        # Combine files[] + symlink-path-only into a single tracking list.
        # POSIX unlink semantics mean os.remove() on a symlink unlinks the
        # symlink itself, not the target — so pkm/remover.py's iteration
        # of db.get_files(name) cleanly handles both. Targets of symlinks
        # are NOT auto-deleted on remove (the helper's record_file calls
        # cover any target files that should also be tracked).
        all_paths = list(manifest_files) + [
            s.get("path", "") for s in manifest_symlinks
        ]
        # PackageDB stores POSIX-relative paths (no leading slash). The
        # add_files signature expects "usr/bin/foo" not "/usr/bin/foo";
        # PackageRemover reconstructs absolute paths via self.root / path
        # (rebased on install-root per H-011 closure at aff8b729).
        rel_paths = [p.lstrip("/") for p in all_paths]

        # Content hashes for the helper-deposited payload, read from the live
        # tree because a helper install has no archive and no staging tree to
        # hash from. Without them add_files recorded a NULL checksum for every
        # path (it only auto-hashes when it can reach the file, and did so
        # without a reference either way), so `pkm verify` on a
        # helper-installed package could confirm existence and nothing more —
        # a post-install change to a proprietary app's binaries was
        # undetectable. Symlinks and unreadable paths are skipped; verify
        # already treats an absent hash as unverifiable and says so.
        helper_hashes = {}
        for rel in rel_paths:
            abs_path = str(self.root / rel)
            try:
                if os.path.isfile(abs_path) and not os.path.islink(abs_path):
                    helper_hashes[rel] = _sha256(abs_path)
            except (OSError, PermissionError):
                continue

        existing = self.db.get_installed(name)
        self.db.conn.execute("BEGIN")
        try:
            if existing is not None:
                # Unified `pkm install <app>` (pkm 2b items 1+3): the <app>
                # package was just installed (its infra files — igos-install-<app>
                # + keyring). Merge the helper footprint (the REAL app's files)
                # into that SAME entry, preserving its row id, so [installed]
                # shows ONE honest <app> entry and `pkm remove <app>` cleans BOTH
                # the infra and the real app files. add_installed here would
                # INSERT OR REPLACE → a fresh id, orphaning the infra file rows.
                pkg_id = existing["id"]
                # PKM-A26: UPDATE (preserve the row id so infra file rows are
                # not orphaned) + emit the db-write trace the bare UPDATE used
                # to skip. Threads release and the payload build.
                self.db.update_helper_merge(
                    pkg_id, name, version, release, commit=False,
                    payload_version=version,
                )
            else:
                pkg_id = self.db.add_installed(
                    name=name,
                    version=version,
                    release=release,
                    install_method="helper",
                    archive_path=str(helper_path),
                    commit=False,
                    payload_version=version,
                )
            # Ingestion REPLACES the payload footprint rather than adding to
            # it. Before this, a second helper run for the same package left
            # the previous payload's rows in place beside the new ones: paths
            # the new build dropped stayed recorded with their old checksums
            # (`pkm verify` then reported a correct install as damaged, one
            # entry per dropped file), and every path present in both builds
            # gained a duplicate row because the files table has no
            # UNIQUE(package_id, path) for INSERT OR REPLACE to resolve
            # against. Archive-deposited rows for paths the payload does not
            # claim survive — the stub's helper binary and vendor keyring are
            # owned by the same package and are not part of what a payload
            # fetch replaces.
            if existing is not None:
                self.db.replace_helper_footprint(
                    pkg_id, rel_paths, commit=False,
                )
            if rel_paths:
                self.db.add_files(pkg_id, rel_paths, hashes=helper_hashes,
                                  commit=False, source="helper")
            if manifest_depends:
                dep_tuples = [(d, "runtime") for d in manifest_depends if isinstance(d, str)]
                if dep_tuples:
                    self.db.add_depends(pkg_id, dep_tuples, commit=False)
            self.db.log_operation(
                "install", name, new_version=version, method="helper",
            )
            self.db.conn.commit()
        except Exception:
            self.db.conn.rollback()
            raise

        # Text manifest for the helper payload. Every other install path
        # writes one; this path did not, so a helper-installed package was
        # absent from /var/lib/igos/packages entirely — nothing for an
        # operator to read, nothing for the manifest-consuming tooling to
        # see, and no provenance record.
        #
        # The file list is read back from the committed rows rather than
        # taken from `rel_paths`, because of the merge case above: a unified
        # `pkm install <app>` first installs the app's infra archive and then
        # merges the helper footprint into that SAME row, so the row owns
        # both sets while rel_paths holds only the helper's half. Writing
        # rel_paths would produce a manifest that understates what the
        # package owns, and the next `pkm import` would then re-register the
        # row from that understatement and drop the infra files' ownership.
        # The rows are the authoritative record; the manifest mirrors them.
        helper_entries = [
            f["path"] + "/" if f["is_dir"] else f["path"]
            for f in self.db.get_files(name)
        ]
        helper_manifest_sha256 = self._write_manifest(
            name, version, helper_entries,
            hashes=self.db.get_file_checksums(name),
            release=release,
        )
        self.db.set_manifest_sha256(name, helper_manifest_sha256)

        # A package has exactly ONE current text manifest. The file is named
        # <name>-<version>, so a payload upgrade writes a new file and used to
        # leave the previous build's file beside it. Two manifests for one
        # package is not a harmless leftover: `pkm import` re-registers from
        # every file it finds in that directory, in filename order, so which
        # build the row ends up claiming depends on how the two version
        # strings happen to sort. Drop the superseded ones now that the new
        # file is written and its hash recorded.
        superseded = self._remove_superseded_manifests(name, version)
        if superseded:
            print(f"  Superseded manifest(s) removed: {', '.join(superseded)}")

        # post_install_actions_log entries are transparency artifacts
        # only in v1.0 (per H-007 design Q3): printed to the user +
        # logged via the operation history; never replayed on remove.
        # Teardown for helper-installed side effects (icon caches, mime
        # databases, etc.) is a future audit row's surface.
        if action_log:
            print(f"  Helper recorded {len(action_log)} post-install action(s):")
            for action in action_log:
                print(f"    - {action}")

        summary = (
            f"{verb} {name} {version} via helper "
            f"({helper_path.name}) — {len(rel_paths)} files tracked"
        )
        if manifest_depends:
            summary += f", {len(manifest_depends)} dep(s) recorded"
        return True, summary, False

    # ------------------------------------------------------------------
    # Helpers — text manifest write-out
    # ------------------------------------------------------------------

    def _record_hook_outputs(self, name, snap_before):
        """Register what a package's archive lifecycle hook just wrote (D-9).

        Args:
            name: the package the hook belongs to.
            snap_before: the pkm.hookrecord.fs_snapshot taken immediately
                before the hook ran.

        Returns (generated_paths, own_modified, messages): the paths
        registered to the package as hook-generated, in pkm's storage form
        (directories with a trailing slash); the package's OWN payload paths
        the hook rewrote in place (D-9b); and the transcript lines describing
        what happened.

        The two path lists are returned separately because the caller must
        re-emit the text manifest for EITHER. A hook that only rewrites its
        own payload creates nothing — docbook-xml, the class's first live
        member, is exactly that shape — so a caller keying the re-emit on
        created paths alone left the reclassification recorded in SQLite and
        nowhere else, and the next from-scratch import could not recover it.

        Failure is DURABLE, not a transcript line. The install transaction has
        already committed by the time a hook runs, so a recording failure
        cannot roll anything back — and a warning printed once scrolls away
        while the unowned files stay on disk forever. Marking the package
        degraded records the condition where `pkm list` and `pkm verify` both
        surface it until a successful reinstall clears it, which is the same
        mechanism a failed critical hook already uses (PKM-A25).
        """
        snap_after = fs_snapshot(self.root)
        created_files, created_dirs, modified = diff_snapshots(
            snap_before, snap_after,
        )
        if not (created_files or created_dirs or modified):
            return [], [], []

        owned = self.db.all_owned_paths()
        claim_files, foreign = claimable(created_files, owned)
        claim_dirs, _ = claimable(created_dirs, owned)
        # Directories first so a `pkm remove` walking the rows in order
        # removes contents before their containers.
        generated_paths = claim_dirs + claim_files

        # D-9b (hook-modified own payload): a MODIFIED path that THIS
        # package's own payload owns is the hook rewriting its own deployed
        # file — a catalog/index the recipe installs pristine and its
        # post_install regenerates in place (docbook-xml's catalog.xml, the
        # class's first live member). The row's recorded checksum is the
        # pre-hook archive hash and can never match again, so verify would
        # report designed behavior as damage forever. Reclassify exactly the
        # own-payload subset to the generated content class (existence-
        # checked, named bucket — the same D-9 treatment hook-CREATED files
        # get). Paths owned by OTHER packages keep hookrecord's original
        # rule verbatim: reported, never absorbed.
        own_paths = {f["path"] for f in self.db.get_files(name)}
        own_modified = sorted(
            p for p in modified if p.rstrip("/") in own_paths)
        other_modified = sorted(
            p for p in modified if p.rstrip("/") not in own_paths)

        messages = format_record_summary(
            generated_paths, foreign, other_modified, own_modified)
        if not generated_paths and not own_modified:
            return [], [], messages

        pkg = self.db.get_installed(name)
        if pkg is None:
            # The row this transaction committed is gone. Nothing can be
            # attributed, and pretending otherwise would be the silent
            # failure this whole seam exists to remove.
            return [], [], messages + [
                f"  hook[record] CRITICAL: {name} is not registered — "
                f"{len(generated_paths)} hook-generated path(s) could not be "
                f"recorded and are UNOWNED on disk"
            ]
        try:
            if generated_paths:
                self.db.record_generated_files(pkg["id"], generated_paths)
            if own_modified:
                self.db.mark_files_generated(pkg["id"], own_modified)
        except (sqlite3.Error, OSError) as e:
            self.db.mark_degraded(name, f"hook-output recording failed: {e}")
            return [], [], messages + [
                f"  hook[record] CRITICAL: could not record "
                f"{len(generated_paths)} hook-generated path(s) — they are "
                f"UNOWNED on disk; package marked DEGRADED: {e}"
            ]
        return generated_paths, own_modified, messages

    # ------------------------------------------------------------------
    # Source-build lane: recording what a recipe's post_install changed
    # ------------------------------------------------------------------

    def hook_baseline(self, name):
        """Hash the package's OWN regular files, for comparison across a hook.

        The archive install path learns which files a hook touched by taking a
        filesystem snapshot immediately before running it. The source-build
        lane runs the recipe's post_install from its driver, after the manifest
        is written and after `pkm import` has registered the rows, so it has no
        such observation and its rows recorded the pre-hook bytes with nothing
        marking them. This is the first half of giving that lane the same
        evidence: the state to compare against, captured before the hook runs.

        SCOPED TO THIS PACKAGE'S OWN ROWS on purpose. A whole-root snapshot
        would also see files other packages own, and a hook that writes one of
        those must keep the reported-never-absorbed treatment — the ownership
        boundary is what stops this mechanism from being a way to silence
        another package's byte check.

        Returns {path: sha256} for the regular files that exist and can be
        read. A path that is missing or unreadable is OMITTED rather than
        recorded as None: absent-then-present and unreadable-then-readable are
        not evidence that a hook rewrote content, and inventing a value for
        either would manufacture a change the machine never observed.
        """
        baseline = {}
        for entry in self.db.get_files(name):
            if entry["is_dir"]:
                continue
            path = entry["path"]
            abs_path = str(self.root / path)
            if not os.path.isfile(abs_path) or os.path.islink(abs_path):
                continue
            try:
                baseline[path] = _sha256(abs_path)
            except (OSError, PermissionError):
                continue
        return baseline

    def record_hook_changes(self, name, baseline):
        """Reclassify this package's own payload files its hook rewrote.

        The second half. Compares the live content of the package's own files
        against `baseline` — captured by hook_baseline before the recipe's
        post_install ran — and treats a path whose bytes changed across that
        window as hook-managed content: the same D-9b rule the archive install
        path applies, reached by the same kind of observation.

        A path is considered ONLY if it is in the baseline AND owned by this
        package. Divergence is never inferred from the recorded checksum: a
        file that simply disagrees with its manifest hash may be damaged, and
        reclassifying that would stop the byte check on exactly the file most
        in need of it. The window is the evidence.

        The manifest is then re-emitted through the ordinary writer so it
        STATES the class, because the row alone cannot survive a from-scratch
        import — which is the whole reason the source lane's rows arrived
        unmarked on the shipped image.

        Returns (changed_paths, messages).
        """
        pkg = self.db.get_installed(name)
        if pkg is None:
            return [], [f"  hook[record] {name} is not registered — nothing "
                        f"to record"]

        owned = {e["path"] for e in self.db.get_files(name) if not e["is_dir"]}
        changed = []
        for path, before in sorted(baseline.items()):
            if path not in owned:
                # The rows moved under us between the two halves. Say so
                # rather than acting on a path this package no longer owns.
                continue
            abs_path = str(self.root / path)
            if not os.path.isfile(abs_path) or os.path.islink(abs_path):
                continue
            try:
                after = _sha256(abs_path)
            except (OSError, PermissionError):
                continue
            if after != before:
                changed.append(path)

        if not changed:
            return [], []

        messages = [
            f"  hook[record] {name}: post_install rewrote "
            f"{len(changed)} of its own payload file(s) — recorded as "
            f"hook-managed content (existence-checked)"
        ] + [f"    {p}" for p in changed]

        try:
            self.db.mark_files_generated(pkg["id"], changed)
        except (sqlite3.Error, OSError) as e:
            self.db.mark_degraded(
                name, f"hook-change recording failed: {e}")
            return [], messages + [
                f"  hook[record] CRITICAL: could not record {len(changed)} "
                f"hook-managed path(s) for {name}; package marked DEGRADED: "
                f"{e}"
            ]

        # Re-emit from the committed rows so the manifest mirrors exactly what
        # the database owns, and re-stamp its provenance so a later import
        # treats the new bytes as the record it was built from rather than as
        # a package needing re-registration.
        file_list = [
            e["path"] + ("/" if e["is_dir"] else "")
            for e in self.db.get_files(name)
        ]
        already = self.db.get_generated_paths(name)
        manifest_sha256 = self._write_manifest(
            name, pkg["version"], file_list,
            hashes=self.db.get_file_checksums(name),
            release=pkg.get("release"),
            description=pkg.get("description"),
            generated=sorted(set(changed) | set(already)),
        )
        self.db.set_manifest_sha256(name, manifest_sha256)
        return changed, messages

    def _remove_superseded_manifests(self, name, keep_version):
        """Delete this package's older text manifests, keeping <name>-<version>.

        Ownership is decided by READING each candidate, not by its filename:
        the file is claimed only when its own PACKAGE NAME header parses back
        to exactly this package. A filename-prefix rule would delete
        `vscode-insiders-1.0` while installing `vscode`, because that name
        also begins with "vscode-".

        Returns the filenames removed, in sorted order. A file that cannot be
        read or unlinked is left alone and omitted from the return — losing a
        stale manifest is not worth failing an otherwise complete install, and
        the next successful run tries again.
        """
        manifest_dir = self.root / "var" / "lib" / "igos" / "packages"
        if not manifest_dir.is_dir():
            return []
        keep = f"{name}-{keep_version}"
        removed = []
        for candidate in sorted(manifest_dir.iterdir()):
            if not candidate.is_file() or candidate.name == keep:
                continue
            if not candidate.name.startswith(f"{name}-"):
                continue
            try:
                meta = _parse_manifest(
                    candidate.read_text(encoding="utf-8", errors="replace")
                )
            except OSError:
                continue
            if not meta or meta.get("name") != name:
                continue
            try:
                candidate.unlink()
            except OSError:
                continue
            removed.append(candidate.name)
        return removed

    def _write_manifest(self, name, version, file_list, hashes=None,
                        supersedes=None, release=None, description=None,
                        generated=None):
        """Write a text manifest alongside the SQLite entry.

        The text manifest is a transparency artifact — pkm SQLite's
        files.checksum is the authoritative source. The manifest mirrors
        the post-install ownership state including any SUPERSEDES header
        and per-file sha256 columns.

        release: the release recorded on the installed row. Emitted as a
            PACKAGE RELEASE header so a later `pkm import` re-registering from
            these bytes carries the true release instead of falling to the
            schema default. None (caller does not know it) omits the header —
            the legacy-tolerant shape _parse_manifest already accepts.
        description: the package's real description, normally the archive
            .PKGINFO's. The manifest used to state a fixed placeholder here,
            and `pkm import` parses this line back into installed.description
            — so a re-register overwrote the true description with the
            placeholder. Passing the real text makes the two paths agree.
            None keeps the placeholder, which stays honest about knowing
            nothing rather than inventing something.
        generated: paths whose live content this package's own sealed hook
            created or rewrote (D-9). Emitted as HOOK-MANAGED headers so a
            later `pkm import` can restore the classification on a database
            that has never seen this package — a source-built chroot registers
            every package for the first time, and until the manifest could
            state this, those rows imported unflagged and the ISO
            metadata-sync gate byte-compared them against pre-hook bytes.
            Only paths present in file_list are emitted: the import refuses a
            header for an undeclared path, so writing one would emit a claim
            the reader is required to reject.

        Returns the sha256 of the manifest bytes just written, so the caller
        can record it on the installed row (Component A provenance).
        """
        manifest_dir = self.root / "var" / "lib" / "igos" / "packages"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{name}-{version}"

        total_size = sum(
            os.path.getsize(str(self.root / f)) for f in file_list
            if not f.endswith("/") and os.path.isfile(str(self.root / f))
        )
        if total_size > 1024 * 1024:
            human_size = f"{total_size / 1024 / 1024:.1f}M"
        else:
            human_size = f"{total_size / 1024:.0f}K"

        from datetime import datetime, timezone
        lines = [
            f"PACKAGE NAME: {name}-{version}",
            f"PACKAGE VERSION: {version}",
        ]
        if release is not None:
            lines.append(f"PACKAGE RELEASE: {release}")
        lines += [
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)",
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"BUILD SYSTEM: InterGenOS pkm",
        ]
        if supersedes:
            lines.append(f"SUPERSEDES: {', '.join(supersedes)}")
        if generated:
            declared = {f.rstrip("/") for f in file_list}
            for path in sorted({p.rstrip("/") for p in generated}):
                if path in declared:
                    lines.append(f"HOOK-MANAGED: {path}")
        lines.extend([
            "DESCRIPTION:",
            f"{name}: {description or '(installed via pkm)'}",
            "",
            "FILE LIST:",
        ])
        for entry in file_list:
            if entry.endswith("/"):
                lines.append(entry)
            else:
                normalized = entry.rstrip("/")
                h = (hashes or {}).get(normalized)
                lines.append(f"{entry} sha256:{h}" if h else entry)

        manifest_path.write_text("\n".join(lines) + "\n")
        return _sha256(str(manifest_path))
