# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Build executor for igos-build.

Runs build phases for each package in dependency order. Handles:
  - Source extraction
  - Patch application
  - Build style phase execution
  - Post-build validation checks
  - Full logging of every command and its output
  - Fatal error handling (halt on failure)
"""

import datetime
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .parser import Package
from . import elfaudit
from . import time64audit
from . import verify_paths_derive

# Forensic-trace shim — loads scripts/lib/igos_trace.py. When
# IGOS_BUILD_DEBUG_VERBOSE is unset, every _trace call short-circuits at
# its gate. The shim is imported defensively so a packaging gap doesn't
# break the build.
try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    _trace = None
    _TRACE_AVAILABLE = False


def _url_basename(url: str) -> str:
    """Return the filename portion of a URL, stripping any ?query or #fragment.

    Robust against CDN/token-bearing source URLs like
    'https://foo.com/pkg-1.0.tar.gz?token=xyz' which would otherwise
    yield 'pkg-1.0.tar.gz?token=xyz' as the cached filename.
    """
    return urlparse(url).path.rsplit("/", 1)[-1]


# The canonical FHS-skeleton owner (build-rules §2.7): the ONE package whose
# archive ships the skeleton on installed systems. Exempt from the prune.
_SKELETON_OWNER = "intergenos-base-files"


def prune_seeded_skeleton(name: str, staging_dir: Path) -> list[str]:
    """Remove SEED-STATE FHS-skeleton members from a staging tree (L27).

    build_package pre-seeds every DESTDIR with the merged-usr compat
    symlinks (bin/lib/sbin/lib64 -> usr/*), lib64, and usr/{bin,lib,sbin}
    so installs follow the live filesystem's layout — load-bearing DURING
    the install, wrong to CAPTURE afterward: left in place, every archive
    and manifest claims an FHS skeleton the package does not own, which is
    exactly how L27 bit (GE-01: 908/913 archives carried ./bin ./lib
    ./sbin ./lib64; evicting ONE mirror-only package's manifest rows
    deleted the chroot's compat symlinks). The pkm remover's
    single-segment refusal is the chokepoint belt; this is the durable
    class fix, mirrored in scripts/pkg-functions.sh
    pkg_prune_seeded_skeleton for the bash-builder tiers.

    Removal is seed-state-verified, never blanket: a compat symlink goes
    only when it still points exactly at usr/<name>; a directory goes
    only via rmdir, i.e. only when EMPTY — glibc's populated /lib64 and
    any real usr/* content are never touched. Returns the pruned member
    names for the build log.
    """
    if name == _SKELETON_OWNER:
        return []
    pruned: list[str] = []
    for link in ("bin", "lib", "sbin", "lib64"):
        p = staging_dir / link
        if p.is_symlink() and os.readlink(str(p)) == f"usr/{link}":
            p.unlink()
            pruned.append(link)
    # usr/ last, so a fully-seed-state usr tree collapses while any real
    # content keeps its whole path.
    for d in ("lib64", "usr/bin", "usr/lib", "usr/sbin", "usr"):
        p = staging_dir / d
        if p.is_dir() and not p.is_symlink():
            try:
                p.rmdir()
                pruned.append(d + "/")
            except OSError:
                pass  # non-empty — real content, kept
    return pruned
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
                # is_relative_to, not str.startswith: a prefix check passes
                # sibling-collision escapes ('/x/y-evil' startswith '/x/y').
                resolved = (dest / member.name).resolve()
                if not resolved.is_relative_to(dest):
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

        # Progress accounting. Set for real in build_all from the plan's own
        # size; initialised here so the emitters are never reached with the
        # attributes missing.
        self._progress_total = 0
        self._progress_index = 0

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
        if pkg.gpu_targets:
            # Declared GPU ISA set (compute-tier detect-or-declare model):
            # recipes consume as ${IGOS_GPU_TARGETS:?} — fail-closed, so a
            # target-sensitive build can never fall through to an upstream
            # default target list silently.
            env["IGOS_GPU_TARGETS"] = pkg.gpu_targets
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
                if _TRACE_AVAILABLE:
                    epoch_result = _trace.traced_run(
                        ["git", "log", "-1", "--format=%ct"],
                        cwd=str(Path(__file__).parent.parent),
                        phase="pkg_env_setup",
                        intent="resolve SOURCE_DATE_EPOCH from git",
                        pkg=pkg.name,
                    )
                    epoch = epoch_result.stdout.strip() if epoch_result.returncode == 0 else ""
                    if not epoch:
                        raise FileNotFoundError("git log returned no output")
                else:
                    epoch = subprocess.check_output(  # trace-coverage: allow — _trace shim unavailable fallback
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
        #
        # ⚠ WIDTH-AWARE (GE-01 L14): pkgconf lets an env PKG_CONFIG_LIBDIR
        # OVERRIDE the i686 personality's DefaultSearchPaths, so exporting
        # the 64-bit dirs here silently DEFEATED the cross file's pinned
        # i686-igos-linux-gnu-pkg-config for every elf_class-32 package —
        # lib32-libxkbcommon's optional icu-uc probe answered from
        # /usr/lib/pkgconfig and injected the 64-bit -licuuc into a -m32
        # link (ld refused loudly; a linkable leak would have been the
        # silent case). A 32-bit package searches the 32-bit world only —
        # lockstep with scripts/lib32-env.sh's PKG_CONFIG_LIBDIR (T2).
        if pkg.elf_class == "32":
            env["PKG_CONFIG_LIBDIR"] = "/usr/lib32/pkgconfig:/usr/share/pkgconfig"
            # ⚠ NATIVE-machine counterpart (L14 follow-up, ge9b-01 lib32-wayland,
            # 2026-07-10): in a meson cross build the HOST machine correctly
            # searches the 32-bit world above, but a `native: true` dependency
            # (a BUILD-MACHINE tool's .pc — wayland-scanner for lib32-wayland;
            # lib32-mesa's wayland platform carries the same dep) is looked up
            # on the BUILD machine, whose pkg-config inherits our 32-bit
            # LIBDIR and goes blind to /usr/lib/pkgconfig — the twin dies at
            # configure even though the 64-bit sibling installed the tool
            # minutes earlier. Meson's build-machine env family is the _PATH_
            # variant (PKG_CONFIG_PATH_FOR_BUILD; there is no LIBDIR_FOR_BUILD),
            # and pkgconf combines PATH entries WITH (not instead of) LIBDIR,
            # so host-side 32-bit isolation is untouched. PROVEN both
            # directions in-chroot on the real refused tree 2026-07-10:
            # without -> "wayland-scanner NO"; with -> "YES 1.24.0". Latent
            # since L14 landed MID-desktop on GE-01 — lib32-wayland had
            # already built that run (L12's provider note), so this burn is
            # the first exposure. Native tools are 64-bit chroot reality.
            env["PKG_CONFIG_PATH_FOR_BUILD"] = "/usr/lib/pkgconfig:/usr/lib64/pkgconfig:/usr/share/pkgconfig"
        else:
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
                # Containment belt before the recursive delete (same rationale
                # as the work-dir belt in build_package): never rmtree a
                # name-joined path without proving it is a strict child of
                # the staging root.
                staging_root = self.pkg_staging.resolve()
                resolved_staging = staging.resolve()
                if (resolved_staging == staging_root
                        or not resolved_staging.is_relative_to(staging_root)):
                    raise RuntimeError(
                        f"SECURITY: staging dir '{resolved_staging}' for "
                        f"'{pkg.name}-{pkg.version}' escapes staging root "
                        f"'{staging_root}' — refusing to build")
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
                # default search entirely so host .pc files cannot leak in.
                # Width-aware (L14): a 32-bit package's staged .pc files
                # land in usr/lib32/pkgconfig — prepend the matching-width
                # staging dir, never the 64-bit one.
                if pkg.elf_class == "32":
                    env["PKG_CONFIG_LIBDIR"] = (
                        f"{staging}/usr/lib32/pkgconfig:"
                        + env["PKG_CONFIG_LIBDIR"]
                    )
                else:
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

    def phase_env(self, env: dict[str, str], phase_name: str) -> dict[str, str]:
        """Return the per-phase environment, scoping DESTDIR to the install phase.

        DESTDIR names the staging root that the install phase writes into and
        the tracker then archives. It is an INSTALL-PHASE-ONLY variable: any
        installer that runs during configure/build/check (an autotools/cmake/
        meson `make install`, an install step buried in a bundled sub-build, or
        a language build-runner's own staged install) would otherwise prepend
        DESTDIR to its prefix and silently relocate that build-phase install out
        of the tree the build then reads. Scoping it here — rather than leaving
        it in the whole-run env from build_env — makes that class impossible by
        construction, mirroring the bash package driver (which exports DESTDIR
        solely around its install step) and the post_install step (which runs on
        the live tree with DESTDIR removed).

        Returns env unchanged for the install phase; a DESTDIR-less shallow copy
        for every other phase (configure/build/check/post_install). direct_install
        packages never have DESTDIR in env to begin with, so this is a no-op for
        them.
        """
        if phase_name == "install":
            return env
        return {k: v for k, v in env.items() if k != "DESTDIR"}

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

        start_ms = time.monotonic() * 1000
        # Build the argv representation for the structured trace. shell=True
        # callers pass a string; convert to a single-element argv for event
        # consistency. Output streaming happens via Popen below — every byte
        # the subprocess emits flows through self.logger.output, which (with
        # verbose on) emits subprocess_output events. The subprocess_end
        # event below pins the final rc + duration to the same boundary.
        argv = [cmd] if isinstance(cmd, str) else list(cmd)

        try:
            proc = subprocess.Popen(  # trace-coverage: allow — streaming Popen wrapped via self.logger.output -> subprocess_output events per line + subprocess_end at completion
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
            duration_ms = int((time.monotonic() * 1000) - start_ms)
            if _TRACE_AVAILABLE:
                try:
                    _trace.trace_event(
                        "subprocess_end",
                        cmd=argv, rc=proc.returncode,
                        duration_ms=duration_ms, cwd=str(cwd),
                        phase="pkg_command",
                    )
                except Exception:
                    pass
            return proc.returncode

        except Exception as e:
            import traceback
            duration_ms = int((time.monotonic() * 1000) - start_ms)
            if _TRACE_AVAILABLE:
                try:
                    _trace.trace_event(
                        "subprocess_exception",
                        cmd=argv, exception=repr(e),
                        duration_ms=duration_ms, cwd=str(cwd),
                    )
                except Exception:
                    pass
            self.logger.error(f"command execution failed: {e}\n{traceback.format_exc()}")
            return 1

    def _verify_source_checksum(self, pkg: Package, src, src_name: str,
                                src_path: Path) -> bool:
        """Verify ONE declared source tarball against its sha256 pin.

        A `generated: true` first-party source carries no pin BY DESIGN —
        its integrity comes from git-controlled inputs + the deterministic
        generator (see parser.Source). Every other source MUST carry a
        64-hex pin (parser-enforced) and MUST verify here. There is no
        placeholder bypass.
        """
        if src.generated and not src.sha256:
            self.logger.info(
                f"Checksum: {src_name} is a generated first-party source — "
                f"no sha pin by design (integrity = git inputs + generator)")
            return True
        if not src.sha256:
            # The parser rejects this shape; belt-and-suspenders for any
            # Package constructed outside parse_template.
            self.logger.error(
                f"SECURITY: {src_name} has no sha256 and is not "
                f"'generated: true' — refusing to use an unpinned source")
            return False
        self.logger.info(f"Verifying SHA256 ({src_name}): {src.sha256[:16]}...")
        if _TRACE_AVAILABLE:
            result = _trace.traced_run(
                ["sha256sum", str(src_path)],
                timeout=300, phase="pkg_source_verify",
                intent=f"verify sha256 of {src_name}", pkg=pkg.name,
            )
        else:
            result = subprocess.run(  # trace-coverage: allow — _trace shim unavailable fallback
                ["sha256sum", str(src_path)],
                capture_output=True, text=True, timeout=300
            )
        actual = result.stdout.split()[0] if result.stdout else ""
        # Normalize case — sha256sum outputs lowercase, but hashes
        # from some upstreams are published uppercase. Hex is case-insensitive.
        if actual.lower() != src.sha256.lower():
            self.logger.error(
                f"Checksum mismatch for {src_name}:\n"
                f"  expected: {src.sha256}\n"
                f"  actual:   {actual}"
            )
            return False
        self.logger.info("Checksum verified.")
        return True

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

        # Verify EVERY declared source before ANY use — not just source[0].
        # Secondary tarballs previously reached the build with no checksum
        # comparison at all, on both consumption paths: the bundled_deps
        # extraction below, and Rule-5 manual `tar xf` calls inside build.sh
        # (e.g. sbsigntool's ccan tree), which read the staged file straight
        # from the sources dir.
        for src in pkg.source:
            src_name = src.filename or _url_basename(src.url)
            src_path = self.sources_dir / src_name
            if not src_path.exists():
                # Hard-fail if a source is missing. The build runs in an
                # offline chroot — network downloads are not available. Run
                # download-sources.py on the host first.
                self.logger.error(
                    f"Source not found: {src_name}\n"
                    f"  Expected at: {src_path}\n"
                    f"  URL: {src.url}\n"
                    f"  Run 'python3 scripts/download-sources.py' on the host to fetch missing sources."
                )
                return None
            self.logger.info(f"Source cached: {src_name}")
            if not self._verify_source_checksum(pkg, src, src_name, src_path):
                return None

        # Extract
        src_dir = pkg_work_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        if not primary.extract:
            # extract: false — a plain-file source (pinned .phar/.run class):
            # checksum is verified above like every source; there is nothing
            # to unpack, and forcing it through tar fails loud on non-archive
            # bytes. build.sh consumes the file from IGOS_SOURCES directly.
            self.logger.info(
                f"Source {tarball_name} declares extract: false — "
                f"checksum verified, extraction skipped")
            return src_dir

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
                    # (is_relative_to, not str.startswith — prefix-collision).
                    zip_root = src_dir.resolve()
                    for member in zf.namelist():
                        resolved = (src_dir / member).resolve()
                        if not resolved.is_relative_to(zip_root):
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
        elif str(tarball_path).endswith(('.rpm', '.msi')):
            # RPMs and MSIs are not tar archives — copy the file raw into
            # src_dir for the package's build.sh to handle (rpm2cpio | cpio
            # for RPMs; installed verbatim as data for MSIs). Used by
            # piggyback packages like shim-signed (binary RPM) and the
            # wine-gecko/wine-mono addon data packages (sha-pinned MSIs
            # wine consumes whole at prefix creation).
            import shutil
            try:
                shutil.copy2(str(tarball_path), str(src_dir / tarball_name))
                exit_code = 0
            except Exception as e:
                self.logger.error(f"Failed to copy raw binary source into src_dir: {e}")
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
                    # B8: reject bundled dep destinations that escape source
                    # tree (is_relative_to, not str.startswith — prefix-collision)
                    if not dest.resolve().is_relative_to(src_dir.resolve()):
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
                    if _TRACE_AVAILABLE:
                        result = _trace.traced_run(
                            ["/bin/bash", "-c", check.script],
                            timeout=300, env=env, cwd=str(cwd),
                            phase="pkg_validate",
                            intent=f"validation: {check.description}",
                            pkg=pkg.name,
                        )
                    else:
                        result = subprocess.run(  # trace-coverage: allow — _trace shim unavailable fallback
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

                    # A validation passes only when the script EXITED ZERO and
                    # printed the expected token. Token-only acceptance let a
                    # script print its success line and then die in a later
                    # assertion with a nonzero exit — and still be recorded as
                    # passed.
                    token_ok = check.expect_contains in result.stdout
                    if result.returncode != 0 or not token_ok:
                        self.logger.error(
                            f"Validation failed: rc={result.returncode}, expected "
                            f"token {'present' if token_ok else 'MISSING'} "
                            f"('{check.expect_contains}')\n"
                            f"  Actual stdout: {result.stdout}\n"
                            f"  Actual stderr: {result.stderr}"
                        )
                        if check.fatal:
                            return False
                        self.logger.warning(
                            f"  Check FAILED (non-fatal, waived): {check.description}")
                        continue
                else:
                    # No content check needed — just run and check exit code.
                    # Shell=False via list preserves streaming logging in run_command. (B10)
                    exit_code = self.run_command(["/bin/bash", "-c", check.script], env, cwd)

                    if exit_code != 0:
                        self.logger.error(f"Validation script exited with code {exit_code}")
                        if check.fatal:
                            return False
                        self.logger.warning(
                            f"  Check FAILED (non-fatal, waived): {check.description}")
                        continue

            self.logger.info(f"  Check passed: {check.description}")

        return True

    def overlay_package_files(self, pkg: Package, env: dict) -> bool:
        """Auto-deploy packages/<tier>/<pkg>/files/ tree into DESTDIR.

        Mirrors PKGBUILD source=(file://...) auto-deploy on Arch and dh_install
        in Debian: any path under packages/<tier>/<pkg>/files/ (rooted at /)
        gets copied into DESTDIR before pkg_archive seals it. Lets packages
        declaratively ship sysusers.d / tmpfiles.d / systemd-unit / apparmor-
        profile content alongside their build.sh without needing per-package
        `cp -av` in do_install.

        Conflict behavior: uses cp -an (--no-clobber) so anything do_install
        already wrote (with custom modes/owners — e.g., intergenos-base-files's
        chmod 0640 shadow file) wins. The overlay only ADDS missing paths;
        never overwrites do_install's work.

        Surfaced 2026-05-27 sysusers.d migration (commit fa28e435): 20
        packages were given files/usr/lib/sysusers.d/<pkg>.conf entries but
        the builder had no mechanism to deploy them into DESTDIR, so the
        new files never landed in the archives. This phase closes that gap
        for the whole codebase, not just the 20 migrated packages.
        """
        if pkg.template_path is None:
            return True
        files_dir = pkg.template_path.parent / "files"
        if not files_dir.is_dir():
            return True
        destdir = env.get("DESTDIR")
        if not destdir:
            return True
        if _TRACE_AVAILABLE:
            result = _trace.traced_run(
                ["cp", "-an", f"{files_dir}/.", f"{destdir}/"],
                phase="pkg_overlay_files",
                intent=f"deploy {files_dir} into DESTDIR",
                pkg=pkg.name,
            )
        else:
            result = subprocess.run(  # trace-coverage: allow — _trace shim unavailable fallback
                ["cp", "-an", f"{files_dir}/.", f"{destdir}/"],
                capture_output=True, text=True,
            )
        if result.returncode != 0:
            self.logger.error(
                f"  overlay-files: cp -an from {files_dir} to {destdir} "
                f"returned rc={result.returncode}: {result.stderr.strip()} — "
                f"a partial files/ overlay (systemd units, sysusers.d, "
                f"apparmor profiles) must never ship silently"
            )
            return False
        # Normalize ownership of every files/-sourced path to root:root. `cp -a`
        # (= --preserve=all) above copied the REPO source's owner — the build
        # user's uid/gid (e.g. 1000) — into DESTDIR. That uid maps to the first
        # human user (intergenos) on the live/installed system, so shipping it
        # leaves /etc/passwd + /etc/group (mode 0664) WRITABLE and /etc/shadow
        # readable by an unprivileged user = trivial local root escalation
        # (found 2026-06-04). Repo-source ownership is never meaningful for the
        # target; legitimate service-user ownership is applied by explicit
        # do_install/post_install chowns on build-OUTPUT paths, not files/
        # overlay paths, so forcing root here is safe. Chowning by path (not the
        # cp itself) ALSO covers packages that self-deploy their files/ tree via
        # `cp -av` in do_install (e.g. intergenos-base-files) before this runs.
        for src in files_dir.rglob("*"):
            dest = Path(destdir) / src.relative_to(files_dir)
            try:
                os.chown(dest, 0, 0, follow_symlinks=False)
            except FileNotFoundError:
                pass
        # Count what was deployed for visibility.
        n_files = sum(1 for _ in files_dir.rglob("*") if _.is_file())
        if n_files > 0:
            self.logger.info(
                f"  overlay-files: deployed {n_files} file(s) from "
                f"{files_dir.relative_to(files_dir.parents[3])}/ to DESTDIR/"
            )
        return True

    def bundle_license(self, pkg: Package, src_dir: Path, env: dict) -> bool:
        """Bundle upstream/derived license text into the install layout (K21.B).

        Closes P-004 (THIRD-PARTY-NOTICES.md generator coverage) + P-010
        (license-text bundled in installed system) + P-019 (CREDITS halves) +
        P-025 (per-package /usr/share/licenses/<name>/) + P-026 (gnome-extensions
        handled by intergenos-default-settings gschema-override package per D-006).

        Delegates to :mod:`license_bundle`, the single source of truth for the
        four strategies, so the Python-tier build-time hook, the bash-tier hook
        (scripts/pkg-functions.sh), and the diagnostic backfill tool
        (scripts/backfill-license-bundles.py) all share identical semantics:

          S1 extract upstream LICENSE/COPYING/COPYRIGHT/NOTICE from the source
             tree (top-level + licenses/license-files/doc/docs + nested root);
          S2 mirror a pass-variant's base package licenses;
          S3 ship the project GPL-3.0-or-later LICENSE for first-party packages;
          S4 write a LICENSE-BY-SPDX stub from package.yml's SPDX declaration.

        If ${DESTDIR}/usr/share/licenses/<package>/ already has content (the
        package's own build.sh staged licenses explicitly), this is a no-op so
        we don't clobber upstream-supplied bundling. Cargo-c packages (P-021)
        are handled by the Rust build-style cargo-license integration, not here.

        Returns True unless no strategy could populate the bundle ("no-licenses"
        — a warning, not a build-fail; the K21.B gate at phase_squashfs is the
        hard check, and SPDX in package.yml is the canonical license-of-record).
        """
        from . import license_bundle

        # Resolve the root the bundle must land in — it MUST match where the
        # package's payload actually installs. DESTDIR-staged packages bundle
        # into the staging tree (deployed wholesale into the chroot afterward).
        # direct_install packages have DESTDIR unset; do_install + the fs-diff
        # tracking both operate on the live root "/" (the builder runs
        # in-chroot, so "/" IS the chroot root). Writing their bundle to
        # self.system_root instead lands it in build/system — a sysroot stub
        # inside the chroot build tree that phase_image deletes ("Cleaning
        # build artifacts from target") before squashfs. That orphaning is the
        # K21.B miss for the Python+direct_install packages (dbus-pass2,
        # gdk-pixbuf-pass2, systemd-pass2, gobject-introspection): bundle_license
        # logs "backfilled" honestly, then the files are cleaned away. Mirror
        # the install destination exactly.
        if pkg.direct_install:
            install_root = Path("/")
        else:
            install_root = Path(env.get("DESTDIR") or str(self.system_root))
        license_dir = install_root / "usr" / "share" / "licenses" / pkg.name

        # S2 base: a pass-variant mirrors its base package's ALREADY-INSTALLED
        # licenses. For direct_install packages the base's bundle lives in the
        # live root "/" (it was installed there before this pass-variant built);
        # for staged packages, under system_root.
        base = license_bundle.base_name_for_pass_variant(pkg.name)
        base_root = Path("/") if pkg.direct_install else self.system_root
        base_license_dir = (
            base_root / "usr" / "share" / "licenses" / base if base else None
        )

        # S3 first-party: ship the canonical project LICENSE (repo root). The
        # Python builder runs on the host, so the repo-root file is reachable
        # (the bash hook, which runs in-chroot where repo-root files are NOT
        # synced, uses the byte-identical packages/core/intergenos-legal/LICENSE).
        firstparty_license = Path(__file__).resolve().parent.parent / "LICENSE"

        result = license_bundle.apply_strategies(
            pkg.name,
            pkg.version,
            license_dir,
            src_root=src_dir,
            base_license_dir=base_license_dir,
            firstparty_license=firstparty_license,
            spdx=pkg.license,
            tier=pkg.tier,
        )

        # Forensic trace (K21.B observability): record WHERE the bundle landed
        # and WHICH strategy populated it. Before this, the trace showed only
        # the phase markers ("bundle-license ran, result=backfilled") but never
        # the destination path — so a bundle written to the wrong root (Bug 1)
        # was indistinguishable from a correct one until squashfs failed, hours
        # later. Emitting license_dir makes the log self-diagnosing.
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event(
                    "bundle_license",
                    pkg=pkg.name,
                    version=pkg.version,
                    license_dir=str(license_dir),
                    strategy=result,
                    direct_install=pkg.direct_install,
                )
            except Exception:
                pass

        if result == "skipped":
            self.logger.info("  licenses already staged by build.sh; skipping bundle")
        elif result == "no-licenses":
            self.logger.warning(
                f"  bundle_license: no license bundled for {pkg.name} "
                f"(no source license, not first-party, no SPDX) — K21.B will flag"
            )
        else:
            self.logger.info(f"  bundle_license: {result} ({pkg.name})")

        return result != "no-licenses"

    def _force_root_ownership(self, target) -> None:
        """Force root:root on staged/installed files before archive + deploy.

        The build runs as root in the chroot, but cp -a/-p/tar and shutil.copy2
        in do_install + license bundling PRESERVE the repo/source build-user uid
        (>=1000 — the virtiofs repo + host-generated asset tarballs), which then
        ships into BOTH the package archive (-> installed systems) AND the chroot
        (-> squashfs). Repo/source ownership is never meaningful for the target;
        legitimate service-user ownership is applied by post_install at install
        time (see overlay_package_files). This single chokepoint makes the
        shipping tree correct at the source, so build-squashfs.sh's uid>=1000
        guard becomes a true zero-hit backstop. (approved staging-
        chokepoint fix, 2026-06-25, root:root arc.) `target` is either a staging
        directory (recursed) or an iterable of absolute live-root file paths
        (the direct_install diff set).
        """
        # L29 (2026-07-05) — two defects in the original blanket chown:
        #  1. The kernel CLEARS setuid/setgid on any chown of a regular
        #     file, even by root, even when ownership does not change —
        #     the GE-01 corpus shipped every setuid binary stripped to
        #     0755 (sudo/su/passwd/pkexec all broken, live + installed).
        #  2. "No package stages non-root ownership" was unverified: at,
        #     fcron, dbus, util-linux stage legitimate system users/groups
        #     (all < 1000), which the blanket chown flattened.
        # Fix: only chown files whose uid OR gid >= 1000 (the actual
        # build-user leak class), and restore suid/sgid across the chown.
        # Verified by check-setuid-inventory.py at squashfs time.
        def _chown(p):
            try:
                st = os.lstat(p)
            except FileNotFoundError:
                return
            if st.st_uid < 1000 and st.st_gid < 1000:
                return
            os.chown(p, 0, 0, follow_symlinks=False)
            if stat.S_ISREG(st.st_mode) and st.st_mode & 0o6000:
                os.chmod(p, stat.S_IMODE(st.st_mode))
        if isinstance(target, (str, Path)) and Path(target).is_dir():
            root = Path(target)
            _chown(root)
            for p in root.rglob("*"):
                _chown(p)
        else:
            for p in target:
                _chown(p)

    def _snapshot_prune(self) -> set[str]:
        """Prune set for the direct_install fs_snapshot walk.

        review finding H3: the static virtual/volatile trees (SNAPSHOT_PRUNE_DEFAULT)
        unioned with THIS build's own source/scratch/staging dirs — build
        inputs and intermediates, never package payload — so an expanded "/"
        walk never counts them. Both the pre- and post-build snapshot pass the
        identical set.
        """
        prune = set(self.SNAPSHOT_PRUNE_DEFAULT) | {
            os.path.normpath(str(self.work_dir)),
            os.path.normpath(str(self.sources_dir)),
            os.path.normpath(str(self.pkg_staging)),
            # Build-observability outputs are never package payload either: the
            # live build-log dir and the forensic-trace root both GROW while a
            # build runs, so a direct_install whole-tree diff that sees them
            # sweeps actively-changing files into the manifest and verification
            # fails on the re-hash (first hit: gobject-introspection on the
            # self-hosted lane burn, 2026-07-18 — the guest-local trace root
            # /var/lib/intergenos-build/trace is inside the walk there).
            os.path.normpath(str(self.log_dir)),
        }
        trace_root = os.environ.get("IGOS_TRACE_ROOT")
        if trace_root:
            prune.add(os.path.normpath(trace_root))
        return prune

    def pkg_elf_audit(self, pkg: Package, target) -> bool:
        """Archive-time ELF word-size audit (elfaudit.py) — fail-closed.

        Runs immediately before the file set is sealed into the package
        archive, on BOTH tracking paths: `target` is the DESTDIR staging
        directory, or the direct_install diff file list. A wrong-width ELF
        object (vs the recipe's `elf_class:` contract, default 64) refuses
        the archive with every violating file named — the multilib failure
        class must die at package time, where the payload exists, not at
        squashfs time, where mirror-only packages are already evicted.
        """
        if pkg.elf_class == "mixed":
            # A waived audit is never a silent one: say it, per package,
            # in the build log. Governance of WHO may declare mixed rides
            # the lib32 mapping-field validator (the RT-14 work item).
            self.logger.warning(
                f"{pkg.name}: elf_class=mixed — the archive width audit is "
                f"waived by the recipe's explicit declaration"
            )
            return True
        if isinstance(target, (str, Path)) and Path(target).is_dir():
            violations, exempted = elfaudit.audit_tree(
                Path(target), pkg.elf_class, pkg.elf_class_exempt
            )
        else:
            violations, exempted = elfaudit.audit_files(
                target, pkg.elf_class, exempt=pkg.elf_class_exempt
            )
        for e in exempted:
            # A waived file is never a silent one (mirrors the mixed waiver).
            self.logger.warning(f"{pkg.name}: ELF-class audit: {e}")
        if violations:
            self.logger.error(
                f"ELF-class audit REFUSED the archive for {pkg.name} "
                f"(elf_class={pkg.elf_class}): {len(violations)} violation(s)"
            )
            for v in violations:
                self.logger.error(f"  {v}")
            return False
        return True

    def pkg_time64_audit(self, pkg: Package) -> bool:
        """Archive-time time64 build-log assertion (time64audit.py, RT-8) —
        fail-closed.

        For an elf_class "32" package, assert the build log carries NO
        enabled `_TIME_BITS=64` define: an upstream that opts itself into
        64-bit time_t skews public struct layouts against the time32 ABI
        the prebuilt game binaries use — silent memory corruption, not an
        error path. Runs beside pkg_elf_audit on both tracking paths,
        scanning THIS package's live build log; a missing/unreadable log on
        a 32-bit package REFUSES (a gate that cannot see must halt). 64-bit
        packages no-op; mixed waives loudly (who may declare mixed is
        governed by the tier validator's ELF_CLASS_MIXED_ALLOWED).
        """
        if pkg.elf_class == "mixed":
            self.logger.warning(
                f"{pkg.name}: elf_class=mixed — the time64 log assertion is "
                f"waived by the recipe's explicit declaration"
            )
            return True
        if pkg.elf_class != "32":
            return True
        reason = time64audit.waiver_reason(pkg.name)
        if reason:
            self.logger.warning(
                f"{pkg.name}: time64 audit WAIVED — {reason}")
            return True
        # Flush the live per-package log so the scan sees every line the
        # build just streamed.
        log_path = getattr(self.logger, "_log_path", None)
        if getattr(self.logger, "_file", None) is not None:
            try:
                self.logger._file.flush()
            except Exception:
                pass
        logs = [log_path] if log_path and Path(log_path).exists() else []
        violations = time64audit.audit_package_logs(logs, pkg.elf_class,
                                                    pkg.name)
        if violations:
            self.logger.error(
                f"time64 audit REFUSED the archive for {pkg.name}: a 32-bit "
                f"package must never enable 64-bit time_t "
                f"({len(violations)} violation(s))"
            )
            for v in violations:
                self.logger.error(f"  {v}")
            return False
        return True

    def build_package(self, pkg: Package) -> bool:
        """Build a single package through all phases.

        Returns:
            True if the build succeeded, False otherwise.
        """
        build_start = time.monotonic()
        self.logger.start_package(pkg.name, pkg.version, pkg.build_style)

        # Set up working directory. Containment belt before the recursive
        # delete: pkg.name is grammar-validated at parse time, but a delete
        # of a name-joined path never proceeds on containment alone being
        # assumed — resolve and require the target to be a strict child of
        # work_dir (defense in depth for any non-parser Package source).
        work_root = self.work_dir.resolve()
        pkg_work_dir = self.work_dir / pkg.name
        resolved_work = pkg_work_dir.resolve()
        if resolved_work == work_root or not resolved_work.is_relative_to(work_root):
            self.logger.error(
                f"SECURITY: work dir '{resolved_work}' for package name "
                f"'{pkg.name}' escapes build work root '{work_root}' — "
                f"refusing to build"
            )
            return False
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
        # review finding H3: the snapshot walks the whole tree (minus a logged prune list)
        # and records per-path ctime, so a write ANYWHERE is seen and content
        # overwrites of pre-existing files are caught by ctime delta — not just
        # net-new paths, and not defeatable by mtime forgery (cp -a / tar -p).
        # Supersedee paths stay IN the snapshot: an untouched predecessor keeps
        # its ctime and is NOT claimed; one the build rewrote has a ctime delta
        # and transfers to the successor (diff_snapshots / diff_new_files). The
        # retained supersedee mtime check is a subsumed cross-check.
        fs_before = None
        build_start_time = time.time()  # used downstream for overwrite detection
        if self.tracked and pkg.direct_install:
            self.logger.info("Taking pre-build filesystem snapshot...")
            fs_before = self.fs_snapshot(prune=self._snapshot_prune())

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

            # Test-allow-list policy (docs/test-allow-list.md) on the
            # builder-driven check phase — the yml-lane counterpart of
            # pkg_run_tests (scripts/pkg-functions.sh). The parser already
            # enforced reason-required fail-closed, so a waiver reaching
            # here is a governed, documented one. Custom-style packages
            # route their own check() through pkg_run_tests; for them this
            # layer is a no-op wrapper around an already-policied command.
            if phase.name == "check" and not pkg.tests_enabled:
                self.logger.warning(
                    f"[tests] check phase SKIPPED (tests.enabled=false). "
                    f"Reason: {pkg.tests_reason}"
                )
                continue

            self.logger.start_phase(phase.name)

            # DESTDIR is scoped to the install phase only (see phase_env): a
            # build-phase install must never inherit it (silent-redirect class).
            phase_env = self.phase_env(env, phase.name)

            for cmd in phase.commands:
                exit_code = self.run_command(cmd, phase_env, src_dir)
                if exit_code != 0:
                    if (phase.name == "check"
                            and pkg.tests_failure_policy == "known_failures"):
                        # LOUD waiver, mirroring pkg_run_tests — visible in
                        # the orchestrator log, never a silent pass (the
                        # L7/L8 lesson: invisible verdicts end in silent
                        # halts; invisible waivers are worse).
                        self.logger.warning(
                            f"[tests] test suite exit {exit_code} — allowed "
                            f"by failure_policy=known_failures\n"
                            f"[tests] warning reason: {pkg.tests_reason}"
                        )
                        continue
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

        # --- Overlay packages/<tier>/<pkg>/files/ into DESTDIR ---
        # Mirrors the PKGBUILD source=(file://...) + dh_install patterns
        # used by Arch and Debian: any file under packages/<tier>/<pkg>/
        # files/ (rooted at /) is auto-copied into DESTDIR before the
        # archive is sealed. Lets packages declaratively ship sysusers.d
        # /tmpfiles.d/systemd-unit/apparmor-profile content alongside
        # their build.sh without needing per-package `cp -av` in
        # do_install. Uses cp -an (no-clobber) so anything do_install
        # already wrote (with custom modes/owners — e.g., intergenos-
        # base-files's chmod'd shadow file) wins; the overlay only adds.
        if success:
            self.logger.start_phase("overlay-files")
            if not self.overlay_package_files(pkg, env):
                success = False
                self.logger.end_phase("overlay-files", 1)
            else:
                self.logger.end_phase("overlay-files", 0)

        # --- Bundle upstream license files (K21.B) ---
        # Stage LICENSE/COPYING/COPYRIGHT/NOTICE files from the source tree to
        # ${DESTDIR}/usr/share/licenses/<package>/ so the installed system has
        # per-package license attribution at the canonical FHS path. Closes
        # P-004 + P-010 + P-019 + P-025; gnome-extensions (P-026) handled by
        # the intergenos-default-settings gschema-override package per D-006.
        if success:
            self.logger.start_phase("bundle-license")
            self.bundle_license(pkg, src_dir, env)
            self.logger.end_phase("bundle-license", 0)

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
                fs_after = self.fs_snapshot(prune=self._snapshot_prune())
                # review finding H3: created ∪ ctime-modified (∪ retained supersedee
                # overwrites) — one source of truth shared with
                # pkg_manifest_from_diff, so the archive/audit/ownership target
                # and the manifest claim the same set. Pre-existing files this
                # build overwrote (invisible to the old net-new-only set diff,
                # and to mtime forgery) are now included.
                new_files = self.diff_new_files(pkg, fs_before, fs_after,
                                                build_start_time)

                # Normalize the just-installed live-root files to root:root before
                # they are archived (-> installed systems) and sealed into the
                # squashfs. See _force_root_ownership. (root:root chokepoint.)
                self._force_root_ownership(new_files)

                if not self.pkg_elf_audit(pkg, new_files):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_time64_audit(pkg):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_manifest_from_diff(pkg, fs_before, fs_after,
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

                # Normalize the staging tree to root:root before manifest/archive/
                # deploy so neither the package archive nor the deployed chroot
                # ships build-user-owned (uid>=1000) files. See
                # _force_root_ownership. (root:root chokepoint.)
                self._force_root_ownership(staging_dir)

                # L27 durable class fix: the staging seed must never be
                # captured — prune seed-state skeleton members before the
                # audits/manifest/archive see the tree (the pkm remover's
                # single-segment refusal remains the chokepoint belt).
                pruned = prune_seeded_skeleton(pkg.name, staging_dir)
                if pruned:
                    self.logger.info(f"Skeleton prune: {' '.join(pruned)}")

                if not self.pkg_elf_audit(pkg, staging_dir):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_time64_audit(pkg):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_manifest(pkg, staging_dir):
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
                # Capture the package's own file hashes BEFORE the hook runs.
                # This lane registers its rows during the track phase above,
                # from the PRISTINE staging tree, so a file the hook rewrites
                # in place would otherwise keep a hash that can never match
                # again — reported as damage on every later check and enough
                # to make the image-metadata gate refuse a correct build. The
                # window between this call and the comparison after the hook
                # is the evidence; nothing is inferred from a file merely
                # disagreeing with its recorded hash.
                hook_baseline = self.pkg_hook_baseline(pkg)
                # post_install runs on the live filesystem — DESTDIR removed via
                # the same install-phase scoping seam used for the build phases.
                post_env = self.phase_env(env, "post_install")
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
                if success:
                    self.pkg_record_hook_changes(pkg, hook_baseline)
                self.logger.end_phase("post_install", 0 if success else 1)

        # A tracked build that failed after pkg_manifest ran must not leave
        # the name-version manifest behind: build_all's --skip-built check
        # treats that manifest (+ matching TEMPLATE_HASH) as proof of a
        # completed install, so a package that failed archive/deploy/verify/
        # register/post_install would be recorded as "already tracked" on the
        # retry and the failed gate would never re-run. Remove the manifest
        # (the skip marker) and quarantine the sealed archive out of the
        # *.igos.tar.gz namespace so the squashfs/manifest phases cannot ship
        # a payload that never passed its gates; the .failed file is kept as
        # the manual-recovery artifact pkg_deploy's error text points at.
        if not success and self.tracked and not pkg.skip_tracking:
            self._remove_failed_tracking_artifacts(pkg)

        elapsed = time.monotonic() - build_start
        self.logger.end_package(success)
        self.summary.record(pkg.name, pkg.version, success, elapsed)
        return success

    def _remove_failed_tracking_artifacts(self, pkg):
        """Erase the completion marker (and quarantine the archive) of a
        build that failed after pkg_manifest wrote them.

        The name-version manifest doubles as the --skip-built completion
        marker; leaving it behind after a downstream gate failure converts
        that failure into a silent skip on the next run.
        """
        manifest = self.pkg_db / f"{pkg.name}-{pkg.version}"
        try:
            if manifest.exists():
                manifest.unlink()
                self.logger.error(
                    f"  removed incomplete completion marker {manifest} — "
                    f"the failed package will rebuild on the next run"
                )
        except OSError as e:
            self.logger.error(
                f"  FAILED to remove completion marker {manifest}: {e} — "
                f"--skip-built would wrongly skip this failed package; "
                f"remove the file manually before the next run"
            )
        archive = self.pkg_archives / f"{pkg.name}-{pkg.version}.igos.tar.gz"
        try:
            if archive.exists():
                quarantined = Path(str(archive) + ".failed")
                archive.replace(quarantined)
                self.logger.error(
                    f"  quarantined unverified archive to {quarantined} "
                    f"(kept for manual recovery; excluded from the "
                    f"*.igos.tar.gz ship namespace)"
                )
        except OSError as e:
            self.logger.error(f"  FAILED to quarantine archive {archive}: {e}")

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
        """Best-effort auto-derive verify_paths sidecar for a staged package.

        Derives from the file list pkg_register_pkm_db just recorded:
        pkg_deploy removes the staging tree on success, so by the time the
        builder's success branch runs there is no staging dir left to walk —
        the old walk hit the missing-dir guard and the sidecar silently
        never derived for staged packages. The staging walk remains only as
        a fallback for callers that run before deploy cleanup. Failures are
        non-fatal.
        """
        try:
            file_list = getattr(self, "_last_registered_paths", None)
            if not file_list:
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

    # ------------------------------------------------------------------
    # Per-package progress counters — the SAME line the bash tiers emit.
    #
    # The bash tiers (ch8, core-extra, base, and the unified tier driver)
    # emit these through scripts/lib/logging.sh. The Python tiers — desktop,
    # ai, extra, compute — ran the same builds and said nothing in this
    # shape, so a consumer following a whole build lost the counter exactly
    # when the Python tiers took over. These emit the identical line so one
    # regex reads the entire build.
    #
    # THE LINE, and the documented consumer regex it must satisfy:
    #
    #   progress: package <N> of <M> — <name> (<tier>) — <state>
    #   ^\[[^]]*\] progress: package ([0-9]+) of ([0-9]+) — (\S+) \(([^)]+)\) — (.*)$
    #
    # N is POSITION IN THE PLAN, so a skipped package consumes an index —
    # "12 of 34" answers how far through the plan we are, not how many
    # compiled. M is len(packages): derived from the plan actually being
    # executed, never written down.
    #
    # The pairing is the fail-closed property: every `start` is followed by
    # `done` or `failed rc=<n>` at the same index, so a package that begins
    # and never ends leaves an unmatched `start`. A hang, an OOM kill or a
    # killed unit cannot look like progress.
    #
    # It is NOT routed through BuildLogger.info(), which indents by two
    # spaces — that indent would put the line outside the documented regex.
    # ------------------------------------------------------------------

    def _progress_stream_path(self) -> Path:
        """The aggregated stream, same file the shell library appends to.

        scripts/lib/logging.sh derives it as <IGOS_LOGS>/build-current.log
        and the tier scripts set IGOS_LOGS to this same build/logs dir, so
        both halves of the build land in one tailable file.
        """
        return self.log_dir / "build-current.log"

    def _progress_emit(self, name: str, tier: str, state: str) -> None:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = (
            f"[{stamp}] progress: package {self._progress_index} "
            f"of {self._progress_total} — {name} ({tier}) — {state}"
        )
        # stdout is what the tier wrapper tees into its per-tier log.
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        # The aggregated stream is an ADDITION and must never be able to
        # fail a build: it is a convenience view, the per-tier logs remain
        # the record. A broken/read-only log dir loses the stream line, not
        # the package.
        try:
            with open(self._progress_stream_path(), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def _progress_begin(self, pkg: Package) -> None:
        self._progress_index += 1
        self._progress_emit(pkg.name, pkg.tier, "start")

    def _progress_end(self, pkg: Package, rc: int) -> None:
        state = "done" if rc == 0 else f"failed rc={rc}"
        self._progress_emit(pkg.name, pkg.tier, state)

    def _progress_skip(self, pkg: Package, reason: str) -> None:
        self._progress_index += 1
        self._progress_emit(pkg.name, pkg.tier, f"skipped ({reason})")

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

        # M is the plan's own size, derived here from the order actually
        # being executed. The index counts every package the plan reaches.
        self._progress_total = total
        self._progress_index = 0

        self.logger.info(f"\nStarting build of {total} package(s)...\n")

        for i, pkg in enumerate(packages, 1):
            # Skip packages that have a tracked manifest.
            # Manifest existence means the package passed EVERY tracking gate —
            # archive, deploy, verify, registration, post_install — because
            # build_package removes the manifest whenever any of those gates
            # fails after pkg_manifest wrote it
            # (_remove_failed_tracking_artifacts). Full file re-verification
            # here would false-rebuild packages whose post_install legitimately
            # moves/deletes files after the manifest was written (e.g., Rust
            # removes .old docs and moves bash completions).
            if self.skip_built:
                manifest = self.pkg_db / f"{pkg.name}-{pkg.version}"
                if manifest.exists():
                    # Rebuild if the recipe OR the first-party source content
                    # changed since last build. _compute_template_hash folds
                    # package.yml + build.sh AND generated-tarball/source_tree
                    # content into one fingerprint, so a source-only edit no
                    # longer slips past skip-built (the intergen-welcome
                    # stale-ship class). Shared with the manifest writer so the
                    # marker below always matches what was stamped at build.
                    rebuild_needed = False
                    current_hash = self._compute_template_hash(pkg)
                    if current_hash:
                        # Check if manifest contains our hash marker
                        manifest_text = manifest.read_text()
                        if f"TEMPLATE_HASH: {current_hash}" not in manifest_text:
                            self.logger.info(f"[{i}/{total}] Rebuilding {pkg.name} {pkg.version} (recipe or source changed)")
                            rebuild_needed = True
                    if not rebuild_needed:
                        self.logger.info(f"[{i}/{total}] Skipping {pkg.name} {pkg.version} (already tracked)")
                        # Terminal: a skip consumes an index and never opens a
                        # pair, so it can never be mistaken for a hang.
                        self._progress_skip(pkg, "already tracked")
                        self.summary.record(pkg.name, pkg.version, True, 0, skipped=True)
                        continue

            self.logger.info(f"[{i}/{total}] Building {pkg.name} {pkg.version}...")
            self._progress_begin(pkg)
            success = self.build_package(pkg)
            # build_package reports a boolean, not the underlying exit code, so
            # a failure is reported as rc=1. The word is what a consumer keys
            # on; the real exit code is in the package's own log.
            self._progress_end(pkg, 0 if success else 1)

            if not success:
                all_success = False
                if halt_on_failure:
                    self.logger.error(
                        f"Build halted at {pkg.name} {pkg.version} "
                        f"({i}/{total}). Fix the error and retry."
                    )
                    # Re-surface the failed package's last output AT the halt so the
                    # error is co-located with the halt message — not scrolled away in
                    # a long run, nor stranded only in the chroot-side per-package log.
                    self.logger.echo_failure_tail(pkg.name, pkg.version)
                    break

        self.summary.print_summary()
        return all_success
