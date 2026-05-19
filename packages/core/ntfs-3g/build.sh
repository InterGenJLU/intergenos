#!/bin/bash
# ntfs-3g 2026.2.25 — NTFS read/write driver + ntfsprogs userspace tools.
# T0-3 sub-cluster 1 — installer runtime dep (disks.py:1071 ntfsresize).
# BLFS 13.0 reference: postlfs/ntfs-3g. --with-fuse=internal bundles FUSE-2
# (required; our chroot ships fuse3, ntfs-3g needs fuse2 for user mounts).

configure() {
    set -e
    # Upstream ships a configure.ac + autogen.sh rather than a pre-baked
    # configure script; regenerate via autogen.sh before configure.
    if [ ! -x configure ] && [ -x autogen.sh ]; then
        ./autogen.sh
    fi
    ./configure --prefix=/usr \
                --disable-static \
                --with-fuse=internal \
                --docdir=/usr/share/doc/ntfs-3g-${version}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    # mount(8) honors `mount -t ntfs` only if /sbin/mount.ntfs (or any
    # /sbin/mount.<fstype>) exists. Symlink to the ntfs-3g userspace driver
    # so `mount -t ntfs` works without manual -t ntfs-3g specification.
    install -dm755 "$DESTDIR/usr/sbin"
    ln -svf ../bin/ntfs-3g "$DESTDIR/usr/sbin/mount.ntfs"
    install -dm755 "$DESTDIR/usr/share/man/man8"
    ln -svf ntfs-3g.8 "$DESTDIR/usr/share/man/man8/mount.ntfs.8" || true
}
