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
    ):
        self.work_dir = Path(work_dir)
        self.log_dir = Path(log_dir)
        self.sources_dir = Path(sources_dir)
        self.patches_dir = Path(patches_dir)
        self.system_root = Path(system_root)
        self.target_triple = target_triple
        self.jobs = jobs or os.cpu_count() or 4

        # Create directories
        for d in [self.work_dir, self.log_dir, self.sources_dir, self.patches_dir, self.system_root]:
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
        env["IGOS_PATCHES"] = str(self.patches_dir)
        env["DESTDIR"] = str(self.system_root)
        env["MAKEFLAGS"] = f"-j{self.jobs}"
        env["LC_ALL"] = "POSIX"
        env["PATH"] = f"{self.system_root}/tools/bin:" + env.get("PATH", "")
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
        tarball_name = primary.url.split("/")[-1]
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
        exit_code = self.run_command(
            f'tar -xf "{tarball_path}" -C "{src_dir}" --strip-components=1',
            env=os.environ.copy(),
            cwd=pkg_work_dir,
        )
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
                    self.run_command(
                        f'tar -xf "{dep_tarball}" -C "{dest}" --strip-components=1',
                        env=os.environ.copy(),
                        cwd=pkg_work_dir,
                    )

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
