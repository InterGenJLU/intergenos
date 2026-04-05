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
import shutil
import subprocess
import time
from pathlib import Path

from .parser import Package
from .styles import get_style
from .log import BuildLogger, SummaryLogger


class BuildExecutor:
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

        self.logger = BuildLogger(self.log_dir)
        self.summary = SummaryLogger()

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
        env["LC_ALL"] = "POSIX"
        env["XML_CATALOG_FILES"] = "/etc/xml/catalog"
        env["PKG_CONFIG_PATH"] = "/usr/lib/pkgconfig:/usr/share/pkgconfig"
        # Include /opt/rustc/bin for Rust toolchain (installed to /opt per BLFS)
        env["PATH"] = f"/opt/rustc/bin:{self.system_root}/tools/bin:" + env.get("PATH", "")

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

                # Mirror root-level symlinks into staging so DESTDIR installs
                # to paths like /usr/sbin work correctly (matching pkg-functions.sh)
                for link in ("bin", "lib", "sbin"):
                    target = Path(f"/{link}")
                    if target.is_symlink():
                        os.symlink(f"usr/{link}", str(staging / link))
                env["DESTDIR"] = str(staging)
                env["PATH"] = f"{staging}/usr/bin:{staging}/usr/sbin:" + env["PATH"]
                env["PKG_CONFIG_PATH"] = f"{staging}/usr/lib/pkgconfig:{staging}/usr/lib64/pkgconfig:" + env.get("PKG_CONFIG_PATH", "")
                existing_ldpath = env.get("LD_LIBRARY_PATH", "")
                new_ldpath = f"{staging}/usr/lib:{staging}/usr/lib64"
                env["LD_LIBRARY_PATH"] = f"{new_ldpath}:{existing_ldpath}" if existing_ldpath else new_ldpath
        else:
            env["DESTDIR"] = str(self.system_root)

        return env

    def run_command(self, cmd: str, env: dict, cwd: Path) -> int:
        """Run a shell command with full output capture and logging.

        Output is streamed line-by-line to both console and log file.
        Nothing is buffered, nothing is truncated.

        Returns:
            The command's exit code.
        """
        self.logger.command(cmd)

        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                executable="/bin/bash",
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
            self.logger.error(f"command execution failed: {e}")
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
        tarball_name = primary.filename or primary.url.split("/")[-1]
        tarball_path = self.sources_dir / tarball_name

        if not tarball_path.exists():
            self.logger.info(f"Source not found locally: {tarball_name}")
            self.logger.info(f"Downloading: {primary.url}")
            exit_code = self.run_command(
                f'wget -q --show-progress -O "{tarball_path}" "{primary.url}"',
                env=os.environ.copy(),
                cwd=self.sources_dir,
            )
            if exit_code != 0:
                self.logger.error(f"Failed to download {primary.url}")
                return None
        else:
            self.logger.info(f"Source cached: {tarball_name}")

        # Verify checksum
        if primary.sha256 and not primary.sha256.startswith("placeholder"):
            self.logger.info(f"Verifying SHA256: {primary.sha256[:16]}...")
            result = subprocess.run(
                ["sha256sum", str(tarball_path)],
                capture_output=True, text=True,
            )
            actual = result.stdout.split()[0] if result.stdout else ""
            if actual != primary.sha256:
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
        if str(tarball_path).endswith('.zip'):
            import zipfile
            try:
                with zipfile.ZipFile(str(tarball_path)) as zf:
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
                exit_code = 1
        elif str(tarball_path).endswith('.lz'):
            extract_cmd = f'tar --lzip -xf "{tarball_path}" -C "{src_dir}" --strip-components=1'
            exit_code = self.run_command(extract_cmd, env=os.environ.copy(), cwd=pkg_work_dir)
        else:
            extract_cmd = f'tar -xf "{tarball_path}" -C "{src_dir}" --strip-components=1'
            exit_code = self.run_command(extract_cmd, env=os.environ.copy(), cwd=pkg_work_dir)
        if exit_code != 0:
            self.logger.error(f"Failed to extract {tarball_name}")
            return None

        # Extract bundled deps (e.g., GMP/MPFR/MPC into GCC source tree)
        for bundled in pkg.bundled_deps:
            if " -> " in bundled:
                dep_name, dest_rel = bundled.split(" -> ", 1)
                dep_tarball = None
                for s in pkg.source[1:]:
                    if dep_name in s.url:
                        dep_tarball = self.sources_dir / s.url.split("/")[-1]
                        break

                if dep_tarball and dep_tarball.exists():
                    dest = src_dir / dest_rel.replace("${version}", pkg.version)
                    dest.mkdir(parents=True, exist_ok=True)
                    self.logger.info(f"Extracting bundled dep: {dep_name} -> {dest}")
                    exit_code = self.run_command(
                        f'tar -xf "{dep_tarball}" -C "{dest}" --strip-components=1',
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

        self.logger.info("Running validation checks...")

        for check in pkg.validation:
            self.logger.info(f"  Check: {check.description} [{check.type}]")

            if check.script:
                exit_code = self.run_command(check.script, env, cwd)

                if check.expect_contains:
                    # Re-run and capture output for content check
                    result = subprocess.run(
                        check.script,
                        shell=True, executable="/bin/bash",
                        capture_output=True, text=True,
                        env=env, cwd=str(cwd),
                    )
                    if check.expect_contains not in result.stdout:
                        self.logger.error(
                            f"Validation failed: expected output to contain '{check.expect_contains}'\n"
                            f"  Actual stdout: {result.stdout}\n"
                            f"  Actual stderr: {result.stderr}"
                        )
                        if check.fatal:
                            return False

                elif exit_code != 0:
                    self.logger.error(f"Validation script exited with code {exit_code}")
                    if check.fatal:
                        return False

            self.logger.info(f"  Check passed: {check.description}")

        return True

    # ------------------------------------------------------------------
    # Package tracking (--tracked mode)
    # ------------------------------------------------------------------

    def pkg_manifest(self, pkg: Package, staging_dir: Path) -> bool:
        """Generate a Slackware-style manifest from staged files.

        Writes: /var/lib/igos/packages/<name>-<version>
        """
        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"

        file_list = []
        for root, dirs, files in os.walk(staging_dir):
            for d in sorted(dirs):
                rel = os.path.relpath(os.path.join(root, d), staging_dir)
                file_list.append(rel + "/")
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(root, f), staging_dir)
                file_list.append(rel)

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
        manifest_content = (
            f"PACKAGE NAME: {pkg.name}-{pkg.version}\n"
            f"PACKAGE VERSION: {pkg.version}\n"
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)\n"
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"BUILD SYSTEM: InterGenOS igos-build\n"
            f"DESCRIPTION:\n"
            f"{pkg.name}: {pkg.description}\n"
            f"\n"
            f"FILE LIST:\n"
        )
        manifest_content += "\n".join(file_list) + "\n"

        manifest_path.write_text(manifest_content)
        self.logger.info(f"Manifest: {manifest_path} ({len(file_list)} entries)")
        return True

    def pkg_archive(self, pkg: Package, staging_dir: Path) -> bool:
        """Create a .igos.tar.gz archive from staged files.

        Creates: /var/lib/igos/archives/<name>-<version>.igos.tar.gz
        """
        archive_path = self.pkg_archives / f"{pkg.name}-{pkg.version}.igos.tar.gz"

        result = subprocess.run(
            ["tar", "-C", str(staging_dir), "-czf", str(archive_path), "."],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            self.logger.error(f"Archive creation failed: {result.stderr}")
            return False

        archive_size = archive_path.stat().st_size
        human = f"{archive_size / 1024 / 1024:.1f}M" if archive_size > 1024*1024 else f"{archive_size / 1024:.0f}K"
        self.logger.info(f"Archive: {archive_path} ({human})")
        return True

    def pkg_deploy(self, pkg: Package, staging_dir: Path) -> bool:
        """Deploy staged files to the live filesystem using tar.

        Safety: pre-checks for top-level entries that would collide with
        root-level symlinks (lib -> usr/lib, bin -> usr/bin, etc.).
        A package staging a real directory over one of these symlinks
        would kill the dynamic linker and break every binary on the system.
        """
        # Pre-deploy safety check: detect staging entries that would clobber
        # load-bearing root symlinks (lib -> usr/lib, bin -> usr/bin, etc.)
        dangerous = []
        for entry in ("lib", "lib64", "bin", "sbin"):
            staged = staging_dir / entry
            root_path = Path("/") / entry
            if staged.is_dir() and root_path.is_symlink():
                dangerous.append(entry)

        if dangerous:
            self.logger.error(
                f"DANGEROUS: {pkg.name}-{pkg.version} staging contains top-level "
                f"dirs that would collide with root symlinks: {' '.join(dangerous)}\n"
                f"  Fix the package build.sh to install to usr/ paths instead"
            )
            return False

        result = subprocess.run(
            ["tar", "-C", str(staging_dir), "-cf", "-", "."],
            capture_output=True,
        )
        if result.returncode != 0:
            self.logger.error(f"Deploy tar-create failed: {result.stderr.decode()}")
            return False

        result2 = subprocess.run(
            ["tar", "-C", "/", "-xf", "-",
             "--no-overwrite-dir", "--keep-directory-symlink"],
            input=result.stdout,
            capture_output=True,
        )
        if result2.returncode != 0:
            self.logger.error(f"Deploy tar-extract failed: {result2.stderr.decode()}")
            return False

        self.logger.info(f"Deployed {pkg.name}-{pkg.version} to live filesystem")

        # Clean up staging directory
        shutil.rmtree(staging_dir, ignore_errors=True)
        return True

    def pkg_verify(self, pkg: Package) -> bool:
        """Verify every file in the manifest exists on the live filesystem.

        Reads: /var/lib/igos/packages/<name>-<version>
        Returns True if all files are present, False if any are missing.
        """
        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"
        if not manifest_path.exists():
            self.logger.error(f"Manifest not found: {manifest_path}")
            return False

        content = manifest_path.read_text()
        in_file_list = False
        missing = []

        for line in content.splitlines():
            if line == "FILE LIST:":
                in_file_list = True
                continue
            if in_file_list and line.strip():
                # Skip directories (trailing /), only verify files
                if line.endswith("/"):
                    continue
                filepath = "/" + line
                if not os.path.lexists(filepath):
                    missing.append(filepath)

        if missing:
            self.logger.error(
                f"Manifest verification FAILED for {pkg.name}-{pkg.version}:\n"
                + "\n".join(f"  MISSING: {f}" for f in missing[:20])
            )
            if len(missing) > 20:
                self.logger.error(f"  ... and {len(missing) - 20} more")
            return False

        self.logger.info("Manifest verified: all files present on live filesystem")
        return True

    # ------------------------------------------------------------------
    # Direct install tracking (filesystem diff)
    # ------------------------------------------------------------------

    def fs_snapshot(self, dirs: list[str] | None = None) -> set[str]:
        """Snapshot all files and symlinks under key system directories.

        Captures regular files, symlinks (both file and directory symlinks),
        and any other non-directory entries. Returns a set of paths for diffing.
        """
        if dirs is None:
            dirs = ["/usr", "/etc", "/opt", "/var/lib", "/lib"]
        snapshot = set()
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for root, dirnames, files in os.walk(d, followlinks=False):
                # Capture regular files and file symlinks
                for f in files:
                    snapshot.add(os.path.join(root, f))
                # Capture directory symlinks (os.walk skips them by default)
                for dn in dirnames:
                    path = os.path.join(root, dn)
                    if os.path.islink(path):
                        snapshot.add(path)
        return snapshot

    def pkg_manifest_from_diff(self, pkg: Package, before: set[str], after: set[str]) -> bool:
        """Generate manifest from filesystem diff (for direct_install packages).

        Writes: /var/lib/igos/packages/<name>-<version>
        """
        new_files = sorted(after - before)

        if not new_files:
            self.logger.error(f"No new files detected for {pkg.name}-{pkg.version}")
            return False

        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"

        # Build directory + file list
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

        # Deduplicate and sort
        file_list = sorted(set(file_list))

        total_size = sum(
            os.path.getsize(f) for f in new_files if os.path.isfile(f)
        )
        human_size = f"{total_size / 1024 / 1024:.1f}M" if total_size > 1024*1024 else f"{total_size / 1024:.0f}K"

        from datetime import datetime, timezone
        manifest_content = (
            f"PACKAGE NAME: {pkg.name}-{pkg.version}\n"
            f"PACKAGE VERSION: {pkg.version}\n"
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)\n"
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"BUILD SYSTEM: InterGenOS igos-build\n"
            f"INSTALL MODE: direct (filesystem diff)\n"
            f"DESCRIPTION:\n"
            f"{pkg.name}: {pkg.description}\n"
            f"\n"
            f"FILE LIST:\n"
        )
        manifest_content += "\n".join(file_list) + "\n"

        manifest_path.write_text(manifest_content)
        self.logger.info(f"Manifest (diff): {manifest_path} ({len(new_files)} files, {len(dirs_seen)} dirs)")
        return True

    def pkg_archive_from_files(self, pkg: Package, new_files: list[str]) -> bool:
        """Create .igos.tar.gz archive from a list of files on the live filesystem."""
        archive_path = self.pkg_archives / f"{pkg.name}-{pkg.version}.igos.tar.gz"

        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            for filepath in new_files:
                f.write(filepath.lstrip("/") + "\n")
            filelist_path = f.name

        result = subprocess.run(
            ["tar", "-C", "/", "-czf", str(archive_path), "-T", filelist_path],
            capture_output=True, text=True,
        )
        os.unlink(filelist_path)

        if result.returncode != 0:
            self.logger.error(f"Archive creation failed: {result.stderr}")
            return False

        archive_size = archive_path.stat().st_size
        human = f"{archive_size / 1024 / 1024:.1f}M" if archive_size > 1024*1024 else f"{archive_size / 1024:.0f}K"
        self.logger.info(f"Archive: {archive_path} ({human})")
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

        # Snapshot filesystem before build (for direct_install diff tracking)
        fs_before = None
        if self.tracked and pkg.direct_install:
            self.logger.info("Taking pre-build filesystem snapshot...")
            fs_before = self.fs_snapshot()

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
        if success and self.tracked:
            self.logger.start_phase("track")

            if pkg.direct_install:
                # Diff-based tracking: compare before/after filesystem snapshots
                self.logger.info("Taking post-build filesystem snapshot...")
                fs_after = self.fs_snapshot()
                new_files = sorted(fs_after - fs_before)

                if not self.pkg_manifest_from_diff(pkg, fs_before, fs_after):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_archive_from_files(pkg, new_files):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_verify(pkg):
                    success = False
                    self.logger.end_phase("track", 1)
                else:
                    self.logger.end_phase("track", 0)
            else:
                # DESTDIR staging: manifest, archive, deploy, verify
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
                else:
                    self.logger.end_phase("track", 0)

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
