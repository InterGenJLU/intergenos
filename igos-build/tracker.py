# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Package tracking — manifest generation, archive creation, deployment, verification.

Extracted from builder.py to reduce the BuildExecutor class size.
These methods handle everything after a successful build:
  1. Generate Slackware-style text manifest with per-file SHA-256
  2. Create .igos.tar.gz archive
  3. Deploy staged files to the live filesystem
  4. Verify deployment against manifest
  5. Populate pkm SQLite database (durable hash record at build time)

Supersedes-aware (RFC v1, ratified 2026-05-01; overlap semantics corrected
2026-07-15): when a package declares `supersedes:`, the supersedee's paths
stay in BOTH snapshots — net-new is only what this build genuinely
created, and supersedee paths the build rewrote are added by measured
mtime detection (_detect_overwrites). The manifest claims only paths the
package actually wrote — paths the supersedee owned but the new package
didn't touch stay retired with the supersedee. (The original scheme
excluded supersedee paths from the pre-build snapshot, which made every
surviving untouched predecessor file appear net-new and let the successor
claim payload it never wrote.)
"""

import json
import os
import shutil
import stat
import subprocess
import tarfile
import time
import yaml
from pathlib import Path

# Reuse pkm's hash function to guarantee tracker/verifier parity
# (GP review nit, RFC ratification 2026-05-01).
from pkm.database import _sha256, PackageDB, _parse_manifest_line

from . import hookseal
from .parser import Package
from .content_hash import template_hash


class PackageTracker:
    """Mixin class providing package tracking methods.

    Requires self.logger, self.pkg_db, self.pkg_archives, self.pkg_staging
    to be set by the host class (BuildExecutor).
    """

    def _compute_template_hash(self, pkg: Package) -> str:
        """Compute the 16-char fingerprint of pkg's recipe + source content.

        Used by both manifest paths (regular DESTDIR and direct-install/
        filesystem-diff). The hash is embedded in the manifest as
        TEMPLATE_HASH: <hex> and read back by builder.py's skip-built check
        to detect when a package's recipe OR its first-party source has
        changed since last build. Delegates to the module-level
        ``template_hash`` so the host-side release auto-bump reuses the exact
        same content hashing.

        Returns empty string if pkg has no template_path (in which case
        the skip-built check defaults to skip — same as today).
        """
        return template_hash(pkg, getattr(self, "sources_dir", None))

    def _build_pkginfo(self, pkg: Package, total_size: int, filecount: int) -> str:
        """Render the canonical .PKGINFO key=value text for an archive.

        Shared by BOTH archive flows so they ship an identical, well-formed
        .PKGINFO: the DESTDIR-staging path (pkg_manifest writes it into
        staging_dir, which pkg_archive then tars) and the direct_install /
        filesystem-diff path (pkg_archive_from_files adds it to the tar). Before
        this was shared, the diff path emitted NO .PKGINFO — the dbus-pass2 /
        systemd-pass2 / gdk-pixbuf-pass2 / gobject-introspection PI-12 gap caught
        on the GBC004 from-scratch build (archives invisible to the repo index +
        tripping build-squashfs Step 4.7). Format: lowercase Arch-style keys per
        pkm._parse_pkginfo (pkm/repo.py)."""
        from datetime import datetime, timezone
        lines = [
            f"pkgname={pkg.name}",
            f"pkgver={pkg.version}",
            f"pkgrel={pkg.release}",
            f"pkgdesc={pkg.description}",
            f"license={pkg.license}",
            f"tier={pkg.tier}",
            f"builddate={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"size={total_size}",
            f"filecount={filecount}",
        ]
        # H-004: one depend=X per runtime dep (build-time deps are not shipped).
        for dep in (pkg.dependencies.runtime if pkg.dependencies else []):
            lines.append(f"depend={dep}")
        if getattr(pkg, "eula_helper", None):
            lines.append(f"eula_helper={pkg.eula_helper}")
        if getattr(pkg, "payload_license", None):
            lines.append(f"payload_license={pkg.payload_license}")
        # 3.0-F28: activation semantics. Emitted only when the package
        # declares it, so pre-F28 archives (and packages that activate live)
        # carry no key — pkm._parse_pkginfo treats absence as False.
        if getattr(pkg, "reboot_required", False):
            lines.append("reboot_required=true")
        return "\n".join(lines) + "\n"

    def _seal_lifecycle_hooks(self, pkg: Package, staging_dir: Path) -> None:
        """Write the recipe's lifecycle functions into staging as .scripts/.

        Shared by both archive paths so the DESTDIR-staged lane and the
        direct-install diff lane seal identically — a seam implemented on one
        lane only is how the direct_install contract came to be honored by one
        builder and silently ignored by the other.

        A SealError is a hard build failure, deliberately: it means the recipe
        declares a lifecycle function whose body could not be extracted
        trustworthily, and shipping a truncated hook that appears to succeed is
        strictly worse than not shipping one.
        """
        build_sh = None
        if pkg.template_path is not None:
            build_sh = pkg.template_path.parent / "build.sh"
        if build_sh is None or not build_sh.is_file():
            return
        sealed = hookseal.seal_into_staging(
            staging_dir, build_sh, pkg.name, pkg.version)
        if sealed:
            self.logger.info(
                f"Sealed lifecycle hook(s) into the archive: "
                f"{', '.join(sealed)}"
            )

    def pkg_manifest(self, pkg: Package, staging_dir: Path) -> bool:
        """Generate a Slackware-style manifest from staged files.

        Writes: /var/lib/igos/packages/<name>-<version>
        Also populates pkm SQLite (RFC §3d) with the package record + per-file
        SHA-256 hashes computed from staging, so the database is durable at
        build time rather than deferred to first install. Supersede semantics
        are wired via SUPERSEDES: header and atomic ownership transfer.
        """
        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"

        file_list = []  # path strings (with trailing / for dirs); manifest format
        file_paths = []  # path strings for files only; used for hashing
        # Ownership rows for pkm SQLite. Files AND directories, built by the
        # same rules as the archive-install walk (pkm/installer.py:912-922) so
        # a package registered by either path yields the same files-table rows:
        # a directory carries a trailing "/" (PackageDB.add_files derives
        # is_dir from it and rstrips it before the INSERT), and a SYMLINKED
        # directory gets no row at all — the archive path skips those, and a
        # symlink recorded as a directory misleads the remover's ancestor
        # sweep. Registering only files left every manifest-declared EMPTY
        # directory of a tracked-deployed package unowned in the DB, which the
        # squashfs ownership gate then reports as an unowned path.
        own_paths = []
        for root, dirs, files in os.walk(staging_dir):
            for d in sorted(dirs):
                rel = os.path.relpath(os.path.join(root, d), staging_dir)
                file_list.append(rel + "/")
                if not os.path.islink(os.path.join(root, d)):
                    own_paths.append(rel + "/")
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(root, f), staging_dir)
                file_list.append(rel)
                file_paths.append(rel)
                own_paths.append(rel)

        if not file_list:
            self.logger.error(f"Staging produced no files for {pkg.name}-{pkg.version}")
            return False

        # Calculate size
        total_size = sum(
            os.path.getsize(os.path.join(root, f))
            for root, _, files in os.walk(staging_dir)
            for f in files
            if os.path.isfile(os.path.join(root, f))
        )
        human_size = f"{total_size / 1024 / 1024:.1f}M" if total_size > 1024*1024 else f"{total_size / 1024:.0f}K"

        from datetime import datetime, timezone
        template_hash = self._compute_template_hash(pkg)

        # Per-file SHA-256 from staging contents (RFC §3c). Same _sha256 as
        # pkm/verifier — imported at module top for byte-exact parity.
        file_hashes = self._compute_file_hashes(file_paths, staging_root=staging_dir)

        supersedes_header = ""
        if pkg.supersedes:
            supersedes_header = "SUPERSEDES: " + ", ".join(pkg.supersedes) + "\n"

        manifest_content = (
            f"PACKAGE NAME: {pkg.name}-{pkg.version}\n"
            f"PACKAGE VERSION: {pkg.version}\n"
            # The release belongs IN the manifest, not only in the .PKGINFO this
            # builder already stamps (pkg_pkginfo -> `pkgrel={pkg.release}`).
            # `pkm import` re-registers a row from these bytes, so a header-less
            # manifest left the import with nothing to carry and the row fell to
            # the schema default — the corpus-wide release=1 reset. Emitting it
            # here makes the manifest self-describing and agrees by construction
            # with the archive pkgrel that squashfs Step 2.7 cross-checks.
            f"PACKAGE RELEASE: {pkg.release}\n"
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)\n"
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"BUILD SYSTEM: InterGenOS igos-build\n"
            f"TEMPLATE_HASH: {template_hash}\n"
            f"{supersedes_header}"
            f"DESCRIPTION:\n"
            f"{pkg.name}: {pkg.description}\n"
            f"\n"
            f"FILE LIST:\n"
        )
        # Render each entry; files get sha256 annotation, dirs do not.
        rendered_lines = []
        for entry in file_list:
            if entry.endswith("/"):
                rendered_lines.append(entry)
            else:
                h = file_hashes.get(entry)
                rendered_lines.append(f"{entry} sha256:{h}" if h else entry)
        manifest_content += "\n".join(rendered_lines) + "\n"

        manifest_path.write_text(manifest_content)
        self.logger.info(
            f"Manifest: {manifest_path} ({len(file_list)} entries, "
            f"{len(file_hashes)} hashed)"
        )

        # H-008: write canonical .PKGINFO key=value alongside the staged tree.
        # pkg_archive (next gate) packs staging_dir/. into the archive, so
        # .PKGINFO travels with the artifact. pkm._parse_pkginfo at
        # pkm/repo.py:575 expects lowercase Arch-style keys; format
        # ratified 2026-05-19 cross-coordinator (Path A). Build-time fields
        # (tier/license/description) come from pkg.* dataclass; size +
        # builddate computed above.
        (staging_dir / ".PKGINFO").write_text(
            self._build_pkginfo(pkg, total_size, len(file_paths))
        )

        # Seal the recipe's lifecycle functions as .scripts/<event>.sh so they
        # travel INSIDE the signed archive and pkm fires them at install time.
        # Without this the recipe's post_install() only ever ran in the build
        # chroot and a target installed from archives never got it at all.
        # Textual extraction, never execution — post_install runs AFTER this
        # point in the builder, so capturing what it DID would capture build-
        # chroot side effects belonging to the wrong filesystem.
        self._seal_lifecycle_hooks(pkg, staging_dir)

        # Stash for pkg_register_pkm_db, which the builder calls at gate-3
        # (after pkg_deploy succeeds). Writing the DB here would violate
        # RFC §4a — a deploy failure would leave pkm with a record for an
        # undeployed package.
        # Two lists, deliberately: `_pending_pkm_paths` stays FILES ONLY
        # because its other consumers are file-shaped (the supersede overlap
        # scope, which the archive path also restricts to files, and the
        # verify_paths sidecar derivation via _last_registered_paths);
        # `_pending_pkm_own_paths` is what the files table gets.
        self._pending_pkm_paths = file_paths
        self._pending_pkm_own_paths = own_paths
        self._pending_pkm_hashes = file_hashes
        # Provenance for the row this manifest describes: pkm keys
        # re-registration on the manifest bytes' sha256, and treats a NULL
        # stored value as unproven, so a row written without it is
        # re-registered by the first corpus-wide `pkm import` regardless of
        # whether anything changed. Stamping it here makes an unchanged
        # package a true no-op on import.
        self._pending_pkm_manifest_sha256 = _sha256(str(manifest_path))

        return True

    def _read_declared_verify_paths(self, pkg: Package) -> list:
        """Resolve a package's declared verify_paths exactly as gate 4.5
        (scripts/pre-squashfs-audit.py) does: the hand-curated ``verify_paths:``
        in package.yml, else the auto-derived JSON sidecar (``SIDECAR_NAME``
        from igos-build/verify_paths_derive.py) beside it. Returns [] if none
        declared (same warning-not-fatal stance as gate 4.5's missing-field)."""
        tpl = getattr(pkg, "template_path", None)
        if not tpl:
            return []
        tpl = Path(tpl)
        try:
            with open(tpl) as f:
                data = yaml.safe_load(f) or {}
            paths = data.get("verify_paths")
            if paths:
                return paths
        except (OSError, yaml.YAMLError):
            pass
        try:
            from .verify_paths_derive import SIDECAR_NAME
            sidecar = tpl.parent / SIDECAR_NAME
            if sidecar.exists():
                return json.loads(sidecar.read_text()).get("verify_paths") or []
        except (OSError, ValueError, ImportError):
            pass
        return []

    def _enforce_mirror_archive_verify_paths(self, pkg: Package,
                                             archive_path: Path) -> bool:
        """Archive-level verify_paths gate for mirror-only (iso_include:false)
        packages — the single archive-seal chokepoint.

        Gate 4.5 (scripts/pre-squashfs-audit.py) checks declared verify_paths
        against the CHROOT and EXEMPTS iso_include:false packages (they are
        never installed into the ISO chroot), so a mirror-only package's
        verify_paths were declarations that nothing enforced at build time —
        the exact gap that let nvidia's dead symlink parse seal an archive
        missing /usr/lib/gbm/nvidia-drm_gbm.so (PI-Z20) with no gate objecting.

        This runs at the seal point shared by BOTH archive flows (the DESTDIR
        pkg_archive and the direct_install/filesystem-diff pkg_archive_from_files
        both call it), so a --tracked single-package build and a full-tier build
        are covered identically. For an iso_include:false package it requires
        every declared verify_path to exist inside the just-sealed .igos.tar.gz
        as a file, dir, or a symlink whose link-target CHAIN resolves to a real
        member of this archive (review finding H9: the earlier lexists-style
        name-membership tolerance let a DANGLING symlink satisfy a load-bearing
        verify_path — the exact PI-Z20 shape this gate exists to catch).
        Fail-closed, naming every miss.

        ISO-included packages get the same gate with ONE added tolerance
        (extended 2026-07-19, empty-archive class): gate 4.5 checks them
        against the CHROOT, but chroot-presence does not prove
        archive-presence — an install step that writes the build environment
        instead of DESTDIR (pip resolving the package as already-satisfied)
        seals a license-only archive that 4.5 cannot see (python-requests +
        python-certifi shipped exactly that way). For iso_include:true a
        declared path may also be satisfied by a member whose name extends it
        with a dot suffix (libfoo.so covered by libfoo.so.1.2.3 — the
        ldconfig-generated SONAME symlink is made in the chroot and never
        shipped in the tar, which was the reason for the old blanket
        exemption; the prefix rule keeps that case passing without exempting
        the whole class)."""
        iso_included = getattr(pkg, "iso_include", None) is not False
        paths = self._read_declared_verify_paths(pkg)
        if not paths:
            return True

        members = {}
        try:
            with tarfile.open(archive_path, "r:gz") as tf:
                for m in tf.getmembers():
                    n = m.name[2:] if m.name.startswith("./") else m.name
                    n = n.lstrip("/")
                    if n:
                        members[n] = m
        except (tarfile.TarError, OSError) as e:
            self.logger.error(
                f"{pkg.name}-{pkg.version}: cannot read sealed archive "
                f"{archive_path} for verify_paths enforcement: {e}")
            return False

        def _has_dir_children(n: str) -> bool:
            prefix = n + "/"
            return any(m.startswith(prefix) for m in members)

        def _chain_verdict(n: str) -> str:
            """Follow a member's symlink chain inside the archive.

            Returns "" when the chain lands on a real (non-symlink) member or
            an implicit directory; otherwise a short reason naming the failure
            (dangling target / loop). Bounded hops guard link loops."""
            seen = set()
            while True:
                info = members.get(n)
                if info is None:
                    if _has_dir_children(n):
                        return ""  # implicit directory — children shipped
                    return f"dangling (target '/{n}' not in archive)"
                if not info.issym():
                    return ""  # regular file / dir / hardlink member
                if n in seen or len(seen) > 40:
                    return "symlink loop"
                seen.add(n)
                target = info.linkname
                if target.startswith("/"):
                    n = os.path.normpath(target).lstrip("/")
                else:
                    n = os.path.normpath(
                        os.path.join(os.path.dirname(n), target)).lstrip("/")
                if not n or n.startswith(".."):
                    return f"dangling (target escapes archive root: {target})"

        missing = []
        for p in paths:
            if not isinstance(p, str) or not p.startswith("/"):
                missing.append(f"{p} <invalid-shape>")
                continue
            n = p.lstrip("/")
            # Exact member: real files/dirs pass; a symlink member must have
            # a link-target chain that resolves INSIDE this archive (a
            # verify_path proves this package's own payload landed — a
            # dangling link proves only that a name was created).
            if n in members:
                reason = _chain_verdict(n)
                if reason:
                    missing.append(f"{p} <symlink {reason}>")
                continue
            # Declared directory whose children shipped (implicit dir).
            if _has_dir_children(n):
                continue
            # ISO-included only: SONAME-style coverage — a member extending
            # the declared name with a dot suffix proves the payload landed
            # (the bare .so symlink is chroot-generated, never in the tar).
            if iso_included and any(
                    m == n or m.startswith(n + ".") for m in members):
                continue
            missing.append(p)

        if missing:
            flavor = ("iso-included" if iso_included
                      else "mirror-only (iso_include:false)")
            self.logger.error(
                f"{pkg.name}-{pkg.version}: {flavor} "
                f"archive is missing {len(missing)} declared verify_path(s) — "
                f"the archive-seal gate proves the payload landed in the tar, "
                f"not merely in the chroot:")
            for p in missing:
                self.logger.error(f"    MISSING from archive: {p}")
            return False
        return True

    def pkg_archive(self, pkg: Package, staging_dir: Path) -> bool:
        """Create a .igos.tar.gz archive from staged files.

        Creates: /var/lib/igos/archives/<name>-<version>.igos.tar.gz
        """
        # Runtime-dir gate (2026-07-17): an archive must never carry var/run
        # or var/lock members (on installed systems both are symlinks into
        # /run, shipped by base-files; a dir member extracted before the
        # symlink lands materializes a REAL dir that systemd-tmpfiles cannot
        # replace -> split-brain runtime dirs), nor any usr/var tree (state
        # under /usr = localstatedir misconfiguration, default ${prefix}/var).
        # The bash-tier twin of this gate lives in pkg-functions.sh
        # pkg_archive and additionally allows base-files' symlink members;
        # no Python-tier package legitimately ships either path in any form.
        for bad, hint in (
            ("var/run", "use a tmpfiles.d entry; strip the dir in do_install"),
            ("var/lock", "use a tmpfiles.d entry; strip the dir in do_install"),
            ("usr/var", "configure with --localstatedir=/var"),
        ):
            if (staging_dir / bad).exists() or (staging_dir / bad).is_symlink():
                self.logger.error(
                    f"runtime-dir gate: {pkg.name}-{pkg.version} stages "
                    f"{bad} — refusing to archive ({hint})"
                )
                return False
        archive_path = self.pkg_archives / f"{pkg.name}-{pkg.version}.igos.tar.gz"

        try:
            from . import _trace
            result = _trace.traced_run(
                ["tar", "-C", str(staging_dir), "-czf", str(archive_path), "."],
                phase="pkg_archive", intent="create igos.tar.gz archive",
                pkg=pkg.name,
            )
        except ImportError:
            result = subprocess.run(  # trace-coverage: allow — _trace shim unavailable fallback
                ["tar", "-C", str(staging_dir), "-czf", str(archive_path), "."],
                capture_output=True, text=True,
            )
        if result.returncode != 0:
            self.logger.error(f"Archive creation failed: {result.stderr}")
            return False

        archive_size = archive_path.stat().st_size
        human = f"{archive_size / 1024 / 1024:.1f}M" if archive_size > 1024*1024 else f"{archive_size / 1024:.0f}K"
        self.logger.info(f"Archive: {archive_path} ({human})")
        if not self._enforce_mirror_archive_verify_paths(pkg, archive_path):
            return False
        return True

    def _validate_staging_paths(self, pkg: Package, staging_dir: Path) -> bool:
        """B4: validate staging-dir paths before deploy to /.

        The check distinguishes:
          - Real file/dir whose .resolve() escapes staging_root: actual escape
            attempt (REJECT).
          - Symlink with absolute target inside THIS package's manifest:
            legitimate intra-package compat symlink (e.g., xkeyboard-config's
            ``/usr/share/X11/xkb -> /usr/share/xkeyboard-config-2``). ALLOW.
          - Symlink with absolute target NOT in this package's manifest:
            cross-package or unknown owner. WARN but allow under current
            policy (cross-package validation against the live package db is
            a future enhancement; for now we trust the build to have produced
            valid symlinks).
          - Symlink with relative target that resolves within staging: ALLOW.
          - Symlink with relative target that escapes staging but resolves
            (post-deploy) to a path in this package's manifest: ALLOW
            (intra-package via complex relative path).
          - Symlink with relative target that escapes staging AND post-deploy
            target is not in this package's manifest: REJECT.

        Returns True if all paths pass; False (with logged error) on first
        failure.
        """
        staging_root = staging_dir.resolve()

        # Pass 1: enumerate all paths this package will install (files, dirs,
        # symlinks), expressed as absolute paths AS THEY WILL APPEAR after
        # deploy to /. os.walk's followlinks=False (default) is required so we
        # don't descend into symlinked dirs.
        package_paths: set[str] = set()
        for root, dirs, files in os.walk(str(staging_dir), followlinks=False):
            for name in files + dirs:
                full = Path(root) / name
                try:
                    rel = full.relative_to(staging_dir)
                except ValueError:
                    continue
                package_paths.add('/' + str(rel))

        # Pass 2: validate each entry against the path set.
        for root, dirs, files in os.walk(str(staging_dir), followlinks=False):
            for name in files + dirs:
                full = Path(root) / name
                if full.is_symlink():
                    target = os.readlink(full)
                    if os.path.isabs(target):
                        # Absolute symlink — allow if target is owned by this
                        # package's manifest. Otherwise warn-but-allow under
                        # current cross-package policy.
                        if target in package_paths:
                            continue
                        self.logger.warning(
                            f"{pkg.name}-{pkg.version}: absolute symlink "
                            f"{full.relative_to(staging_dir)} -> {target} "
                            f"target not in this package's manifest "
                            f"(cross-package validation deferred)"
                        )
                        continue
                    # Relative symlink — resolve and check whether it stays
                    # within staging or, if not, whether the post-deploy
                    # target is in this package's manifest.
                    # (is_relative_to, not str.startswith: a prefix check
                    # blesses sibling-collision escapes — a target resolving
                    # to '<staging>-evil/…' startswith '<staging>'.)
                    resolved_abs = (full.parent / target).resolve()
                    if resolved_abs.is_relative_to(staging_root):
                        continue  # stays within staging — safe
                    # Escapes staging via relative path. Compute what it would
                    # resolve to AFTER deploy to / and check intra-package.
                    deploy_target = os.path.normpath(
                        '/' + str(full.parent.relative_to(staging_dir))
                        + '/' + target
                    )
                    if deploy_target in package_paths:
                        continue  # intra-package via complex relative path
                    self.logger.error(
                        f"SECURITY: symlink {full} -> {target} (would resolve "
                        f"to {deploy_target} after deploy) escapes staging "
                        f"and target is not in this package's manifest — "
                        f"rejecting package deployment"
                    )
                    return False
                # Non-symlink (regular file or dir) — original escape check.
                # With followlinks=False this is belt-and-suspenders since
                # os.walk won't descend into symlinked dirs.
                # (is_relative_to, not str.startswith — prefix-collision.)
                resolved = full.resolve()
                if not resolved.is_relative_to(staging_root):
                    self.logger.error(
                        f"SECURITY: staging path '{resolved}' escapes "
                        f"staging root '{staging_root}' — rejecting "
                        f"package deployment"
                    )
                    return False

        return True

    def pkg_deploy(self, pkg: Package, staging_dir: Path, root: str = "/") -> bool:
        """Deploy staged files to the live filesystem using tar.

        Safety:
          - Pre-checks for top-level entries that would collide with
            root-level symlinks (lib -> usr/lib, bin -> usr/bin, etc.).
          - Pre-checks that the live filesystem has enough free space to
            accommodate the staged content + 10% headroom; refuses to start
            rather than leaving a partial extraction on disk-full.
          - On extract failure, logs the archive path so the user has a
            durable recovery artifact to re-deploy from or inspect.
          - Deploys the PAYLOAD only: ./.PKGINFO is archive metadata
            (pkg_manifest writes it into staging AFTER manifest enumeration
            so pkg_archive packs it), so it is excluded here — deploying it
            put an untracked, unverified /.PKGINFO on the live root that
            every subsequent package silently overwrote.

        root is the extract target ("/" in production; parameterized for
        tests, same convention as pkg_verify).
        """
        dangerous = []
        for entry in ("lib", "lib64", "bin", "sbin"):
            staged = staging_dir / entry
            root_path = Path(root) / entry
            if staged.is_dir() and not staged.is_symlink() and root_path.is_symlink():
                dangerous.append(entry)

        if dangerous:
            self.logger.error(
                f"DANGEROUS: {pkg.name}-{pkg.version} staging contains top-level "
                f"dirs that would collide with root symlinks: {' '.join(dangerous)}\n"
                f"  Fix the package build.sh to install to usr/ paths instead"
            )
            return False

        # Pre-check free space. A mid-deploy ENOSPC crash leaves the live
        # filesystem with partial files — better to refuse than to partially
        # deploy.
        staging_bytes = 0
        # NB: the walk variable must not be named `root` — it would shadow
        # the deploy-target parameter and redirect the extract below.
        for walk_dir, _dirs, files in os.walk(staging_dir):
            for f in files:
                try:
                    staging_bytes += (Path(walk_dir) / f).stat().st_size
                except OSError:
                    pass
        required_bytes = int(staging_bytes * 1.1)
        free_bytes = shutil.disk_usage(root).free
        if free_bytes < required_bytes:
            self.logger.error(
                f"Insufficient free space for {pkg.name}-{pkg.version} deploy:\n"
                f"  required (+10% headroom): {required_bytes:,} bytes\n"
                f"  free on /: {free_bytes:,} bytes"
            )
            return False

        archive_path = self.pkg_archives / f"{pkg.name}-{pkg.version}.igos.tar.gz"

        # B4: validate staging paths before archiving for deploy to /.
        if not self._validate_staging_paths(pkg, staging_dir):
            return False

        # tar -cf - | tar -xf - pipeline halves. capture_output captures the
        # archive bytes verbatim into result.stdout (which is then piped into
        # the second tar via the input= kwarg). The traced_run wrapper is
        # text=True by default, which would corrupt the binary tar stream;
        # the two halves emit pkg_deploy_start / pkg_deploy_end events at
        # the pkg_deploy boundary (see pkg-functions.sh sibling — same
        # contract) rather than per-half subprocess events.
        result = subprocess.run(  # trace-coverage: allow — binary-stream pipeline half (tar -cf -); events emitted at pkg_deploy boundary
            ["tar", "-C", str(staging_dir), "--exclude=./.PKGINFO",
             "--exclude=./.scripts", "-cf", "-", "."],
            capture_output=True,
        )
        if result.returncode != 0:
            self.logger.error(
                f"Deploy tar-create failed: {result.stderr.decode()}\n"
                f"  Staging dir: {staging_dir}\n"
                f"  Archive for manual recovery: {archive_path}"
            )
            return False

        result2 = subprocess.run(  # trace-coverage: allow — binary-stream pipeline half (tar -xf -); events emitted at pkg_deploy boundary
            ["tar", "-C", str(root), "-xf", "-",
             "--no-overwrite-dir", "--keep-directory-symlink"],
            input=result.stdout,
            capture_output=True,
        )
        if result2.returncode != 0:
            self.logger.error(
                f"Deploy tar-extract failed: {result2.stderr.decode()}\n"
                f"  Partial files may exist on live filesystem.\n"
                f"  Archive for manual recovery / re-deploy: {archive_path}\n"
                f"  To retry the deploy manually:\n"
                f"    sudo tar -C / -xf {archive_path} --no-overwrite-dir --keep-directory-symlink"
            )
            return False

        # Setuid/setgid/sticky safety-net — mirrors pkm/installer.py:169-184
        # and scripts/pkg-functions.sh's pkg_deploy. The tar pipeline above
        # SHOULD preserve these bits when running as root. The May 12 2026
        # chroot deploy dropped setuid on every binary in polkit/util-linux/
        # shadow/sudo (pkexec, su, sudo, mount, etc.), discovered when Forge
        # GUI elevation failed in the May 15 smoke test. Root cause not
        # pinpointed to a specific stripping operation. This loop re-applies
        # any setuid/setgid/sticky bit present in staging to the deployed
        # file. Idempotent — no-op when the tar pipeline preserved correctly.
        try:
            for staged_path in staging_dir.rglob("*"):
                if not staged_path.is_file() or staged_path.is_symlink():
                    continue
                staged_mode = staged_path.stat().st_mode
                if staged_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                    rel = staged_path.relative_to(staging_dir)
                    deployed_path = Path(root) / rel
                    if deployed_path.exists() and not deployed_path.is_symlink():
                        deployed_mode = deployed_path.stat().st_mode
                        if (deployed_mode & 0o7777) != (staged_mode & 0o7777):
                            deployed_path.chmod(staged_mode & 0o7777)
                            self.logger.info(
                                f"  setuid-restore: {deployed_path} -> "
                                f"{oct(staged_mode & 0o7777)}"
                            )
        except (OSError, ValueError) as e:
            # A failed restore pass means security-relevant mode bits are in
            # an UNKNOWN state on the live filesystem (the May 12 2026 class
            # above stripped setuid from pkexec/su/sudo/mount). Registering
            # the deploy anyway would let a broken privileged payload become
            # skippable — fail the deploy instead; staging is left in place
            # for inspection.
            self.logger.error(
                f"  setuid/setgid restore pass FAILED for "
                f"{pkg.name}-{pkg.version}: {e} — refusing to record the "
                f"deployment with unverified security-mode bits"
            )
            return False

        self.logger.info(f"Deployed {pkg.name}-{pkg.version} to live filesystem")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return True

    def pkg_verify(self, pkg: Package, root="/") -> bool:
        """Verify every file in the manifest exists on the live filesystem.

        Args:
            pkg: Package object (name + version used to locate manifest).
            root: Filesystem root for path reconstruction (default "/").
                  Pass a non-"/" root when verifying a chroot from outside
                  it (e.g. build-host verifying a build-target chroot).
                  Manifest paths are stored POSIX-relative; passing a
                  leading-slash path through Path(root) / path will silently
                  drop the root (pathlib absolute-right-operand rule).
        """
        root_path = Path(root)
        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"
        if not manifest_path.exists():
            self.logger.error(f"Manifest not found: {manifest_path}")
            return False

        content = manifest_path.read_text()
        in_file_list = False
        missing = []
        mismatched = []
        hashed = 0

        for line in content.splitlines():
            if line == "FILE LIST:":
                in_file_list = True
                continue
            if in_file_list and line.strip():
                if line.endswith("/"):
                    continue
                # _parse_manifest_line handles paths with whitespace correctly
                # (anchors hash suffix at end-of-line via regex). Linux-firmware
                # surfaced this — files like "brcmfmac43455-sdio.Raspberry Pi
                # Foundation-Raspberry Pi 4 Model B.txt.xz" have spaces.
                path, h = _parse_manifest_line(line)
                filepath = str(root_path / path)
                if not os.path.lexists(filepath):
                    missing.append(filepath)
                elif h and os.path.isfile(filepath) and not os.path.islink(filepath):
                    # Verify deployed BYTES against the manifest hash — the
                    # manifest stores per-file SHA-256 computed at staging
                    # time, and presence-only verification let a corrupted
                    # or partially-extracted deploy register as verified.
                    # Symlinks and non-regular files carry no hash and are
                    # covered by the lexists check above.
                    if _sha256(filepath) != h:
                        mismatched.append(filepath)
                    else:
                        hashed += 1

        if missing or mismatched:
            self.logger.error(
                f"manifest verification failed for {pkg.name}-{pkg.version}:\n"
                + "\n".join(f"  missing: {f}" for f in missing[:20])
                + ("\n" if missing and mismatched else "")
                + "\n".join(f"  hash mismatch: {f}" for f in mismatched[:20])
            )
            overflow = max(0, len(missing) - 20) + max(0, len(mismatched) - 20)
            if overflow:
                self.logger.error(f"  ... and {overflow} more")
            return False

        self.logger.info(
            f"Manifest verified: all files present, {hashed} content "
            f"hash(es) match on live filesystem"
        )
        return True

    # ------------------------------------------------------------------
    # Direct install tracking (filesystem diff)
    # ------------------------------------------------------------------

    # review finding H3: the pre/post-build walk covers the whole tree so a direct_install
    # write ANYWHERE is observed (the former 6-root walk — /usr /etc /opt
    # /var/lib /lib /boot — missed writes outside those roots). These trees are
    # the explicit, LOGGED prune list: kernel-virtual and volatile filesystems,
    # plus /sources and /home. The builder unions its own work/sources/staging
    # (build scratch) dirs on top. None of these hold package payload.
    SNAPSHOT_PRUNE_DEFAULT = frozenset({
        "/proc", "/sys", "/dev", "/run", "/tmp", "/sources", "/home",
        # /root and the in-chroot recipe-copy tree are build scratch: root's
        # tool caches (g-ir-scanner writes content-addressed entries under
        # /root/.cache) and the builder's own __pycache__ under the repo copy
        # were swept into direct_install manifests as claimed payload — then
        # a later rebuild replaced the cache entries / the image build
        # stripped the repo copy, and pkm verify reported phantom-missing
        # files on pristine media (ge9b-07 pre-install eval, 2026-07-20:
        # 13 rows across gdk-pixbuf-pass2 + gobject-introspection). No
        # package ships payload under either tree.
        "/root", "/mnt/intergenos",
    })

    def fs_snapshot(self, dirs: list[str] | None = None,
                    exclude_paths: set[str] | None = None,
                    prune: set[str] | None = None,
                    ) -> dict[str, tuple[int, int, int]]:
        """Snapshot files + symlinked dirs as path -> (size, mtime_ns, ctime_ns).

        review finding H3: an EXPANDED walk of the whole filesystem (default root "/")
        with an explicit, logged prune list, returning per-path metadata
        instead of a bare path set. ctime_ns is the load-bearing field:
        modification detection (see diff_snapshots) keys on ctime, which the
        kernel bumps on every content/metadata write and which — unlike mtime —
        cannot be set from userland (cp -a / tar -p / touch -r preserve mtime
        but not ctime). One os.lstat per walked entry (cost discipline;
        direct_install builds only).

        Args:
            dirs: directories to walk. Defaults to ["/"] — the whole tree.
            exclude_paths: absolute paths to drop from the snapshot (generic
                filter; no longer used for supersedes — overlap is measured by
                ctime in diff_snapshots).
            prune: absolute directory paths NOT to descend into. Defaults to
                SNAPSHOT_PRUNE_DEFAULT; the builder passes its work/sources/
                staging dirs unioned on top so build scratch is never counted.

        Returns:
            dict mapping absolute path -> (st_size, st_mtime_ns, st_ctime_ns)
            for every regular file and symlinked directory under the walk.
        """
        if dirs is None:
            dirs = ["/"]
        prune_set = {
            os.path.normpath(p)
            for p in (self.SNAPSHOT_PRUNE_DEFAULT if prune is None else prune)
        }
        self.logger.info(f"fs_snapshot: walk={dirs} prune={sorted(prune_set)}")
        snapshot: dict[str, tuple[int, int, int]] = {}
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for root, dirnames, files in os.walk(d, topdown=True,
                                                 followlinks=False):
                # Never descend into a pruned directory. Rewriting dirnames in
                # place is the documented os.walk prune idiom (topdown only).
                dirnames[:] = [
                    dn for dn in dirnames
                    if os.path.normpath(os.path.join(root, dn)) not in prune_set
                ]
                for f in files:
                    path = os.path.join(root, f)
                    try:
                        st = os.lstat(path)
                    except OSError:
                        continue
                    snapshot[path] = (st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                for dn in dirnames:
                    path = os.path.join(root, dn)
                    if os.path.islink(path):
                        try:
                            st = os.lstat(path)
                        except OSError:
                            continue
                        snapshot[path] = (st.st_size, st.st_mtime_ns,
                                          st.st_ctime_ns)
        if exclude_paths:
            for p in exclude_paths:
                snapshot.pop(p, None)
        return snapshot

    @staticmethod
    def diff_snapshots(
        before: dict[str, tuple[int, int, int]],
        after: dict[str, tuple[int, int, int]],
    ) -> tuple[set[str], set[str]]:
        """Split two fs_snapshots into (created, modified) path sets.

        created  = paths present in `after` but not `before`.
        modified = paths present in BOTH whose ctime_ns changed. ctime is
            bumped by the kernel on any content or metadata write and cannot
            be forged from userland, so this catches content overwrites of
            pre-existing files that a path-set diff cancels and that mtime
            forgery (cp -a / tar -p) would hide. It generalizes the former
            supersedee-only mtime overwrite check to EVERY pre-existing file.
        """
        before_keys = set(before)
        after_keys = set(after)
        created = after_keys - before_keys
        modified = {
            p for p in (before_keys & after_keys)
            if before[p][2] != after[p][2]
        }
        return created, modified

    def diff_new_files(
        self, pkg: Package,
        before: dict[str, tuple[int, int, int]],
        after: dict[str, tuple[int, int, int]],
        build_start_time: float | None = None,
    ) -> list[str]:
        """Sorted paths this build created or modified (single source of truth).

        Shared by the builder (archive / elf-audit / ownership target) and
        pkg_manifest_from_diff (manifest + hashes) so the archived payload and
        the manifest claim exactly the same set — no divergence.

        = created ∪ ctime-modified ∪ (retained) supersedee mtime overwrites.
        The supersedee mtime cross-check is subsumed by the ctime `modified`
        set for real writes; it is kept as belt-and-suspenders so a declared
        supersedee rewrite is still claimed even absent a ctime delta.
        """
        created, modified = self.diff_snapshots(before, after)
        new_paths = created | modified
        if getattr(pkg, "supersedes", None) and build_start_time is not None:
            new_paths |= self._detect_overwrites(pkg, build_start_time)
        return sorted(new_paths)

    def _get_supersedee_paths(self, pkg: Package) -> set[str]:
        """Read tracked paths from each supersedee's text manifest.

        Drives measured overwrite detection (_detect_overwrites): the paths
        a supersedee owns are checked by mtime for genuine rewrites by this
        build. Returns absolute paths from every package this one declares
        as superseded. Missing manifests are silently skipped — corresponds
        to the missing-supersedee allow-with-warn case (RFC §11).
        """
        if not pkg.supersedes:
            return set()
        paths: set[str] = set()
        for predecessor_name in pkg.supersedes:
            for manifest_file in sorted(self.pkg_db.iterdir()) if self.pkg_db.exists() else []:
                if not manifest_file.is_file():
                    continue
                if not manifest_file.name.startswith(f"{predecessor_name}-"):
                    continue
                paths.update(self._parse_manifest_paths(manifest_file))
        return paths

    @staticmethod
    def _parse_manifest_paths(manifest_file: Path) -> set[str]:
        """Extract absolute file paths from a text manifest's FILE LIST.

        Tolerates both the original format and the supersedes-extended
        format (entries may carry an "<path> sha256:<hex>" annotation).
        Directory entries (trailing slash) are excluded.
        """
        paths: set[str] = set()
        in_files = False
        try:
            content = manifest_file.read_text()
        except (OSError, UnicodeDecodeError):
            return paths
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "FILE LIST:":
                in_files = True
                continue
            if not in_files or not stripped:
                continue
            # _parse_manifest_line handles paths with whitespace correctly.
            entry, _h = _parse_manifest_line(line)
            if entry.endswith("/"):
                continue
            paths.add("/" + entry.lstrip("/"))
        return paths

    def _detect_overwrites(self, pkg: Package, build_start_time: float) -> set[str]:
        """Identify supersedee paths the build modified during this run.

        Per DS critique on RFC §3b: pass2's manifest must include only paths
        pass2 actually wrote, never paths the supersedee owned that pass2
        didn't touch. mtime change vs build_start_time is the signal — files
        modules_install / cp / etc. all bump mtime past the build start.

        Returns: subset of supersedee_paths whose mtime is at or after the
        build started (i.e. were rewritten during this build).
        """
        overwrites: set[str] = set()
        for path in self._get_supersedee_paths(pkg):
            try:
                # Never-payload prune trees apply here too. A supersedee
                # manifest sealed under PRE-prune builder code can still
                # carry build-scratch rows (/root scanner cache, the
                # in-chroot repo copy), and a rebuild that regenerates the
                # same content-addressed cache entry gives it a fresh mtime
                # — mtime-flagging would re-import the phantom into this
                # build's payload even though the fs-diff prune excludes the
                # tree (F23 second limb, caught live 2026-07-21:
                # gdk-pixbuf-pass2 recut shipped root/.cache/g-ir-scanner/*
                # via exactly this path; gobject-introspection escaped only
                # because its rewritten entry hashed to a different
                # filename). The prune set is the single definition of
                # "never payload" — every collection path honors it.
                if any(path == root or path.startswith(root + "/")
                       for root in PackageTracker.SNAPSHOT_PRUNE_DEFAULT):
                    continue
                if not os.path.lexists(path):
                    continue
                # Directories are never "overwritten" payload: a dir's mtime
                # bumps whenever ANY entry inside it changes, so a shared dir
                # row from a supersedee manifest (usr/bin, etc/dbus-1, run —
                # the bash-era manifests carry them) always looks freshly
                # rewritten by a pass2 that installed into it. Feeding such a
                # dir into the archive file list made tar sweep its entire
                # subtree (2026-07-20: dbus-pass2 sealed at 2.5 GB carrying
                # all of /usr/bin + /run). Only files and symlinks can be
                # meaningfully rewritten by a build.
                if os.path.isdir(path) and not os.path.islink(path):
                    continue
                if os.path.getmtime(path) >= build_start_time:
                    overwrites.add(path)
            except OSError:
                continue
        return overwrites

    @staticmethod
    def _compute_file_hashes(paths: list[str], staging_root: Path | None = None) -> dict[str, str]:
        """Compute SHA-256 for each file path. Returns {relpath: hex_digest}.

        If staging_root is given, paths are interpreted relative to it
        (DESTDIR-staged install). Otherwise paths are absolute (direct
        install on the live filesystem).
        """
        hashes: dict[str, str] = {}
        for path in paths:
            try:
                if staging_root is not None:
                    abs_path = staging_root / path
                else:
                    abs_path = Path(path)
                if abs_path.is_file() and not abs_path.is_symlink():
                    rel = path if staging_root is None else str(path)
                    hashes[rel] = _sha256(str(abs_path))
            except (OSError, PermissionError):
                continue
        return hashes

    def pkg_manifest_from_diff(self, pkg: Package,
                                before: dict[str, tuple[int, int, int]],
                                after: dict[str, tuple[int, int, int]],
                                build_start_time: float | None = None) -> bool:
        """Generate manifest from filesystem diff (for direct_install packages).

        review finding H3 (generalizes the supersedes overlap correction of 2026-07-15):
          - created = paths present in `after` but not `before`.
          - modified = pre-existing paths whose ctime_ns changed between the
            snapshots — content overwrites the old net-new-only path-set diff
            cancelled, caught by ctime (not defeatable by mtime forgery). This
            generalizes the former supersedee-only mtime check to every
            pre-existing file. Untouched predecessors keep their ctime and are
            NOT claimed; ones this build rewrote transfer to the successor.
          - The retained supersedee mtime overwrite check is a subsumed
            belt-and-suspenders cross-check (see diff_new_files).

        Per-file SHA-256 is computed from the live filesystem for the claimed
        paths only (hashing restricted to manifest-claimed paths) and written
        to both the text manifest and pkm SQLite (RFC §3c-§3d).
        """
        new_files = self.diff_new_files(pkg, before, after, build_start_time)

        if not new_files:
            self.logger.error(f"No new files detected for {pkg.name}-{pkg.version}")
            return False

        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"

        file_list = []
        dirs_seen = set()
        for filepath in new_files:
            parts = Path(filepath).relative_to("/")
            for i in range(1, len(parts.parts)):
                parent = str(Path(*parts.parts[:i]))
                if parent not in dirs_seen:
                    dirs_seen.add(parent)
                    file_list.append(parent + "/")
            file_list.append(str(parts))

        file_list = sorted(set(file_list))

        total_size = sum(
            os.path.getsize(f) for f in new_files if os.path.isfile(f)
        )
        human_size = f"{total_size / 1024 / 1024:.1f}M" if total_size > 1024*1024 else f"{total_size / 1024:.0f}K"

        # Compute hashes from live filesystem (direct_install already deployed)
        file_hashes: dict[str, str] = {}
        for abs_path in new_files:
            try:
                if os.path.isfile(abs_path) and not os.path.islink(abs_path):
                    file_hashes[abs_path.lstrip("/")] = _sha256(abs_path)
            except (OSError, PermissionError):
                continue

        from datetime import datetime, timezone
        template_hash = self._compute_template_hash(pkg)

        supersedes_header = ""
        if pkg.supersedes:
            supersedes_header = "SUPERSEDES: " + ", ".join(pkg.supersedes) + "\n"

        manifest_content = (
            f"PACKAGE NAME: {pkg.name}-{pkg.version}\n"
            f"PACKAGE VERSION: {pkg.version}\n"
            # Same release-honesty rider as the staged writer above — the
            # direct_install lane emits the identical header so neither tracking
            # path leaves `pkm import` guessing.
            f"PACKAGE RELEASE: {pkg.release}\n"
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)\n"
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"BUILD SYSTEM: InterGenOS igos-build\n"
            f"INSTALL MODE: direct (filesystem diff)\n"
            f"TEMPLATE_HASH: {template_hash}\n"
            f"{supersedes_header}"
            f"DESCRIPTION:\n"
            f"{pkg.name}: {pkg.description}\n"
            f"\n"
            f"FILE LIST:\n"
        )
        rendered_lines = []
        for entry in file_list:
            if entry.endswith("/"):
                rendered_lines.append(entry)
            else:
                h = file_hashes.get(entry)
                rendered_lines.append(f"{entry} sha256:{h}" if h else entry)
        manifest_content += "\n".join(rendered_lines) + "\n"

        manifest_path.write_text(manifest_content)
        _created, modified = self.diff_snapshots(before, after)
        self.logger.info(
            f"Manifest (diff): {manifest_path} "
            f"({len(new_files)} files, {len(dirs_seen)} dirs, "
            f"{len(modified)} modified pre-existing, "
            f"{len(file_hashes)} hashed)"
        )

        # Stash for pkg_register_pkm_db. For direct_install packages the
        # install already happened (gate-3 effectively), so registration
        # could run here — but builder calls pkg_register_pkm_db
        # uniformly for both flows so reviewers see one gate.
        # The ownership list carries the same parent directories the manifest
        # above declares, so this lane registers directories too. A parent that
        # is a SYMLINK on the live root gets no row, matching the archive walk
        # (a symlink recorded as a directory misleads the remover's ancestor
        # sweep); the live root is the only place to check, since this lane has
        # no staging tree.
        rel_paths = [p.lstrip("/") for p in new_files]
        own_paths = []
        for entry in file_list:
            if entry.endswith("/") and os.path.islink("/" + entry.rstrip("/")):
                continue
            own_paths.append(entry)
        self._pending_pkm_paths = rel_paths
        self._pending_pkm_own_paths = own_paths
        self._pending_pkm_hashes = file_hashes
        # Same provenance stamp as the staged writer above.
        self._pending_pkm_manifest_sha256 = _sha256(str(manifest_path))

        return True

    def _emit_db_event(self, pkg, operation, **extra):
        """Emit a pkg_db_write event when forensic-trace is loaded."""
        try:
            from . import _trace
            _trace.trace_event(
                "pkg_db_write",
                pkg=pkg.name, version=pkg.version,
                operation=operation, **extra,
            )
        except Exception:
            pass

    def pkg_hook_baseline(self, pkg: Package) -> dict:
        """Hash the package's own files before its post_install runs.

        The other half of the same rule the bash lane follows: this lane
        registers its rows during the track phase, from the PRISTINE staging
        tree, and only afterwards does the builder run the recipe's
        post_install. A file the hook rewrites in place therefore keeps a hash
        that can never match again, which reads as damage to every later check
        and makes the image-metadata gate refuse a correct build. The baseline
        captured here, compared after the hook, is what attributes the change
        to the hook instead of guessing from a disagreement.

        Returns {} — never raises — when the package is not registered or the
        database cannot be opened. A failure to take the baseline must not
        destroy an otherwise good build; it is reported by the comparison
        half, which can see that it has nothing to compare against.
        """
        try:
            db = PackageDB()
        except Exception as e:
            self.logger.warning(f"pkm DB open failed for hook baseline: {e}")
            return {}
        try:
            from pkm.installer import PackageInstaller
            if db.get_installed(pkg.name) is None:
                return {}
            return PackageInstaller(db, root=str(db.root)).hook_baseline(
                pkg.name)
        except Exception as e:
            self.logger.warning(
                f"could not take a hook baseline for {pkg.name}: {e}")
            return {}
        finally:
            db.close()

    def pkg_record_hook_changes(self, pkg: Package, baseline: dict) -> bool:
        """Record which of the package's own files its post_install rewrote.

        Files whose content changed across the post_install window are marked
        hook-managed and the text manifest is re-emitted stating the class, so
        the record survives a later import — the row alone does not. Only the
        package's own files are considered; a file another package owns keeps
        the reported-never-absorbed treatment.

        Returns True when the comparison ran. A failure is REPORTED and does
        not fail the build: the package is installed and correct on disk, and
        the condition worth surfacing is that its classification may be wrong
        on the shipped image, not that the build should be thrown away.
        """
        if not baseline:
            return True
        try:
            db = PackageDB()
        except Exception as e:
            self.logger.warning(
                f"pkm DB open failed recording hook changes for "
                f"{pkg.name}: {e}")
            return False
        try:
            from pkm.installer import PackageInstaller
            changed, messages = PackageInstaller(
                db, root=str(db.root)).record_hook_changes(pkg.name, baseline)
            for line in messages:
                self.logger.info(line)
            if changed:
                self.logger.info(
                    f"  {pkg.name}: {len(changed)} own payload file(s) "
                    f"recorded as hook-managed")
            return True
        except Exception as e:
            self.logger.warning(
                f"could not record hook changes for {pkg.name}: {e} — any "
                f"file its post_install rewrote stays recorded as ordinary "
                f"payload")
            return False
        finally:
            db.close()

    def pkg_register_pkm_db(self, pkg: Package) -> bool:
        """Final TRACK-phase step: write pkm SQLite at gate-3 (post-deploy).

        Reads file paths + per-file SHA-256 hashes that pkg_manifest /
        pkg_manifest_from_diff stashed on the executor. Per RFC §4a, this
        runs ONLY after the deploy step has succeeded — a deploy failure
        leaves pkm DB untouched, so the package state is honest: no
        record claims files that aren't on disk.

        For supersede packages, runs the atomic ownership transfer and
        predecessor retirement inside a single SQLite transaction. A
        failure here rolls the entire supersede back and surfaces an
        error; the deployed files are on disk but pkm has not yet
        accounted for them, so a re-run can complete the registration
        cleanly.
        """
        rel_paths = getattr(self, "_pending_pkm_paths", None)
        file_hashes = getattr(self, "_pending_pkm_hashes", None)
        if rel_paths is None or file_hashes is None:
            self.logger.error(
                f"pkg_register_pkm_db called without pending paths/hashes — "
                f"pkg_manifest or pkg_manifest_from_diff must run first"
            )
            return False
        # Ownership list (files + directories); both manifest builders stash it.
        # Falling back to the files-only list keeps a third-party caller that
        # set only the older stash working, at that caller's own parity cost.
        own_paths = getattr(self, "_pending_pkm_own_paths", None)
        if own_paths is None:
            own_paths = rel_paths
        manifest_sha256 = getattr(
            self, "_pending_pkm_manifest_sha256", None)
        result = self._write_pkm_db(pkg, rel_paths, file_hashes, own_paths,
                                    manifest_sha256)
        self._emit_db_event(
            pkg, "pkm_db_register",
            rc=0 if result else 1, file_count=len(rel_paths),
        )
        # Keep a copy for the post-register verify_paths sidecar derivation:
        # pkg_deploy rmtree's staging on success, so this recorded list is
        # the only surviving file-list source by the time the builder's
        # success branch derives the sidecar (walking the deleted staging
        # dir silently derived nothing).
        self._last_registered_paths = list(rel_paths)
        # Clear the stash so a subsequent package can't accidentally inherit
        self._pending_pkm_paths = None
        self._pending_pkm_own_paths = None
        self._pending_pkm_hashes = None
        self._pending_pkm_manifest_sha256 = None
        return result

    def _write_pkm_db(self, pkg: Package, rel_paths: list[str],
                       file_hashes: dict[str, str],
                       own_paths: list[str] | None = None,
                       manifest_sha256: str | None = None) -> bool:
        """Populate pkm SQLite with this package's record + files (RFC §3d).

        `rel_paths` is the FILE list — it scopes the supersede overlap, which
        the archive path likewise restricts to files
        (pkm/installer.py:_paths_owned_by). `own_paths` is the ownership list
        the files table receives: the same paths plus the package's
        directories, trailing-slash marked. They are separate arguments
        because a directory must be OWNED but must not be transferred as
        overlap.

        For supersede packages, also runs the atomic ownership transfer
        and predecessor retirement inside a single SQLite transaction.
        Caller should treat False as a hard failure — the package is on
        disk but pkm cannot account for it.
        """
        if own_paths is None:
            own_paths = rel_paths
        try:
            db = PackageDB()
        except Exception as e:
            self.logger.error(f"pkm DB open failed: {e}")
            return False

        try:
            pkg_id = db.add_installed(
                name=pkg.name,
                version=pkg.version,
                release=pkg.release,
                tier=pkg.tier,
                description=pkg.description,
                license_=pkg.license,
                install_method="source-build",
                # Provenance of the text manifest this row mirrors, so an
                # unchanged package is a no-op on the next `pkm import`
                # instead of a re-register.
                manifest_sha256=manifest_sha256,
                # 3.0-F28 activation semantics come from the recipe here, the
                # same value the archive's .PKGINFO carries (tracker
                # _build_pkginfo emits reboot_required from this field), so a
                # source-built row and an archive-installed row agree.
                reboot_required=1 if getattr(
                    pkg, "reboot_required", False) else 0,
                # Declared per the add_installed destructive contract: a
                # rebuild re-registers a name the chroot already carries, so
                # this call cascades that row's files and depends away.
                # add_files below re-registers the ownership rows from the
                # fresh build's own file list. This path writes no depends
                # rows at all (it never has), so a package whose row carried
                # deps written by another path — an archive install or a
                # `pkm import` carry into the same chroot — loses them here.
                replace_existing=True,
            )
            # B7/S-D 2 (USA-1 walk closure): pass file_hashes so chroot-mode
            # builds don't fall through to the database.add_files live-FS
            # fallback — that fallback expects files at self.root="/" on the
            # build host, but chroot builds stage to /mnt/igos and never
            # deploy to / on host, so the fallback yielded NULL checksum.
            # Manifests already carry sha256 (pkg_manifest above); propagate
            # them so the build-time pkm DB matches the manifest.
            db.add_files(pkg_id, own_paths, hashes=file_hashes)

            # Supersede transition: transfer ownership of overlap paths from
            # each predecessor to this package, then mark each predecessor as
            # superseded. Caller-managed transaction wraps this so a failure
            # rolls the whole thing back without leaving pkm in a half-state.
            if pkg.supersedes:
                db.conn.execute("BEGIN")
                try:
                    pkg_set = set(rel_paths)
                    for predecessor_name in pkg.supersedes:
                        pred = db.get_installed(predecessor_name)
                        if pred is None:
                            self.logger.info(
                                f"  supersedes target '{predecessor_name}' not "
                                f"in pkm DB — supersede is a no-op (RFC §11)"
                            )
                            continue
                        pred_paths = {
                            f["path"] for f in db.get_files(predecessor_name)
                        }
                        overlap = pred_paths & pkg_set
                        if overlap:
                            overlap_hashes = {
                                p: file_hashes.get(p) for p in overlap
                            }
                            db.transfer_file_ownership(
                                predecessor_name, pkg_id,
                                list(overlap), overlap_hashes
                            )
                        db.mark_superseded(predecessor_name, pkg.name)
                        self.logger.info(
                            f"  superseded '{predecessor_name}' "
                            f"({len(overlap)} overlap paths transferred)"
                        )
                    db.conn.execute("COMMIT")
                except Exception:
                    db.conn.execute("ROLLBACK")
                    raise

            return True
        except Exception as e:
            self.logger.error(f"pkm DB write failed for {pkg.name}-{pkg.version}: {e}")
            return False
        finally:
            db.close()

    def pkg_archive_from_files(self, pkg: Package, new_files: list[str]) -> bool:
        """Create .igos.tar.gz archive from a list of files on the live filesystem."""
        archive_path = self.pkg_archives / f"{pkg.name}-{pkg.version}.igos.tar.gz"

        import tempfile, shutil
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            for filepath in new_files:
                f.write(filepath.lstrip("/") + "\n")
            filelist_path = f.name

        # PI-12: this diff/direct_install path archives files straight off the
        # live root (-C / -T filelist) and has no staging_dir, so historically
        # NO ./.PKGINFO traveled in the archive — making it invisible to the repo
        # index (pkm) and tripping build-squashfs Step 4.7 (caught on the GBC004
        # from-scratch build for dbus-pass2 / systemd-pass2 / gdk-pixbuf-pass2 /
        # gobject-introspection). Emit the same canonical .PKGINFO the DESTDIR
        # path ships and add it to the tar as ./.PKGINFO — matching the DESTDIR
        # archives and the Step 4.7 / wellformed_pkginfo predicate, which
        # extracts `./.PKGINFO`.
        pkginfo_dir = tempfile.mkdtemp(prefix="igos-pkginfo-")
        total_size = sum(
            os.path.getsize(p) for p in new_files
            if os.path.isfile(p) and not os.path.islink(p)
        )
        (Path(pkginfo_dir) / ".PKGINFO").write_text(
            self._build_pkginfo(pkg, total_size, len(new_files))
        )
        # Seal the recipe's lifecycle hooks into the SAME metadata dir, so the
        # direct-install lane ships them exactly as the DESTDIR lane does. A
        # seam implemented on one lane only is the shape that let direct_install
        # be honored by one builder and silently ignored by the other; the
        # members are added to the tar below beside ./.PKGINFO.
        self._seal_lifecycle_hooks(pkg, Path(pkginfo_dir))
        sealed_members = sorted(
            f"./{hookseal.SCRIPTS_DIR}/{p.name}"
            for p in (Path(pkginfo_dir) / hookseal.SCRIPTS_DIR).glob("*.sh")
        ) if (Path(pkginfo_dir) / hookseal.SCRIPTS_DIR).is_dir() else []
        # ./.PKGINFO first (from pkginfo_dir), then the live-root payload.
        # --no-recursion is load-bearing: GNU tar archives a directory named
        # in -T RECURSIVELY by default, so any dir path in new_files (created
        # dirs, supersedee dir rows) would sweep its whole live subtree into
        # the archive — shared dirs drag in every other package's files
        # (2026-07-20: dbus-pass2/systemd-pass2/gobject-introspection sealed
        # at 2.2-2.5 GB each this way). With --no-recursion a dir archives as
        # a metadata-only entry, exactly matching the manifest's claim.
        tar_cmd = ["tar", "-czf", str(archive_path), "--no-recursion",
                   "-C", pkginfo_dir, "./.PKGINFO", *sealed_members,
                   "-C", "/", "-T", filelist_path]

        try:
            from . import _trace
            result = _trace.traced_run(
                tar_cmd,
                phase="pkg_archive_from_files",
                intent=f"archive {len(new_files)} files + .PKGINFO for {pkg.name}",
                pkg=pkg.name,
            )
        except ImportError:
            result = subprocess.run(  # trace-coverage: allow — _trace shim unavailable fallback
                tar_cmd,
                capture_output=True, text=True,
            )
        os.unlink(filelist_path)
        shutil.rmtree(pkginfo_dir, ignore_errors=True)

        if result.returncode != 0:
            self.logger.error(f"Archive creation failed: {result.stderr}")
            return False

        archive_size = archive_path.stat().st_size
        human = f"{archive_size / 1024 / 1024:.1f}M" if archive_size > 1024*1024 else f"{archive_size / 1024:.0f}K"
        self.logger.info(f"Archive: {archive_path} ({human})")

        # Member-count gate (archive-contains-too-MUCH class): the sealed tar
        # must hold exactly the claimed file list + ./.PKGINFO — nothing tar
        # recursion, duplicate -T entries, or a future regression smuggles in.
        # The verify_paths seal gate proves claimed payload is PRESENT; this
        # proves nothing EXTRA rode along (the 2026-07-20 contamination class
        # passed the presence gate at 2.5 GB). Fail-loud, quarantine like a
        # seal failure.
        count_res = subprocess.run(  # trace-coverage: allow — read-only member count
            ["tar", "-tzf", str(archive_path)],
            capture_output=True, text=True,
        )
        if count_res.returncode != 0:
            self.logger.error(
                f"Archive member-count gate: cannot list {archive_path}: "
                f"{count_res.stderr.strip()}"
            )
            return False
        member_count = len(count_res.stdout.splitlines())
        # The archive legitimately holds three member classes: the claimed
        # payload, ./.PKGINFO, and the sealed lifecycle hooks under
        # ./.scripts/ — the tar_cmd above adds all three. The original
        # arithmetic counted only the first two, so the FIRST direct_install
        # package that sealed a hook (systemd-pass2, ge9b-13 attempt 2,
        # 2026-08-05) failed this gate deterministically on a correct archive.
        expected = len(new_files) + 1 + len(sealed_members)
        if member_count != expected:
            self.logger.error(
                f"Archive member-count gate FAILED for {pkg.name}: tar holds "
                f"{member_count} members, claimed file list is {expected} "
                f"({len(new_files)} payload + ./.PKGINFO + "
                f"{len(sealed_members)} sealed hook(s)) — the archive "
                f"contains content the manifest does not claim (or lost "
                f"claimed content). Quarantining."
            )
            try:
                archive_path.rename(archive_path.with_suffix(
                    archive_path.suffix + ".failed"))
            except OSError:
                pass
            return False

        if not self._enforce_mirror_archive_verify_paths(pkg, archive_path):
            return False
        return True
