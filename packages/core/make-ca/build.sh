#!/bin/bash
# make-ca 1.16.1 — CA certificate management
# BLFS 13.0
# Note: requires network access to download cert data

configure() {
    set -e
    # Fix deprecated mktemp option
    sed '/mktemp/s/-t //' -i make-ca
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    install -vdm755 "${DESTDIR}/etc/ssl/local"
}

post_install() {
    set -e
    # Download and process certificates (requires network)
    /usr/sbin/make-ca -g

    # Enable weekly certificate update timer
    systemctl enable update-pki.timer

    # Configure Python to use system certs
    mkdir -pv /etc/profile.d
    cat > /etc/profile.d/pythoncerts.sh << "EOF"
# Begin /etc/profile.d/pythoncerts.sh
export _PIP_STANDALONE_CERT=/etc/pki/tls/certs/ca-bundle.crt
# End /etc/profile.d/pythoncerts.sh
EOF
}
