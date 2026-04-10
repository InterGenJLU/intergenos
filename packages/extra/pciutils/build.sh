#!/bin/bash
# pciutils 3.14.0 — PCI device listing and configuration utilities
# BLFS 13.0

configure() {
    # BLFS: Prevent installing pci.ids — hwdata provides it
    sed -r '/INSTALL/{/PCI_IDS|update-pciids /d; s/update-pciids.8//}' \
        -i Makefile
}

build() {
    make PREFIX=/usr                \
         SHAREDIR=/usr/share/hwdata \
         SHARED=yes                 \
         -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR"              \
         PREFIX=/usr                     \
         SHAREDIR=/usr/share/hwdata      \
         SHARED=yes                      \
         install install-lib

    # BLFS: Ensure shared library has correct permissions
    chmod -v 755 "$DESTDIR/usr/lib/libpci.so"
}
