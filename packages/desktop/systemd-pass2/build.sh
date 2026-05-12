#!/bin/bash
# Systemd 259.1 — Pass 2 rebuild with PAM support
# BLFS 13.0
#
# LFS builds systemd without PAM (Chapter 8). After linux-pam is
# installed, systemd must be rebuilt with PAM support so that:
#   - pam_systemd.so is built and installed
#   - systemd-logind can create proper user sessions
#   - GDM and GNOME can register display sessions
#
# Without this rebuild, GNOME desktop login fails because
# systemd --user cannot start (no XDG_RUNTIME_DIR created).

configure() {
    set -e
    # Same sed fix as pass 1
    sed -e 's/GROUP="render"/GROUP="video"/' \
        -e 's/GROUP="sgx", //'               \
        -i rules.d/50-udev-default.rules.in

    mkdir -p build
    cd       build

    meson setup ..                          \
        --prefix=/usr                       \
        --libdir=/usr/lib                   \
        --buildtype=release                 \
        -D default-dnssec=allow-downgrade   \
        -D firstboot=false                  \
        -D install-tests=false              \
        -D ldconfig=false                   \
        -D sysusers=false                   \
        -D rpmmacrosdir=/usr/lib/rpm/macros.d \
        -D homed=enabled                    \
        -D man=enabled                      \
        -D mode=release                     \
        -D pam=enabled                      \
        -D pamconfdir=/etc/pam.d            \
        -D dev-kvm-mode=0660                \
        -D nobody-group=nogroup             \
        -D sysupdate=enabled                \
        -D ukify=enabled                    \
        -D apparmor=enabled                 \
        -D tpm2=enabled                     \
        -D libfido2=enabled                 \
        -D xkbcommon=enabled                \
        -D seccomp=enabled                  \
        -D libcryptsetup=enabled            \
        -D idn=true                         \
        -D qrencode=enabled                 \
        -D gcrypt=enabled                   \
        -D gnutls=enabled                   \
        -D libarchive=enabled               \
        -D libcurl=enabled                  \
        -D bashcompletiondir=/usr/share/bash-completion/completions \
        -D docdir=/usr/share/doc/systemd-259.1
}

build() {
    set -e
    cd build
    ninja -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd build
    # Direct install — overwrites pass 1 systemd with PAM-enabled version
    ninja install
}

post_install() {
    set -e
    # Verify pam_systemd.so was installed
    if [ -f /usr/lib/security/pam_systemd.so ]; then
        echo "  pam_systemd.so installed successfully"
    else
        echo "  ERROR: pam_systemd.so not found after rebuild!"
        return 1
    fi

    # Reload systemd to pick up new binaries
    systemctl daemon-reexec 2>/dev/null || true
}
