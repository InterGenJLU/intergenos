#!/bin/bash
# InterGenOS Toolchain Build — Chapter 5 (Cross-Toolchain)
#
# This script runs INSIDE the build VM and builds the cross-compilation
# toolchain from the package templates and build.sh scripts.
#
# Environment setup follows LFS 13.0 Chapter 4.4, adapted for InterGenOS:
#   - $IGOS replaces $LFS
#   - $IGOS_TARGET replaces $LFS_TGT
#   - Sources read from /mnt/intergenos/build/sources/ (virtiofs)
#   - Patches read from /mnt/intergenos/build/patches/ (virtiofs)
#   - Build logs written to /mnt/intergenos/build/logs/
#
# Usage:
#   From the host: ssh christopher@192.168.122.69 'bash /mnt/intergenos/scripts/toolchain-build.sh'
#   Or inside VM:  bash /mnt/intergenos/scripts/toolchain-build.sh

# NOTE: Do NOT use 'set -e' — it interacts badly with piping and
# causes SIGPIPE (signal 13) to kill configure scripts.
# We handle errors explicitly per-phase instead.

# ============================================================================
# Environment (LFS 13.0 Chapter 4.4, adapted for InterGenOS)
# ============================================================================

# Disable bash hash (ensures newly built tools are found immediately)
set +h

umask 022

export IGOS=/mnt/igos
export IGOS_TARGET=x86_64-igos-linux-gnu
export IGOS_SOURCES=/mnt/intergenos/build/sources
export IGOS_PATCHES=/mnt/intergenos/build/patches
export IGOS_LOGS=/mnt/intergenos/build/logs
export IGOS_JOBS=$(nproc)

export LC_ALL=POSIX
export PATH=/usr/bin
if [ ! -L /bin ]; then PATH=/bin:$PATH; fi
export PATH=$IGOS/tools/bin:$PATH
export CONFIG_SITE=$IGOS/usr/share/config.site

# Create log directory
mkdir -pv $IGOS_LOGS

# ============================================================================
# Build helper functions
# ============================================================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$IGOS_LOGS/toolchain-build.log"
}

build_package() {
    local pkg_name="$1"
    local pkg_version="$2"
    local tarball="$3"
    local build_script="/mnt/intergenos/packages/toolchain/${pkg_name}/build.sh"
    local pkg_log="$IGOS_LOGS/${pkg_name}-$(date '+%Y%m%d-%H%M%S').log"

    log "=========================================="
    log "Building: $pkg_name $pkg_version"
    log "Log: $pkg_log"
    log "=========================================="

    # Create work directory
    local workdir="/tmp/igos-build/${pkg_name}"
    rm -rf "$workdir"
    mkdir -pv "$workdir"

    # Verify source integrity before extraction
    local yml="/mnt/intergenos/packages/toolchain/${pkg_name}/package.yml"
    local expected_sha256
    expected_sha256=$(grep 'sha256:' "$yml" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"' | tr -d "'")
    if [ -n "$expected_sha256" ] && [ "$expected_sha256" != "NEEDS_CHECKSUM" ]; then
        local actual_sha256
        actual_sha256=$(sha256sum "$IGOS_SOURCES/$tarball" | cut -d' ' -f1)
        if [ "$actual_sha256" != "$expected_sha256" ]; then
            log "FATAL: Checksum mismatch for $tarball"
            log "  expected: $expected_sha256"
            log "  actual:   $actual_sha256"
            return 1
        fi
        log "Checksum verified: $tarball"
    fi

    # Extract source
    log "Extracting: $tarball"
    tar -xf "$IGOS_SOURCES/$tarball" -C "$workdir" --strip-components=1 \
        --no-same-owner --no-same-permissions

    cd "$workdir"

    # Export version for build.sh scripts to reference as $PKG_VERSION
    export PKG_VERSION="$pkg_version"

    # Source the build script to get functions
    if [ ! -f "$build_script" ]; then
        log "ERROR: No build.sh found at $build_script"
        return 1
    fi

    source "$build_script"

    # Run build phases
    local start_time=$(date +%s)

    log "--- [CONFIGURE] ---"
    configure >> "$pkg_log" 2>&1
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "FAILED in configure phase (exit $rc)"
        log "Last 20 lines of log:"
        tail -20 "$pkg_log" | while read line; do log "  $line"; done
        return 1
    fi
    log "  configure completed successfully"

    log "--- [BUILD] ---"
    build >> "$pkg_log" 2>&1
    rc=$?
    if [ $rc -ne 0 ]; then
        log "FAILED in build phase (exit $rc)"
        log "Last 20 lines of log:"
        tail -20 "$pkg_log" | while read line; do log "  $line"; done
        return 1
    fi
    log "  build completed successfully"

    # Install (some packages define install, some don't)
    if type -t install | grep -q function; then
        log "--- [INSTALL] ---"
        install >> "$pkg_log" 2>&1
        rc=$?
        if [ $rc -ne 0 ]; then
            log "FAILED in install phase (exit $rc)"
            log "Last 20 lines of log:"
            tail -20 "$pkg_log" | while read line; do log "  $line"; done
            return 1
        fi
        log "  install completed successfully"
    fi

    local end_time=$(date +%s)
    local elapsed=$(( end_time - start_time ))

    log "SUCCESS: $pkg_name $pkg_version (${elapsed}s)"
    log ""

    # Clean up work directory
    cd /
    rm -rf "$workdir"

    return 0
}

# ============================================================================
# Chapter 5: Cross-Toolchain Build Order
# ============================================================================

TOTAL_START=$(date +%s)

log ""
log "============================================"
log "  InterGenOS Toolchain Build"
log "  LFS 13.0 Chapter 5: Cross-Toolchain"
log "  Target: $IGOS_TARGET"
log "  Jobs: $IGOS_JOBS"
log "  Start: $(date)"
log "============================================"
log ""

# Verify environment
log "Checking environment..."
log "  IGOS=$IGOS"
log "  IGOS_TARGET=$IGOS_TARGET"
log "  PATH=$PATH"
log "  IGOS_SOURCES=$IGOS_SOURCES"
log ""

if [ ! -d "$IGOS/tools" ]; then
    log "Creating $IGOS/tools..."
    mkdir -pv "$IGOS/tools"
fi

if [ ! -f "$IGOS_SOURCES/binutils-2.46.0.tar.xz" ]; then
    log "ERROR: Source tarballs not found at $IGOS_SOURCES"
    exit 1
fi

# --- 5.2: Binutils Pass 1 ---
build_package "binutils-pass1" "2.46.0" "binutils-2.46.0.tar.xz" || exit 1

# --- 5.3: GCC Pass 1 ---
# GCC needs GMP, MPFR, MPC extracted into its source tree
log "Preparing GCC Pass 1 (extracting bundled deps)..."
GCC_WORK="/tmp/igos-build/gcc-pass1"
rm -rf "$GCC_WORK"
mkdir -pv "$GCC_WORK"
tar -xf "$IGOS_SOURCES/gcc-15.2.0.tar.xz" -C "$GCC_WORK" --strip-components=1
cd "$GCC_WORK"
tar -xf "$IGOS_SOURCES/mpfr-4.2.2.tar.xz"
mv -v mpfr-4.2.2 mpfr
tar -xf "$IGOS_SOURCES/gmp-6.3.0.tar.xz"
mv -v gmp-6.3.0 gmp
tar -xf "$IGOS_SOURCES/mpc-1.3.1.tar.gz"
mv -v mpc-1.3.1 mpc

GCC_LOG="$IGOS_LOGS/gcc-pass1-$(date '+%Y%m%d-%H%M%S').log"
log "=========================================="
log "Building: gcc-pass1 15.2.0"
log "Log: $GCC_LOG"
log "=========================================="

export PKG_VERSION="15.2.0"
source /mnt/intergenos/packages/toolchain/gcc-pass1/build.sh

GCC_START=$(date +%s)
log "--- [CONFIGURE] ---"
configure >> "$GCC_LOG" 2>&1 || { log "FAILED in gcc-pass1 configure"; tail -20 "$GCC_LOG" | while read l; do log "  $l"; done; exit 1; }
log "  configure completed successfully"

log "--- [BUILD] ---"
build >> "$GCC_LOG" 2>&1 || { log "FAILED in gcc-pass1 build"; tail -20 "$GCC_LOG" | while read l; do log "  $l"; done; exit 1; }
log "  build completed successfully"

log "--- [INSTALL] ---"
install >> "$GCC_LOG" 2>&1 || { log "FAILED in gcc-pass1 install"; tail -20 "$GCC_LOG" | while read l; do log "  $l"; done; exit 1; }
log "  install completed successfully"

GCC_END=$(date +%s)
log "SUCCESS: gcc-pass1 15.2.0 ($((GCC_END - GCC_START))s)"
cd /
rm -rf "$GCC_WORK"

# --- 5.4: Linux API Headers ---
build_package "linux-headers" "6.18.10" "linux-6.18.10.tar.xz" || exit 1

# --- 5.5: Glibc ---
# Glibc needs the FHS patch applied first
log "Preparing Glibc (applying patches)..."
GLIBC_WORK="/tmp/igos-build/glibc"
rm -rf "$GLIBC_WORK"
mkdir -pv "$GLIBC_WORK"
tar -xf "$IGOS_SOURCES/glibc-2.43.tar.xz" -C "$GLIBC_WORK" --strip-components=1
cd "$GLIBC_WORK"
patch -Np1 -i "$IGOS_PATCHES/glibc-fhs-1.patch"

GLIBC_LOG="$IGOS_LOGS/glibc-$(date '+%Y%m%d-%H%M%S').log"
log "=========================================="
log "Building: glibc 2.43"
log "Log: $GLIBC_LOG"
log "=========================================="

export PKG_VERSION="2.43"
source /mnt/intergenos/packages/toolchain/glibc/build.sh

GLIBC_START=$(date +%s)
log "--- [CONFIGURE] ---"
configure >> "$GLIBC_LOG" 2>&1 || { log "FAILED in glibc configure"; tail -20 "$GLIBC_LOG" | while read l; do log "  $l"; done; exit 1; }
log "  configure completed successfully"

log "--- [BUILD] ---"
build >> "$GLIBC_LOG" 2>&1 || { log "FAILED in glibc build"; tail -20 "$GLIBC_LOG" | while read l; do log "  $l"; done; exit 1; }
log "  build completed successfully"

log "--- [INSTALL] ---"
install >> "$GLIBC_LOG" 2>&1 || { log "FAILED in glibc install"; tail -20 "$GLIBC_LOG" | while read l; do log "  $l"; done; exit 1; }
log "  install completed successfully"

log "--- [SANITY CHECK] ---"
check >> "$GLIBC_LOG" 2>&1 || { log "FAILED in glibc sanity check"; tail -20 "$GLIBC_LOG" | while read l; do log "  $l"; done; exit 1; }
log "  sanity check passed"

GLIBC_END=$(date +%s)
log "SUCCESS: glibc 2.43 ($((GLIBC_END - GLIBC_START))s)"
cd /
rm -rf "$GLIBC_WORK"

# --- 5.6: Libstdc++ ---
log "Preparing Libstdc++ (from GCC source)..."
LIBSTDCPP_WORK="/tmp/igos-build/libstdcpp"
rm -rf "$LIBSTDCPP_WORK"
mkdir -pv "$LIBSTDCPP_WORK"
tar -xf "$IGOS_SOURCES/gcc-15.2.0.tar.xz" -C "$LIBSTDCPP_WORK" --strip-components=1
cd "$LIBSTDCPP_WORK"

LIBSTDCPP_LOG="$IGOS_LOGS/libstdcpp-$(date '+%Y%m%d-%H%M%S').log"
log "=========================================="
log "Building: libstdcpp 15.2.0"
log "Log: $LIBSTDCPP_LOG"
log "=========================================="

export PKG_VERSION="15.2.0"
source /mnt/intergenos/packages/toolchain/libstdcpp/build.sh

LIBSTDCPP_START=$(date +%s)
log "--- [CONFIGURE] ---"
configure >> "$LIBSTDCPP_LOG" 2>&1 || { log "FAILED in libstdcpp configure"; tail -20 "$LIBSTDCPP_LOG" | while read l; do log "  $l"; done; exit 1; }
log "  configure completed successfully"

log "--- [BUILD] ---"
build >> "$LIBSTDCPP_LOG" 2>&1 || { log "FAILED in libstdcpp build"; tail -20 "$LIBSTDCPP_LOG" | while read l; do log "  $l"; done; exit 1; }
log "  build completed successfully"

log "--- [INSTALL] ---"
install >> "$LIBSTDCPP_LOG" 2>&1 || { log "FAILED in libstdcpp install"; tail -20 "$LIBSTDCPP_LOG" | while read l; do log "  $l"; done; exit 1; }
log "  install completed successfully"

LIBSTDCPP_END=$(date +%s)
log "SUCCESS: libstdcpp 15.2.0 ($((LIBSTDCPP_END - LIBSTDCPP_START))s)"
cd /
rm -rf "$LIBSTDCPP_WORK"

# ============================================================================
# Summary
# ============================================================================

TOTAL_END=$(date +%s)
TOTAL_ELAPSED=$(( TOTAL_END - TOTAL_START ))

log ""
log "============================================"
log "  TOOLCHAIN BUILD COMPLETE"
log "  Total time: ${TOTAL_ELAPSED}s ($(( TOTAL_ELAPSED / 60 ))m)"
log "  Packages built: 5"
log "    1. binutils-pass1 2.46.0"
log "    2. gcc-pass1 15.2.0"
log "    3. linux-headers 6.18.10"
log "    4. glibc 2.43"
log "    5. libstdcpp 15.2.0"
log ""
log "  Cross-toolchain installed to: $IGOS/tools/"
log "  Logs at: $IGOS_LOGS/"
log "============================================"
