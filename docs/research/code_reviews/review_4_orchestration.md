# Code Review Request: InterGenOS Build Orchestration Scripts

I'm requesting a thorough code review of the bash scripts that orchestrate the entire InterGenOS build pipeline. InterGenOS is a Linux distribution built entirely from source following Linux From Scratch (LFS 13.0) and Beyond LFS (BLFS 13.0).

These scripts manage the complete lifecycle of building a bootable Linux distribution from nothing:

- **build-intergenos.sh** — The master orchestrator. Drives 12 sequential phases from host validation through bootable disk image creation. Supports `--start-at` for resuming, `--stop-after` for partial builds, and `--checkpoint` for saving tarball snapshots between phases. Handles signal trapping for clean shutdown.

- **pkg-functions.sh** — The package tracking engine used by the bash-based build scripts. Implements DESTDIR staging (install to a temporary directory, generate manifest, create archive, deploy to live filesystem via tar pipe). This is the Slackware-style tracking system that produces both text manifests and `.igos.tar.gz` archives.

- **chroot-setup.sh / chroot-enter.sh / chroot-teardown.sh** — Chroot lifecycle management. Mounts virtual kernel filesystems, enters the chroot with a sanitized environment, and tears down cleanly.

- **chroot-build-ch8.sh** — Builds ~130 core system packages in LFS Chapter 8 order. Sources each package's `build.sh`, calls `configure()`, `build()`, `check()`, `do_install()` in sequence, then runs package tracking.

- **chroot-build-core-extra.sh / chroot-build-desktop.sh / chroot-build-tier.sh** — Tier-specific build scripts for BLFS packages.

- **create-image.sh** — Creates a bootable QCOW2 disk image from the completed chroot filesystem.

The build runs on an Ubuntu 24.04 KVM virtual machine. The chroot environment has no internet access — all source tarballs and patches are pre-staged.

I would appreciate your assessment of the following areas in particular:

1. **Shell scripting safety** — Variable quoting, word splitting, `set -e` behavior across function calls and subshells. Are there unquoted expansions that could cause failures with spaces or special characters?
2. **Chroot mount/unmount correctness** — Is there any risk of leaked mounts if the build is interrupted? Does the teardown handle partial mount states?
3. **DESTDIR staging and deployment** — The deploy step uses `tar -C staging -cf - . | tar -C / -xf - --keep-directory-symlink`. Is this safe? Could it clobber root-level symlinks?
4. **Checkpoint save/restore** — Checkpoints tear down the chroot, tar the filesystem, then re-mount. Is this reliable? Could a failure during save leave the chroot in a broken state?
5. **CWD reset between build phases** — We recently added `cd "$workdir"` before each phase call to prevent `cd` in one phase from leaking into the next. Is this sufficient, or are there edge cases?
6. **Package tracking correctness** — Manifest generation, file counting, archive creation, deploy verification. Are there race conditions or edge cases?
7. **Signal handling** — The orchestrator traps SIGINT/SIGTERM. Is cleanup adequate?

The complete source follows. There are 10 files totaling approximately 3,000 lines of bash.

---

## Source Code

### build-intergenos.sh
```bash
#!/bin/bash
# ==========================================================================
# InterGenOS Master Build Orchestrator
#
# Drives the entire build from fresh VM to bootable disk image.
# One command, clear progress, full control.
#
# Usage:
#   sudo bash build-intergenos.sh --user <username>
#   sudo bash build-intergenos.sh --user <username> --start-at <phase>
#   sudo bash build-intergenos.sh --user <username> --stop-after <phase>
#   sudo bash build-intergenos.sh --user <username> --checkpoint
#
# Phases (in order):
#   validate     — Verify host meets all build requirements
#   setup        — Create build root, verify sources and patches
#   toolchain    — Cross-compilation toolchain (LFS Chapters 5-6)
#   chroot-prep  — Mount virtual filesystems for chroot (Chapter 7 prep)
#   chroot-tools — Build temporary tools inside chroot (Chapter 7)
#   core         — Build LFS core packages in chroot (Chapter 8)
#   config       — System configuration in chroot (Chapter 9)
#   core-extra   — Build additional core packages in chroot
#   base         — Build base packages in chroot
#   desktop      — Build desktop packages in chroot (GNOME + dependencies)
#   image        — Package chroot into bootable disk image
#
# Controls:
#   --start-at <phase>   Start (or resume) at a specific phase
#   --stop-after <phase> Stop after the named phase completes
#   --checkpoint          Save a tarball after each significant phase
#   touch /mnt/igos/.build-stop   Graceful halt between phases
#   Ctrl+C               Immediate stop (traps SIGINT)
#
# ==========================================================================

set -euo pipefail

# ==========================================================================
# Constants
# ==========================================================================

IGOS=/mnt/igos
IGOS_TARGET=x86_64-igos-linux-gnu
SCRIPTS=/mnt/intergenos/scripts
SOURCES=/mnt/intergenos/build/sources
PATCHES=/mnt/intergenos/build/patches
LOGS=/mnt/intergenos/build/logs
PHASE_FILE="${LOGS}/.build-phase"
STOP_FILE="${IGOS}/.build-stop"
CHECKPOINT_DIR="/home"
BUILD_LOG="${LOGS}/build-intergenos-$(date '+%Y%m%d-%H%M%S').log"

PHASES=(
    validate
    setup
    toolchain
    chroot-prep
    chroot-tools
    core
    config
    core-extra
    base
    desktop
    image
)

# ==========================================================================
# Argument parsing
# ==========================================================================

BUILD_USER=""
START_AT=""
STOP_AFTER=""
CHECKPOINT=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user)
            BUILD_USER="$2"
            shift 2
            ;;
        --start-at)
            START_AT="$2"
            shift 2
            ;;
        --stop-after)
            STOP_AFTER="$2"
            shift 2
            ;;
        --checkpoint)
            CHECKPOINT=true
            shift
            ;;
        -h|--help)
            head -30 "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: sudo bash $0 --user <username> [--start-at <phase>] [--stop-after <phase>]"
            exit 1
            ;;
    esac
done

if [ -z "$BUILD_USER" ]; then
    echo "Error: --user <username> is required"
    echo "Usage: sudo bash $0 --user <username> [--start-at <phase>] [--stop-after <phase>]"
    exit 1
fi

# Verify running as root (needed for chroot phases)
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: must run as root (use sudo)"
    exit 1
fi

# Verify build user exists
if ! id "$BUILD_USER" > /dev/null 2>&1; then
    echo "Error: user '$BUILD_USER' does not exist"
    exit 1
fi

# Validate --start-at and --stop-after are real phase names
validate_phase_name() {
    local name="$1"
    local label="$2"
    if [ -n "$name" ]; then
        local found=false
        for p in "${PHASES[@]}"; do
            if [ "$p" = "$name" ]; then
                found=true
                break
            fi
        done
        if ! $found; then
            echo "Error: unknown phase '$name' for $label"
            echo "Valid phases: ${PHASES[*]}"
            exit 1
        fi
    fi
}

validate_phase_name "$START_AT" "--start-at"
validate_phase_name "$STOP_AFTER" "--stop-after"

# ==========================================================================
# Logging
# ==========================================================================

mkdir -p "$LOGS"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$BUILD_LOG"
}

# ==========================================================================
# Checkpoint support
# ==========================================================================

save_checkpoint() {
    local phase="$1"
    local checkpoint="${CHECKPOINT_DIR}/${BUILD_USER}/intergenos-${phase}-$(date '+%Y%m%d-%H%M%S').tar.gz"

    log ""
    log ">>> Saving checkpoint: $checkpoint"

    mkdir -p "$(dirname "$checkpoint")"

    # Tear down chroot mounts temporarily for a clean snapshot
    bash "${SCRIPTS}/chroot-teardown.sh" > /dev/null 2>&1 || true

    local start_time=$(date +%s)
    tar -C "$IGOS" --one-file-system -czf "$checkpoint" . 2>&1

    local elapsed=$(( $(date +%s) - start_time ))
    local size=$(du -h "$checkpoint" | cut -f1)

    log ">>> Checkpoint saved: $size in ${elapsed}s"
    log ">>> Restore with: rm -rf ${IGOS}/* && tar -C ${IGOS} -xzf ${checkpoint}"

    # Re-mount chroot filesystems
    bash "${SCRIPTS}/chroot-setup.sh" > /dev/null 2>&1 || true
}

# ==========================================================================
# Signal handling
# ==========================================================================

CURRENT_PHASE=""

cleanup() {
    log ""
    log "!!! Build interrupted during phase: ${CURRENT_PHASE:-none}"
    log "!!! Resume with: sudo bash $0 --user $BUILD_USER --start-at ${CURRENT_PHASE:-validate}"
    log ""
    exit 130
}

trap cleanup SIGINT SIGTERM

# ==========================================================================
# Phase runner
# ==========================================================================

SKIPPING=true
if [ -z "$START_AT" ]; then
    SKIPPING=false
fi

run_phase() {
    local phase="$1"
    local description="$2"
    shift 2
    # remaining args are the function/command to run

    # Handle --start-at
    if $SKIPPING; then
        if [ "$phase" = "$START_AT" ]; then
            SKIPPING=false
        else
            log "[SKIP ] $phase — $description"
            return 0
        fi
    fi

    # Check for graceful stop request
    if [ -f "$STOP_FILE" ]; then
        rm -f "$STOP_FILE"
        log ""
        log ">>> Stop requested (found $STOP_FILE)"
        log ">>> Stopped before phase: $phase"
        log ">>> Resume with: sudo bash $0 --user $BUILD_USER --start-at $phase"
        log ""
        exit 0
    fi

    CURRENT_PHASE="$phase"
    local start_time=$(date +%s)

    log ""
    log "================================================================"
    log "  PHASE: $phase — $description"
    log "  Started: $(date)"
    log "================================================================"
    log ""

    # Record current phase
    echo "$phase" > "$PHASE_FILE"

    # Run the phase
    "$@"
    local rc=$?

    local elapsed=$(( $(date +%s) - start_time ))
    local minutes=$(( elapsed / 60 ))
    local seconds=$(( elapsed % 60 ))

    if [ $rc -ne 0 ]; then
        log ""
        log "!!! PHASE FAILED: $phase ($description)"
        log "!!! Exit code: $rc"
        log "!!! Elapsed: ${minutes}m ${seconds}s"
        log "!!! Resume with: sudo bash $0 --user $BUILD_USER --start-at $phase"
        log ""
        exit $rc
    fi

    log ""
    log "[DONE ] $phase — ${minutes}m ${seconds}s"

    # Save checkpoint after significant phases
    if $CHECKPOINT; then
        case "$phase" in
            toolchain|core|kernel|desktop)
                save_checkpoint "$phase"
                ;;
        esac
    fi

    # Handle --stop-after
    if [ "$phase" = "$STOP_AFTER" ]; then
        log ""
        log ">>> Stopping after phase: $phase (--stop-after)"
        local next_idx=0
        for i in "${!PHASES[@]}"; do
            if [ "${PHASES[$i]}" = "$phase" ]; then
                next_idx=$((i + 1))
                break
            fi
        done
        if [ $next_idx -lt ${#PHASES[@]} ]; then
            log ">>> Resume with: sudo bash $0 --user $BUILD_USER --start-at ${PHASES[$next_idx]}"
        fi
        log ""
        exit 0
    fi
}

# ==========================================================================
# Phase implementations
# ==========================================================================

phase_validate() {
    # LFS 13.0 requires /bin/sh -> bash (Ubuntu defaults to dash)
    if [ "$(readlink -f /bin/sh)" != "/usr/bin/bash" ]; then
        log "  /bin/sh does not point to bash — fixing..."
        ln -sf /usr/bin/bash /bin/sh
        log "  /bin/sh -> bash"
    fi

    log "Running host requirements check..."
    python3 "${SCRIPTS}/host-check.py"
}

phase_setup() {
    # Create build root
    if [ ! -d "$IGOS" ]; then
        mkdir -p "$IGOS"
    fi
    chown "${BUILD_USER}:${BUILD_USER}" "$IGOS"
    chmod 755 "$IGOS"
    log "  /mnt/igos owned by $BUILD_USER"

    # Create LFS directory layout (Section 4.2)
    # These directories and symlinks must exist before the toolchain build
    mkdir -pv "$IGOS"/{etc,var} "$IGOS"/usr/{bin,lib,sbin}
    for i in bin lib sbin; do
        if [ ! -L "$IGOS/$i" ]; then
            ln -sv "usr/$i" "$IGOS/$i"
        fi
    done
    case $(uname -m) in
        x86_64) mkdir -pv "$IGOS/lib64" ;;
    esac
    # Tools directory for cross-toolchain
    mkdir -pv "$IGOS/tools"
    chown -R "${BUILD_USER}:${BUILD_USER}" "$IGOS"
    log "  LFS directory layout created (Section 4.2)"

    # Verify virtiofs
    if ! mount | grep -q "intergenos.*virtiofs"; then
        log "ERROR: /mnt/intergenos not mounted via virtiofs"
        return 1
    fi
    log "  virtiofs mount OK"

    # Verify critical sources exist
    local missing=0
    for src in binutils-2.46.0.tar.xz gcc-15.2.0.tar.xz glibc-2.43.tar.xz \
               linux-6.18.10.tar.xz gmp-6.3.0.tar.xz mpfr-4.2.2.tar.xz mpc-1.3.1.tar.gz; do
        if [ ! -f "${SOURCES}/$src" ]; then
            log "  MISSING: $src"
            missing=$((missing + 1))
        fi
    done
    if [ $missing -gt 0 ]; then
        log "ERROR: $missing critical source tarballs missing from $SOURCES"
        return 1
    fi

    local total=$(ls "$SOURCES" | wc -l)
    log "  Sources: $total tarballs on host"

    # Verify patches
    if [ ! -f "${PATCHES}/glibc-fhs-1.patch" ]; then
        log "ERROR: glibc-fhs-1.patch missing from $PATCHES"
        return 1
    fi
    log "  Patches: OK"

    # --- Place everything directly on the target filesystem ---
    # Like build_003: no bind mounts, no tricks. The chroot is self-contained.
    # Everything the chroot needs is physically present on $IGOS.

    # Copy source tarballs (LFS Section 3.1)
    log "  Copying sources to $IGOS/sources/..."
    mkdir -pv "$IGOS/sources"
    chmod -v a+wt "$IGOS/sources"
    cp -n "${SOURCES}"/* "$IGOS/sources/" 2>/dev/null || true
    cp -n "${PATCHES}"/* "$IGOS/sources/" 2>/dev/null || true
    local placed=$(ls "$IGOS/sources" | wc -l)
    log "  Placed $placed files in $IGOS/sources/"

    # Copy build infrastructure (scripts, packages, igos-build)
    # Preserves paths so /mnt/intergenos/scripts/... works inside the chroot
    log "  Copying build infrastructure to $IGOS/mnt/intergenos/..."
    mkdir -pv "$IGOS/mnt/intergenos"
    cp -a /mnt/intergenos/scripts    "$IGOS/mnt/intergenos/"
    cp -a /mnt/intergenos/packages   "$IGOS/mnt/intergenos/"
    cp -a /mnt/intergenos/igos-build "$IGOS/mnt/intergenos/"
    cp    /mnt/intergenos/igos-build.py "$IGOS/mnt/intergenos/" 2>/dev/null || true
    log "  Build infrastructure placed on target filesystem"

    chown -R "${BUILD_USER}:${BUILD_USER}" "$IGOS"
    log "  Build root: $IGOS ready (self-contained)"
}

phase_toolchain() {
    # Toolchain must run as the build user, NOT root
    # env -i wipes ALL host variables (LFS 13.0 Section 4.4 requirement)
    # Only HOME, TERM, and PATH survive — prevents host CFLAGS, LD_LIBRARY_PATH, etc.
    # from contaminating the cross-compilation
    log "Running cross-toolchain build as $BUILD_USER (Ch 5)..."
    su - "$BUILD_USER" -c "env -i HOME=/home/${BUILD_USER} TERM=${TERM} bash ${SCRIPTS}/toolchain-build.sh" 2>&1 | tee -a "$BUILD_LOG"
    # Check if toolchain produced the expected output
    if [ ! -x "${IGOS}/tools/bin/${IGOS_TARGET}-gcc" ]; then
        log "ERROR: Toolchain build did not produce ${IGOS_TARGET}-gcc"
        return 1
    fi
    log "  Cross-toolchain verified: ${IGOS_TARGET}-gcc exists"

    # Temp tools (Ch 6) — cross-compiled utilities needed inside the chroot
    log "Running temp-tools build as $BUILD_USER (Ch 6)..."
    su - "$BUILD_USER" -c "env -i HOME=/home/${BUILD_USER} TERM=${TERM} bash ${SCRIPTS}/temp-tools-build.sh" 2>&1 | tee -a "$BUILD_LOG"
    # Verify coreutils installed (env is needed for chroot entry)
    if [ ! -x "${IGOS}/usr/bin/env" ]; then
        log "ERROR: Temp-tools build did not produce /usr/bin/env (coreutils)"
        return 1
    fi
    log "  Temp-tools verified: /usr/bin/env exists"
}

phase_chroot_prep() {
    log "Setting up chroot environment..."
    bash "${SCRIPTS}/chroot-setup.sh" 2>&1 | tee -a "$BUILD_LOG"

    # Verify mounts
    if ! mountpoint -q "${IGOS}/dev"; then
        log "ERROR: ${IGOS}/dev not mounted"
        return 1
    fi
    log "  Chroot mounts verified"
}

phase_chroot_tools() {
    log "Building temporary tools in chroot..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build.sh" 2>&1 | tee -a "$BUILD_LOG"
}

sync_chroot_scripts() {
    # Sync scripts and packages into the chroot copy.
    # The setup phase copies build infrastructure to $IGOS/mnt/intergenos/,
    # but --start-at skips setup and code changes between restarts aren't
    # reflected. This ensures the chroot always has the latest.
    log "  Syncing scripts into chroot..."
    rsync -a /mnt/intergenos/scripts/   "$IGOS/mnt/intergenos/scripts/"
    rsync -a /mnt/intergenos/packages/  "$IGOS/mnt/intergenos/packages/"
    rsync -a /mnt/intergenos/config/    "$IGOS/mnt/intergenos/config/" 2>/dev/null || true
    # Sync Python builder for desktop tier
    rsync -a /mnt/intergenos/igos-build.py "$IGOS/mnt/intergenos/" 2>/dev/null || true
    rsync -a /mnt/intergenos/igos-build/   "$IGOS/mnt/intergenos/igos-build/" 2>/dev/null || true
}

phase_core() {
    sync_chroot_scripts
    log "Building core system in chroot (Ch 8, LFS order)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-ch8.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_config() {
    # Clear IGOS_START_AT so it doesn't leak from core restarts
    # into subsequent phases (config, core-extra, kernel)
    unset IGOS_START_AT
    sync_chroot_scripts
    log "Configuring system in chroot (Ch 9)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-config-ch9.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_core_extra() {
    sync_chroot_scripts
    log "Building additional core packages in chroot (BLFS)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-core-extra.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_kernel() {
    sync_chroot_scripts
    log "Building kernel in chroot (Ch 10)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-ch10.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_desktop() {
    sync_chroot_scripts
    log "Building desktop packages in chroot (GNOME + dependencies)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-desktop.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_extra() {
    sync_chroot_scripts
    log "Building extra tier packages in chroot (user applications)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-extra.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_image() {
    log "Packaging chroot into bootable disk image..."

    # Tear down chroot mounts before imaging
    log "  Tearing down chroot mounts..."
    bash "${SCRIPTS}/chroot-teardown.sh" 2>&1 | tee -a "$BUILD_LOG" || true

    # Clean up build artifacts from the target filesystem
    # These were placed during setup — the built system doesn't need them
    log "  Cleaning build artifacts from target..."
    rm -rf "${IGOS}/mnt/intergenos"
    rm -rf "${IGOS}/sources"
    rm -rf "${IGOS}/tmp"/*
    mkdir -p "${IGOS}/tmp"
    chmod 1777 "${IGOS}/tmp"
    log "  Build artifacts removed"

    # Create the image on VM local storage (fast)
    local image_path="/home/${BUILD_USER}/intergenos.qcow2"
    bash "${SCRIPTS}/create-image.sh" "$image_path" 500G 2>&1 | tee -a "$BUILD_LOG"

    log ""
    log "  Disk image created at: $image_path (on build VM)"
    log ""
    log "  To use it, copy to the host and create a VM:"
    log "    scp ${BUILD_USER}@<vm-ip>:${image_path} /mnt/intergenos/vm/intergenos.qcow2"
    log "    See create-image.sh output above for virt-install command."
}

# ==========================================================================
# Main — run all phases
# ==========================================================================

BUILD_START=$(date +%s)

log ""
log "================================================================"
log "  InterGenOS Build"
log "  User: $BUILD_USER"
log "  Target: $IGOS"
log "  Started: $(date)"
if [ -n "$START_AT" ]; then
    log "  Starting at: $START_AT"
fi
if [ -n "$STOP_AFTER" ]; then
    log "  Stopping after: $STOP_AFTER"
fi
if $CHECKPOINT; then
    log "  Checkpoints: enabled (saving to ${CHECKPOINT_DIR}/${BUILD_USER}/)"
fi
log "================================================================"

run_phase "validate"     "Verify host requirements"            phase_validate
run_phase "setup"        "Create build environment"            phase_setup
run_phase "toolchain"    "Cross-compilation toolchain (Ch 5-6)" phase_toolchain
run_phase "chroot-prep"  "Prepare chroot environment (Ch 7)"   phase_chroot_prep
run_phase "chroot-tools" "Build temp tools in chroot (Ch 7)"   phase_chroot_tools
run_phase "core"         "Build core system (Ch 8, LFS order)" phase_core
run_phase "config"       "System configuration (Ch 9)"         phase_config
run_phase "core-extra"   "Build extra core packages (BLFS)"    phase_core_extra
run_phase "kernel"       "Build kernel (Ch 10)"                phase_kernel
run_phase "desktop"     "Build desktop (GNOME on Wayland)"    phase_desktop
run_phase "extra"       "Build extra tier (applications)"     phase_extra
run_phase "image"       "Package bootable disk image"         phase_image

# ==========================================================================
# Done
# ==========================================================================

BUILD_ELAPSED=$(( $(date +%s) - BUILD_START ))
BUILD_HOURS=$(( BUILD_ELAPSED / 3600 ))
BUILD_MINUTES=$(( (BUILD_ELAPSED % 3600) / 60 ))

log ""
log "================================================================"
log "  InterGenOS Build Complete"
log "  Total time: ${BUILD_HOURS}h ${BUILD_MINUTES}m"
log "  Finished: $(date)"
log "================================================================"
log ""
```

### pkg-functions.sh
```bash
#!/bin/bash
# InterGenOS Package Functions — DESTDIR Staging + Slackware-style Tracking
#
# Sourced by the Chapter 8 build runner. Provides functions to:
#   1. Stage a package's installed files via DESTDIR
#   2. Generate a file manifest
#   3. Create a compressed archive (.igos.tar.gz)
#   4. Deploy staged files to the live filesystem
#   5. Run post-install hooks on the live system
#
# Database: /var/lib/igos/packages/<name>-<version>  (one text file per package)
# Archives: /var/lib/igos/archives/<name>-<version>.igos.tar.gz
#
# Design: Slackware-style manifests — human-readable, cat-inspectable,
# no binary database, no dependency resolution at install time.
# The build system handles build order; this layer tracks installed state.

# ============================================================================
# Configuration
# ============================================================================

IGOS_PKG_DB="/var/lib/igos/packages"
IGOS_PKG_ARCHIVES="/var/lib/igos/archives"
IGOS_PKG_STAGING="/tmp/igos-staging"

# ============================================================================
# Internal helpers
# ============================================================================

pkg_log() {
    echo "[pkg] $*" | tee -a "$IGOS_LOGS/pkg-install.log"
}

pkg_error() {
    echo "[pkg] ERROR: $*" | tee -a "$IGOS_LOGS/pkg-install.log" >&2
}

# ============================================================================
# pkg_init — Create database and archive directories
# ============================================================================

pkg_init() {
    mkdir -pv "$IGOS_PKG_DB"
    mkdir -pv "$IGOS_PKG_ARCHIVES"
    mkdir -pv "$IGOS_PKG_STAGING"
}

# ============================================================================
# pkg_stage — Run install() with DESTDIR pointing to a staging directory
#
# Usage: pkg_stage <name> <version>
#
# Expects:
#   - CWD is the package build directory
#   - An install() function is defined (from the package's build.sh)
#   - Or a pkg_custom_install() function for exception packages
#
# Sets: PKG_DEST (the staging root for this package)
# ============================================================================

pkg_stage() {
    local name="$1"
    local version="$2"

    export PKG_DEST="${IGOS_PKG_STAGING}/${name}-${version}"

    # Clean any prior staging attempt
    rm -rf "$PKG_DEST"
    mkdir -pv "$PKG_DEST"

    # Mirror root-level symlinks so DESTDIR installs follow them.
    # Without this, `make install DESTDIR=...` creates real /lib, /bin, /sbin
    # directories that collide with the root filesystem's symlinks.
    for link in bin lib sbin; do
        if [ -L "/$link" ]; then
            ln -sv "usr/$link" "${PKG_DEST}/$link"
        fi
    done
    case $(uname -m) in
        x86_64) mkdir -pv "${PKG_DEST}/lib64" ;;
    esac
    mkdir -pv "${PKG_DEST}/usr/"{bin,lib,sbin}

    # Export DESTDIR for autotools/meson packages
    export DESTDIR="$PKG_DEST"

    pkg_log "Staging ${name}-${version} to ${PKG_DEST}"

    # Run the package's do_install function
    # Named do_install (not install) to avoid collision with /usr/bin/install.
    # Output appends to the most recent build log for this package so all
    # output is in one place. Falls back to a standalone install log.
    local install_log
    install_log=$(ls -t "${IGOS_LOGS}/${name}-"*".log" 2>/dev/null | head -1)
    if [ -z "$install_log" ]; then
        install_log="${IGOS_LOGS}/${name}-install-$(date '+%Y%m%d-%H%M%S').log"
    fi

    if declare -f do_install > /dev/null 2>&1; then
        echo "=== [INSTALL] $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$install_log"
        do_install >> "$install_log" 2>&1
    else
        pkg_error "No do_install() function defined for ${name}"
        return 1
    fi

    local rc=$?
    if [ $rc -ne 0 ]; then
        pkg_error "Staging failed for ${name}-${version} (exit $rc)"
        return 1
    fi

    # Verify something was actually staged
    local file_count
    file_count=$(find "$PKG_DEST" -not -type d | wc -l)
    if [ "$file_count" -eq 0 ]; then
        pkg_error "Staging produced no files for ${name}-${version}"
        pkg_error "Check that do_install() uses \$DESTDIR or the correct staging variable"
        return 1
    fi

    pkg_log "Staged ${file_count} files for ${name}-${version}"

    # Unset DESTDIR so it doesn't leak into post-install steps
    unset DESTDIR

    return 0
}

# ============================================================================
# pkg_manifest — Generate a Slackware-style manifest from staged files
#
# Usage: pkg_manifest <name> <version> [description]
#
# Writes: $IGOS_PKG_DB/<name>-<version>
# ============================================================================

pkg_manifest() {
    local name="$1"
    local version="$2"
    local description="${3:-No description}"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"
    local manifest="${IGOS_PKG_DB}/${name}-${version}"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    # Calculate sizes
    local uncompressed_size
    uncompressed_size=$(du -sb "$dest" | cut -f1)
    local uncompressed_human
    uncompressed_human=$(du -sh "$dest" | cut -f1)

    # Generate file list — paths relative to staging root, sorted
    # Directories listed with trailing /
    local file_list
    file_list=$(cd "$dest" && find . -mindepth 1 | sed 's|^\./||' | sort)

    # Write the manifest
    cat > "$manifest" << EOF
PACKAGE NAME: ${name}-${version}
PACKAGE VERSION: ${version}
UNCOMPRESSED SIZE: ${uncompressed_human} (${uncompressed_size} bytes)
BUILD DATE: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
BUILD SYSTEM: InterGenOS LFS 13.0
DESCRIPTION:
${name}: ${description}

FILE LIST:
${file_list}
EOF

    pkg_log "Manifest written: ${manifest} ($(echo "$file_list" | wc -l) entries)"
    return 0
}

# ============================================================================
# pkg_archive — Create a .igos.tar.gz archive from staged files
#
# Usage: pkg_archive <name> <version>
#
# Creates: $IGOS_PKG_ARCHIVES/<name>-<version>.igos.tar.gz
#
# Uses gzip during initial build (available from Chapter 7).
# Archives can be re-compressed to zstd later if desired.
# ============================================================================

pkg_archive() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"
    local archive="${IGOS_PKG_ARCHIVES}/${name}-${version}.igos.tar.gz"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    # Create the archive — rooted at the staging directory so paths are relative
    # This means extracting to / will put files in the right place
    tar -C "$dest" -czf "$archive" .

    local rc=$?
    if [ $rc -ne 0 ]; then
        pkg_error "Archive creation failed for ${name}-${version}"
        return 1
    fi

    local archive_size
    archive_size=$(du -sh "$archive" | cut -f1)
    pkg_log "Archive created: ${archive} (${archive_size})"

    # Update manifest with compressed size
    local manifest="${IGOS_PKG_DB}/${name}-${version}"
    if [ -f "$manifest" ]; then
        local compressed_bytes
        compressed_bytes=$(stat -c%s "$archive")
        sed -i "/^BUILD DATE:/i COMPRESSED SIZE: ${archive_size} (${compressed_bytes} bytes)" "$manifest"
    fi

    return 0
}

# ============================================================================
# pkg_deploy — Copy staged files to the live filesystem
#
# Usage: pkg_deploy <name> <version>
#
# Copies everything from the staging directory to /
# Preserves permissions, ownership, and symlinks
#
# Safety: pre-checks for top-level entries that would collide with root-level
# symlinks (lib -> usr/lib, bin -> usr/bin, etc.). A package staging a real
# directory over one of these symlinks would kill the system.
# ============================================================================

pkg_deploy() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    # Pre-deploy safety check: detect staging entries that would collide with
    # root-level symlinks. These symlinks (lib -> usr/lib, bin -> usr/bin, etc.)
    # are load-bearing — replacing them with real directories is catastrophic.
    local dangerous=""
    for entry in lib lib64 bin sbin; do
        if [ -d "${dest}/${entry}" ] && [ ! -L "${dest}/${entry}" ] && [ -L "/${entry}" ]; then
            dangerous="${dangerous} ${entry}"
        fi
    done

    if [ -n "$dangerous" ]; then
        pkg_error "DANGEROUS: ${name}-${version} staging contains top-level dirs" \
                  "that would collide with root symlinks:${dangerous}"
        pkg_error "Fix the package build.sh to install to usr/ paths instead"
        return 1
    fi

    pkg_log "Deploying ${name}-${version} to live filesystem"

    # Use tar for deployment:
    # --no-overwrite-dir    preserves metadata of existing real directories
    # --keep-directory-symlink  follows existing symlinks to directories instead
    #                           of replacing them (e.g., /var/run -> /run)
    tar -C "${dest}" -cf - . \
        | tar -C / -xf - --no-overwrite-dir --keep-directory-symlink

    local rc=$?
    if [ $rc -ne 0 ]; then
        pkg_error "Deploy failed for ${name}-${version}"
        return 1
    fi

    pkg_log "Deployed ${name}-${version}"
    return 0
}

# ============================================================================
# pkg_cleanup — Remove staging directory after successful install
#
# Usage: pkg_cleanup <name> <version>
# ============================================================================

pkg_cleanup() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"

    rm -rf "$dest"
}

# ============================================================================
# pkg_install — Full pipeline: stage -> manifest -> archive -> deploy -> cleanup
#
# Usage: pkg_install <name> <version> [description]
#
# This is the main entry point called by the build runner after
# configure/build/check have completed.
# ============================================================================

pkg_install() {
    local name="$1"
    local version="$2"
    local description="${3:-No description}"

    pkg_log "=========================================="
    pkg_log "Installing package: ${name}-${version}"
    pkg_log "=========================================="

    local start
    start=$(date +%s)

    # Ensure database directories exist
    pkg_init

    # Stage
    pkg_stage "$name" "$version" || return 1

    # Generate manifest
    pkg_manifest "$name" "$version" "$description" || return 1

    # Create archive
    pkg_archive "$name" "$version" || return 1

    # Deploy to live filesystem
    pkg_deploy "$name" "$version" || return 1

    # Clean up staging directory
    pkg_cleanup "$name" "$version"

    local elapsed=$(( $(date +%s) - start ))
    pkg_log "Package ${name}-${version} installed successfully (${elapsed}s)"
    pkg_log ""

    return 0
}

# ============================================================================
# pkg_info — Display information about an installed package
#
# Usage: pkg_info <name>-<version>
#    or: pkg_info (no args — list all installed packages)
# ============================================================================

pkg_info() {
    if [ -z "$1" ]; then
        # List all installed packages
        if [ -d "$IGOS_PKG_DB" ]; then
            for manifest in "$IGOS_PKG_DB"/*; do
                [ -f "$manifest" ] || continue
                local pkg_name pkg_version
                pkg_name=$(grep "^PACKAGE NAME:" "$manifest" | cut -d: -f2- | tr -d ' ')
                pkg_version=$(grep "^PACKAGE VERSION:" "$manifest" | cut -d: -f2- | tr -d ' ')
                local desc
                desc=$(grep "^${pkg_name%%"-$pkg_version"}:" "$manifest" | head -1)
                echo "${pkg_name}  ${desc:+— $desc}"
            done
        else
            echo "No packages installed."
        fi
    else
        # Show specific package
        local manifest="${IGOS_PKG_DB}/$1"
        if [ -f "$manifest" ]; then
            cat "$manifest"
        else
            echo "Package $1 is not installed."
            return 1
        fi
    fi
}

# ============================================================================
# pkg_files — List files owned by an installed package
#
# Usage: pkg_files <name>-<version>
# ============================================================================

pkg_files() {
    local manifest="${IGOS_PKG_DB}/$1"
    if [ ! -f "$manifest" ]; then
        echo "Package $1 is not installed."
        return 1
    fi

    # Extract file list (everything after "FILE LIST:" line)
    sed -n '/^FILE LIST:$/,$ { /^FILE LIST:$/d; p }' "$manifest"
}

# ============================================================================
# pkg_owner — Find which package owns a file
#
# Usage: pkg_owner /usr/bin/gcc
# ============================================================================

pkg_owner() {
    local target="$1"

    # Strip leading / for comparison against manifest paths
    target="${target#/}"

    if [ -d "$IGOS_PKG_DB" ]; then
        for manifest in "$IGOS_PKG_DB"/*; do
            [ -f "$manifest" ] || continue
            if sed -n '/^FILE LIST:$/,$ p' "$manifest" | grep -qx "$target"; then
                basename "$manifest"
            fi
        done
    fi
}

# ============================================================================
# pkg_remove — Remove an installed package
#
# Usage: pkg_remove <name>-<version>
#
# Removes all files owned by the package (in reverse order so dirs come last),
# then removes the manifest. Does NOT remove the archive.
# ============================================================================

pkg_remove() {
    local pkg="$1"
    local manifest="${IGOS_PKG_DB}/${pkg}"

    if [ ! -f "$manifest" ]; then
        pkg_error "Package ${pkg} is not installed."
        return 1
    fi

    pkg_log "Removing package: ${pkg}"

    # Get file list, reverse sorted (files before their parent directories)
    local files
    files=$(pkg_files "$pkg" | sort -r)

    local removed=0
    local skipped=0

    while IFS= read -r file; do
        [ -z "$file" ] && continue
        local fullpath="/${file}"

        if [ -d "$fullpath" ] && [ ! -L "$fullpath" ]; then
            # Only remove directory if empty
            rmdir "$fullpath" 2>/dev/null && removed=$((removed+1))
        elif [ -e "$fullpath" ] || [ -L "$fullpath" ]; then
            rm -f "$fullpath" && removed=$((removed+1))
        else
            skipped=$((skipped+1))
        fi
    done <<< "$files"

    # Remove the manifest
    rm -f "$manifest"

    pkg_log "Removed ${pkg}: ${removed} files/dirs removed, ${skipped} already absent"
    return 0
}
```

### chroot-setup.sh
```bash
#!/bin/bash
# InterGenOS Chroot Setup — LFS 13.0 Sections 7.2-7.3
#
# Runs as ROOT on the HOST (build VM), NOT inside the chroot.
# Prepares the target system for chroot entry:
#   1. Changes ownership from build user to root
#   2. Creates virtual kernel filesystem mount points
#   3. Mounts /dev, /dev/pts, /proc, /sys, /run, /dev/shm
#
# Usage (as root on build VM):
#   sudo bash /mnt/intergenos/scripts/chroot-setup.sh
#
# After this, use chroot-enter.sh to enter the chroot.

set -e

IGOS=/mnt/igos

echo "InterGenOS Chroot Setup"
echo "======================="
echo "Target: $IGOS"
echo ""

# Verify we're root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root."
    exit 1
fi

# Check system state and inform the user
if [ ! -d "$IGOS/usr/bin" ]; then
    echo "ERROR: $IGOS/usr/bin not found. The target system doesn't appear to be built."
    echo "       Build the toolchain (Chapters 5-6) before running this script."
    exit 1
fi

if [ -d "$IGOS/tools" ]; then
    echo "NOTE: /tools directory exists — the cross-toolchain is still present."
    echo "      This is expected if you haven't completed Chapter 7 cleanup yet."
else
    echo "NOTE: /tools directory is gone — Chapter 7 cleanup has been completed."
    echo "      This is the expected state for Chapter 8 builds."
fi

# --- 7.2: Changing Ownership ---
echo "--- Changing ownership to root ---"
chown -R root:root $IGOS/{usr,var,etc,tools} 2>/dev/null || true
case $(uname -m) in
    x86_64) chown -R root:root $IGOS/lib64 2>/dev/null || true ;;
esac
echo "  Done"

# --- 7.3: Preparing Virtual Kernel File Systems ---
echo "--- Creating virtual filesystem mount points ---"
mkdir -pv $IGOS/{dev,proc,sys,run}

# --- 7.3.1: Mounting and Populating /dev ---
echo "--- Mounting /dev (bind mount from host) ---"
if ! mountpoint -q $IGOS/dev; then
    mount -v --bind /dev $IGOS/dev
else
    echo "  Already mounted"
fi

# --- 7.3.2: Mounting Virtual Kernel File Systems ---
echo "--- Mounting /dev/pts ---"
if ! mountpoint -q $IGOS/dev/pts; then
    mount -vt devpts devpts -o gid=5,mode=0620 $IGOS/dev/pts
else
    echo "  Already mounted"
fi

echo "--- Mounting /proc ---"
if ! mountpoint -q $IGOS/proc; then
    mount -vt proc proc $IGOS/proc
else
    echo "  Already mounted"
fi

echo "--- Mounting /sys ---"
if ! mountpoint -q $IGOS/sys; then
    mount -vt sysfs sysfs $IGOS/sys
else
    echo "  Already mounted"
fi

echo "--- Mounting /run ---"
if ! mountpoint -q $IGOS/run; then
    mount -vt tmpfs tmpfs $IGOS/run
else
    echo "  Already mounted"
fi

# Handle /dev/shm — may be a symlink or mount point depending on host
echo "--- Setting up /dev/shm ---"
if [ -h $IGOS/dev/shm ]; then
    install -v -d -m 1777 $IGOS$(realpath /dev/shm)
else
    if ! mountpoint -q $IGOS/dev/shm; then
        mount -vt tmpfs -o nosuid,nodev tmpfs $IGOS/dev/shm
    else
        echo "  Already mounted"
    fi
fi

# --- Timezone: match host ---
echo "--- Syncing host timezone into chroot ---"
if [ -f /etc/localtime ]; then
    # Copy as a regular file (not symlink) so it works even before
    # glibc installs /usr/share/zoneinfo/ in the chroot
    cp -fL /etc/localtime $IGOS/etc/localtime
    echo "  Copied host /etc/localtime"
    # Also store the timezone name so glibc post_install can use it
    if [ -f /etc/timezone ]; then
        cp -f /etc/timezone $IGOS/etc/timezone
        echo "  Copied host /etc/timezone ($(cat /etc/timezone))"
    fi
else
    echo "  WARNING: /etc/localtime not found on host, chroot will use UTC"
fi

echo ""
echo "======================="
echo "Chroot environment ready."
echo ""
echo "To enter:  sudo bash /mnt/intergenos/scripts/chroot-enter.sh"
echo "To build:  sudo bash /mnt/intergenos/scripts/chroot-enter.sh /mnt/intergenos/scripts/chroot-build.sh"
echo "To clean:  sudo bash /mnt/intergenos/scripts/chroot-teardown.sh"
```

### chroot-enter.sh
```bash
#!/bin/bash
# InterGenOS Chroot Entry — "Drop In"
# LFS 13.0 Section 7.4
#
# Enters the chroot environment with a clean, controlled environment.
# Can be used interactively or to run a script inside the chroot.
#
# Usage (as root on build VM):
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh                    # interactive
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh /path/to/script    # run script inside
#
# The script path must be accessible from INSIDE the chroot.
# For our build scripts, they're on the virtiofs mount, so use:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh /mnt/intergenos/scripts/chroot-build.sh

IGOS=/mnt/igos

# Verify we're root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root."
    exit 1
fi

# Verify virtual filesystems are mounted
if ! mountpoint -q $IGOS/dev; then
    echo "ERROR: Virtual filesystems not mounted. Run chroot-setup.sh first."
    exit 1
fi

# Determine what to run inside the chroot
if [ -n "$1" ]; then
    # Run a specific script inside the chroot (pass all arguments through)
    CHROOT_CMD="/bin/bash $*"
else
    # Interactive shell
    CHROOT_CMD="/bin/bash --login"
fi

# Number of cores for parallel builds
JOBS=$(nproc)

# Capture host timezone before entering chroot
HOST_TZ="$(cat /etc/timezone 2>/dev/null || echo UTC)"

# Enter the chroot with a clean environment
# env -i clears ALL host environment variables
# Only HOME, TERM, TZ, PS1, PATH, MAKEFLAGS, TESTSUITEFLAGS survive
chroot "$IGOS" /usr/bin/env -i               \
    HOME=/root                               \
    TERM="$TERM"                             \
    TZ="$HOST_TZ"                            \
    PS1='\[\e[1;34m\][\[\e[m\]\[\e[1;31m\](igos-chroot)\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;34m\][\[\e[m\]\[\e[1;37m\]<\[\e[m\]\[\e[1;32m\]\w\[\e[m\]\[\e[1;37m\]>\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;37m\]:\[\e[m\]\[\e[1;31m\]#\[\e[m\] ' \
    PATH=/usr/bin:/usr/sbin                  \
    MAKEFLAGS="-j${JOBS}"                    \
    TESTSUITEFLAGS="-j${JOBS}"               \
    IGOS_JOBS="${JOBS}"                       \
    IGOS_SOURCES=/sources                    \
    IGOS_PATCHES=/sources                    \
    IGOS_LOGS=/var/log/igos-build            \
    PKG_VERSION=""                           \
    IGOS_START_AT="${IGOS_START_AT:-}"       \
    $CHROOT_CMD
```

### chroot-teardown.sh
```bash
#!/bin/bash
# InterGenOS Chroot Teardown — "Drop Out"
#
# Unmounts virtual kernel filesystems in the correct order.
# Safe to run even if some mounts aren't present.
#
# Usage (as root on build VM):
#   sudo bash /mnt/intergenos/scripts/chroot-teardown.sh

IGOS=/mnt/igos

# Verify we're root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root."
    exit 1
fi

echo "InterGenOS Chroot Teardown"
echo "=========================="

# Unmount in reverse order of mounting
# Some may not be mounted — that's fine, we ignore errors

echo "--- Unmounting /dev/shm ---"
umount $IGOS/dev/shm 2>/dev/null && echo "  Done" || echo "  Not mounted"

echo "--- Unmounting /run ---"
umount $IGOS/run 2>/dev/null && echo "  Done" || echo "  Not mounted"

echo "--- Unmounting /sys ---"
umount $IGOS/sys 2>/dev/null && echo "  Done" || echo "  Not mounted"

echo "--- Unmounting /proc ---"
umount $IGOS/proc 2>/dev/null && echo "  Done" || echo "  Not mounted"

echo "--- Unmounting /dev/pts ---"
umount $IGOS/dev/pts 2>/dev/null && echo "  Done" || echo "  Not mounted"

echo "--- Unmounting /dev ---"
umount $IGOS/dev 2>/dev/null && echo "  Done" || echo "  Not mounted"

echo ""
echo "=========================="
echo "Chroot environment torn down."
echo "To re-enter: run chroot-setup.sh first, then chroot-enter.sh"
```

### chroot-build-ch8.sh
```bash
#!/bin/bash
# InterGenOS Chapter 8 Build — Final System
# LFS 13.0 Systemd + nano from BLFS
#
# Runs INSIDE the chroot (launched via chroot-enter.sh).
# Builds LFS core packages with DESTDIR staging
# and Slackware-style package tracking.
#
# Each package is:
#   1. Extracted to /tmp/igos-build/<name>
#   2. Built using configure() / build() / check() from build.sh
#   3. Staged via DESTDIR into /tmp/igos-staging/<name>-<version>
#   4. Manifest + archive created
#   5. Deployed to the live filesystem
#   6. Post-install hooks run (if defined)
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-ch8.sh
#
# To resume after a failure, set IGOS_START_AT=<package-name>:
#   IGOS_START_AT=gcc-core sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-ch8.sh

set +h
set -e
umask 022

# ============================================================================
# Environment
# ============================================================================

IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/var/log/igos-build
IGOS_JOBS=$(nproc)
IGOS_PACKAGES=/mnt/intergenos/packages/core
IGOS_START_AT="${IGOS_START_AT:-}"

export IGOS_SOURCES IGOS_PATCHES IGOS_LOGS IGOS_JOBS

mkdir -pv "$IGOS_LOGS"

# Source the package tracking functions
source /mnt/intergenos/scripts/pkg-functions.sh

# ============================================================================
# Logging
# ============================================================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$IGOS_LOGS/ch8-build.log"
}

# ============================================================================
# Build helper — sources per-package build.sh, runs phased functions
# ============================================================================

build_ch8_package() {
    local pkg_dir="$1"     # directory name under /packages/core/
    local name="$2"        # package name for display/tracking
    local version="$3"     # package version
    local tarball="$4"     # source tarball filename
    local description="$5" # one-line description for manifest

    local build_script="${IGOS_PACKAGES}/${pkg_dir}/build.sh"
    local pkg_log="${IGOS_LOGS}/${name}-ch8-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    # Verify build script exists
    if [ ! -f "$build_script" ]; then
        log "ERROR: No build.sh found at $build_script"
        log "       This package needs a build script before it can be built."
        return 1
    fi

    log "=========================================="
    log "  Chapter 8: ${name} ${version}"
    log "  Log: ${pkg_log}"
    log "=========================================="

    export PKG_VERSION="$version"

    # Clean and extract
    rm -rf "$workdir"
    mkdir -pv "$workdir"
    tar -xf "${IGOS_SOURCES}/${tarball}" -C "$workdir" --strip-components=1 || {
        log "ERROR: Failed to extract ${tarball}"
        return 1
    }
    cd "$workdir"

    local start=$(date +%s)

    # Clear any previously-defined functions
    unset -f configure build check do_install post_install

    # Source the package build script (defines configure/build/check/install)
    source "$build_script"

    # Reset CWD before each phase. Build functions may cd into subdirectories
    # (e.g., NSS's build() does "cd nss") and bash doesn't scope cd to
    # functions — it persists into the next call. Without resetting, later
    # phases start from the wrong directory.

    # --- CONFIGURE ---
    if declare -f configure > /dev/null 2>&1; then
        cd "$workdir"
        log "  [CONFIGURE] starting..."
        configure >> "$pkg_log" 2>&1
        local rc=$?
        if [ $rc -ne 0 ]; then
            log "  FAILED in configure (exit $rc)"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            return 1
        fi
        log "  [CONFIGURE] done"
    fi

    # --- BUILD ---
    if declare -f build > /dev/null 2>&1; then
        cd "$workdir"
        log "  [BUILD] starting..."
        build >> "$pkg_log" 2>&1
        local rc=$?
        if [ $rc -ne 0 ]; then
            log "  FAILED in build (exit $rc)"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            return 1
        fi
        log "  [BUILD] done"
    fi

    # --- CHECK (optional — failures logged but don't stop the build) ---
    if declare -f check > /dev/null 2>&1; then
        cd "$workdir"
        log "  [CHECK] starting..."
        check >> "$pkg_log" 2>&1
        log "  [CHECK] done (see log for results)"
    fi

    # --- INSTALL (via DESTDIR staging + package tracking) ---
    cd "$workdir"
    log "  [INSTALL] staging..."
    pkg_install "$name" "$version" "$description"
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "  FAILED in install/staging (exit $rc)"
        return 1
    fi

    # --- POST-INSTALL (runs on live system if defined) ---
    if declare -f post_install > /dev/null 2>&1; then
        cd "$workdir"
        log "  [POST-INSTALL] running live system hooks..."
        post_install >> "$pkg_log" 2>&1
        log "  [POST-INSTALL] done"
    fi

    local elapsed=$(( $(date +%s) - start ))
    log "  SUCCESS: ${name} ${version} (${elapsed}s)"
    log ""

    # Clean up build directory
    cd /
    rm -rf "$workdir"

    return 0
}

# ============================================================================
# Build Order — LFS 13.0 Chapter 8 (Systemd) + nano
#
# Format: build_ch8_package <pkg-dir> <name> <version> <tarball> <description>
#
# The pkg-dir maps to /packages/core/<pkg-dir>/build.sh
# Order matches LFS book exactly. Nano added after Vim.
# ============================================================================

log ""
log "============================================"
log "  InterGenOS Chapter 8 Build"
PKG_COUNT=$(grep -c '^run_package' "$0" 2>/dev/null || echo "?")
log "  LFS 13.0 Systemd — ${PKG_COUNT} packages"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# Initialize package database
pkg_init

SKIP=true
if [ -z "$IGOS_START_AT" ]; then
    SKIP=false
fi

run_package() {
    local pkg_dir="$1"
    local name="$2"

    if $SKIP; then
        if [ "$name" = "$IGOS_START_AT" ] || [ "$pkg_dir" = "$IGOS_START_AT" ]; then
            SKIP=false
            log ">>> Resuming build at: $name"
        else
            log "  Skipping: $name (resuming from $IGOS_START_AT)"
            return 0
        fi
    fi

    build_ch8_package "$@" || {
        log ""
        log "!!! BUILD FAILED: $name"
        log "!!! Fix the issue and re-run with: IGOS_START_AT=$name"
        log ""
        exit 1
    }
}

# ============================================================================
# 8.3 — 8.5: Man-pages, Iana-Etc, Glibc
# ============================================================================

run_package "man-pages" "man-pages" "6.17" \
    "man-pages-6.17.tar.xz" \
    "Linux man pages"

run_package "iana-etc" "iana-etc" "20260202" \
    "iana-etc-20260202.tar.gz" \
    "Network services and protocols data"

run_package "glibc-core" "glibc" "2.43" \
    "glibc-2.43.tar.xz" \
    "GNU C Library"

# ============================================================================
# 8.6 — 8.11: Compression + file type
# ============================================================================

run_package "zlib" "zlib" "1.3.2" \
    "zlib-1.3.2.tar.gz" \
    "Compression library"

run_package "bzip2" "bzip2" "1.0.8" \
    "bzip2-1.0.8.tar.gz" \
    "Block-sorting file compressor"

run_package "xz" "xz" "5.8.2" \
    "xz-5.8.2.tar.xz" \
    "XZ Utils compression"

run_package "lz4" "lz4" "1.10.0" \
    "lz4-1.10.0.tar.gz" \
    "Fast lossless compression"

run_package "zstd" "zstd" "1.5.7" \
    "zstd-1.5.7.tar.gz" \
    "Zstandard real-time compression"

run_package "file" "file" "5.46" \
    "file-5.46.tar.gz" \
    "File type determination"

# ============================================================================
# 8.12 — 8.16: Text/pattern libraries + tools
# ============================================================================

run_package "readline" "readline" "8.3" \
    "readline-8.3.tar.gz" \
    "Command line editing library"

run_package "pcre2" "pcre2" "10.47" \
    "pcre2-10.47.tar.bz2" \
    "Perl-compatible regular expressions"

run_package "m4-core" "m4" "1.4.21" \
    "m4-1.4.21.tar.xz" \
    "GNU macro processor"

run_package "bc" "bc" "7.0.3" \
    "bc-7.0.3.tar.xz" \
    "Arbitrary precision calculator"

run_package "flex" "flex" "2.6.4" \
    "flex-2.6.4.tar.gz" \
    "Lexical analyzer generator"

# ============================================================================
# 8.17 — 8.20: Test infrastructure
# ============================================================================

run_package "tcl" "tcl" "8.6.17" \
    "tcl8.6.17-src.tar.gz" \
    "Tool Command Language"

run_package "expect" "expect" "5.45.4" \
    "expect5.45.4.tar.gz" \
    "Tool for automating interactive programs"

run_package "dejagnu" "dejagnu" "1.6.3" \
    "dejagnu-1.6.3.tar.gz" \
    "Testing framework"

run_package "pkgconf" "pkgconf" "2.5.1" \
    "pkgconf-2.5.1.tar.xz" \
    "Package compiler and linker metadata toolkit"

# ============================================================================
# 8.21 — 8.30: Core toolchain + security
# ============================================================================

run_package "binutils-core" "binutils" "2.46.0" \
    "binutils-2.46.0.tar.xz" \
    "GNU binary utilities"

run_package "gmp" "gmp" "6.3.0" \
    "gmp-6.3.0.tar.xz" \
    "GNU multiple precision arithmetic library"

run_package "mpfr" "mpfr" "4.2.2" \
    "mpfr-4.2.2.tar.xz" \
    "Multiple precision floating-point library"

run_package "mpc" "mpc" "1.3.1" \
    "mpc-1.3.1.tar.gz" \
    "Multiple precision complex arithmetic library"

run_package "attr" "attr" "2.5.2" \
    "attr-2.5.2.tar.gz" \
    "Extended attribute utilities"

run_package "acl" "acl" "2.3.2" \
    "acl-2.3.2.tar.xz" \
    "Access control list utilities"

run_package "libcap" "libcap" "2.77" \
    "libcap-2.77.tar.xz" \
    "POSIX capabilities library"

run_package "libxcrypt" "libxcrypt" "4.5.2" \
    "libxcrypt-4.5.2.tar.xz" \
    "Password hashing library"

run_package "shadow" "shadow" "4.19.3" \
    "shadow-4.19.3.tar.xz" \
    "Password and user account utilities"

run_package "gcc-core" "gcc" "15.2.0" \
    "gcc-15.2.0.tar.xz" \
    "GNU Compiler Collection"

# ============================================================================
# 8.31 — 8.43: Core system utilities
# ============================================================================

run_package "ncurses-core" "ncurses" "6.6" \
    "ncurses-6.6.tar.gz" \
    "Terminal-independent screen handling library"

run_package "sed-core" "sed" "4.9" \
    "sed-4.9.tar.xz" \
    "Stream editor"

run_package "psmisc" "psmisc" "23.7" \
    "psmisc-23.7.tar.xz" \
    "Process management utilities"

run_package "gettext" "gettext" "1.0" \
    "gettext-1.0.tar.xz" \
    "Internationalization utilities"

run_package "bison-core" "bison" "3.8.2" \
    "bison-3.8.2.tar.xz" \
    "Parser generator"

run_package "grep-core" "grep" "3.12" \
    "grep-3.12.tar.xz" \
    "Pattern matching utility"

run_package "bash" "bash" "5.3" \
    "bash-5.3.tar.gz" \
    "GNU Bourne-Again Shell"

run_package "libtool" "libtool" "2.5.4" \
    "libtool-2.5.4.tar.xz" \
    "Generic library support script"

run_package "gdbm" "gdbm" "1.26" \
    "gdbm-1.26.tar.gz" \
    "GNU database manager"

run_package "gperf" "gperf" "3.3" \
    "gperf-3.3.tar.gz" \
    "Perfect hash function generator"

run_package "expat" "expat" "2.7.4" \
    "expat-2.7.4.tar.xz" \
    "XML parsing library"

run_package "inetutils" "inetutils" "2.7" \
    "inetutils-2.7.tar.gz" \
    "Network utilities"

run_package "less" "less" "692" \
    "less-692.tar.gz" \
    "Text file viewer"

# ============================================================================
# 8.44 — 8.47: Perl ecosystem
# ============================================================================

run_package "perl-core" "perl" "5.42.0" \
    "perl-5.42.0.tar.xz" \
    "Practical Extraction and Report Language"

run_package "xml-parser" "xml-parser" "2.47" \
    "XML-Parser-2.47.tar.gz" \
    "Perl XML parser module"

run_package "intltool" "intltool" "0.51.0" \
    "intltool-0.51.0.tar.gz" \
    "Internationalization tool"

run_package "autoconf" "autoconf" "2.72" \
    "autoconf-2.72.tar.xz" \
    "Automatic configure script builder"

run_package "automake" "automake" "1.18.1" \
    "automake-1.18.1.tar.xz" \
    "Automatic Makefile generator"

# ============================================================================
# 8.49 — 8.52: Crypto + ELF + FFI + SQLite
# ============================================================================

run_package "openssl" "openssl" "3.6.1" \
    "openssl-3.6.1.tar.gz" \
    "Cryptography and SSL/TLS toolkit"

run_package "elfutils" "elfutils" "0.194" \
    "elfutils-0.194.tar.bz2" \
    "ELF object file access library"

run_package "libffi" "libffi" "3.5.2" \
    "libffi-3.5.2.tar.gz" \
    "Foreign function interface library"

run_package "sqlite" "sqlite" "3510200" \
    "sqlite-autoconf-3510200.tar.gz" \
    "Self-contained SQL database engine"

# ============================================================================
# 8.53 — 8.59: Python ecosystem + build tools
# ============================================================================

run_package "python" "python" "3.14.3" \
    "Python-3.14.3.tar.xz" \
    "Python programming language"

run_package "flit-core" "flit-core" "3.12.0" \
    "flit_core-3.12.0.tar.gz" \
    "Python build backend (minimal)"

run_package "packaging" "packaging" "26.0" \
    "packaging-26.0.tar.gz" \
    "Python packaging utilities"

run_package "wheel" "wheel" "0.46.3" \
    "wheel-0.46.3.tar.gz" \
    "Python wheel format support"

run_package "setuptools" "setuptools" "82.0.0" \
    "setuptools-82.0.0.tar.gz" \
    "Python package build system"

run_package "ninja" "ninja" "1.13.2" \
    "ninja-1.13.2.tar.gz" \
    "Small build system with a focus on speed"

run_package "meson" "meson" "1.10.1" \
    "meson-1.10.1.tar.gz" \
    "High-productivity build system"

# ============================================================================
# 8.60 — 8.73: System utilities + coreutils
# ============================================================================

run_package "kmod" "kmod" "34.2" \
    "kmod-34.2.tar.xz" \
    "Kernel module utilities"

run_package "coreutils-core" "coreutils" "9.10" \
    "coreutils-9.10.tar.xz" \
    "GNU core utilities"

run_package "diffutils-core" "diffutils" "3.12" \
    "diffutils-3.12.tar.xz" \
    "File comparison utilities"

run_package "gawk-core" "gawk" "5.3.2" \
    "gawk-5.3.2.tar.xz" \
    "Pattern scanning and processing language"

run_package "findutils-core" "findutils" "4.10.0" \
    "findutils-4.10.0.tar.xz" \
    "File finding utilities"

run_package "groff" "groff" "1.23.0" \
    "groff-1.23.0.tar.gz" \
    "Document formatting system"

run_package "grub" "grub" "2.14" \
    "grub-2.14.tar.xz" \
    "GRand Unified Bootloader"

run_package "gzip-core" "gzip" "1.14" \
    "gzip-1.14.tar.xz" \
    "GNU compression utility"

run_package "iproute2" "iproute2" "6.18.0" \
    "iproute2-6.18.0.tar.xz" \
    "Networking and traffic control utilities"

run_package "kbd" "kbd" "2.9.0" \
    "kbd-2.9.0.tar.xz" \
    "Keyboard mapping utilities"

run_package "libpipeline" "libpipeline" "1.5.8" \
    "libpipeline-1.5.8.tar.gz" \
    "Pipeline manipulation library"

run_package "make-core" "make" "4.4.1" \
    "make-4.4.1.tar.gz" \
    "GNU build automation tool"

run_package "patch-core" "patch" "2.8" \
    "patch-2.8.tar.xz" \
    "File patching utility"

run_package "tar-core" "tar" "1.35" \
    "tar-1.35.tar.xz" \
    "Tape archiver"

run_package "texinfo-core" "texinfo" "7.2" \
    "texinfo-7.2.tar.xz" \
    "Documentation system"

# ============================================================================
# 8.75 — 8.76: Editors (LFS vim + InterGenOS nano)
# ============================================================================

run_package "vim" "vim" "9.2.0078" \
    "vim-9.2.0078.tar.gz" \
    "Vi IMproved text editor"

run_package "nano" "nano" "8.7.1" \
    "nano-8.7.1.tar.xz" \
    "Small, friendly text editor"

# ============================================================================
# 8.76 — 8.79: Python support + systemd + D-Bus
# ============================================================================

run_package "markupsafe" "markupsafe" "3.0.3" \
    "markupsafe-3.0.3.tar.gz" \
    "XML/HTML markup safe string library"

run_package "jinja2" "jinja2" "3.1.6" \
    "jinja2-3.1.6.tar.gz" \
    "Template engine for Python"

run_package "systemd" "systemd" "259.1" \
    "systemd-259.1.tar.gz" \
    "System and service manager"

run_package "dbus" "dbus" "1.16.2" \
    "dbus-1.16.2.tar.xz" \
    "Message bus system"

# ============================================================================
# 8.80 — 8.83: Final system utilities
# ============================================================================

run_package "man-db" "man-db" "2.13.1" \
    "man-db-2.13.1.tar.xz" \
    "Man page viewer and database"

run_package "procps-ng" "procps-ng" "4.0.6" \
    "procps-ng-4.0.6.tar.xz" \
    "Process monitoring utilities"

run_package "util-linux-core" "util-linux" "2.41.3" \
    "util-linux-2.41.3.tar.xz" \
    "System utilities"

run_package "e2fsprogs" "e2fsprogs" "1.47.3" \
    "e2fsprogs-1.47.3.tar.gz" \
    "Ext2/3/4 filesystem utilities"

# ============================================================================
# 8.84 — 8.86: Stripping and Cleanup
# ============================================================================

# --- 8.85: Stripping debug symbols — SKIPPED ---
# Debug symbols are kept during development. They're essential for debugging
# segfaults and tracing issues during desktop tier builds. Stripping will be
# done in create-image.sh when packaging the final distributable image.
log "  8.85: Stripping — SKIPPED (keeping debug symbols for development)"

# ============================================================================
# 8.86: Cleanup
# ============================================================================

log "============================================"
log "  8.86: Cleaning up"
log "============================================"

# Remove temp files
rm -rf /tmp/{*,.*} 2>/dev/null || true

# Remove libtool .la files
find /usr/lib /usr/libexec -name \*.la -delete 2>/dev/null

# NOTE: LFS removes x86_64-lfs-linux-gnu* cross-compiler remnants here.
# InterGenOS uses x86_64-igos-linux-gnu for BOTH the cross-compiler and
# the final system, so there's nothing to remove — the triplet-named
# directories (e.g., /usr/libexec/gcc/x86_64-igos-linux-gnu/) contain
# the LIVE compiler, not remnants. Deleting them bricks GCC.
#
# If x86_64-pc-linux-gnu files exist, that indicates a build that used
# the wrong triplet somewhere.
PC_REMNANTS=$(find /usr -depth -name "$(uname -m)-pc-linux-gnu*" 2>/dev/null)
if [ -n "$PC_REMNANTS" ]; then
    log "WARNING: Found x86_64-pc-linux-gnu files — triplet misconfiguration!"
    log "  These should be x86_64-igos-linux-gnu. Investigate before continuing."
    echo "$PC_REMNANTS" | while IFS= read -r f; do log "    $f"; done
fi

# Remove tester user
userdel -r tester 2>/dev/null || true

log "  Cleanup complete"

# ============================================================================
# Summary
# ============================================================================

TOTAL_PACKAGES=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log "============================================"
log "  CHAPTER 8 BUILD COMPLETE"
log "  Packages tracked: ${TOTAL_PACKAGES}"
log "  Archives: /var/lib/igos/archives/"
log "  Manifests: /var/lib/igos/packages/"
log "  Finish: $(date)"
log "============================================"
log ""
log "  Next: Chapter 9 (System Configuration)"
log "        Chapter 10 (Bootloader + Kernel)"
log "============================================"
```

### chroot-build-core-extra.sh
```bash
#!/bin/bash
# InterGenOS Core Extra Build — additional packages beyond LFS
# Builds after Chapter 8 completes, inside the chroot.
#
# These packages were promoted from "base" to "core" because they are
# foundational libraries or build dependencies required by the build
# system and/or by many downstream packages.
#
# Groups:
#   A. TLS/Certificate chain (libtasn1, libunistring, libidn2, p11-kit, make-ca, libpsl)
#   B. Network tools (nghttp2, libssh2, curl, wget, git)
#   C. Authentication (linux-pam, sudo) + shadow rebuild
#   D. Foundational libraries (glib2, libarchive, libuv, nspr, nss)
#   E. Build infrastructure (cmake)
#
# Uses the same package tracking as Chapter 8 (pkg-functions.sh).
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-core-extra.sh
#
# To resume after a failure:
#   IGOS_START_AT=<name> sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-core-extra.sh

set +h
set -e
umask 022

# ============================================================================
# Environment
# ============================================================================

IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/var/log/igos-build
IGOS_JOBS=$(nproc)
IGOS_PACKAGES=/mnt/intergenos/packages/core
IGOS_START_AT="${IGOS_START_AT:-}"

export IGOS_SOURCES IGOS_PATCHES IGOS_LOGS IGOS_JOBS

mkdir -pv "$IGOS_LOGS"

# Source the package tracking functions
source /mnt/intergenos/scripts/pkg-functions.sh

# ============================================================================
# Logging
# ============================================================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$IGOS_LOGS/core-extra-build.log"
}

# ============================================================================
# Build helper — same pattern as Chapter 8
# ============================================================================

build_core_package() {
    local pkg_dir="$1"
    local name="$2"
    local version="$3"
    local tarball="$4"
    local description="$5"

    local build_script="${IGOS_PACKAGES}/${pkg_dir}/build.sh"
    local pkg_log="${IGOS_LOGS}/${name}-core-extra-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    if [ ! -f "$build_script" ]; then
        log "ERROR: No build.sh found at $build_script"
        return 1
    fi

    log "=========================================="
    log "  Core Extra: ${name} ${version}"
    log "  Log: ${pkg_log}"
    log "=========================================="

    export PKG_VERSION="$version"

    # Clean and extract
    rm -rf "$workdir"
    mkdir -pv "$workdir"
    tar -xf "${IGOS_SOURCES}/${tarball}" -C "$workdir" --strip-components=1 || {
        log "ERROR: Failed to extract ${tarball}"
        return 1
    }
    cd "$workdir"

    local start=$(date +%s)

    # Clear any previously-defined functions
    unset -f configure build check do_install post_install

    # Source the package build script
    source "$build_script"

    # Reset CWD before each phase. Build functions may cd into subdirectories
    # (e.g., NSS's build() does "cd nss") and bash doesn't scope cd to
    # functions — it persists into the next call. Without resetting, later
    # phases start from the wrong directory.

    # --- CONFIGURE ---
    if declare -f configure > /dev/null 2>&1; then
        cd "$workdir"
        log "  [CONFIGURE] starting..."
        configure >> "$pkg_log" 2>&1
        local rc=$?
        if [ $rc -ne 0 ]; then
            log "  FAILED in configure (exit $rc)"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            return 1
        fi
        log "  [CONFIGURE] done"
    fi

    # --- BUILD ---
    if declare -f build > /dev/null 2>&1; then
        cd "$workdir"
        log "  [BUILD] starting..."
        build >> "$pkg_log" 2>&1
        local rc=$?
        if [ $rc -ne 0 ]; then
            log "  FAILED in build (exit $rc)"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            return 1
        fi
        log "  [BUILD] done"
    fi

    # --- CHECK (optional — failures logged but don't stop the build) ---
    if declare -f check > /dev/null 2>&1; then
        cd "$workdir"
        log "  [CHECK] starting..."
        check >> "$pkg_log" 2>&1
        log "  [CHECK] done (see log for results)"
    fi

    # --- INSTALL (via DESTDIR staging + package tracking) ---
    cd "$workdir"
    log "  [INSTALL] staging..."
    pkg_install "$name" "$version" "$description"
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "  FAILED in install/staging (exit $rc)"
        return 1
    fi

    # --- POST-INSTALL (runs on live system if defined) ---
    if declare -f post_install > /dev/null 2>&1; then
        cd "$workdir"
        log "  [POST-INSTALL] running live system hooks..."
        post_install >> "$pkg_log" 2>&1
        log "  [POST-INSTALL] done"
    fi

    local elapsed=$(( $(date +%s) - start ))
    log "  SUCCESS: ${name} ${version} (${elapsed}s)"
    log ""

    cd /
    rm -rf "$workdir"
    return 0
}

# ============================================================================
# Resume support
# ============================================================================

SKIP=true
if [ -z "$IGOS_START_AT" ]; then
    SKIP=false
fi

run_package() {
    local pkg_dir="$1"
    local name="$2"

    if $SKIP; then
        if [ "$name" = "$IGOS_START_AT" ] || [ "$pkg_dir" = "$IGOS_START_AT" ]; then
            SKIP=false
            log ">>> Resuming build at: $name"
        else
            log "  Skipping: $name (resuming from $IGOS_START_AT)"
            return 0
        fi
    fi

    build_core_package "$@" || {
        log ""
        log "!!! BUILD FAILED: $name"
        log "!!! Fix the issue and re-run with: IGOS_START_AT=$name"
        log ""
        exit 1
    }
}

# ============================================================================
# Build Order — additional core packages
# ============================================================================

log ""
log "============================================"
log "  InterGenOS Core Extra Build"
EXTRA_PKG_COUNT=$(grep -c '^run_package' "$0" 2>/dev/null || echo "?")
log "  ${EXTRA_PKG_COUNT} packages beyond LFS 13.0"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# Initialize package database (continues from Chapter 8)
pkg_init

# --- Group A: TLS/Certificate Chain foundations ---

run_package "libtasn1" "libtasn1" "4.21.0" \
    "libtasn1-4.21.0.tar.gz" \
    "ASN.1 library used by GnuTLS and p11-kit"

run_package "libunistring" "libunistring" "1.4.2" \
    "libunistring-1.4.2.tar.xz" \
    "Unicode string library for C"

# --- Group D: Foundational libraries (no deps) ---

run_package "libuv" "libuv" "1.52.1" \
    "libuv-v1.52.1.tar.gz" \
    "Multi-platform asynchronous I/O library"

run_package "libarchive" "libarchive" "3.8.6" \
    "libarchive-3.8.6.tar.xz" \
    "Multi-format archive and compression library"

run_package "nghttp2" "nghttp2" "1.68.1" \
    "nghttp2-1.68.1.tar.xz" \
    "HTTP/2 C library"

run_package "nspr" "nspr" "4.38.2" \
    "nspr-4.38.2.tar.gz" \
    "Netscape Portable Runtime"

# --- Group C: PAM + sudo ---

run_package "linux-pam" "linux-pam" "1.7.2" \
    "Linux-PAM-1.7.2.tar.xz" \
    "Pluggable Authentication Modules"

run_package "shadow-pam" "shadow-pam" "4.19.3" \
    "shadow-4.19.3.tar.xz" \
    "Shadow password suite (rebuilt with Linux-PAM support)"

# --- Group C2: OpenSSH (requires linux-pam + shadow-pam) ---

run_package "openssh" "openssh" "10.2p1" \
    "openssh-10.2p1.tar.gz" \
    "Secure Shell client and server"

# --- Group D: glib2 bootstrap (Void Linux approach) ---
# Three separate packages break the circular dependency:
#   glib2-bootstrap (no introspection) → gobject-introspection → glib2 (full)
# Each is a standard DESTDIR build. No hacks needed.

run_package "glib2-bootstrap" "glib2-bootstrap" "2.86.4" \
    "glib-2.86.4.tar.xz" \
    "GLib core library (bootstrap — without introspection)"

run_package "gobject-introspection" "gobject-introspection" "1.86.0" \
    "gobject-introspection-1.86.0.tar.xz" \
    "GObject type introspection framework"

run_package "glib2" "glib2" "2.86.4" \
    "glib-2.86.4.tar.xz" \
    "GLib core library (full — with introspection)"

# --- Group A: TLS chain (deps on libtasn1, libunistring) ---

run_package "libidn2" "libidn2" "2.3.8" \
    "libidn2-2.3.8.tar.gz" \
    "Internationalized domain names library"

run_package "p11-kit" "p11-kit" "0.26.2" \
    "p11-kit-0.26.2.tar.xz" \
    "PKCS#11 module loading library"

# --- Group C: sudo ---

run_package "sudo" "sudo" "1.9.17p2" \
    "sudo-1.9.17p2.tar.gz" \
    "Execute commands as another user"

# --- Group B: libssh2 (before curl) ---

run_package "libssh2" "libssh2" "1.11.1" \
    "libssh2-1.11.1.tar.gz" \
    "Client-side SSH2 library"

# --- Group D+E: NSS ---

run_package "nss" "nss" "3.121" \
    "nss-3.121.tar.gz" \
    "Network Security Services"

run_package "make-ca" "make-ca" "1.16.1" \
    "make-ca-1.16.1.tar.gz" \
    "CA certificate management utility"

# --- Group A: libpsl (deps on libidn2, libunistring) ---

run_package "libpsl" "libpsl" "0.21.5" \
    "libpsl-0.21.5.tar.gz" \
    "Public Suffix List library"

# --- Group B: Network tools ---

run_package "curl" "curl" "8.19.0" \
    "curl-8.19.0.tar.xz" \
    "Command line tool and library for transferring data with URLs"

run_package "wget" "wget" "1.25.0" \
    "wget-1.25.0.tar.gz" \
    "Network file retriever"

# --- Group E: Build infrastructure ---

run_package "cmake" "cmake" "4.3.1" \
    "cmake-4.3.1.tar.gz" \
    "Cross-platform build system generator"

run_package "git" "git" "2.53.0" \
    "git-2.53.0.tar.xz" \
    "Distributed version control system"

# ============================================================================
# Summary
# ============================================================================

TOTAL_CORE_EXTRA=$(grep -c "^run_package" "$0" | head -1)
TOTAL_TRACKED=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log "============================================"
log "  Core Extra Build Complete"
log "  Total tracked packages: ${TOTAL_TRACKED}"
log "  End: $(date)"
log "============================================"
```

### chroot-build-desktop.sh
```bash
#!/bin/bash
# InterGenOS Desktop Build — 337 packages for GNOME on Wayland
# Runs INSIDE the chroot after core, config, core-extra, and kernel complete.
#
# Handles all prerequisites automatically:
#   1. Installs PyYAML for the Python builder
#   2. Builds base-tier dependencies needed by desktop packages
#   3. Runs igos-build with --skip-built for safe restarts
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-desktop.sh

set +h
set -e
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/var/log/igos-build
IGOS_JOBS=$(nproc)

mkdir -pv "$IGOS_LOGS"

DESKTOP_LOG="$IGOS_LOGS/desktop-build-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$DESKTOP_LOG"
}

log ""
log "============================================"
log "  InterGenOS Desktop Build"
log "  337 packages for GNOME on Wayland"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# ============================================================================
# Step 1: Ensure PyYAML is available for igos-build
# ============================================================================

log "--- Checking Python dependencies for igos-build ---"

if python3 -c "import yaml" 2>/dev/null; then
    log "  PyYAML: already installed"
else
    log "  PyYAML: not found — installing..."

    # Python 3.14 ships without setuptools — bootstrap it first if needed
    if ! python3 -c "import setuptools" 2>/dev/null; then
        SETUPTOOLS_TAR=$(ls ${IGOS_SOURCES}/setuptools-*.tar.gz 2>/dev/null | head -1)
        if [ -n "$SETUPTOOLS_TAR" ]; then
            log "  Bootstrapping setuptools from $SETUPTOOLS_TAR..."
            SETUPTOOLS_WORK=$(mktemp -d)
            tar -xf "$SETUPTOOLS_TAR" -C "$SETUPTOOLS_WORK" --strip-components=1
            SITE=$(python3 -c "import site; print(site.getsitepackages()[0])")
            cp -r "$SETUPTOOLS_WORK/setuptools" "$SITE/"
            cp -r "$SETUPTOOLS_WORK/_distutils_hack" "$SITE/" 2>/dev/null || true
            rm -rf "$SETUPTOOLS_WORK"
            if python3 -c "import setuptools" 2>/dev/null; then
                log "  setuptools: bootstrapped"
            else
                log "ERROR: Failed to bootstrap setuptools"
                exit 1
            fi
        else
            log "ERROR: No setuptools tarball found in $IGOS_SOURCES"
            exit 1
        fi
    fi

    PYYAML_TAR=$(ls ${IGOS_SOURCES}/PyYAML-*.tar.gz ${IGOS_SOURCES}/pyyaml-*.tar.gz 2>/dev/null | head -1)
    if [ -z "$PYYAML_TAR" ]; then
        log "ERROR: No PyYAML tarball found in $IGOS_SOURCES"
        exit 1
    fi

    TMPDIR=$(mktemp -d)
    tar -xzf "$PYYAML_TAR" -C "$TMPDIR" --strip-components=1
    cd "$TMPDIR"
    python3 setup.py install 2>&1 | tail -5
    cd /
    rm -rf "$TMPDIR"

    if python3 -c "import yaml" 2>/dev/null; then
        log "  PyYAML: installed manually"
    else
        log "ERROR: Failed to install PyYAML — igos-build cannot run"
        exit 1
    fi
fi

log "  Python: $(python3 --version 2>&1)"
log "  PyYAML: $(python3 -c 'import yaml; print(yaml.__version__)')"

# ============================================================================
# Step 2: Build base-tier prerequisites needed by desktop packages
# ============================================================================

log ""
log "--- Building base-tier prerequisites ---"

cd /mnt/intergenos

# These base packages are build dependencies for desktop packages
# but aren't part of the desktop tier. Build them first.
BASE_DEPS="libtirpc popt which"

for dep in $BASE_DEPS; do
    if [ -f "/var/lib/igos/packages/${dep}-"* ] 2>/dev/null; then
        log "  $dep: already tracked — skipping"
    else
        log "  $dep: building..."
        python3 igos-build.py \
            --build --tracked --only "$dep" \
            --sources-dir "$IGOS_SOURCES" \
            2>&1 | tee -a "$DESKTOP_LOG"

        if [ ${PIPESTATUS[0]} -ne 0 ]; then
            log "ERROR: Failed to build base dependency: $dep"
            exit 1
        fi
        log "  $dep: done"
    fi
done

log "  Base prerequisites complete"

# ============================================================================
# Step 3: Run igos-build for desktop tier
# ============================================================================

log ""
log "--- Running igos-build for desktop tier ---"
log ""

python3 igos-build.py \
    --build \
    --tracked \
    --skip-built \
    --tier desktop \
    --sources-dir "$IGOS_SOURCES" \
    2>&1 | tee -a "$DESKTOP_LOG"

BUILD_RC=${PIPESTATUS[0]}

if [ $BUILD_RC -ne 0 ]; then
    log ""
    log "!!! Desktop build failed (exit $BUILD_RC)"
    log "!!! Check logs in $IGOS_LOGS/"
    log "!!! Fix the failing package, then re-run this script."
    log "!!! --skip-built will resume from where it left off."
    exit $BUILD_RC
fi

# ============================================================================
# Step 4: Apply InterGenOS desktop branding
# ============================================================================

log ""
log "--- Applying InterGenOS desktop branding ---"

# Install gsettings override for GNOME defaults (dark theme, fonts, colors)
if [ -f /mnt/intergenos/config/gsettings/90_intergenos.gschema.override ]; then
    install -v -m644 /mnt/intergenos/config/gsettings/90_intergenos.gschema.override \
        /usr/share/glib-2.0/schemas/
    glib-compile-schemas /usr/share/glib-2.0/schemas/
    log "  gsettings overrides installed (dark theme, fonts, branding)"
fi

# ============================================================================
# Summary
# ============================================================================

TOTAL_TRACKED=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log "============================================"
log "  DESKTOP BUILD COMPLETE"
log "  Total tracked packages: ${TOTAL_TRACKED}"
log "  End: $(date)"
log "============================================"
```

### chroot-build-tier.sh
```bash
#!/bin/bash
# ==========================================================================
# InterGenOS Unified Tier Builder
#
# Runs INSIDE the chroot. Bootstraps PyYAML into the temporary Python
# (from LFS Ch. 7), then invokes the Python builder for any tier.
#
# Replaces the per-tier bash build scripts (chroot-build-ch8.sh,
# chroot-build-core-extra.sh, chroot-build-base.sh, chroot-build-desktop.sh)
# with a single entry point. One builder, one set of templates.
#
# Usage:
#   bash /mnt/intergenos/scripts/chroot-build-tier.sh --tier core
#   bash /mnt/intergenos/scripts/chroot-build-tier.sh --tier base
#   bash /mnt/intergenos/scripts/chroot-build-tier.sh --tier desktop
#
# The Python builder handles dependency resolution, build ordering,
# DESTDIR staging, manifest tracking, and skip-built logic.
# ==========================================================================

set +h
set -e
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/var/log/igos-build
TIER=""

# --------------------------------------------------------------------------
# Parse arguments
# --------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tier)
            TIER="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 --tier <core|base|desktop>"
            exit 1
            ;;
    esac
done

if [ -z "$TIER" ]; then
    echo "ERROR: --tier argument is required"
    echo "Usage: $0 --tier <core|base|desktop>"
    exit 1
fi

mkdir -pv "$IGOS_LOGS"

TIER_LOG="${IGOS_LOGS}/${TIER}-build-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$TIER_LOG"
}

log ""
log "============================================"
log "  InterGenOS Tier Build: ${TIER}"
log "  Start: $(date)"
log "  Cores: $(nproc)"
log "============================================"
log ""

# ==========================================================================
# Step 1: Ensure PyYAML is available for the Python builder
# ==========================================================================

log "--- Checking Python dependencies for igos-build ---"

if python3 -c "import yaml" 2>/dev/null; then
    log "  PyYAML: already installed"
else
    log "  PyYAML: not found — installing..."

    # Try pip first (may work on some Python versions)
    if pip3 install --no-cache-dir PyYAML 2>/dev/null; then
        if python3 -c "import yaml" 2>/dev/null; then
            log "  PyYAML: installed via pip"
        else
            log "  PyYAML: pip reported success but import failed — using manual install"
            PIP_BROKEN=true
        fi
    else
        log "  PyYAML: pip failed — using manual install"
        PIP_BROKEN=true
    fi

    # Manual install fallback (required for Python 3.14 where pip is broken)
    if [ "${PIP_BROKEN:-}" = "true" ]; then
        # Python 3.14 ships without setuptools or distutils — bootstrap setuptools first
        if ! python3 -c "import setuptools" 2>/dev/null; then
            SETUPTOOLS_TAR=$(ls ${IGOS_SOURCES}/setuptools-*.tar.gz 2>/dev/null | head -1)
            if [ -z "$SETUPTOOLS_TAR" ]; then
                log "ERROR: No setuptools tarball found in $IGOS_SOURCES"
                exit 1
            fi
            log "  Bootstrapping setuptools from $SETUPTOOLS_TAR..."
            SETUPTOOLS_WORK=$(mktemp -d)
            tar -xf "$SETUPTOOLS_TAR" -C "$SETUPTOOLS_WORK" --strip-components=1
            SITE=$(python3 -c "import site; print(site.getsitepackages()[0])")
            cp -r "$SETUPTOOLS_WORK/setuptools" "$SITE/"
            cp -r "$SETUPTOOLS_WORK/_distutils_hack" "$SITE/"
            if [ -d "$SETUPTOOLS_WORK/setup.cfg" ]; then
                cp "$SETUPTOOLS_WORK/setup.cfg" "$SITE/"
            fi
            rm -rf "$SETUPTOOLS_WORK"
            if python3 -c "import setuptools" 2>/dev/null; then
                log "  setuptools: bootstrapped"
            else
                log "ERROR: Failed to bootstrap setuptools"
                exit 1
            fi
        fi

        PYYAML_TAR=$(ls ${IGOS_SOURCES}/PyYAML-*.tar.gz ${IGOS_SOURCES}/pyyaml-*.tar.gz 2>/dev/null | head -1)
        if [ -z "$PYYAML_TAR" ]; then
            log "ERROR: No PyYAML tarball found in $IGOS_SOURCES"
            log "       Download PyYAML from https://pypi.org/project/PyYAML/"
            exit 1
        fi

        log "  Installing PyYAML from $PYYAML_TAR..."
        PYYAML_WORK=$(mktemp -d)
        tar -xf "$PYYAML_TAR" -C "$PYYAML_WORK" --strip-components=1
        cd "$PYYAML_WORK"
        python3 setup.py install 2>&1 | tail -5
        cd /
        rm -rf "$PYYAML_WORK"

        if python3 -c "import yaml" 2>/dev/null; then
            log "  PyYAML: installed manually"
        else
            log "ERROR: Failed to install PyYAML — igos-build cannot run"
            exit 1
        fi
    fi
fi

# Verify
if ! python3 -c "import yaml; print(f'PyYAML {yaml.__version__}')" 2>/dev/null; then
    log "ERROR: PyYAML import test failed"
    exit 1
fi

log "  Python: $(python3 --version 2>&1)"
log "  PyYAML: $(python3 -c 'import yaml; print(yaml.__version__)')"

# ==========================================================================
# Step 2: Run the Python builder for the requested tier
# ==========================================================================

log ""
log "--- Running igos-build for ${TIER} tier ---"
log ""

cd /mnt/intergenos

python3 igos-build.py \
    --build \
    --tracked \
    --skip-built \
    --tier "$TIER" \
    --sources-dir "$IGOS_SOURCES" \
    2>&1 | tee -a "$TIER_LOG"

BUILD_RC=${PIPESTATUS[0]}

if [ $BUILD_RC -ne 0 ]; then
    log ""
    log "!!! ${TIER^} build failed (exit $BUILD_RC)"
    log "!!! Check logs in $IGOS_LOGS/"
    exit $BUILD_RC
fi

log ""
log "============================================"
log "  ${TIER^} build complete!"
log "  End: $(date)"
log "============================================"
```

### create-image.sh
```bash
#!/bin/bash
# InterGenOS — Package chroot into bootable disk image
#
# Takes the completed chroot at /mnt/igos and creates a bootable qcow2
# disk image suitable for a KVM virtual machine.
#
# Must run on the HOST (not inside the chroot).
# Requires: qemu-img, qemu-nbd, parted, mkfs.ext4
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/create-image.sh <output-path> [disk-size]
#
# Example:
#   sudo bash /mnt/intergenos/scripts/create-image.sh /mnt/intergenos/vm/intergenos.qcow2 500G

set -euo pipefail

CHROOT=/mnt/igos
IMAGE="${1:?Usage: create-image.sh <output-path.qcow2> [disk-size]}"
DISK_SIZE="${2:-500G}"
NBD_DEV=/dev/nbd0
MOUNT_POINT=/mnt/image-root

log() {
    echo "[IMAGE] $*"
}

err() {
    echo "[ERROR] $*" >&2
}

cleanup() {
    log "Cleaning up..."
    umount "${MOUNT_POINT}/sys" 2>/dev/null || true
    umount "${MOUNT_POINT}/proc" 2>/dev/null || true
    umount "${MOUNT_POINT}/dev/pts" 2>/dev/null || true
    umount "${MOUNT_POINT}/dev" 2>/dev/null || true
    umount "$MOUNT_POINT" 2>/dev/null || true
    qemu-nbd --disconnect "$NBD_DEV" 2>/dev/null || true
}

trap cleanup EXIT

# ============================================================================
# Preflight checks
# ============================================================================

if [ "$(id -u)" -ne 0 ]; then
    err "Must run as root"
    exit 1
fi

if [ ! -d "$CHROOT/usr/bin" ]; then
    err "Chroot at $CHROOT doesn't look valid (no /usr/bin)"
    exit 1
fi

if [ ! -f "$CHROOT/boot/vmlinuz-"* ] 2>/dev/null; then
    err "No kernel found in $CHROOT/boot/"
    exit 1
fi

for tool in qemu-img qemu-nbd parted mkfs.ext4; do
    if ! command -v "$tool" > /dev/null 2>&1; then
        err "Required tool not found: $tool"
        exit 1
    fi
done

# ============================================================================
# Step 1: Create qcow2 disk image
# ============================================================================

log "Creating ${DISK_SIZE} qcow2 image at ${IMAGE}..."
qemu-img create -f qcow2 "$IMAGE" "$DISK_SIZE"

# ============================================================================
# Step 2: Connect image as block device
# ============================================================================

log "Loading nbd module and connecting image..."
modprobe nbd max_part=8
qemu-nbd --connect="$NBD_DEV" "$IMAGE"

# Wait for device to appear
sleep 1

# ============================================================================
# Step 3: Partition the disk (GPT + BIOS boot)
# ============================================================================

log "Creating partition table..."
parted -s "$NBD_DEV" mklabel gpt
parted -s "$NBD_DEV" mkpart bios_grub 1MiB 2MiB
parted -s "$NBD_DEV" set 1 bios_grub on
parted -s "$NBD_DEV" mkpart root ext4 2MiB 100%

# Wait for partition devices
sleep 1
partprobe "$NBD_DEV" 2>/dev/null || true
sleep 1

# ============================================================================
# Step 4: Format root partition
# ============================================================================

log "Formatting root partition..."
mkfs.ext4 -L intergenos "${NBD_DEV}p2"

# ============================================================================
# Step 5: Mount and copy chroot contents
# ============================================================================

log "Mounting image and copying chroot..."
mkdir -p "$MOUNT_POINT"
mount "${NBD_DEV}p2" "$MOUNT_POINT"

# Use tar to preserve everything correctly
# --one-file-system avoids copying virtual filesystems (/proc, /sys, etc.)
tar -C "$CHROOT" --one-file-system -cf - . | tar -C "$MOUNT_POINT" -xf -

log "  Copy complete: $(du -sh "$MOUNT_POINT" | cut -f1)"

# ============================================================================
# Step 6: Create /etc/fstab
# ============================================================================

log "Writing /etc/fstab..."
cat > "${MOUNT_POINT}/etc/fstab" << 'EOF'
# /etc/fstab — InterGenOS
# <file system>  <mount point>  <type>  <options>         <dump>  <pass>
/dev/vda2         /              ext4    defaults          1       1
EOF

# ============================================================================
# Step 7: Create /etc/default/grub
# ============================================================================

log "Writing GRUB defaults..."
mkdir -p "${MOUNT_POINT}/etc/default"
cat > "${MOUNT_POINT}/etc/default/grub" << 'EOF'
# GRUB defaults for InterGenOS
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_DISTRIBUTOR="InterGenOS"
GRUB_CMDLINE_LINUX_DEFAULT=""
GRUB_CMDLINE_LINUX="root=/dev/vda2 console=tty0 console=ttyS0,115200"
GRUB_TERMINAL="console serial"
GRUB_SERIAL_COMMAND="serial --speed=115200"
GRUB_DISABLE_OS_PROBER=true
EOF

# ============================================================================
# Step 8: Install GRUB bootloader
# ============================================================================

log "Installing GRUB..."

# Bind mount host filesystems into the image
mount --bind /dev "${MOUNT_POINT}/dev"
mount --bind /dev/pts "${MOUNT_POINT}/dev/pts"
mount -t proc proc "${MOUNT_POINT}/proc"
mount -t sysfs sysfs "${MOUNT_POINT}/sys"

# Install GRUB to the disk (BIOS/i386-pc mode)
chroot "$MOUNT_POINT" grub-install --target=i386-pc "$NBD_DEV"

# Generate GRUB config
chroot "$MOUNT_POINT" grub-mkconfig -o /boot/grub/grub.cfg

# Unmount bind mounts
umount "${MOUNT_POINT}/sys"
umount "${MOUNT_POINT}/proc"
umount "${MOUNT_POINT}/dev/pts"
umount "${MOUNT_POINT}/dev"

# ============================================================================
# Step 8b: Apply post-deploy fixes for VM boot
# ============================================================================

log "Applying post-deploy fixes..."

# Enable serial console for VM management
chroot "$MOUNT_POINT" /bin/bash -c '
    ln -sf /usr/lib/systemd/system/serial-getty@.service \
        /etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service
'

# Enable networking (systemd-networkd + resolved)
chroot "$MOUNT_POINT" /bin/bash -c '
    ln -sf /usr/lib/systemd/system/systemd-networkd.service \
        /etc/systemd/system/multi-user.target.wants/systemd-networkd.service
    ln -sf /usr/lib/systemd/system/systemd-resolved.service \
        /etc/systemd/system/multi-user.target.wants/systemd-resolved.service
'

# Create DHCP network config
mkdir -p "${MOUNT_POINT}/etc/systemd/network"
cat > "${MOUNT_POINT}/etc/systemd/network/10-dhcp.network" << 'NETEOF'
[Match]
Name=en*

[Network]
DHCP=yes
NETEOF

# Set up DNS resolution via systemd-resolved
ln -sf /run/systemd/resolve/stub-resolv.conf "${MOUNT_POINT}/etc/resolv.conf"

# Set root password for initial access (no expiry for testing)
chroot "$MOUNT_POINT" /bin/bash -c '
    chpasswd <<< "root:intergenos"
    passwd -x 99999 root
'

log "  Post-deploy fixes applied (serial console, networking, DNS, root password)"

# ============================================================================
# Step 9: Unmount and disconnect
# ============================================================================

log "Unmounting image..."
umount "$MOUNT_POINT"

log "Disconnecting NBD..."
qemu-nbd --disconnect "$NBD_DEV"

# Clear the trap since we cleaned up manually
trap - EXIT

# ============================================================================
# Done
# ============================================================================

FINAL_SIZE=$(du -h "$IMAGE" | cut -f1)

log ""
log "============================================"
log "  InterGenOS disk image created"
log "  Image: $IMAGE"
log "  Size:  $FINAL_SIZE"
log "============================================"
log ""
log "  Create a VM with:"
log "    virt-install --name intergenos --ram 12288 --vcpus 12 \\"
log "      --cpu host-passthrough --machine q35 --os-variant linux2022 \\"
log "      --disk path=$IMAGE,format=qcow2,bus=virtio \\"
log "      --import --network network=default,model=virtio \\"
log "      --graphics vnc,listen=0.0.0.0 --video virtio --noautoconsole"
log ""
```
