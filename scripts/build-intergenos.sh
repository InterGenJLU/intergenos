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
#
# Phases (in order):
#   validate     — Verify host meets all build requirements
#   setup        — Create build root, verify sources and patches
#   toolchain    — Cross-compilation toolchain (LFS Chapters 5-6)
#   chroot-prep  — Mount virtual filesystems for chroot (Chapter 7 prep)
#   chroot-tools — Build temporary tools inside chroot (Chapter 7)
#   core         — Build 82 core packages in chroot (Chapter 8)
#   config       — System configuration in chroot (Chapter 9)
#   core-extra   — Build 19 additional core packages in chroot
#   base         — Build 20 base packages in chroot
#   image        — Package chroot into bootable disk image
#
# Controls:
#   --start-at <phase>   Start (or resume) at a specific phase
#   --stop-after <phase> Stop after the named phase completes
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
PHASE_FILE="${IGOS}/.build-phase"
STOP_FILE="${IGOS}/.build-stop"
BUILD_LOG="${LOGS}/build-intergenos.log"

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
    image
)

# ==========================================================================
# Argument parsing
# ==========================================================================

BUILD_USER=""
START_AT=""
STOP_AFTER=""

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
    log "  Sources: $total tarballs in $SOURCES"

    # Verify patches
    if [ ! -f "${PATCHES}/glibc-fhs-1.patch" ]; then
        log "ERROR: glibc-fhs-1.patch missing from $PATCHES"
        return 1
    fi
    log "  Patches: OK"
    log "  Build root: $IGOS ready"
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

phase_core() {
    log "Building 82 core packages in chroot (this will take a while)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-ch8.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_config() {
    log "Configuring system in chroot..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-config-ch9.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_core_extra() {
    log "Building 19 additional core packages in chroot..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-core-extra.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_base() {
    log "Building 20 base packages in chroot..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-base.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_image() {
    log "Packaging chroot into bootable disk image..."

    # Tear down chroot mounts before imaging
    log "  Tearing down chroot mounts..."
    bash "${SCRIPTS}/chroot-teardown.sh" 2>&1 | tee -a "$BUILD_LOG" || true

    # Create the image on VM local storage (fast)
    local image_path="/home/${BUILD_USER}/intergenos.qcow2"
    bash "${SCRIPTS}/create-image.sh" "$image_path" 500G 2>&1 | tee -a "$BUILD_LOG"

    log ""
    log "  Disk image created at: $image_path (on build VM)"
    log ""
    log "  To use it, copy to the host and create a VM:"
    log "    scp ${BUILD_USER}@<vm-ip>:${image_path} /mnt/jarvis-storage/VMs/intergenos.qcow2"
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
log "================================================================"

run_phase "validate"     "Verify host requirements"            phase_validate
run_phase "setup"        "Create build environment"            phase_setup
run_phase "toolchain"    "Cross-compilation toolchain (Ch 5-6)" phase_toolchain
run_phase "chroot-prep"  "Prepare chroot environment (Ch 7)"   phase_chroot_prep
run_phase "chroot-tools" "Build temp tools in chroot (Ch 7)"   phase_chroot_tools
run_phase "core"         "Build 82 core packages (Ch 8)"       phase_core
run_phase "config"       "System configuration (Ch 9)"         phase_config
run_phase "core-extra"   "Build 19 extra core packages"        phase_core_extra
run_phase "base"         "Build 20 base packages"              phase_base
run_phase "image"        "Create bootable disk image"          phase_image

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
