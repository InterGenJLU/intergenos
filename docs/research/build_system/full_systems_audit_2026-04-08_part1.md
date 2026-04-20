# InterGenOS Full Systems Audit — Part 1 of 5
# Master Orchestrator and Chroot Management Scripts

**Date:** 2026-04-08
**Prepared for:** External Security Auditors
**Project:** InterGenOS — AI-Integrated Linux Distribution
**Owner:** InterGenJLU
**License:** GPL-3.0-or-later
**Build Base:** LFS 13.0 (Systemd)

---

## Document Structure

This audit is split into 5 parts due to the volume of untruncated source code:

- **Part 1** (this file): Introduction, Master Orchestrator, Chroot Management Scripts
- **Part 2**: Build Phase Scripts (Toolchain, Temp-Tools, Ch7, Ch8, Ch9, Ch10, Core-Extra, Base, Desktop, Extra, Tier)
- **Part 3**: Python Build System (all modules), Package Functions, Image Creator, Host Check
- **Part 4**: Kernel Configuration Files (complete, 3521+ lines)
- **Part 5**: Complete Dependency Graph (all 541 packages with resolved dependencies)

---

## 1. System Overview

InterGenOS is a Linux distribution built entirely from source following Linux From Scratch 13.0 (Systemd variant). The build system consists of:

- A **master orchestrator** (`build-intergenos.sh`) that drives all phases
- **Chroot management scripts** for entering/exiting the build environment
- **Phase-specific bash scripts** for each build stage (toolchain, core, config, desktop, etc.)
- A **Python build system** (`igos-build/`) for dependency resolution and automated package building
- **YAML package templates** (`packages/`) defining 458+ packages across 5 tiers
- A **disk image creator** for producing bootable qcow2/raw images

### Build Pipeline

```
Build VM (Ubuntu 24.04) -> chroot at /mnt/igos -> build everything -> bootable disk image -> target VM/bare metal
```

### Package Tiers

| Tier | Purpose | Build Method |
|------|---------|-------------|
| toolchain | Cross-compilation (LFS Ch. 5-7) | Bash scripts |
| core | Full system (LFS Ch. 8 + TLS, PAM, glib2, curl, cmake) | Bash scripts (pkg-functions.sh) |
| base | End-user CLI tools | Bash scripts (pkg-functions.sh) |
| desktop | GNOME on Wayland (337 packages) | Python builder (igos-build) |
| extra | User-facing applications | Python builder (igos-build) |

---

## 2. Master Orchestrator: build-intergenos.sh

**Path:** `/mnt/intergenos/scripts/build-intergenos.sh`

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
CHECKPOINT_DIR="/mnt/intergenos/build/checkpoints"
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
    local checkpoint="${CHECKPOINT_DIR}/intergenos-${phase}-$(date '+%Y%m%d-%H%M%S').tar.gz"

    log ""
    log ">>> Saving checkpoint: $checkpoint"

    mkdir -p "${CHECKPOINT_DIR}"

    # Remove any checkpoint tarballs that landed inside the chroot
    # (from previous runs with old CHECKPOINT_DIR) so they don't compound
    rm -f "${IGOS}/home/${BUILD_USER}"/intergenos-*.tar.gz 2>/dev/null || true

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
    log "!!! Cleaning up..."

    # Tear down chroot mounts to prevent host filesystem corruption
    if [ -f "${SCRIPTS}/chroot-teardown.sh" ]; then
        bash "${SCRIPTS}/chroot-teardown.sh" >/dev/null 2>&1 || true
    fi

    # Kill any child processes spawned by this build
    pkill -P $$ 2>/dev/null || true

    log "!!! Resume with: sudo bash $0 --user $BUILD_USER --start-at ${CURRENT_PHASE:-validate}"
    log ""
    exit 130
}

trap cleanup SIGINT SIGTERM SIGHUP

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
    # Ensure chroot virtual filesystems are mounted.
    # When using --start-at to resume from a later phase, the chroot-prep
    # phase (which normally mounts these) is skipped. Without mounts,
    # chroot-enter.sh refuses to enter.
    if ! mountpoint -q "${IGOS}/dev" 2>/dev/null; then
        log "  Chroot not mounted — running chroot-setup.sh..."
        bash "${SCRIPTS}/chroot-setup.sh" 2>&1 | tee -a "$BUILD_LOG"
    fi

    # Sync scripts and packages into the chroot copy.
    # The setup phase copies build infrastructure to $IGOS/mnt/intergenos/,
    # but --start-at skips setup and code changes between restarts aren't
    # reflected. This ensures the chroot always has the latest.
    log "  Syncing scripts into chroot..."
    rsync -a --delete /mnt/intergenos/scripts/   "$IGOS/mnt/intergenos/scripts/"
    rsync -a --delete /mnt/intergenos/packages/  "$IGOS/mnt/intergenos/packages/"
    rsync -a --delete /mnt/intergenos/config/    "$IGOS/mnt/intergenos/config/" 2>/dev/null || true
    # Sync Python builder for desktop tier
    rsync -a /mnt/intergenos/igos-build.py "$IGOS/mnt/intergenos/" 2>/dev/null || true
    rsync -a --delete /mnt/intergenos/igos-build/   "$IGOS/mnt/intergenos/igos-build/" 2>/dev/null || true
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

    # Clean up build artifacts from the target filesystem.
    # These were placed during setup — the built system doesn't need them.
    # IMPORTANT: Do NOT remove kernel headers/source — they're needed for
    # out-of-tree module builds (NVIDIA, VirtualBox, etc.)
    log "  Cleaning build artifacts from target..."
    rm -rf "${IGOS}/mnt/intergenos"
    rm -rf "${IGOS}/sources"
    rm -rf "${IGOS}/tmp"/*
    mkdir -p "${IGOS}/tmp"
    chmod 1777 "${IGOS}/tmp"
    # Clean build work dirs but preserve kernel source/headers
    if [ -d "${IGOS}/mnt/intergenos/build/work" ]; then
        for d in "${IGOS}/mnt/intergenos/build/work"/*/; do
            case "$(basename "$d")" in
                linux*|kernel*) log "  Preserving $(basename "$d")" ;;
                *) rm -rf "$d" ;;
            esac
        done
    fi
    log "  Build artifacts removed"

    # Create the image — write to virtiofs-shared path so the host
    # can access it directly without copying through SSH
    local image_path="/mnt/intergenos/build/intergenos.qcow2"
    bash "${SCRIPTS}/create-image.sh" "$image_path" 500G 2>&1 | tee -a "$BUILD_LOG"

    log ""
    log "  Disk image created at: $image_path"
    log "  (accessible from host via virtiofs)"
    log ""
    log "  Create a VM with:"
    log "    cp ${image_path} /mnt/jarvis-storage/VMs/intergenos.qcow2"
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
    log "  Checkpoints: enabled (saving to ${CHECKPOINT_DIR}/)"
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

---

## 3. Chroot Setup: chroot-setup.sh

**Path:** `/mnt/intergenos/scripts/chroot-setup.sh`

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
# The chroot has no zoneinfo database until glibc is built in Ch. 8.
# Without the actual zoneinfo files, TZ=America/Chicago resolves to UTC.
# Fix: copy the host's zoneinfo tree for the local timezone into the chroot
# so timestamps are correct from the very first chroot command.
echo "--- Syncing host timezone into chroot ---"
if [ -f /etc/localtime ]; then
    cp -fL /etc/localtime $IGOS/etc/localtime
    echo "  Copied host /etc/localtime"

    if [ -f /etc/timezone ]; then
        cp -f /etc/timezone $IGOS/etc/timezone
        HOST_TZ="$(cat /etc/timezone)"
        echo "  Copied host /etc/timezone ($HOST_TZ)"

        # Copy the specific zoneinfo file so TZ= resolves before glibc Ch.8
        HOST_ZONEINFO="/usr/share/zoneinfo/$HOST_TZ"
        if [ -f "$HOST_ZONEINFO" ]; then
            mkdir -p "$IGOS/usr/share/zoneinfo/$(dirname "$HOST_TZ")"
            cp -f "$HOST_ZONEINFO" "$IGOS/usr/share/zoneinfo/$HOST_TZ"
            echo "  Copied $HOST_ZONEINFO into chroot"
        fi

        # Also copy the UTC/posix fallbacks that date/printf may need
        for tz_file in UTC posixrules; do
            src="/usr/share/zoneinfo/$tz_file"
            if [ -f "$src" ]; then
                cp -f "$src" "$IGOS/usr/share/zoneinfo/$tz_file"
            fi
        done
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

---

## 4. Chroot Entry: chroot-enter.sh

**Path:** `/mnt/intergenos/scripts/chroot-enter.sh`

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

# Capture host timezone for use inside chroot.
# The chroot has no zoneinfo database until glibc Ch.8, so Olson names
# like "America/Chicago" resolve to UTC. Instead, compute a POSIX TZ
# string (e.g., "CST6CDT") on the host where zoneinfo exists, and pass
# that in. POSIX TZ strings work without any zoneinfo files.
HOST_TZ_OLSON="$(cat /etc/timezone 2>/dev/null || echo UTC)"
HOST_TZ_POSIX="$(TZ="$HOST_TZ_OLSON" date +%Z 2>/dev/null || echo UTC)"
# Build a POSIX offset string: e.g., CST6CDT or EST5EDT
# date +%z gives offset like -0500, convert to hours
HOST_OFFSET="$(TZ="$HOST_TZ_OLSON" date +%z 2>/dev/null || echo +0000)"
OFFSET_SIGN="${HOST_OFFSET:0:1}"
OFFSET_HH="${HOST_OFFSET:1:2}"
# POSIX TZ offsets are inverted: UTC-5 is expressed as XXX5
# Strip leading zero for POSIX format
OFFSET_NUM=$((10#$OFFSET_HH))
if [ "$OFFSET_SIGN" = "-" ]; then
    POSIX_OFFSET="$OFFSET_NUM"
else
    POSIX_OFFSET="-$OFFSET_NUM"
fi
# Use the abbreviated zone name with the offset
HOST_TZ="${HOST_TZ_POSIX}${POSIX_OFFSET}"

# Enter the chroot with a clean environment
# env -i clears ALL host environment variables
# Only HOME, TERM, TZ, PS1, PATH, MAKEFLAGS, TESTSUITEFLAGS survive
chroot "$IGOS" /usr/bin/env -i               \
    HOME=/root                               \
    TERM="$TERM"                             \
    TZ="$HOST_TZ"                            \
    PS1='\[\e[1;34m\][\[\e[m\]\[\e[1;31m\](igos-chroot)\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;34m\][\[\e[m\]\[\e[1;37m\]<\[\e[m\]\[\e[1;32m\]\w\[\e[m\]\[\e[1;37m\]>\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;37m\]:\[\e[m\]\[\e[1;31m\]#\[\e[m\] ' \
    PATH=/usr/bin:/usr/sbin:/bin:/sbin        \
    MAKEFLAGS="-j${JOBS}"                    \
    TESTSUITEFLAGS="-j${JOBS}"               \
    IGOS_JOBS="${JOBS}"                       \
    IGOS_SOURCES=/sources                    \
    IGOS_PATCHES=/sources                    \
    IGOS_LOGS=/mnt/intergenos/build/logs            \
    PKG_VERSION=""                           \
    IGOS_START_AT="${IGOS_START_AT:-}"       \
    $CHROOT_CMD
```

---

## 5. Chroot Teardown: chroot-teardown.sh

**Path:** `/mnt/intergenos/scripts/chroot-teardown.sh`

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

# Defensive validation — if $IGOS is empty or "/", unmounting
# would target host filesystems, which is catastrophic.
if [ -z "$IGOS" ] || [ "$IGOS" = "/" ]; then
    echo "ERROR: \$IGOS is empty or '/' — refusing to unmount host filesystems"
    exit 1
fi

if [ ! -d "$IGOS" ]; then
    echo "WARNING: $IGOS does not exist — nothing to unmount"
    exit 0
fi

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

---

**End of Part 1. Continue to Part 2 for all build phase scripts.**
