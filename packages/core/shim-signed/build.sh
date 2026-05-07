#!/bin/bash
# shim-signed — extract Microsoft-signed shim from Fedora RPM
# Per D1-7 decision: piggyback on Fedora's shim until our own MS-signed
# shim ships post-Monday.

configure() {
    set -e
    # No configure step — we're extracting prebuilt binaries
    return 0
}

build() {
    set -e
    # Extract RPM payload
    rpm2cpio shim-x64-${PKG_VERSION}-2.x86_64.rpm | cpio -idmv

    # Verify the binaries we expect are present
    test -f boot/efi/EFI/fedora/shimx64.efi || {
        echo "ERROR: shimx64.efi not found in extracted RPM" >&2
        exit 1
    }
    test -f boot/efi/EFI/fedora/mmx64.efi || {
        echo "ERROR: mmx64.efi not found in extracted RPM" >&2
        exit 1
    }
}

do_install() {
    set -e
    # Stage shim binaries under /usr/share/shim-signed/ where Forge expects
    install -d "$DESTDIR/usr/share/shim-signed"
    install -m 0644 boot/efi/EFI/fedora/shimx64.efi "$DESTDIR/usr/share/shim-signed/"
    install -m 0644 boot/efi/EFI/fedora/mmx64.efi  "$DESTDIR/usr/share/shim-signed/"
    if [ -f boot/efi/EFI/fedora/shimx64-fedora.cer ]; then
        install -m 0644 boot/efi/EFI/fedora/shimx64-fedora.cer "$DESTDIR/usr/share/shim-signed/"
    fi
}
