#!/bin/bash
# intel-ucode 20250211 — Intel CPU microcode firmware
# Source: Intel Linux Processor Microcode Data Files

configure() {
    set -e
    : # No configuration needed — firmware files only
}

build() {
    set -e
    : # No compilation needed — firmware files only
}

do_install() {
    set -e
    # Install microcode firmware files
    mkdir -p "${DESTDIR}/lib/firmware/intel-ucode"
    cp -v intel-ucode/* "${DESTDIR}/lib/firmware/intel-ucode/"
}

post_install() {
    set -e
    # Generate early-load cpio image for GRUB
    # iucode_tool selects only the microcode matching this CPU
    if command -v iucode_tool >/dev/null 2>&1; then
        iucode_tool -S /lib/firmware/intel-ucode/ \
            --write-earlyfw=/boot/intel-ucode.img
    fi
}
