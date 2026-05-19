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
import stat
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .database import (
    PackageDB,
    ARCHIVE_DIR,
    MANIFEST_DIR,
    _sha256,
    _parse_manifest_line,
)
from .repo import _read_package_meta
from .hooks import (
    run_canonical_hooks,
    run_archive_lifecycle_hook,
    format_hook_summary,
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


# Environment allowlist for install-helper subprocess execution (H-024).
# Default-deny per Holy-Grail-aligned security posture. Anything outside this
# set is dropped before exec to prevent inherited-variable attacks via e.g.
# LD_PRELOAD / LD_LIBRARY_PATH / LD_AUDIT (library injection), *_PROXY (MitM
# of helper-time upstream downloads), PYTHONPATH (Python-module hijack).
HELPER_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM",
    "TMPDIR", "SHELL",
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


def _read_helper_manifest(name):
    """Read + validate the helper manifest produced by helper-lib.sh.

    H-007. Returns (manifest_dict, None) on success or
    (None, error_message) on absent/malformed/disallowed manifest.
    Phase A grace-period semantics: a missing manifest is NOT an
    error from pkm's perspective — _run_helper falls back to the
    legacy "register only add_installed + log_operation" behavior
    and warns once on the operation log. Phase B flips the missing-
    manifest case to a hard failure once all bundled helpers have
    migrated to the helper-lib API.
    """
    manifest_path = HELPER_MANIFEST_DIR / f"{name}.manifest"
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
        if (not isinstance(path, str)) or (
            not any(path.startswith(p) for p in HELPER_PATH_ALLOWLIST_PREFIXES)
        ):
            bad_paths.append(path)
            if len(bad_paths) >= 5:
                break
    for entry in symlinks:
        if not isinstance(entry, dict) or "path" not in entry:
            return None, f"symlink entry malformed: {entry!r}"
        link = entry.get("path", "")
        if not isinstance(link, str) or not any(
            link.startswith(p) for p in HELPER_PATH_ALLOWLIST_PREFIXES
        ):
            bad_paths.append(link)
            if len(bad_paths) >= 5:
                break
    if bad_paths:
        return None, (
            "path(s) outside helper-manifest allowlist (accepts only "
            f"{', '.join(HELPER_PATH_ALLOWLIST_PREFIXES)}): {bad_paths!r}"
        )

    return manifest, None


def _safe_extract_tar(archive_path, dest, exclude_paths=None):
    """Extract a .tar.gz archive to dest using the PEP 706 'data' filter.

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

    Fail-closed: any FilterError aborts extraction and returns
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

    def member_filter(member, path):
        if member.name in exclude_set:
            return None
        return tarfile.data_filter(member, path)

    try:
        with tarfile.open(str(archive_path), "r:gz") as tf:
            tf.extractall(path=str(dest), filter=member_filter)
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


class PackageInstaller:
    """Install packages from pre-built archives."""

    def __init__(self, db: PackageDB, root="/"):
        self.db = db
        self.root = Path(root)

    def install(self, name, archive_path=None, queue=None, expected_sha256=None,
                install_reason="manual"):
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
            return False, f"{name} {existing['version']} is already installed. Use 'pkm reinstall' to replace."
        if existing and existing.get("superseded_by"):
            return False, (
                f"{name} {existing['version']} was superseded by "
                f"{existing['superseded_by']} on {existing.get('superseded_at')}. "
                f"To revert, run 'pkm reinstall {name}' explicitly — this will "
                f"un-retire {name} and is not recommended without a clear reason."
            )

        if not archive_path:
            archive_path = self._find_archive(name)
        if not archive_path:
            helper = self._find_helper(name)
            if helper:
                return False, (
                    f"No local archive for '{name}', but an install helper exists.\n"
                    f"         Run: pkm install-helper {name}"
                )
            return False, f"No archive found for '{name}' in {ARCHIVE_DIR}"

        archive_path = Path(archive_path)
        if not archive_path.exists():
            return False, f"Archive not found: {archive_path}"

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

        staging = Path(tempfile.mkdtemp(prefix=f"pkm-{name}-"))
        try:
            # H-022: extract archive to staging via Python
            # tarfile + PEP 706 'data' filter. Path-traversal, absolute-path,
            # escape-via-symlink, and device-file members are rejected; the
            # filter also strips uid/gid/uname/gname and setuid/setgid/sticky
            # bits (the latter are selectively restored further down based on
            # original archive metadata). Fail-closed: any FilterError aborts
            # the install with a message naming the offending member.
            ok, err = _safe_extract_tar(archive_path, staging)
            if not ok:
                return False, err

            # Read staged manifest (if present): SUPERSEDES + file list + hashes.
            supersedes_decl, manifest_files, manifest_hashes = self._read_staged_manifest(staging, name)

            # H-008: read canonical .PKGINFO key=value for tier/description/
            # license/build_date population at add_installed below. Falls back
            # to empty dict for archives built before .PKGINFO ratification.
            pkginfo = _read_package_meta(archive_path) or {}

            # Build canonical file list from the staged tree itself; the
            # manifest's file list is a transparency artifact, not the
            # authoritative ownership record. Skip archive-level metadata
            # files (.PKGINFO + package.yml) — they're provenance/metadata,
            # not installed-system payload.
            file_list = []
            for root, dirs, files in os.walk(staging):
                for d in sorted(dirs):
                    rel = os.path.relpath(os.path.join(root, d), staging)
                    if not os.path.islink(os.path.join(root, d)):
                        file_list.append(rel + "/")
                for f in sorted(files):
                    rel = os.path.relpath(os.path.join(root, f), staging)
                    if rel in _ARCHIVE_METADATA_FILES:
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

            # Safety check: don't clobber root-level symlinks (e.g., usr-merge).
            dangerous = []
            for entry in ("lib", "lib64", "bin", "sbin"):
                staged = staging / entry
                root_path = self.root / entry
                if staged.is_dir() and not staged.is_symlink() and root_path.is_symlink():
                    dangerous.append(entry)
            if dangerous:
                return False, (
                    f"DANGEROUS: Archive contains top-level dirs that would "
                    f"collide with root symlinks: {' '.join(dangerous)}"
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
                _ARCHIVE_METADATA_FILES | set(config_plan["protect"])
            )
            ok, err = _safe_extract_tar(
                archive_path, self.root, exclude_paths=deploy_excludes,
            )
            if not ok:
                return False, f"Failed to deploy: {err}"

            # Restore setuid/setgid/sticky bits that hardened-tar dropped.
            try:
                with tarfile.open(str(archive_path)) as tf:
                    for member in tf.getmembers():
                        if not member.isfile():
                            continue
                        if member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                            deployed = (self.root / member.name.lstrip("/")).resolve()
                            try:
                                deployed.relative_to(self.root.resolve())
                            except ValueError:
                                continue  # path escapes install root
                            if deployed.exists():
                                deployed.chmod(member.mode)
            except (OSError, tarfile.TarError) as e:
                print(f"  WARNING: setuid restore failed for {name}: {e}", file=sys.stderr)

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
                pkg_id = self.db.add_installed(
                    name=name, version=version, install_method="archive",
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
                    commit=False,
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

            # Generate text manifest reflecting the supersede outcome.
            self._write_manifest(
                name, version, file_list,
                hashes=hashes_for_db,
                supersedes=superseded_names,
            )

            # D-005 Phase A: fire per-package post-install runtime hook if
            # shipped. Hook lives at <root>/var/lib/pkm/hooks/<name>/post-install
            # (executable). Used by linux-kernel to rebuild + sign the UKI on
            # every install/upgrade per D-005 Option A. Pre-Q2-framework path;
            # kept for backward compatibility with linux-kernel's existing wiring.
            self._run_post_install_hook(name, version)

            # Q2 hook framework (operator-greenlit 2026-05-19): archive-side
            # lifecycle hook (.scripts/post_install.sh inside the archive,
            # opt-in for bespoke packages) followed by content-triggered
            # canonical hooks scanning file_list for patterns that trigger
            # depmod/ldconfig/glib-compile-schemas/apparmor-reload/etc.
            archive_hook_result = run_archive_lifecycle_hook(
                staging, "post_install", name, version, self.root,
            )
            canonical_result = run_canonical_hooks(
                self.root, file_list, name, version, "install",
            )
            hook_summary = format_hook_summary(archive_hook_result, canonical_result)

            file_count = len([f for f in file_list if not f.endswith("/")])
            extra = ""
            if superseded_names:
                extra = f" — superseded {', '.join(superseded_names)}"

            critical_hook_ids = (
                archive_hook_result.critical_failures
                + canonical_result.critical_failures
            )
            if critical_hook_ids:
                # Critical hook failure surfaces install as failed-with-rollback-
                # required. Deploy + DB commit already happened; the caller
                # (cmd_install / cmd_upgrade) decides whether to invoke the Q1
                # rollback flow. Hook summary is included so the user can see
                # which hook failed and why before deciding.
                return False, (
                    f"Installed {name} {version} ({file_count} files){extra}, "
                    f"but critical post-install hook(s) FAILED: "
                    f"{', '.join(critical_hook_ids)}. Live system state may "
                    f"diverge from package metadata. Rollback recommended.\n"
                    f"{hook_summary}"
                )

            msg = f"Installed {name} {version} ({file_count} files){extra}"
            if hook_summary:
                msg = msg + "\n" + hook_summary
            # Q4: surface .pkmnew sidecars in the success message so the
            # operator sees pending config-merge work without scrolling
            # back through per-package output.
            pkmnew_summary = summary_lines(pkmnew_written)
            if pkmnew_summary:
                msg = msg + "\n" + pkmnew_summary
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
        env["PKM_PACKAGE_ROOT"] = str(self.root)
        try:
            result = subprocess.run([str(hook)], env=env)
            if result.returncode != 0:
                print(
                    f"  WARNING: post-install hook for {name} exited "
                    f"{result.returncode}; install proceeds (hook is "
                    f"non-fatal). Re-run manually: {hook}",
                    file=sys.stderr,
                )
        except (OSError, subprocess.SubprocessError) as e:
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
        """Search ARCHIVE_DIR for an archive matching the package name."""
        if not ARCHIVE_DIR.exists():
            return None
        for f in sorted(ARCHIVE_DIR.iterdir(), reverse=True):
            if f.name.startswith(f"{name}-") and f.name.endswith(".igos.tar.gz"):
                return f
        return None

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

        helper_env = {k: v for k, v in os.environ.items() if k in HELPER_ENV_ALLOWLIST}

        result = subprocess.run(
            [str(helper_path)],
            env=helper_env,
        )

        print(f"  {'-' * 50}")

        if result.returncode != 0:
            return False, f"Install helper failed (exit {result.returncode})"

        # H-007: read + validate the manifest the helper-lib wrote.
        manifest, err = _read_helper_manifest(name)
        if manifest is None:
            # Phase A grace period: register the package without file
            # tracking + warn the user that footprint tracking is
            # unavailable for this helper-installed package. Operator
            # decision (D-009 item 5) flips this to a hard failure
            # when all bundled helpers have migrated.
            print(
                f"  WARNING: install-helper for '{name}' did not write a "
                f"trackable manifest ({err}). pkm files/verify/remove "
                f"for this package will not see helper-installed files. "
                f"Helper authors: source /usr/share/igos/helpers/helper-lib.sh "
                f"and call igos_helper_init + record_file + commit (see "
                f"docs/architecture/helper-manifest-spec-v1.md).",
                file=sys.stderr,
            )
            self.db.add_installed(
                name=name,
                version="latest",
                install_method="helper",
                archive_path=str(helper_path),
            )
            self.db.log_operation(
                "install", name, new_version="latest", method="helper",
            )
            return True, (
                f"Installed {name} via helper ({helper_path.name}) "
                f"— footprint tracking unavailable (no manifest)"
            )

        # Manifest present + validated: wire files + depends through the
        # DB inside a single BEGIN/COMMIT so the install record stays
        # atomic with the file/depend rows.
        version = manifest.get("version_installed") or "latest"
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

        self.db.conn.execute("BEGIN")
        try:
            pkg_id = self.db.add_installed(
                name=name,
                version=version,
                install_method="helper",
                archive_path=str(helper_path),
                commit=False,
            )
            if rel_paths:
                self.db.add_files(pkg_id, rel_paths, commit=False)
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
            f"Installed {name} {version} via helper "
            f"({helper_path.name}) — {len(rel_paths)} files tracked"
        )
        if manifest_depends:
            summary += f", {len(manifest_depends)} dep(s) recorded"
        return True, summary

    # ------------------------------------------------------------------
    # Helpers — text manifest write-out
    # ------------------------------------------------------------------

    def _write_manifest(self, name, version, file_list, hashes=None, supersedes=None):
        """Write a text manifest alongside the SQLite entry.

        The text manifest is a transparency artifact — pkm SQLite's
        files.checksum is the authoritative source. The manifest mirrors
        the post-install ownership state including any SUPERSEDES header
        and per-file sha256 columns.
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
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)",
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"BUILD SYSTEM: InterGenOS pkm",
        ]
        if supersedes:
            lines.append(f"SUPERSEDES: {', '.join(supersedes)}")
        lines.extend([
            "DESCRIPTION:",
            f"{name}: (installed via pkm)",
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
