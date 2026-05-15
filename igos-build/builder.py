"""Build executor for igos-build.

Runs build phases for each package in dependency order. Handles:
  - Source extraction
  - Patch application
  - Build style phase execution
  - Post-build validation checks
  - Full logging of every command and its output
  - Fatal error handling (halt on failure)
"""

import os
import shlex
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .parser import Package
from . import verify_paths_derive


def _url_basename(url: str) -> str:
    """Return the filename portion of a URL, stripping any ?query or #fragment.

    Robust against CDN/token-bearing source URLs like
    'https://foo.com/pkg-1.0.tar.gz?token=xyz' which would otherwise
    yield 'pkg-1.0.tar.gz?token=xyz' as the cached filename.
    """
    return urlparse(url).path.rsplit("/", 1)[-1]
from .styles import get_style
from .log import BuildLogger, SummaryLogger
from .tracker import PackageTracker


def _validate_tar_members(tarball_path: Path, dest_dir: Path, logger) -> bool:
    """Pre-inspect tar archive members for path-traversal attacks. (B3/B8)

    Rejects any archive containing '..' components or absolute paths
    that would escape the extraction destination.
    Returns True if all members pass, False if any fail.
    """
    dest = dest_dir.resolve()
    try:
        with tarfile.open(str(tarball_path)) as tf:
            for member in tf.getmembers():
                resolved = (dest / member.name).resolve()
                if not str(resolved).startswith(str(dest)):
                    logger.error(
                        f"SECURITY: tar member '{member.name}' escapes "
                        f"extraction root '{dest}' — rejecting archive"
                    )
                    return False
    except tarfile.TarError as e:
        logger.error(f"Failed to inspect tar archive: {e}")
        return False
    return True


class BuildExecutor(PackageTracker):
    """Executes package builds with full logging and validation.

    Directory layout during builds:
        {work_dir}/{pkg.name}/
            src/          — extracted source tree
            build/        — out-of-tree build directory (if needed)

    Environment variables available to build scripts:
        IGOS            — target system root (e.g., /mnt/intergenos/build/system)
        IGOS_TARGET     — target triple (x86_64-igos-linux-gnu)
        IGOS_JOBS       — parallel make jobs
        IGOS_SOURCES    — path to downloaded source tarballs
        IGOS_PATCHES    — path to patch files
        DESTDIR         — installation destination
    """

    def __init__(
        self,
        work_dir: Path,
        log_dir: Path,
        sources_dir: Path,
        patches_dir: Path,
        system_root: Path,
        target_triple: str = "x86_64-igos-linux-gnu",
        jobs: int | None = None,
        tracked: bool = False,
        skip_built: bool = False,
        json_log: bool = False,
    ):
        self.work_dir = Path(work_dir)
        self.log_dir = Path(log_dir)
        self.sources_dir = Path(sources_dir)
        self.patches_dir = Path(patches_dir)
        self.system_root = Path(system_root)
        self.target_triple = target_triple
        self.jobs = jobs or os.cpu_count() or 4
        self.tracked = tracked
        self.skip_built = skip_built

        # Package tracking paths (Slackware-style manifests + archives)
        self.pkg_db = Path("/var/lib/igos/packages")
        self.pkg_archives = Path("/var/lib/igos/archives")
        self.pkg_staging = Path("/tmp/igos-staging")

        # Create directories
        dirs = [self.work_dir, self.log_dir, self.sources_dir, self.patches_dir, self.system_root]
        if self.tracked:
            dirs.extend([self.pkg_db, self.pkg_archives, self.pkg_staging])
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        self.logger = BuildLogger(self.log_dir, json_log=json_log)
        self.summary = SummaryLogger(log_dir=self.log_dir, json_log=json_log)

    def build_env(self, pkg: Package) -> dict[str, str]:
        """Build the environment variables dict for a package."""
        env = os.environ.copy()
        env["IGOS"] = str(self.system_root)
        env["IGOS_TARGET"] = pkg.target_triple or self.target_triple
        env["IGOS_JOBS"] = str(self.jobs)
        env["IGOS_SOURCES"] = str(self.sources_dir)
        env["IGOS_SOURCES_DIR"] = str(self.sources_dir)  # alias for build.sh compat
        env["IGOS_PATCHES"] = str(self.patches_dir)
        env["PKG_VERSION"] = str(pkg.version)
        env["version"] = str(pkg.version)  # convenience for build.sh scripts
        env["MAKEFLAGS"] = f"-j{self.jobs}"
        # Target x86-64-v2 (2009+ CPUs: Nehalem, Sandy Bridge, Ivy Bridge, all Zen).
        # Without this, GCC on the build host (Ryzen 9 5900X, x86-64-v3) emits
        # AVX2/FMA3 instructions that crash on older hardware (libffi invalid
        # opcode on Ivy Bridge i5-3570). Matches Fedora 40, RHEL 9, SUSE 15.
        env.setdefault("CFLAGS", "-march=x86-64-v2 -mtune=generic -O2 -pipe")
        env.setdefault("CXXFLAGS", "-march=x86-64-v2 -mtune=generic -O2 -pipe")
        env["LC_ALL"] = "POSIX"
        # Reproducible builds: SOURCE_DATE_EPOCH prevents timestamps from
        # varying between builds. Adopted by Debian, Arch, NixOS.
        if "SOURCE_DATE_EPOCH" not in env:
            import subprocess
            try:
                epoch = subprocess.check_output(
                    ["git", "log", "-1", "--format=%ct"],
                    cwd=str(Path(__file__).parent.parent), text=True, stderr=subprocess.DEVNULL
                ).strip()
                env["SOURCE_DATE_EPOCH"] = epoch
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass  # not in a git repo or git not available
        env["XML_CATALOG_FILES"] = "/etc/xml/catalog"
        # PKG_CONFIG_LIBDIR replaces the default search path (unlike
        # PKG_CONFIG_PATH which augments it). This prevents host .pc files
        # from leaking into the build and causing non-deterministic results.
        env["PKG_CONFIG_LIBDIR"] = "/usr/lib/pkgconfig:/usr/lib64/pkgconfig:/usr/share/pkgconfig"
        env.pop("PKG_CONFIG_PATH", None)  # ensure only LIBDIR is used
        # GObject Introspection typelib path — needed by g-ir-scanner when
        # building GTK, GStreamer, and other GI-consuming packages
        env["GI_TYPELIB_PATH"] = "/usr/lib/girepository-1.0"
        # Include the Rust toolchain bin dir in PATH. Default is /opt/rustc/bin
        # (installed there per BLFS). Override via IGOS_RUSTC_BIN env var for
        # alternate-location Rust installations (per §1 B8). The default
        # remains the BLFS path so existing builds are unchanged.
        env["PATH"] = f"{os.environ.get('IGOS_RUSTC_BIN', '/opt/rustc/bin')}:{self.system_root}/tools/bin:" + env.get("PATH", "")

        # When tracked, each package stages into its own DESTDIR
        # and staged files are made visible as a sysroot for multi-pass builds
        if self.tracked:
            if pkg.direct_install:
                # Multi-pass packages install directly to /
                # Tracking uses filesystem diff instead of DESTDIR staging
                # DESTDIR must be unset (not empty string) — some build systems
                # treat "" differently from unset
                env.pop("DESTDIR", None)
            else:
                staging = self.pkg_staging / f"{pkg.name}-{pkg.version}"
                if staging.exists():
                    shutil.rmtree(staging)
                staging.mkdir(parents=True)

                # Prepare staging directory to match live filesystem layout.
                # Mirrors what pkg-functions.sh does for bash-built packages:
                #   1. Create usr/{bin,lib,sbin} so make install has targets
                #   2. For bin/lib/sbin/lib64: mirror the running filesystem —
                #      symlink to usr/<x> when the host has /<x> as a symlink
                #      (usrmerge convention, modern Ubuntu/Fedora/Arch);
                #      otherwise create a real dir as fallback (chroot during
                #      LFS Ch.8 before usrmerge convergence, lib64 only).
                #
                # pkg_deploy's dangerous-check (tracker.py:271) rejects staging
                # that mismatches the running fs: real dir in staging while
                # the live fs has a symlink would clobber the symlink. Mirror
                # the running fs and the check passes naturally — and packages
                # built host-side via igos-build.py --tracked don't trip on
                # /lib64 → usr/lib64 (latent until lzip 2026-05-13).
                for d in ("usr/bin", "usr/lib", "usr/sbin"):
                    (staging / d).mkdir(parents=True, exist_ok=True)
                import platform
                for link in ("bin", "lib", "sbin", "lib64"):
                    target = Path(f"/{link}")
                    if target.is_symlink():
                        os.symlink(f"usr/{link}", str(staging / link))
                    elif link == "lib64" and platform.machine() == "x86_64":
                        (staging / link).mkdir(exist_ok=True)
                env["DESTDIR"] = str(staging)
                env["PATH"] = f"{staging}/usr/bin:{staging}/usr/sbin:" + env["PATH"]
                # PKG_CONFIG_LIBDIR: staging first, then system — replaces
                # default search entirely so host .pc files cannot leak in
                env["PKG_CONFIG_LIBDIR"] = (
                    f"{staging}/usr/lib/pkgconfig:{staging}/usr/lib64/pkgconfig:"
                    + env["PKG_CONFIG_LIBDIR"]
                )
                # GI typelib resolution for staged packages
                env["GI_TYPELIB_PATH"] = (
                    f"{staging}/usr/lib/girepository-1.0:"
                    + env["GI_TYPELIB_PATH"]
                )
                # LD_LIBRARY_PATH for runtime lib resolution during build
                existing_ldpath = env.get("LD_LIBRARY_PATH", "")
                new_ldpath = f"{staging}/usr/lib:{staging}/usr/lib64"
                env["LD_LIBRARY_PATH"] = f"{new_ldpath}:{existing_ldpath}" if existing_ldpath else new_ldpath
        else:
            env["DESTDIR"] = str(self.system_root)

        return env

    def run_command(self, cmd, env: dict, cwd: Path) -> int:
        """Run a shell command with full output capture and logging.

        Output is streamed line-by-line to both console and log file.
        Nothing is buffered, nothing is truncated.

        Accepts both str (shell=True) and list (shell=False) to support
        the shell injection hardening migration. (B10)

        Returns:
            The command's exit code.
        """
        self.logger.command(cmd)

        try:
            proc = subprocess.Popen(
                cmd,
                shell=isinstance(cmd, str),
                executable="/bin/bash" if isinstance(cmd, str) else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(cwd),
            )

            # Stream output line by line — never buffer, never truncate
            for line in iter(proc.stdout.readline, b""):
                decoded = line.decode("utf-8", errors="replace")
                self.logger.output(decoded)

            proc.wait()
            return proc.returncode

        except Exception as e:
            import traceback
            self.logger.error(f"command execution failed: {e}\n{traceback.format_exc()}")
            return 1

    def extract_source(self, pkg: Package, pkg_work_dir: Path) -> Path | None:
        """Download (if needed) and extract the primary source tarball.

        Returns:
            Path to the extracted source directory, or None on failure.
        """
        if not pkg.source:
            self.logger.info("No source defined — skipping extraction")
            return pkg_work_dir

        primary = pkg.source[0]
        tarball_name = primary.filename or _url_basename(primary.url)
        tarball_path = self.sources_dir / tarball_name

        if not tarball_path.exists():
            # Hard-fail if source is missing. The build runs in an offline
            # chroot — network downloads are not available. Run
            # download-sources.py on the host first.
            self.logger.error(
                f"Source not found: {tarball_name}\n"
                f"  Expected at: {tarball_path}\n"
                f"  URL: {primary.url}\n"
                f"  Run 'python3 scripts/download-sources.py' on the host to fetch missing sources."
            )
            return None
        else:
            self.logger.info(f"Source cached: {tarball_name}")

        # Verify checksum
        if primary.sha256 and not primary.sha256.startswith("placeholder"):
            self.logger.info(f"Verifying SHA256: {primary.sha256[:16]}...")
            result = subprocess.run(
                ["sha256sum", str(tarball_path)],
                capture_output=True, text=True, timeout=300
            )
            actual = result.stdout.split()[0] if result.stdout else ""
            # Normalize case — sha256sum outputs lowercase, but hashes
            # from some upstreams are published uppercase. Hex is case-insensitive.
            if actual.lower() != primary.sha256.lower():
                self.logger.error(
                    f"Checksum mismatch for {tarball_name}:\n"
                    f"  expected: {primary.sha256}\n"
                    f"  actual:   {actual}"
                )
                return None
            self.logger.info("Checksum verified.")
        else:
            self.logger.info("Checksum: placeholder — skipping verification")

        # Extract
        src_dir = pkg_work_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        self.logger.info(f"Extracting to {src_dir}")
        # Use Python zipfile for .zip, lzip for .lz, tar for everything else
        # All extraction uses hardened flags to prevent path traversal,
        # symlink attacks, and UID/GID injection.
        TAR_SAFETY = "--no-same-owner --no-same-permissions"
        if str(tarball_path).endswith('.zip'):
            import zipfile
            try:
                with zipfile.ZipFile(str(tarball_path)) as zf:
                    # Validate members before extraction — reject path traversal
                    for member in zf.namelist():
                        resolved = (src_dir / member).resolve()
                        if not str(resolved).startswith(str(src_dir.resolve())):
                            self.logger.error(
                                f"SECURITY: zip member '{member}' escapes extraction root — rejecting archive"
                            )
                            return None
                    zf.extractall(str(src_dir))
                # Strip one component level if there's a single top-level dir
                entries = list(src_dir.iterdir())
                if len(entries) == 1 and entries[0].is_dir():
                    top = entries[0]
                    for item in top.iterdir():
                        item.rename(src_dir / item.name)
                    top.rmdir()
                exit_code = 0
            except Exception as e:
                self.logger.error(f"Failed to extract zip: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
                exit_code = 1
        elif str(tarball_path).endswith('.lz'):
            if not _validate_tar_members(tarball_path, src_dir, self.logger):
                return None
            extract_cmd = f'tar --lzip -xf {shlex.quote(str(tarball_path))} -C {shlex.quote(str(src_dir))} --strip-components=1 {TAR_SAFETY}'
            exit_code = self.run_command(extract_cmd, env=os.environ.copy(), cwd=pkg_work_dir)
        elif str(tarball_path).endswith('.rpm'):
            # RPMs are not tar archives — copy the file raw into src_dir for
            # the package's build.sh to handle (typically via rpm2cpio | cpio).
            # Used by piggyback packages like shim-signed that consume a
            # binary RPM rather than building from source.
            import shutil
            try:
                shutil.copy2(str(tarball_path), str(src_dir / tarball_name))
                exit_code = 0
            except Exception as e:
                self.logger.error(f"Failed to copy RPM source into src_dir: {e}")
                exit_code = 1
        else:
            if not _validate_tar_members(tarball_path, src_dir, self.logger):
                return None
            extract_cmd = f'tar -xf {shlex.quote(str(tarball_path))} -C {shlex.quote(str(src_dir))} --strip-components=1 {TAR_SAFETY}'
            exit_code = self.run_command(extract_cmd, env=os.environ.copy(), cwd=pkg_work_dir)
        if exit_code != 0:
            self.logger.error(f"Failed to extract {tarball_name}")
            return None

        # Extract bundled deps (e.g., GMP/MPFR/MPC into GCC source tree)
        for bundled in pkg.bundled_deps:
            if " -> " in bundled:
                dep_name, dest_rel = bundled.split(" -> ", 1)
                dep_tarball = None
                # Match dep_name against URL substring OR explicit filename.
                # Some upstream snapshot URLs (e.g., cgit ?p=...;sf=tgz) don't
                # contain the dep_name as a substring; the filename: field
                # provides a stable local name that does.
                for s in pkg.source[1:]:
                    candidate_name = s.filename or _url_basename(s.url)
                    if dep_name in s.url or dep_name in candidate_name:
                        dep_tarball = self.sources_dir / candidate_name
                        break

                if dep_tarball and dep_tarball.exists():
                    dest = src_dir / dest_rel.replace("${version}", pkg.version)
                    # B8: reject bundled dep destinations that escape source tree
                    if not str(dest.resolve()).startswith(str(src_dir.resolve())):
                        self.logger.error(
                            f"SECURITY: bundled dep destination '{dest_rel}' "
                            f"escapes source tree — rejecting"
                        )
                        return None
                    dest.mkdir(parents=True, exist_ok=True)
                    self.logger.info(f"Extracting bundled dep: {dep_name} -> {dest}")
                    # B3: validate tar members before extraction
                    if not _validate_tar_members(dep_tarball, dest, self.logger):
                        self.logger.error(f"Rejected bundled dep tarball: {dep_name}")
                        return None
                    exit_code = self.run_command(
                        f'tar -xf {shlex.quote(str(dep_tarball))} -C {shlex.quote(str(dest))} --strip-components=1 {TAR_SAFETY}',
                        env=os.environ.copy(),
                        cwd=pkg_work_dir,
                    )
                    if exit_code != 0:
                        self.logger.error(f"Failed to extract bundled dep: {dep_name}")
                        return None
                else:
                    self.logger.error(f"Bundled dep tarball not found: {dep_name}")
                    return None

        return src_dir

    def run_validation(self, pkg: Package, env: dict, cwd: Path) -> bool:
        """Run post-build validation checks.

        Returns:
            True if all checks pass (or no checks defined).
            False if any fatal check fails.
        """
        if not pkg.validation:
            return True

        # B5: warn when no fatal check exists (all checks can be bypassed)
        fatal_count = sum(1 for c in pkg.validation if c.fatal)
        if fatal_count == 0 and len(pkg.validation) > 0:
            self.logger.warning(
                f"All {len(pkg.validation)} validation checks for "
                f"{pkg.name} have fatal=false — none will halt the build on failure"
            )

        self.logger.info("Running validation checks...")

        for check in pkg.validation:
            self.logger.info(f"  Check: {check.description} [{check.type}]")

            if check.script:
                if check.expect_contains:
                    # Run once with output capture for content check.
                    # shell=False + ["/bin/bash", "-c", script] eliminates
                    # injection risks while preserving bash semantics. (B10)
                    result = subprocess.run(
                        ["/bin/bash", "-c", check.script],
                        capture_output=True, text=True, timeout=300,
                        env=env, cwd=str(cwd),
                    )
                    # Log the output (mirrors what run_command does)
                    self.logger.command(check.script)
                    if result.stdout:
                        self.logger.output(result.stdout)
                    if result.stderr:
                        self.logger.output(result.stderr)

                    if check.expect_contains not in result.stdout:
                        self.logger.error(
                            f"Validation failed: expected output to contain '{check.expect_contains}'\n"
                            f"  Actual stdout: {result.stdout}\n"
                            f"  Actual stderr: {result.stderr}"
                        )
                        if check.fatal:
                            return False
                else:
                    # No content check needed — just run and check exit code.
                    # Shell=False via list preserves streaming logging in run_command. (B10)
                    exit_code = self.run_command(["/bin/bash", "-c", check.script], env, cwd)

                    if exit_code != 0:
                        self.logger.error(f"Validation script exited with code {exit_code}")
                        if check.fatal:
                            return False

            self.logger.info(f"  Check passed: {check.description}")

        return True

    def build_package(self, pkg: Package) -> bool:
        """Build a single package through all phases.

        Returns:
            True if the build succeeded, False otherwise.
        """
        build_start = time.monotonic()
        self.logger.start_package(pkg.name, pkg.version, pkg.build_style)

        # Set up working directory
        pkg_work_dir = self.work_dir / pkg.name
        if pkg_work_dir.exists():
            shutil.rmtree(pkg_work_dir)
        pkg_work_dir.mkdir(parents=True)

        env = self.build_env(pkg)
        success = True

        # B9: reject direct_install + skip_tracking (untraceable root writes)
        if pkg.direct_install and pkg.skip_tracking:
            self.logger.error(
                f"SECURITY: refusing to build {pkg.name} with "
                f"direct_install=true and skip_tracking=true. "
                f"This combination installs directly to the live filesystem "
                f"with zero audit trail. Remove one flag or both."
            )
            return False

        # Snapshot filesystem before build (for direct_install diff tracking).
        # If the package declares supersedes, exclude the supersedee's tracked
        # paths from the snapshot so writes that overlap appear net-new (per
        # RFC §3a — supersedes-aware snapshot exclusion).
        fs_before = None
        build_start_time = time.time()  # used downstream for overwrite detection
        if self.tracked and pkg.direct_install:
            self.logger.info("Taking pre-build filesystem snapshot...")
            exclude_paths = self._get_supersedee_paths(pkg) if pkg.supersedes else None
            if exclude_paths:
                self.logger.info(
                    f"  excluding {len(exclude_paths)} supersedee paths "
                    f"({', '.join(pkg.supersedes)})"
                )
            fs_before = self.fs_snapshot(exclude_paths=exclude_paths)

        # --- Extract source ---
        self.logger.start_phase("extract")
        src_dir = self.extract_source(pkg, pkg_work_dir)
        if src_dir is None:
            self.logger.end_phase("extract", 1)
            self.logger.end_package(False)
            elapsed = time.monotonic() - build_start
            self.summary.record(pkg.name, pkg.version, False, elapsed)
            return False
        self.logger.end_phase("extract", 0)

        # --- Run build style phases ---
        # build.sh is always authoritative: if it exists, use CustomStyle
        # regardless of declared build_style. build_style remains as a label
        # for humans and generate-templates.py, not a builder instruction.
        build_sh = pkg.template_path.parent / "build.sh" if pkg.template_path else None
        if build_sh and build_sh.exists():
            style = get_style("custom")
        else:
            style = get_style(pkg.build_style)
        phases = style.all_phases(pkg)

        for phase in phases:
            if not phase.commands:
                continue

            self.logger.start_phase(phase.name)

            for cmd in phase.commands:
                exit_code = self.run_command(cmd, env, src_dir)
                if exit_code != 0:
                    self.logger.end_phase(phase.name, exit_code)
                    self.logger.error(
                        f"Build failed in [{phase.name}] phase.\n"
                        f"  Package: {pkg.name} {pkg.version}\n"
                        f"  Command: {cmd}\n"
                        f"  Exit code: {exit_code}\n"
                        f"  Log: {self.log_dir}/{pkg.name}-*.log\n"
                        f"\n  Check the log file for full output above this error."
                    )
                    success = False
                    break

            if not success:
                break

            self.logger.end_phase(phase.name, 0)

        # --- Run validation ---
        if success:
            self.logger.start_phase("validate")
            if not self.run_validation(pkg, env, src_dir):
                success = False
                self.logger.end_phase("validate", 1)
            else:
                self.logger.end_phase("validate", 0)

        # --- Package tracking (manifest, archive, deploy, verify) ---
        if success and self.tracked and pkg.skip_tracking:
            self.logger.info(f"Skipping tracking for {pkg.name} (skip_tracking=true)")
        elif success and self.tracked:
            self.logger.start_phase("track")

            if pkg.direct_install:
                # Diff-based tracking: compare before/after filesystem snapshots.
                # Files are already on /. pkm SQLite registration runs at
                # gate-3 (post-verify) per RFC §4a — same gate as staged.
                self.logger.info("Taking post-build filesystem snapshot...")
                fs_after = self.fs_snapshot()
                new_files = sorted(fs_after - fs_before)

                if not self.pkg_manifest_from_diff(pkg, fs_before, fs_after,
                                                    build_start_time=build_start_time):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_archive_from_files(pkg, new_files):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_verify(pkg):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_register_pkm_db(pkg):
                    success = False
                    self.logger.end_phase("track", 1)
                else:
                    self.logger.end_phase("track", 0)
                    self._auto_derive_verify_paths(pkg, new_files)
            else:
                # DESTDIR staging: manifest, archive, deploy, verify, register.
                # pkm SQLite write-through happens at gate-3 (after deploy
                # succeeds) so a deploy failure cannot leave pkm with a
                # record for an undeployed package (RFC §4a).
                staging_dir = self.pkg_staging / f"{pkg.name}-{pkg.version}"

                if not self.pkg_manifest(pkg, staging_dir):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_archive(pkg, staging_dir):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_deploy(pkg, staging_dir):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_verify(pkg):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_register_pkm_db(pkg):
                    success = False
                    self.logger.end_phase("track", 1)
                else:
                    self.logger.end_phase("track", 0)
                    self._auto_derive_verify_paths_from_staging(pkg, staging_dir)

        # --- Post-install (runs on live filesystem, after deploy) ---
        # post_install hooks handle things like catalog registration, systemd
        # enable, config file generation — anything that must run on the live
        # system rather than in DESTDIR.
        if success:
            post_phase = style.post_install(pkg)
            if post_phase.commands:
                self.logger.start_phase("post_install")
                # Run with DESTDIR unset so commands target the live filesystem
                post_env = env.copy()
                post_env.pop("DESTDIR", None)
                for cmd in post_phase.commands:
                    exit_code = self.run_command(cmd, post_env, src_dir)
                    if exit_code != 0:
                        self.logger.error(
                            f"post_install failed for {pkg.name} {pkg.version}\n"
                            f"  Command: {cmd}\n"
                            f"  Exit code: {exit_code}"
                        )
                        success = False
                        break
                self.logger.end_phase("post_install", 0 if success else 1)

        elapsed = time.monotonic() - build_start
        self.logger.end_package(success)
        self.summary.record(pkg.name, pkg.version, success, elapsed)
        return success

    def _auto_derive_verify_paths(self, pkg, new_files):
        """Best-effort auto-derive verify_paths sidecar from direct_install diff.

        Failures are non-fatal — sidecar is a fallback, not a gate.
        """
        try:
            written = verify_paths_derive.derive_and_write_sidecar(pkg, new_files)
            if written:
                self.logger.info(
                    f"  auto-derived verify_paths sidecar written for {pkg.name}"
                )
        except Exception as e:
            # Don't let sidecar derivation break a successful build
            self.logger.info(
                f"  auto-derive verify_paths sidecar skipped for {pkg.name}: {e}"
            )

    def _auto_derive_verify_paths_from_staging(self, pkg, staging_dir):
        """Best-effort auto-derive verify_paths sidecar from DESTDIR staging.

        Walks the staging directory to build the installed-file list and
        delegates to verify_paths_derive. Failures are non-fatal.
        """
        try:
            staging_path = Path(staging_dir)
            if not staging_path.is_dir():
                return
            file_list = []
            for root, _dirs, files in os.walk(staging_path):
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), staging_path)
                    file_list.append(rel)
            written = verify_paths_derive.derive_and_write_sidecar(pkg, file_list)
            if written:
                self.logger.info(
                    f"  auto-derived verify_paths sidecar written for {pkg.name}"
                )
        except Exception as e:
            self.logger.info(
                f"  auto-derive verify_paths sidecar skipped for {pkg.name}: {e}"
            )

    def build_all(self, packages: list[Package], halt_on_failure: bool = True) -> bool:
        """Build all packages in the given order.

        Args:
            packages: List of Package objects in build order.
            halt_on_failure: If True, stop at the first failure.

        Returns:
            True if all builds succeeded, False otherwise.
        """
        total = len(packages)
        all_success = True

        self.logger.info(f"\nStarting build of {total} package(s)...\n")

        for i, pkg in enumerate(packages, 1):
            # Skip packages that have a tracked manifest.
            # Manifest existence is sufficient — full file verification already
            # ran at install time. Re-verifying here causes false rebuilds when
            # post_install moves/deletes files after the manifest was written
            # (e.g., Rust removes .old docs and moves bash completions).
            if self.skip_built:
                manifest = self.pkg_db / f"{pkg.name}-{pkg.version}"
                if manifest.exists():
                    # Check if template has changed since last build
                    # by comparing a hash of package.yml + build.sh
                    rebuild_needed = False
                    if pkg.template_path:
                        import hashlib
                        hasher = hashlib.sha256()
                        for tpl_file in [pkg.template_path, pkg.template_path.parent / "build.sh"]:
                            if tpl_file.exists():
                                hasher.update(tpl_file.read_bytes())
                        current_hash = hasher.hexdigest()[:16]
                        # Check if manifest contains our hash marker
                        manifest_text = manifest.read_text()
                        if f"TEMPLATE_HASH: {current_hash}" not in manifest_text:
                            self.logger.info(f"[{i}/{total}] Rebuilding {pkg.name} {pkg.version} (template changed)")
                            rebuild_needed = True
                    if not rebuild_needed:
                        self.logger.info(f"[{i}/{total}] Skipping {pkg.name} {pkg.version} (already tracked)")
                        self.summary.record(pkg.name, pkg.version, True, 0, skipped=True)
                        continue

            self.logger.info(f"[{i}/{total}] Building {pkg.name} {pkg.version}...")
            success = self.build_package(pkg)

            if not success:
                all_success = False
                if halt_on_failure:
                    self.logger.error(
                        f"Build halted at {pkg.name} {pkg.version} "
                        f"({i}/{total}). Fix the error and retry."
                    )
                    break

        self.summary.print_summary()
        return all_success
