#!/bin/bash
# Systemd 259.1
# LFS 13.0 Section 8.78
#
# Complex meson build. DESTDIR supported.
# Post-install: machine-id-setup, preset-all.

configure() {
    set -e
    # Fix udev rules: render -> video group, remove sgx
    sed -e 's/GROUP="render"/GROUP="video"/' \
        -e 's/GROUP="sgx", //'               \
        -i rules.d/50-udev-default.rules.in

    mkdir -p build
    cd       build

    # `bootloader=enabled` is explicit on purpose. The default `auto` resolves
    # via meson's feature.require() against pyelftools-found + EFI-enabled +
    # x86_64-EFI-arch (per src/systemd/meson.build:1925-1928). When pyelftools
    # is missing, `auto` SILENTLY disables the bootloader and linuxx64.efi.stub
    # never gets built — surfacing later as opaque "STUB not found" from
    # scripts/build-uki.sh. Forcing `enabled` flips silent-disable to loud-
    # error per Holy Grail (explicit > implicit) + Prime Directive (don't
    # hide things). pyelftools is now in our host-deps to satisfy the require
    # condition under all build environments.
    meson setup ..                \
        --prefix=/usr             \
        --libdir=/usr/lib         \
        --buildtype=release       \
        -D default-dnssec=no      \
        -D firstboot=false        \
        -D install-tests=false    \
        -D ldconfig=false         \
        -D sysusers=false         \
        -D rpmmacrosdir=no        \
        -D homed=disabled         \
        -D man=disabled           \
        -D mode=release           \
        -D pamconfdir=no          \
        -D dev-kvm-mode=0660      \
        -D nobody-group=nogroup   \
        -D sysupdate=disabled     \
        -D ukify=disabled         \
        -D bootloader=enabled     \
        -D sbat-distro=intergenos \
        -D sbat-distro-summary="InterGenOS" \
        -D sbat-distro-pkgname=systemd \
        -D sbat-distro-version=259.1-1 \
        -D sbat-distro-generation=1 \
        -D sbat-distro-url=https://github.com/InterGenJLU/intergenos \
        -D docdir=/usr/share/doc/systemd-259.1
}

build() {
    set -e
    cd build
    ninja -j${IGOS_JOBS}
}

check() {
    set -e
    cd build
    # os-release is needed for tests
    echo 'NAME="InterGenOS"' > /etc/os-release
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        unshare -m ninja test
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Install man pages from separate tarball
    mkdir -pv "${DESTDIR}/usr/share/man"
    tar -xf ${IGOS_SOURCES}/systemd-man-pages-259.1.tar.xz \
        --no-same-owner --strip-components=1                \
        -C "${DESTDIR}/usr/share/man"
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    set -e
    # Create machine ID (unique per machine, never bake into a package)
    systemd-machine-id-setup

    # Enable/disable services per preset policy
    systemctl preset-all
}
