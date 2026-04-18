#!/bin/bash
# sbsigntool — sign/verify EFI binaries for Secure Boot
# Required by: Forge installer (mok.sign_efi_binary, mok.verify_efi_signature)
# Required by: build pipeline (signing the kernel image)

configure() {
    ./autogen.sh 2>/dev/null || autoreconf -fiv
    ./configure --prefix=/usr --sysconfdir=/etc --disable-static
}

build() {
    make
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
