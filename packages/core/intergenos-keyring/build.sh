#!/bin/bash
# intergenos-keyring 0.1.0 — InterGenOS GPG release keyring
# https://github.com/InterGenJLU/intergenos
#
# Installs: /etc/pkm/trusted.gpg containing the InterGenOS master public
# release-signing key. pkm verifies InterGenOS.db signatures against this
# keyring on every `pkm sync` / `pkm update` / `pkm refresh`. Without this
# keyring on disk every sync fails closed (the canonical broken state
# pre this package).
#
# Source: docs/signing-key.asc (the canonical armored pubkey, also
# published on keys.openpgp.org and keyserver.ubuntu.com). The
# fingerprint is documented at docs/signing-key.md and pinned in
# pkm/release-keys.json.

build() {
    set -e
    # No build step — keyring is generated at install-time from the
    # committed pubkey.
    return 0
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/etc/pkm"

    # Import the ASCII-armored master pubkey into a binary GPG keyring
    # at the path pkm/repo.py expects (GPG_KEYRING = /etc/pkm/trusted.gpg).
    # Use a temporary GNUPGHOME so the build host's gpg state is untouched.
    TMPHOME=$(mktemp -d)
    trap "rm -rf '$TMPHOME'" EXIT RETURN

    GNUPGHOME="$TMPHOME" gpg \
        --no-permission-warning \
        --no-default-keyring \
        --keyring "${DESTDIR}/etc/pkm/trusted.gpg" \
        --import /mnt/intergenos/docs/signing-key.asc

    # Trust DB lockfiles emitted by gpg in the destination dir are
    # transient — strip them so the package only ships the keyring itself.
    rm -f "${DESTDIR}/etc/pkm/trusted.gpg~" \
          "${DESTDIR}/etc/pkm/trusted.gpg.lock"

    chmod 644 "${DESTDIR}/etc/pkm/trusted.gpg"
}
