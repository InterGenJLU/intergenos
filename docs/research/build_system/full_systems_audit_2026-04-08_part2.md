# InterGenOS Full Systems Audit — Part 2 of 5
# Build Phase Scripts (Toolchain through Desktop)

**Date:** 2026-04-08
**Prepared for:** External Security Auditors

---

## 1. Toolchain Build: toolchain-build.sh

**Path:** `/mnt/intergenos/scripts/toolchain-build.sh`

```bash
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

    # Extract source
    log "Extracting: $tarball"
    tar -xf "$IGOS_SOURCES/$tarball" -C "$workdir" --strip-components=1

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
```

---

## 2. Temporary Tools Build: temp-tools-build.sh

**Path:** `/mnt/intergenos/scripts/temp-tools-build.sh`

```bash
#!/bin/bash
# InterGenOS Temporary Tools Build — Chapter 6 (Cross-Compiled Temporary Tools)
#
# These packages are cross-compiled using the toolchain built in Chapter 5.
# Commands match LFS 13.0 book EXACTLY (substituting $IGOS for $LFS and
# $IGOS_TARGET for $LFS_TGT). No deviations from the book for temp tools.
#
# Usage:
#   ssh christopher@192.168.122.69 'nohup bash /mnt/intergenos/scripts/temp-tools-build.sh > /mnt/intergenos/build/logs/temp-tools-stdout.log 2>&1 &'

# Disable bash hash
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

mkdir -pv $IGOS_LOGS

# ============================================================================
# Logging
# ============================================================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$IGOS_LOGS/temp-tools-build.log"
}

# ============================================================================
# Build helper — exact LFS commands, no abstractions
# ============================================================================

run_pkg() {
    local name="$1"
    local version="$2"
    local tarball="$3"
    shift 3
    # Remaining args are the exact build commands as a heredoc

    export PKG_VERSION="$version"

    local pkg_log="$IGOS_LOGS/${name}-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    log "=========================================="
    log "Building: $name $version"
    log "Log: $pkg_log"
    log "=========================================="

    rm -rf "$workdir"
    mkdir -pv "$workdir"
    tar -xf "$IGOS_SOURCES/$tarball" -C "$workdir" --strip-components=1
    cd "$workdir"

    local start=$(date +%s)

    # Run the build commands, logging everything
    bash -e "$@" >> "$pkg_log" 2>&1
    local rc=$?

    local elapsed=$(( $(date +%s) - start ))

    if [ $rc -ne 0 ]; then
        log "FAILED: $name $version (${elapsed}s, exit $rc)"
        log "Last 20 lines:"
        tail -20 "$pkg_log" | while read l; do log "  $l"; done
        cd /
        return 1
    fi

    log "SUCCESS: $name $version (${elapsed}s)"
    log ""
    cd /
    rm -rf "$workdir"
    return 0
}

# ============================================================================
# Chapter 6 Build Order — LFS 13.0 commands verbatim
# ============================================================================

TOTAL_START=$(date +%s)

log ""
log "============================================"
log "  InterGenOS Temporary Tools Build"
log "  LFS 13.0 Chapter 6: Cross-Compiled Tools"
log "  Target: $IGOS_TARGET"
log "  Jobs: $IGOS_JOBS"
log "  Start: $(date)"
log "============================================"
log ""

# Verify cross-toolchain
if [ ! -x "$IGOS/tools/bin/${IGOS_TARGET}-gcc" ]; then
    log "ERROR: Cross-compiler not found. Run toolchain-build.sh first."
    exit 1
fi
log "Cross-compiler: $($IGOS/tools/bin/${IGOS_TARGET}-gcc --version | head -1)"
log ""

# --- 6.2: M4 ---
cat > /tmp/build-m4.sh << 'BUILDEOF'
./configure --prefix=/usr --host=$IGOS_TARGET --build=$(build-aux/config.guess)
make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
BUILDEOF
run_pkg "m4" "1.4.21" "m4-1.4.21.tar.xz" /tmp/build-m4.sh || exit 1

# --- 6.3: Ncurses ---
cat > /tmp/build-ncurses.sh << 'BUILDEOF'
mkdir build
pushd build
  ../configure --prefix=$IGOS/tools AWK=gawk
  make -C include
  make -C progs tic
  install progs/tic $IGOS/tools/bin
popd

./configure --prefix=/usr                \
            --host=$IGOS_TARGET          \
            --build=$(./config.guess)    \
            --mandir=/usr/share/man      \
            --with-manpage-format=normal \
            --with-shared                \
            --without-normal             \
            --with-cxx-shared            \
            --without-debug              \
            --without-ada                \
            --disable-stripping          \
            AWK=gawk

make -j${IGOS_JOBS}
make DESTDIR=$IGOS TIC_PATH=$(pwd)/build/progs/tic install
ln -sv libncursesw.so $IGOS/usr/lib/libncurses.so
sed -e 's/^#if.*XOPEN.*$/#if 1/' -i $IGOS/usr/include/curses.h
BUILDEOF
run_pkg "ncurses" "6.6" "ncurses-6.6.tar.gz" /tmp/build-ncurses.sh || exit 1

# --- 6.4: Bash ---
cat > /tmp/build-bash.sh << 'BUILDEOF'
./configure --prefix=/usr                      \
            --build=$(sh support/config.guess) \
            --host=$IGOS_TARGET                \
            --without-bash-malloc

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
ln -sv bash $IGOS/bin/sh
BUILDEOF
run_pkg "bash" "5.3" "bash-5.3.tar.gz" /tmp/build-bash.sh || exit 1

# --- 6.5: Coreutils ---
cat > /tmp/build-coreutils.sh << 'BUILDEOF'
./configure --prefix=/usr                     \
            --host=$IGOS_TARGET               \
            --build=$(build-aux/config.guess) \
            --enable-install-program=hostname \
            --enable-no-install-program=kill,uptime

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install

mv -v $IGOS/usr/bin/chroot              $IGOS/usr/sbin
mkdir -pv $IGOS/usr/share/man/man8
mv -v $IGOS/usr/share/man/man1/chroot.1 $IGOS/usr/share/man/man8/chroot.8
sed -i 's/"1"/"8"/' $IGOS/usr/share/man/man8/chroot.8
BUILDEOF
run_pkg "coreutils" "9.10" "coreutils-9.10.tar.xz" /tmp/build-coreutils.sh || exit 1

# --- 6.6: Diffutils ---
cat > /tmp/build-diffutils.sh << 'BUILDEOF'
./configure --prefix=/usr   \
            --host=$IGOS_TARGET \
            gl_cv_func_strcasecmp_works=y \
            --build=$(./build-aux/config.guess)

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
BUILDEOF
run_pkg "diffutils" "3.12" "diffutils-3.12.tar.xz" /tmp/build-diffutils.sh || exit 1

# --- 6.7: File ---
cat > /tmp/build-file.sh << 'BUILDEOF'
mkdir build
pushd build
  ../configure --disable-bzlib      \
               --disable-libseccomp \
               --disable-xzlib      \
               --disable-zlib
  make
popd

./configure --prefix=/usr --host=$IGOS_TARGET --build=$(./config.guess)
make FILE_COMPILE=$(pwd)/build/src/file -j${IGOS_JOBS}
make DESTDIR=$IGOS install
rm -v $IGOS/usr/lib/libmagic.la
BUILDEOF
run_pkg "file" "5.46" "file-5.46.tar.gz" /tmp/build-file.sh || exit 1

# --- 6.8: Findutils ---
cat > /tmp/build-findutils.sh << 'BUILDEOF'
./configure --prefix=/usr                   \
            --localstatedir=/var/lib/locate \
            --host=$IGOS_TARGET             \
            --build=$(build-aux/config.guess)

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
BUILDEOF
run_pkg "findutils" "4.10.0" "findutils-4.10.0.tar.xz" /tmp/build-findutils.sh || exit 1

# --- 6.9: Gawk ---
cat > /tmp/build-gawk.sh << 'BUILDEOF'
sed -i 's/extras//' Makefile.in

./configure --prefix=/usr   \
            --host=$IGOS_TARGET \
            --build=$(build-aux/config.guess)

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
BUILDEOF
run_pkg "gawk" "5.3.2" "gawk-5.3.2.tar.xz" /tmp/build-gawk.sh || exit 1

# --- 6.10: Grep ---
cat > /tmp/build-grep.sh << 'BUILDEOF'
./configure --prefix=/usr   \
            --host=$IGOS_TARGET \
            --build=$(./build-aux/config.guess)

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
BUILDEOF
run_pkg "grep" "3.12" "grep-3.12.tar.xz" /tmp/build-grep.sh || exit 1

# --- 6.11: Gzip ---
cat > /tmp/build-gzip.sh << 'BUILDEOF'
./configure --prefix=/usr --host=$IGOS_TARGET

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
BUILDEOF
run_pkg "gzip" "1.14" "gzip-1.14.tar.xz" /tmp/build-gzip.sh || exit 1

# --- 6.12: Make ---
cat > /tmp/build-make.sh << 'BUILDEOF'
./configure --prefix=/usr   \
            --host=$IGOS_TARGET \
            --build=$(build-aux/config.guess)

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
BUILDEOF
run_pkg "make" "4.4.1" "make-4.4.1.tar.gz" /tmp/build-make.sh || exit 1

# --- 6.13: Patch ---
cat > /tmp/build-patch.sh << 'BUILDEOF'
./configure --prefix=/usr   \
            --host=$IGOS_TARGET \
            --build=$(build-aux/config.guess)

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
BUILDEOF
run_pkg "patch" "2.8" "patch-2.8.tar.xz" /tmp/build-patch.sh || exit 1

# --- 6.14: Sed ---
cat > /tmp/build-sed.sh << 'BUILDEOF'
./configure --prefix=/usr   \
            --host=$IGOS_TARGET \
            --build=$(./build-aux/config.guess)

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
BUILDEOF
run_pkg "sed" "4.9" "sed-4.9.tar.xz" /tmp/build-sed.sh || exit 1

# --- 6.15: Tar ---
cat > /tmp/build-tar.sh << 'BUILDEOF'
./configure --prefix=/usr   \
            --host=$IGOS_TARGET \
            --build=$(build-aux/config.guess)

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
BUILDEOF
run_pkg "tar" "1.35" "tar-1.35.tar.xz" /tmp/build-tar.sh || exit 1

# --- 6.16: Xz ---
cat > /tmp/build-xz.sh << 'BUILDEOF'
./configure --prefix=/usr                     \
            --host=$IGOS_TARGET               \
            --build=$(build-aux/config.guess) \
            --disable-static                  \
            --docdir=/usr/share/doc/xz-$PKG_VERSION

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
rm -v $IGOS/usr/lib/liblzma.la
BUILDEOF
run_pkg "xz" "5.8.2" "xz-5.8.2.tar.xz" /tmp/build-xz.sh || exit 1

# --- 6.17: Binutils Pass 2 ---
cat > /tmp/build-binutils2.sh << 'BUILDEOF'
sed '6031s/$add_dir//' -i ltmain.sh

mkdir -v build
cd       build

../configure                   \
    --prefix=/usr              \
    --build=$(../config.guess) \
    --host=$IGOS_TARGET        \
    --disable-nls              \
    --enable-shared            \
    --enable-gprofng=no        \
    --disable-werror           \
    --enable-64-bit-bfd        \
    --enable-new-dtags         \
    --enable-default-hash-style=gnu

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
rm -v $IGOS/usr/lib/lib{bfd,ctf,ctf-nobfd,opcodes,sframe}.{a,la}
BUILDEOF
run_pkg "binutils-pass2" "2.46.0" "binutils-2.46.0.tar.xz" /tmp/build-binutils2.sh || exit 1

# --- 6.18: GCC Pass 2 ---
log "Preparing GCC Pass 2 (extracting bundled deps)..."
GCC2_WORK="/tmp/igos-build/gcc-pass2"
rm -rf "$GCC2_WORK"
mkdir -pv "$GCC2_WORK"
tar -xf "$IGOS_SOURCES/gcc-15.2.0.tar.xz" -C "$GCC2_WORK" --strip-components=1
cd "$GCC2_WORK"
tar -xf "$IGOS_SOURCES/mpfr-4.2.2.tar.xz"
mv -v mpfr-4.2.2 mpfr
tar -xf "$IGOS_SOURCES/gmp-6.3.0.tar.xz"
mv -v gmp-6.3.0 gmp
tar -xf "$IGOS_SOURCES/mpc-1.3.1.tar.gz"
mv -v mpc-1.3.1 mpc

GCC2_LOG="$IGOS_LOGS/gcc-pass2-$(date '+%Y%m%d-%H%M%S').log"
export PKG_VERSION="15.2.0"

log "=========================================="
log "Building: gcc-pass2 15.2.0"
log "Log: $GCC2_LOG"
log "=========================================="

GCC2_START=$(date +%s)

bash -e << 'GCCEOF' >> "$GCC2_LOG" 2>&1
case $(uname -m) in
  x86_64)
    sed -e '/m64=/s/lib64/lib/' \
        -i.orig gcc/config/i386/t-linux64
  ;;
esac

sed '/thread_header =/s/@.*@/gthr-posix.h/' \
    -i libgcc/Makefile.in libstdc++-v3/include/Makefile.in

mkdir -v build
cd       build

../configure                                    \
    --build=$(../config.guess)                  \
    --host=$IGOS_TARGET                         \
    --target=$IGOS_TARGET                       \
    --prefix=/usr                               \
    --with-build-sysroot=$IGOS                  \
    --enable-default-pie                        \
    --enable-default-ssp                        \
    --disable-nls                               \
    --disable-multilib                          \
    --disable-libatomic                         \
    --disable-libgomp                           \
    --disable-libquadmath                       \
    --disable-libsanitizer                      \
    --disable-libssp                            \
    --disable-libvtv                            \
    --enable-languages=c,c++                    \
    LDFLAGS_FOR_TARGET=-L$PWD/$IGOS_TARGET/libgcc

make -j${IGOS_JOBS}
make DESTDIR=$IGOS install
ln -sv gcc $IGOS/usr/bin/cc
GCCEOF

GCC2_RC=$?
GCC2_END=$(date +%s)

if [ $GCC2_RC -ne 0 ]; then
    log "FAILED: gcc-pass2 15.2.0 ($((GCC2_END - GCC2_START))s, exit $GCC2_RC)"
    log "Last 20 lines:"
    tail -20 "$GCC2_LOG" | while read l; do log "  $l"; done
    exit 1
fi
log "SUCCESS: gcc-pass2 15.2.0 ($((GCC2_END - GCC2_START))s)"
cd /
rm -rf "$GCC2_WORK"

# ============================================================================
# Summary
# ============================================================================

TOTAL_END=$(date +%s)
TOTAL_ELAPSED=$(( TOTAL_END - TOTAL_START ))

log ""
log "============================================"
log "  TEMPORARY TOOLS BUILD COMPLETE"
log "  Total time: ${TOTAL_ELAPSED}s ($(( TOTAL_ELAPSED / 60 ))m)"
log "  Packages: 18 (m4 through gcc-pass2)"
log "  Logs at: $IGOS_LOGS/"
log "============================================"
```

---

## 3. Chroot Build (Chapter 7): chroot-build.sh

**Path:** `/mnt/intergenos/scripts/chroot-build.sh`

```bash
#!/bin/bash
# InterGenOS Chroot Build — LFS 13.0 Sections 7.5-7.12
#
# Runs INSIDE the chroot (launched via chroot-enter.sh).
# Creates directory layout, essential files, and builds 6 packages.
# All commands match LFS 13.0 book verbatim.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh /mnt/intergenos/scripts/chroot-build.sh

set +h
umask 022

IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)

mkdir -p "$IGOS_LOGS"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$IGOS_LOGS/chroot-build.log"
}

log ""
log "============================================"
log "  InterGenOS Chroot Build"
log "  LFS 13.0 Chapter 7.5-7.12"
log "  Start: $(date)"
log "============================================"
log ""

# ============================================================================
# 7.5: Creating Directories
# ============================================================================

log "--- 7.5: Creating Directories ---"

mkdir -pv /{boot,home,mnt,opt,srv}
mkdir -pv /etc/{opt,sysconfig}
mkdir -pv /lib/firmware
mkdir -pv /media/{floppy,cdrom}
mkdir -pv /usr/{,local/}{include,src}
mkdir -pv /usr/lib/locale
mkdir -pv /usr/local/{bin,lib,sbin}
mkdir -pv /usr/{,local/}share/{color,dict,doc,info,locale,man}
mkdir -pv /usr/{,local/}share/{misc,terminfo,zoneinfo}
mkdir -pv /usr/{,local/}share/man/man{1..8}
mkdir -pv /var/{cache,local,log,mail,opt,spool}
mkdir -pv /var/lib/{color,misc,locate}

ln -sfv /run /var/run
ln -sfv /run/lock /var/lock

install -dv -m 0750 /root
install -dv -m 1777 /tmp /var/tmp

log "  Directories created"

# ============================================================================
# 7.6: Creating Essential Files and Symlinks
# ============================================================================

log "--- 7.6: Creating Essential Files ---"

# /etc/mtab
ln -sv /proc/self/mounts /etc/mtab

# /etc/hosts
cat > /etc/hosts << EOF
127.0.0.1  localhost $(hostname)
::1        localhost
EOF

# /etc/passwd
cat > /etc/passwd << "EOF"
root:x:0:0:root:/root:/bin/bash
bin:x:1:1:bin:/dev/null:/usr/bin/false
daemon:x:6:6:Daemon User:/dev/null:/usr/bin/false
messagebus:x:18:18:D-Bus Message Daemon User:/run/dbus:/usr/bin/false
systemd-journal-gateway:x:73:73:systemd Journal Gateway:/:/usr/bin/false
systemd-journal-remote:x:74:74:systemd Journal Remote:/:/usr/bin/false
systemd-journal-upload:x:75:75:systemd Journal Upload:/:/usr/bin/false
systemd-network:x:76:76:systemd Network Management:/:/usr/bin/false
systemd-resolve:x:77:77:systemd Resolver:/:/usr/bin/false
systemd-timesync:x:78:78:systemd Time Synchronization:/:/usr/bin/false
systemd-coredump:x:79:79:systemd Core Dumper:/:/usr/bin/false
uuidd:x:80:80:UUID Generation Daemon User:/dev/null:/usr/bin/false
systemd-oom:x:81:81:systemd Out Of Memory Daemon:/:/usr/bin/false
nobody:x:65534:65534:Unprivileged User:/dev/null:/usr/bin/false
EOF

# /etc/group
cat > /etc/group << "EOF"
root:x:0:
bin:x:1:daemon
sys:x:2:
kmem:x:3:
tape:x:4:
tty:x:5:
daemon:x:6:
floppy:x:7:
disk:x:8:
lp:x:9:
dialout:x:10:
audio:x:11:
video:x:12:
utmp:x:13:
clock:x:14:
cdrom:x:15:
adm:x:16:
messagebus:x:18:
systemd-journal:x:23:
input:x:24:
mail:x:34:
kvm:x:61:
systemd-journal-gateway:x:73:
systemd-journal-remote:x:74:
systemd-journal-upload:x:75:
systemd-network:x:76:
systemd-resolve:x:77:
systemd-timesync:x:78:
systemd-coredump:x:79:
uuidd:x:80:
systemd-oom:x:81:
wheel:x:97:
users:x:999:
nogroup:x:65534:
EOF

# Test user (needed by some Chapter 8 test suites)
echo "tester:x:101:101::/home/tester:/bin/bash" >> /etc/passwd
echo "tester:x:101:" >> /etc/group
install -o tester -d /home/tester

# Initialize log files
touch /var/log/{btmp,lastlog,faillog,wtmp}
chgrp -v utmp /var/log/lastlog
chmod -v 664  /var/log/lastlog
chmod -v 600  /var/log/btmp

log "  Essential files created"

# ============================================================================
# Build helper
# ============================================================================

build_in_chroot() {
    local name="$1"
    local version="$2"
    local tarball="$3"
    shift 3

    export PKG_VERSION="$version"

    local pkg_log="$IGOS_LOGS/${name}-chroot-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    log "=========================================="
    log "Building: $name $version (in chroot)"
    log "Log: $pkg_log"
    log "=========================================="

    rm -rf "$workdir"
    mkdir -pv "$workdir"
    tar -xf "$IGOS_SOURCES/$tarball" -C "$workdir" --strip-components=1
    cd "$workdir"

    local start=$(date +%s)

    # Run the build commands
    bash -e "$@" >> "$pkg_log" 2>&1
    local rc=$?

    local elapsed=$(( $(date +%s) - start ))

    if [ $rc -ne 0 ]; then
        log "FAILED: $name $version (${elapsed}s, exit $rc)"
        log "Last 20 lines:"
        tail -20 "$pkg_log" | while read l; do log "  $l"; done
        return 1
    fi

    log "SUCCESS: $name $version (${elapsed}s)"
    log ""
    cd /
    rm -rf "$workdir"
    return 0
}

# ============================================================================
# 7.7: Gettext
# ============================================================================

cat > /tmp/build-gettext-chroot.sh << 'BUILDEOF'
./configure --disable-shared
make -j${IGOS_JOBS}
cp -v gettext-tools/src/{msgfmt,msgmerge,xgettext} /usr/bin
BUILDEOF
build_in_chroot "gettext" "1.0" "gettext-1.0.tar.xz" /tmp/build-gettext-chroot.sh || exit 1

# ============================================================================
# 7.8: Bison
# ============================================================================

cat > /tmp/build-bison-chroot.sh << 'BUILDEOF'
./configure --prefix=/usr --docdir=/usr/share/doc/bison-$PKG_VERSION
make -j${IGOS_JOBS}
make install
BUILDEOF
build_in_chroot "bison" "3.8.2" "bison-3.8.2.tar.xz" /tmp/build-bison-chroot.sh || exit 1

# ============================================================================
# 7.9: Perl
# ============================================================================

cat > /tmp/build-perl-chroot.sh << 'BUILDEOF'
sh Configure -des                                         \
             -D prefix=/usr                               \
             -D vendorprefix=/usr                         \
             -D useshrplib                                \
             -D privlib=/usr/lib/perl5/5.42/core_perl     \
             -D archlib=/usr/lib/perl5/5.42/core_perl     \
             -D sitelib=/usr/lib/perl5/5.42/site_perl     \
             -D sitearch=/usr/lib/perl5/5.42/site_perl    \
             -D vendorlib=/usr/lib/perl5/5.42/vendor_perl \
             -D vendorarch=/usr/lib/perl5/5.42/vendor_perl

make -j${IGOS_JOBS}
make install
BUILDEOF
build_in_chroot "perl" "5.42.0" "perl-5.42.0.tar.xz" /tmp/build-perl-chroot.sh || exit 1

# ============================================================================
# 7.10: Python
# ============================================================================

cat > /tmp/build-python-chroot.sh << 'BUILDEOF'
./configure --prefix=/usr   \
            --enable-shared \
            --without-ensurepip

make -j${IGOS_JOBS}
make install
BUILDEOF
build_in_chroot "python" "3.14.3" "Python-3.14.3.tar.xz" /tmp/build-python-chroot.sh || exit 1

# ============================================================================
# 7.11: Texinfo
# ============================================================================

cat > /tmp/build-texinfo-chroot.sh << 'BUILDEOF'
./configure --prefix=/usr
make -j${IGOS_JOBS}
make install
BUILDEOF
build_in_chroot "texinfo" "7.2" "texinfo-7.2.tar.xz" /tmp/build-texinfo-chroot.sh || exit 1

# ============================================================================
# 7.12: Util-linux
# ============================================================================

cat > /tmp/build-utillinux-chroot.sh << 'BUILDEOF'
mkdir -pv /var/lib/hwclock

./configure --libdir=/usr/lib     \
            --runstatedir=/run    \
            --disable-chfn-chsh   \
            --disable-login       \
            --disable-nologin     \
            --disable-su          \
            --disable-setpriv     \
            --disable-runuser     \
            --disable-pylibmount  \
            --disable-static      \
            --disable-liblastlog2 \
            --without-python      \
            ADJTIME_PATH=/var/lib/hwclock/adjtime \
            --docdir=/usr/share/doc/util-linux-$PKG_VERSION

make -j${IGOS_JOBS}
make install
BUILDEOF
build_in_chroot "util-linux" "2.41.3" "util-linux-2.41.3.tar.xz" /tmp/build-utillinux-chroot.sh || exit 1

# ============================================================================
# 7.13: Cleaning up
# ============================================================================

log "--- 7.13: Cleaning up ---"

# Remove temporary documentation
rm -rf /usr/share/{info,man,doc}/*

# Remove libtool .la files
find /usr/{lib,libexec} -name \*.la -delete

# Remove cross-compilation tools (no longer needed)
rm -rf /tools

log "  Cleanup complete"

# ============================================================================
# Summary
# ============================================================================

log ""
log "============================================"
log "  CHROOT BUILD COMPLETE"
log "  Packages built: 6"
log "    1. gettext 1.0"
log "    2. bison 3.8.2"
log "    3. perl 5.42.0"
log "    4. python 3.14.3"
log "    5. texinfo 7.2"
log "    6. util-linux 2.41.3"
log ""
log "  Directory layout created (FHS compliant)"
log "  Essential files created (passwd, group, hosts)"
log "  Cross-toolchain removed (/tools deleted)"
log "  System ready for Chapter 8 (core system build)"
log "============================================"
```

---

## 4. Chapter 8 Core Build: chroot-build-ch8.sh

**Path:** `/mnt/intergenos/scripts/chroot-build-ch8.sh`

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
set -o pipefail
umask 022

# ============================================================================
# Environment
# ============================================================================

IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)
IGOS_PACKAGES=/mnt/intergenos/packages/core
IGOS_START_AT="${IGOS_START_AT:-}"

export IGOS_SOURCES IGOS_PATCHES IGOS_LOGS IGOS_JOBS

mkdir -p "$IGOS_LOGS"

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

run_package "pyyaml" "pyyaml" "6.0.3" \
    "pyyaml-6.0.3.tar.gz" \
    "YAML parser for Python (required by igos-build)"

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

---

## 5. Chapter 9 System Configuration: chroot-config-ch9.sh

**Path:** `/mnt/intergenos/scripts/chroot-config-ch9.sh`

```bash
#!/bin/bash
# InterGenOS Chapter 9 — System Configuration
# LFS 13.0 Systemd
#
# Runs INSIDE the chroot (launched via chroot-enter.sh).
# Creates all system configuration files for Chapter 9.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-config-ch9.sh

set -e
umask 022

IGOS_LOGS=/mnt/intergenos/build/logs
mkdir -p "$IGOS_LOGS"

LOGFILE="$IGOS_LOGS/ch9-config-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOGFILE"
}

install_config() {
    local dest="$1"
    local desc="$2"
    log "  Installing $dest — $desc"
}

log "=========================================="
log "  InterGenOS Chapter 9: System Configuration"
log "=========================================="

# ============================================================================
# 9.2.1 — Network Interface Configuration (systemd-networkd, DHCP)
# ============================================================================

install_config "/etc/systemd/network/10-dhcp.network" "DHCP network config"
mkdir -p /etc/systemd/network
cat > /etc/systemd/network/10-dhcp.network << "EOF"
[Match]
Name=en*

[Network]
DHCP=ipv4

[DHCPv4]
UseDNS=true
UseDomains=true
EOF

# ============================================================================
# 9.2.2 — /etc/resolv.conf
# ============================================================================

# systemd-resolved creates /etc/resolv.conf as a symlink on boot.
# DNS servers are configured in the .network file above.
# No static resolv.conf needed — systemd-resolved handles it.
log "  /etc/resolv.conf — managed by systemd-resolved (no static file created)"

# ============================================================================
# 9.2.3 — /etc/hostname
# ============================================================================

install_config "/etc/hostname" "system hostname"
echo "intergenos" > /etc/hostname

# ============================================================================
# 9.2.4 — /etc/hosts
# ============================================================================

install_config "/etc/hosts" "static host lookups"
cat > /etc/hosts << "EOF"
# Begin /etc/hosts

127.0.0.1    localhost
127.0.1.1    intergenos.localdomain intergenos
::1          localhost ip6-localhost ip6-loopback
ff02::1      ip6-allnodes
ff02::2      ip6-allrouters

# End /etc/hosts
EOF

# ============================================================================
# 9.5 — System Clock
# ============================================================================

# Hardware clock is UTC (KVM default).
# systemd-timedated assumes UTC when /etc/adjtime is absent.
log "  /etc/adjtime — not created (systemd defaults to UTC)"

# ============================================================================
# 9.6 — Console Configuration
# ============================================================================

install_config "/etc/vconsole.conf" "console keymap and font"
cat > /etc/vconsole.conf << "EOF"
KEYMAP=us
FONT=Lat2-Terminus16
EOF

# ============================================================================
# 9.7 — System Locale
# ============================================================================

install_config "/etc/locale.conf" "system locale"
cat > /etc/locale.conf << "EOF"
LANG=en_US.UTF-8
EOF

install_config "/etc/profile" "login shell locale setup"
cat > /etc/profile << "EOF"
# Begin /etc/profile

for i in $(locale); do
  unset ${i%=*}
done

if [[ "$TERM" = linux ]]; then
  export LANG=C.UTF-8
else
  source /etc/locale.conf

  for i in $(locale); do
    key=${i%=*}
    if [[ -v $key ]]; then
      export $key
    fi
  done
fi

# Source profile.d drop-ins
if [ -d /etc/profile.d ]; then
  for script in /etc/profile.d/*.sh; do
    [ -r "$script" ] && . "$script"
  done
  unset script
fi

# End /etc/profile
EOF

install_config "/etc/bashrc" "interactive non-login shell setup"
cat > /etc/bashrc << "EOF"
# Begin /etc/bashrc

# Source profile.d drop-ins for interactive non-login shells
if [ -d /etc/profile.d ]; then
  for script in /etc/profile.d/*.sh; do
    [ -r "$script" ] && . "$script"
  done
  unset script
fi

# Aliases — carried forward from InterGenOS build_003 (2015)
alias ls='ls -a --group-directories-first --time-style=+"%d.%m.%Y %H:%M" --color=auto -F'
alias ll='ls -lah'
alias grep='grep --color=auto'
alias ..='cd ..'
alias ...='cd ../..'
alias ping='ping -c 3'

export EDITOR=nano
export LC_COLLATE="C"

HISTSIZE=1000
HISTFILESIZE=2000
HISTCONTROL=ignoreboth
shopt -s histappend

# End /etc/bashrc
EOF

# bash looks for /etc/bash.bashrc for non-login interactive shells
# (e.g. GNOME Terminal). Symlink so both names work.
ln -sf /etc/bashrc /etc/bash.bashrc

install_config "/etc/skel" "skeleton files for new user accounts"
mkdir -p /etc/skel
cat > /etc/skel/.bashrc << "EOF"
# ~/.bashrc
if [ -f /etc/bash.bashrc ]; then
    . /etc/bash.bashrc
fi
EOF

cat > /etc/skel/.bash_profile << "EOF"
# ~/.bash_profile
if [ -f ~/.bashrc ]; then
    . ~/.bashrc
fi
EOF

# Root shell configs
cp /etc/skel/.bashrc /root/.bashrc
cp /etc/skel/.bash_profile /root/.bash_profile
log "    /etc/bash.bashrc (symlink)"
log "    /etc/skel/.bashrc + .bash_profile"

install_config "/etc/profile.d/prompt.sh" "custom PS1 prompts"
mkdir -p /etc/profile.d
cat > /etc/profile.d/prompt.sh << "EOF"
# InterGenOS shell prompts
# Blue brackets, white delimiters, green path
# User: green username + $    Root: red username + #

if [ "$(id -u)" -eq 0 ]; then
  PS1='\[\e[1;34m\][\[\e[m\]\[\e[1;31m\]\u\[\e[m\]\[\e[1;34m\]@\[\e[m\]\[\e[1;37m\]\H\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;34m\][\[\e[m\]\[\e[1;37m\]<\[\e[m\]\[\e[1;32m\]\w\[\e[m\]\[\e[1;37m\]>\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;37m\]:\[\e[m\]\[\e[1;31m\]#\[\e[m\] '
else
  PS1='\[\e[1;34m\][\[\e[m\]\[\e[1;32m\]\u\[\e[m\]\[\e[1;34m\]@\[\e[m\]\[\e[1;37m\]\H\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;34m\][\[\e[m\]\[\e[1;37m\]<\[\e[m\]\[\e[1;32m\]\w\[\e[m\]\[\e[1;37m\]>\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;37m\]:\[\e[m\]\[\e[1;32m\]$\[\e[m\] '
fi
export PS1
EOF

# ============================================================================
# 9.8 — /etc/inputrc
# ============================================================================

install_config "/etc/inputrc" "readline configuration"
cat > /etc/inputrc << "EOF"
# Begin /etc/inputrc

# Allow the command prompt to wrap to the next line
set horizontal-scroll-mode Off

# Enable 8-bit input
set meta-flag On
set input-meta On

# Turns off 8th bit stripping
set convert-meta Off

# Keep the 8th bit for display
set output-meta On

# none, visible or audible
set bell-style none

# All of the following map the escape sequence of the value
# contained in the 1st argument to the readline specific functions
"\eOd": backward-word
"\eOc": forward-word

# for linux console
"\e[1~": beginning-of-line
"\e[4~": end-of-line
"\e[5~": beginning-of-history
"\e[6~": end-of-history
"\e[3~": delete-char
"\e[2~": quoted-insert

# for xterm
"\eOH": beginning-of-line
"\eOF": end-of-line

# for Konsole
"\e[H": beginning-of-line
"\e[F": end-of-line

# End /etc/inputrc
EOF

# ============================================================================
# 9.9 — /etc/shells
# ============================================================================

install_config "/etc/shells" "valid login shells"
cat > /etc/shells << "EOF"
# Begin /etc/shells

/bin/sh
/bin/bash

# End /etc/shells
EOF

# ============================================================================
# 9.10 — Systemd Usage and Configuration
# ============================================================================

# 9.10.2 — Disable screen clearing at boot (keep boot messages visible)
install_config "/etc/systemd/system/getty@tty1.service.d/noclear.conf" "disable boot screen clear"
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/noclear.conf << "EOF"
[Service]
TTYVTDisallocate=no
EOF

# 9.10.3 — /tmp as tmpfs (keep systemd default — tmpfs is fine)
log "  /tmp — keeping systemd default (tmpfs)"

# 9.10.8 — Core dump limit
install_config "/etc/systemd/coredump.conf.d/maxuse.conf" "core dump size limit"
mkdir -p /etc/systemd/coredump.conf.d
cat > /etc/systemd/coredump.conf.d/maxuse.conf << "EOF"
[Coredump]
MaxUse=5G
EOF

# ============================================================================
# InterGenOS Branding — TTY Login Banner and MOTD
# ============================================================================

install_config "/etc/issue" "TTY login banner"
cat > /etc/issue << "EOF"

  InterGenOS 1.0-dev (Revival)
  Kernel \r on \m (\l)

EOF

install_config "/etc/motd" "message of the day"
cat > /etc/motd << "EOF"

  Welcome to InterGenOS
  "A system you understand, can modify, and can trust."

  Documentation:  https://github.com/InterGenJLU/intergenos
  Report issues:  https://github.com/InterGenJLU/intergenos/issues

EOF

# ============================================================================
# InterGenOS Identity Files
# ============================================================================

install_config "/etc/os-release" "OS identification (freedesktop.org)"
cat > /etc/os-release << "EOF"
NAME="InterGenOS"
VERSION="1.0-dev (Revival)"
ID=intergenos
ID_LIKE=lfs
VERSION_ID=1.0
VERSION_CODENAME=revival
PRETTY_NAME="InterGenOS 1.0-dev (Revival)"
HOME_URL="https://github.com/InterGenJLU/intergenos"
BUG_REPORT_URL="https://github.com/InterGenJLU/intergenos/issues"
EOF

install_config "/etc/lsb-release" "LSB compatibility identification"
cat > /etc/lsb-release << "EOF"
DISTRIB_ID="InterGenOS"
DISTRIB_RELEASE="1.0-dev"
DISTRIB_CODENAME="revival"
DISTRIB_DESCRIPTION="InterGenOS 1.0-dev (Revival)"
EOF

install_config "/etc/igos-release" "InterGenOS version stamp"
echo "1.0-dev" > /etc/igos-release

install_config "/usr/bin/lsb_release" "LSB release query command"
cat > /usr/bin/lsb_release << "SCRIPT"
#!/bin/bash
# lsb_release — LSB conformance query command for InterGenOS
# Reads from /etc/os-release and /etc/lsb-release

LSB_VERSION="core-5.0-amd64:core-5.0-noarch"

# Source os-release for data
if [ -f /etc/os-release ]; then
    . /etc/os-release
fi

# Source lsb-release for LSB-specific fields
if [ -f /etc/lsb-release ]; then
    . /etc/lsb-release
fi

SHORT=0

usage() {
    echo "Usage: lsb_release [OPTION]..."
    echo "  -v, --version     Show LSB version"
    echo "  -i, --id          Show distributor ID"
    echo "  -d, --description Show description"
    echo "  -r, --release     Show release number"
    echo "  -c, --codename    Show codename"
    echo "  -a, --all         Show all of the above"
    echo "  -s, --short       Use short output format"
    echo "  -h, --help        Show this help"
}

show_version()     { [ $SHORT -eq 1 ] && echo "$LSB_VERSION" || echo "LSB Version:	$LSB_VERSION"; }
show_id()          { [ $SHORT -eq 1 ] && echo "${DISTRIB_ID}" || echo "Distributor ID:	${DISTRIB_ID}"; }
show_description() { [ $SHORT -eq 1 ] && echo "${DISTRIB_DESCRIPTION}" || echo "Description:	${DISTRIB_DESCRIPTION}"; }
show_release()     { [ $SHORT -eq 1 ] && echo "${DISTRIB_RELEASE}" || echo "Release:	${DISTRIB_RELEASE}"; }
show_codename()    { [ $SHORT -eq 1 ] && echo "${DISTRIB_CODENAME}" || echo "Codename:	${DISTRIB_CODENAME}"; }

show_all() {
    show_version
    show_id
    show_description
    show_release
    show_codename
}

if [ $# -eq 0 ]; then
    show_version
    exit 0
fi

# Parse for -s/--short first
for arg in "$@"; do
    case "$arg" in
        -s|--short) SHORT=1 ;;
    esac
done

for arg in "$@"; do
    case "$arg" in
        -v|--version)     show_version ;;
        -i|--id)          show_id ;;
        -d|--description) show_description ;;
        -r|--release)     show_release ;;
        -c|--codename)    show_codename ;;
        -a|--all)         show_all ;;
        -s|--short)       ;; # already handled
        -h|--help)        usage; exit 0 ;;
        *)                echo "Unknown option: $arg"; usage; exit 1 ;;
    esac
done
SCRIPT
chmod 755 /usr/bin/lsb_release

# ============================================================================
# Summary
# ============================================================================

log ""
log "=========================================="
log "  Chapter 9 Configuration Complete"
log "=========================================="
log ""
log "  Files created:"
log "    /etc/systemd/network/10-dhcp.network"
log "    /etc/hostname"
log "    /etc/hosts"
log "    /etc/vconsole.conf"
log "    /etc/locale.conf"
log "    /etc/profile"
log "    /etc/bashrc"
log "    /etc/profile.d/prompt.sh"
log "    /etc/inputrc"
log "    /etc/shells"
log "    /etc/issue"
log "    /etc/motd"
log "    /etc/os-release"
log "    /etc/lsb-release"
log "    /etc/igos-release"
log "    /usr/bin/lsb_release"
log "    /etc/systemd/system/getty@tty1.service.d/noclear.conf"
log "    /etc/systemd/coredump.conf.d/maxuse.conf"
log ""
log "  Not created (by design):"
log "    /etc/resolv.conf — managed by systemd-resolved"
log "    /etc/adjtime — absent = UTC (systemd default)"
log ""
log "  Log: $LOGFILE"
```

---

## 6. Chapter 10 Kernel Build: chroot-build-ch10.sh

**Path:** `/mnt/intergenos/scripts/chroot-build-ch10.sh`

```bash
#!/bin/bash
# InterGenOS Chapter 10 — Making the System Bootable
# LFS 13.0 Systemd
#
# Runs INSIDE the chroot (launched via chroot-enter.sh).
# Builds the Linux kernel (Section 10.3).
#
# NOTE: Sections 10.2 (fstab) and 10.4 (GRUB) are handled during
# image deployment, not here — they depend on the target VM's
# disk layout which isn't known at chroot build time.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-ch10.sh

set +h
set -e
umask 022

IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)
IGOS_PACKAGES=/mnt/intergenos/packages/core

export IGOS_SOURCES IGOS_PATCHES IGOS_LOGS IGOS_JOBS

mkdir -p "$IGOS_LOGS"

# Source the package tracking functions
source /mnt/intergenos/scripts/pkg-functions.sh

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$IGOS_LOGS/ch10-build.log"
}

# ============================================================================
# Build helper — same pattern as chroot-build-ch8.sh
# ============================================================================

build_ch10_package() {
    local pkg_dir="$1"
    local name="$2"
    local version="$3"
    local tarball="$4"
    local description="$5"

    local build_script="${IGOS_PACKAGES}/${pkg_dir}/build.sh"
    local pkg_log="${IGOS_LOGS}/${name}-ch10-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    if [ ! -f "$build_script" ]; then
        log "ERROR: No build.sh found at $build_script"
        return 1
    fi

    log "=========================================="
    log "  Chapter 10: ${name} ${version}"
    log "  Log: ${pkg_log}"
    log "=========================================="

    export PKG_VERSION="$version"

    rm -rf "$workdir"
    mkdir -pv "$workdir"
    tar -xf "${IGOS_SOURCES}/${tarball}" -C "$workdir" --strip-components=1 || {
        log "ERROR: Failed to extract ${tarball}"
        return 1
    }
    cd "$workdir"

    local start=$(date +%s)

    unset -f configure build check do_install post_install
    source "$build_script"

    # --- CONFIGURE ---
    if declare -f configure > /dev/null 2>&1; then
        log "  [CONFIGURE] starting..."
        configure >> "$pkg_log" 2>&1
        if [ $? -ne 0 ]; then
            log "  FAILED in configure"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            return 1
        fi
        log "  [CONFIGURE] done"
    fi

    # --- BUILD ---
    if declare -f build > /dev/null 2>&1; then
        log "  [BUILD] starting..."
        build >> "$pkg_log" 2>&1
        if [ $? -ne 0 ]; then
            log "  FAILED in build"
            tail -20 "$pkg_log" | while IFS= read -r l; do log "    $l"; done
            return 1
        fi
        log "  [BUILD] done"
    fi

    # --- INSTALL (via DESTDIR staging + package tracking) ---
    log "  [INSTALL] staging..."
    pkg_install "$name" "$version" "$description"
    if [ $? -ne 0 ]; then
        log "  FAILED in install/staging"
        return 1
    fi

    # --- POST-INSTALL ---
    if declare -f post_install > /dev/null 2>&1; then
        log "  [POST-INSTALL] running..."
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
# Initialize
# ============================================================================

pkg_init

log ""
log "============================================"
log "  InterGenOS Chapter 10 — Bootable System"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# ============================================================================
# 10.3: Linux Kernel
# ============================================================================

build_ch10_package "linux-kernel" "linux-kernel" "6.18.10" \
    "linux-6.18.10.tar.xz" \
    "Linux kernel" || {
    log "!!! KERNEL BUILD FAILED"
    exit 1
}

# ============================================================================
# Summary
# ============================================================================

log ""
log "============================================"
log "  CHAPTER 10 BUILD COMPLETE"
log ""
log "  Kernel installed to /boot/vmlinuz-6.18.10-igos"
log "  Modules installed to /lib/modules/6.18.10"
log ""
log "  NOTE: /etc/fstab and GRUB configuration will be"
log "  completed during image deployment to the target VM."
log "============================================"
```

---

## 7. Core Extra Build: chroot-build-core-extra.sh

**Path:** `/mnt/intergenos/scripts/chroot-build-core-extra.sh`

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
set -o pipefail
umask 022

# ============================================================================
# Environment
# ============================================================================

IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)
IGOS_PACKAGES=/mnt/intergenos/packages/core
IGOS_START_AT="${IGOS_START_AT:-}"

export IGOS_SOURCES IGOS_PATCHES IGOS_LOGS IGOS_JOBS

mkdir -p "$IGOS_LOGS"

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

---

## 8. Base Package Build: chroot-build-base.sh

**Path:** `/mnt/intergenos/scripts/chroot-build-base.sh`

```bash
#!/bin/bash
# InterGenOS Base Package Build — end-user tools and services beyond core
# Builds after core-extra completes, inside the chroot.
#
# These are end-user tools and system services that don't need to be
# in core (not build dependencies, not foundational libraries).
#
# Uses the same package tracking as Chapter 8 (pkg-functions.sh).
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-base.sh
#
# To resume after a failure:
#   IGOS_START_AT=<name> sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-base.sh

set +h
set -e
set -o pipefail
umask 022

# ============================================================================
# Environment
# ============================================================================

IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)
IGOS_PACKAGES=/mnt/intergenos/packages/base
IGOS_START_AT="${IGOS_START_AT:-}"

export IGOS_SOURCES IGOS_PATCHES IGOS_LOGS IGOS_JOBS

mkdir -p "$IGOS_LOGS"

# Source the package tracking functions
source /mnt/intergenos/scripts/pkg-functions.sh

# ============================================================================
# Logging
# ============================================================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$IGOS_LOGS/base-build.log"
}

# ============================================================================
# Build helper — same pattern as Chapter 8 and core-extra
# ============================================================================

build_base_package() {
    local pkg_dir="$1"
    local name="$2"
    local version="$3"
    local tarball="$4"
    local description="$5"

    local build_script="${IGOS_PACKAGES}/${pkg_dir}/build.sh"
    local pkg_log="${IGOS_LOGS}/${name}-base-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    if [ ! -f "$build_script" ]; then
        log "ERROR: No build.sh found at $build_script"
        return 1
    fi

    log "=========================================="
    log "  Base: ${name} ${version}"
    log "  Log: ${pkg_log}"
    log "=========================================="

    export PKG_VERSION="$version"

    # Clean and extract
    rm -rf "$workdir"
    mkdir -pv "$workdir"

    # Use bsdtar for .lz archives, tar for everything else
    if [[ "$tarball" == *.lz ]]; then
        bsdtar -xf "${IGOS_SOURCES}/${tarball}" -C "$workdir" --strip-components=1 || {
            log "ERROR: Failed to extract ${tarball}"
            return 1
        }
    else
        tar -xf "${IGOS_SOURCES}/${tarball}" -C "$workdir" --strip-components=1 || {
            log "ERROR: Failed to extract ${tarball}"
            return 1
        }
    fi
    cd "$workdir"

    local start=$(date +%s)

    unset -f configure build check do_install post_install

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

    # --- CHECK (optional) ---
    if declare -f check > /dev/null 2>&1; then
        cd "$workdir"
        log "  [CHECK] starting..."
        check >> "$pkg_log" 2>&1
        log "  [CHECK] done (see log for results)"
    fi

    # --- INSTALL ---
    cd "$workdir"
    log "  [INSTALL] staging..."
    pkg_install "$name" "$version" "$description"
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "  FAILED in install/staging (exit $rc)"
        return 1
    fi

    # --- POST-INSTALL ---
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

    build_base_package "$@" || {
        log ""
        log "!!! BUILD FAILED: $name"
        log "!!! Fix the issue and re-run with: IGOS_START_AT=$name"
        log ""
        exit 1
    }
}

# ============================================================================
# Build Order — base packages
#
# Dependencies that are in core (already installed) don't need to be
# listed here. Only inter-base dependencies affect ordering.
# ============================================================================

log ""
log "============================================"
log "  InterGenOS Base Package Build"
BASE_PKG_COUNT=$(grep -c '^run_package' "$0" 2>/dev/null || echo "?")
log "  ${BASE_PKG_COUNT} packages"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

pkg_init

# --- No-dependency packages ---

run_package "cpio" "cpio" "2.15" \
    "cpio-2.15.tar.bz2" \
    "GNU cpio — copies files into or out of archives"

run_package "ed" "ed" "1.22.5" \
    "ed-1.22.5.tar.lz" \
    "Classic UNIX line editor"

run_package "fcron" "fcron" "3.4.0" \
    "fcron-3.4.0.src.tar.gz" \
    "Periodical command scheduler"

run_package "htop" "htop" "3.4.1" \
    "htop-3.4.1.tar.xz" \
    "Interactive process viewer"

run_package "iotop" "iotop" "1.31" \
    "iotop-1.31.tar.xz" \
    "I/O monitoring tool"

run_package "libtirpc" "libtirpc" "1.3.7" \
    "libtirpc-1.3.7.tar.bz2" \
    "Transport-Independent RPC library"

run_package "pax" "pax" "20240817" \
    "paxmirabilis-20240817.tgz" \
    "POSIX standard archive utility"

run_package "perl-file-fcntllock" "perl-file-fcntllock" "0.22" \
    "File-FcntlLock-0.22.tar.gz" \
    "Perl module for file locking"

run_package "popt" "popt" "1.19" \
    "popt-1.19.tar.gz" \
    "Command line option parsing library"

run_package "screen" "screen" "5.0.1" \
    "screen-5.0.1.tar.gz" \
    "GNU Screen terminal multiplexer"

run_package "strace" "strace" "6.19" \
    "strace-6.19.tar.xz" \
    "System call tracer"

run_package "time" "time" "1.9" \
    "time-1.9.tar.gz" \
    "GNU time — resource usage summary"

run_package "which" "which" "2.23" \
    "which-2.23.tar.gz" \
    "Utility to show the full path of commands"

# --- Packages with dependencies on other base packages ---

run_package "libnsl" "libnsl" "2.0.1" \
    "libnsl-2.0.1.tar.xz" \
    "NIS library"

run_package "lsof" "lsof" "4.99.6" \
    "lsof-4.99.6.tar.gz" \
    "List open files"

run_package "rsync" "rsync" "3.4.1" \
    "rsync-3.4.1.tar.gz" \
    "Fast incremental file transfer"

run_package "atop" "atop" "2.12.1" \
    "atop-2.12.1.tar.gz" \
    "Advanced system and process monitor"

run_package "exim" "exim" "4.99.1" \
    "exim-4.99.1.tar.xz" \
    "Message Transfer Agent"

run_package "at" "at" "3.2.5" \
    "at_3.2.5.orig.tar.gz" \
    "Job scheduling commands"

run_package "btop" "btop" "1.4.6" \
    "btop-1.4.6.tar.gz" \
    "Resource monitor with TUI"

# ============================================================================
# Summary
# ============================================================================

TOTAL_TRACKED=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log "============================================"
log "  Base Package Build Complete"
log "  Total tracked packages: ${TOTAL_TRACKED}"
log "  End: $(date)"
log "============================================"
```

---

## 9. Desktop Build: chroot-build-desktop.sh

**Path:** `/mnt/intergenos/scripts/chroot-build-desktop.sh`

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
set -o pipefail
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)

mkdir -p "$IGOS_LOGS"

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
# Step 1: Verify Python dependencies for igos-build
# ============================================================================
# PyYAML is installed as a Chapter 8 system package (alongside setuptools).
# If it's missing, the core build is broken — fail hard, don't try to fix it.

log "--- Verifying Python dependencies for igos-build ---"

if ! python3 -c "import yaml" 2>/dev/null; then
    log "ERROR: PyYAML missing — Chapter 8 build is incomplete or corrupt"
    log "       PyYAML must be installed as a core system package."
    exit 1
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

---

## 10. Extra Tier Build: chroot-build-extra.sh

**Path:** `/mnt/intergenos/scripts/chroot-build-extra.sh`

```bash
#!/bin/bash
# InterGenOS Extra Tier Build — User-facing applications
# Runs INSIDE the chroot after desktop tier completes.
#
# Uses igos-build (Python builder) for dependency resolution and build
# ordering. Packages in this tier are optional — the desktop works without them.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-build-extra.sh

set +h
set -e
umask 022

IGOS_SOURCES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)

mkdir -p "$IGOS_LOGS"

EXTRA_LOG="$IGOS_LOGS/extra-build-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$EXTRA_LOG"
}

log ""
log "============================================"
log "  InterGenOS Extra Tier Build"
log "  User-facing applications"
log "  Start: $(date)"
log "  Cores: ${IGOS_JOBS}"
log "============================================"
log ""

# ============================================================================
# Step 1: Verify Python dependencies for igos-build
# ============================================================================
# PyYAML is installed as a Chapter 8 system package (alongside setuptools).
# If it's missing, the core build is broken — fail hard, don't try to fix it.

if ! python3 -c "import yaml" 2>/dev/null; then
    log "ERROR: PyYAML missing — Chapter 8 build is incomplete or corrupt"
    log "       PyYAML must be installed as a core system package."
    exit 1
fi

# ============================================================================
# Step 2: Run igos-build for extra tier
# ============================================================================

log "--- Running igos-build for extra tier ---"
log ""

cd /mnt/intergenos

python3 igos-build.py \
    --build \
    --tracked \
    --skip-built \
    --tier extra \
    --sources-dir "$IGOS_SOURCES" \
    2>&1 | tee -a "$EXTRA_LOG"

BUILD_RC=${PIPESTATUS[0]}

if [ $BUILD_RC -ne 0 ]; then
    log ""
    log "!!! Extra tier build failed (exit $BUILD_RC)"
    log "!!! Check logs in $IGOS_LOGS/"
    log "!!! Fix the failing package, then re-run this script."
    log "!!! --skip-built will resume from where it left off."
    exit $BUILD_RC
fi

# ============================================================================
# Summary
# ============================================================================

TOTAL_TRACKED=$(ls /var/lib/igos/packages/ 2>/dev/null | wc -l)

log ""
log "============================================"
log "  EXTRA TIER BUILD COMPLETE"
log "  Total tracked packages: ${TOTAL_TRACKED}"
log "  End: $(date)"
log "============================================"
log ""
log "  To install proprietary applications, run as the target user:"
log "    sudo igos-install-chrome       # Google Chrome"
log "    sudo igos-install-vscode       # Visual Studio Code"
log "    igos-install-claude-code       # Claude Code (CLI + extension)"
log ""
```

---

## 11. Unified Tier Builder: chroot-build-tier.sh

**Path:** `/mnt/intergenos/scripts/chroot-build-tier.sh`

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
IGOS_LOGS=/mnt/intergenos/build/logs
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

mkdir -p "$IGOS_LOGS"

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
# Step 1: Verify Python dependencies for igos-build
# ==========================================================================
# PyYAML is installed as a Chapter 8 system package (alongside setuptools).
# If it's missing, the core build is broken — fail hard, don't try to fix it.

log "--- Verifying Python dependencies for igos-build ---"

if ! python3 -c "import yaml" 2>/dev/null; then
    log "ERROR: PyYAML missing — Chapter 8 build is incomplete or corrupt"
    log "       PyYAML must be installed as a core system package."
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

---

**End of Part 2. Continue to Part 3 for the Python build system, package functions, image creator, and host checker.**
