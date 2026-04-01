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
