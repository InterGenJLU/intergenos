#!/bin/bash
# gvfs 1.58.2 — GNOME virtual filesystem
# BLFS 13.0
#
# Disabled backends (dependencies not in tree):
#   afc (libimobiledevice), cdda (libcdio), dnssd (avahi),
#   goa (gnome-online-accounts), google (libgdata),
#   gphoto2 (libgphoto2), mtp (libmtp), nfs (libnfs),
#   onedrive (libmsgraph), bluray (libbluray)
#
# Enabled backends (all deps available):
#   admin, afp, archive, http/dav, sftp, smb (samba), udisks2, wsdd,
#   fuse, gcr, gcrypt, gudev, keyring, logind, libusb

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dman=false \
          -Dafc=false \
          -Dcdda=false \
          -Ddnssd=false \
          -Dgoa=false \
          -Dgoogle=false \
          -Dgphoto2=false \
          -Dmtp=false \
          -Dnfs=false \
          -Donedrive=false \
          -Dbluray=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
