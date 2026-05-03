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
    kernel
    desktop
    ai
    extra
    image
)

# ==========================================================================
# Argument parsing
# ==========================================================================

BUILD_USER=""
START_AT=""
STOP_AFTER=""
CHECKPOINT=false
ROOT_PASSWORD_ARG=""
USER_PASSWORD_ARG=""
IMAGE_USER_NAME="intergenos"

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
        --root-password)
            ROOT_PASSWORD_ARG="$2"
            shift 2
            ;;
        --user-password)
            USER_PASSWORD_ARG="$2"
            shift 2
            ;;
        --image-user)
            IMAGE_USER_NAME="$2"
            shift 2
            ;;
        -h|--help)
            head -30 "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: sudo bash $0 --user <username> --root-password <pw> --user-password <pw> [--image-user <name>] [--start-at <phase>] [--stop-after <phase>]"
            exit 1
            ;;
    esac
done

if [ -z "$BUILD_USER" ]; then
    echo "Error: --user <username> is required"
    echo "Usage: sudo bash $0 --user <username> --root-password <pw> --user-password <pw> [--image-user <name>] [--start-at <phase>] [--stop-after <phase>]"
    exit 1
fi

# Path 4: image credentials must be explicit. No defaults — the literal
# "intergenos" default has been retired (S1/S2 fleet vote A 2026-04-29).
# Path 3's first-boot greeter will overwrite these on the user's first
# boot, but the build-time creds must still be the builder's choice
# rather than a guessable shared default.
if [ -z "$ROOT_PASSWORD_ARG" ]; then
    echo "Error: --root-password <value> is required (no default permitted)"
    echo "       Path 3 first-boot greeter will prompt the end user to set their own"
    echo "       password on first boot; this build-time value is the brief-window"
    echo "       fallback that nobody normally encounters."
    echo "       Generate a strong one with e.g. 'pwgen -s 20 1' if unsure."
    exit 1
fi
if [ -z "$USER_PASSWORD_ARG" ]; then
    echo "Error: --user-password <value> is required (no default permitted)"
    exit 1
fi
export ROOT_PASSWORD="$ROOT_PASSWORD_ARG"
export IMAGE_USER_PASSWORD="$USER_PASSWORD_ARG"
export IMAGE_USER="$IMAGE_USER_NAME"

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
    local checkpoint="${CHECKPOINT_DIR}/intergenos-${phase}-$(date '+%Y%m%d-%H%M%S').tar.zst"

    log ""
    log ">>> Saving checkpoint: $checkpoint"

    mkdir -p "${CHECKPOINT_DIR}"

    # Remove any checkpoint tarballs that landed inside the chroot
    # (from previous runs with old CHECKPOINT_DIR) so they don't compound
    rm -f "${IGOS}/home/${BUILD_USER}"/intergenos-*.tar.gz 2>/dev/null || true
    rm -f "${IGOS}/home/${BUILD_USER}"/intergenos-*.tar.zst 2>/dev/null || true

    # Tear down chroot mounts temporarily for a clean snapshot
    bash "${SCRIPTS}/chroot-teardown.sh" > /dev/null 2>&1 || true

    local start_time=$(date +%s)
    tar -C "$IGOS" --one-file-system --zstd -cf "$checkpoint" . 2>&1

    local elapsed=$(( $(date +%s) - start_time ))
    local size=$(du -h "$checkpoint" | cut -f1)

    log ">>> Checkpoint saved: $size in ${elapsed}s"
    log ">>> Restore with: rm -rf ${IGOS}/* && tar -C ${IGOS} --zstd -xf ${checkpoint}"

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
            toolchain|core|kernel|desktop|ai)
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
    # -a (archive mode) recurses into directories and preserves attrs.
    # Without it, prior `cp -n` silently dropped directory-shaped sources
    # (e.g., libreoffice-externals/), surfaced as build halts much later.
    # 2>/dev/null was suppressing the "-r not specified; omitting directory"
    # warning that would have caught this; drop it so real errors stay visible.
    cp -an "${SOURCES}"/* "$IGOS/sources/" || true
    cp -an "${PATCHES}"/* "$IGOS/sources/" || true
    local placed=$(ls "$IGOS/sources" | wc -l)
    log "  Placed $placed files in $IGOS/sources/"

    # Copy build infrastructure (scripts, packages, igos-build)
    # Preserves paths so /mnt/intergenos/scripts/... works inside the chroot
    log "  Copying build infrastructure to $IGOS/mnt/intergenos/..."
    mkdir -pv "$IGOS/mnt/intergenos"
    cp -a /mnt/intergenos/scripts    "$IGOS/mnt/intergenos/"
    cp -a /mnt/intergenos/packages   "$IGOS/mnt/intergenos/"
    cp -a /mnt/intergenos/igos-build "$IGOS/mnt/intergenos/"
    # pkm is a runtime dependency of igos-build/tracker.py (per RFC v1
    # 2026-05-01: tracker imports pkm.database._sha256 for tracker/verifier
    # parity). Without this sync, desktop-phase Python orchestrator fails
    # with ModuleNotFoundError on import.
    cp -a /mnt/intergenos/pkm        "$IGOS/mnt/intergenos/"
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
    # ${TERM@Q} (bash 4.4+) literal-quotes the value so any command-substitution
    # syntax inside $TERM does not re-evaluate when su's shell parses the -c arg.
    su - "$BUILD_USER" -c "env -i HOME=/home/${BUILD_USER} TERM=${TERM@Q} bash ${SCRIPTS}/toolchain-build.sh" 2>&1 | tee -a "$BUILD_LOG"
    # Check if toolchain produced the expected output
    if [ ! -x "${IGOS}/tools/bin/${IGOS_TARGET}-gcc" ]; then
        log "ERROR: Toolchain build did not produce ${IGOS_TARGET}-gcc"
        return 1
    fi
    log "  Cross-toolchain verified: ${IGOS_TARGET}-gcc exists"

    # Temp tools (Ch 6) — cross-compiled utilities needed inside the chroot
    log "Running temp-tools build as $BUILD_USER (Ch 6)..."
    su - "$BUILD_USER" -c "env -i HOME=/home/${BUILD_USER} TERM=${TERM@Q} bash ${SCRIPTS}/temp-tools-build.sh" 2>&1 | tee -a "$BUILD_LOG"
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
    # Sync Python builder for desktop tier (igos-build + its pkm dependency
    # per RFC v1 tracker/verifier parity)
    rsync -a /mnt/intergenos/igos-build.py "$IGOS/mnt/intergenos/" 2>/dev/null || true
    rsync -a --delete /mnt/intergenos/igos-build/   "$IGOS/mnt/intergenos/igos-build/" 2>/dev/null || true
    rsync -a --delete /mnt/intergenos/pkm/          "$IGOS/mnt/intergenos/pkm/"        2>/dev/null || true
}

phase_core() {
    sync_chroot_scripts
    log "Building core system in chroot (Ch 8, LFS order)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-ch8.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_config() {
    # Clear IGOS_START_AT / IGOS_STOP_AFTER so per-package resume
    # context from one phase doesn't leak into subsequent phases
    # (config, core-extra, kernel).
    unset IGOS_START_AT IGOS_STOP_AFTER
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

phase_ai() {
    sync_chroot_scripts
    log "Building AI tier packages in chroot (InterGen assistant)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-ai.sh" 2>&1 | tee -a "$BUILD_LOG"
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
run_phase "ai"          "Build AI tier (InterGen assistant)"  phase_ai
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
