#!/bin/bash
# pkm 0.1.0 — InterGenOS package manager
# https://github.com/InterGenJLU/intergenos
#
# Installs: Python package at /usr/lib/python3.14/site-packages/pkm,
# /usr/bin/pkm CLI shim, default /etc/pkm/repos.conf pointing at the
# official VPS mirror, and the runtime directories pkm expects
# (/var/lib/igos/{packages,archives}).
#
# Pure Python, stdlib-only — no compile step, no third-party deps.

build() {
    # No build step — pure Python.
    return 0
}

do_install() {
    # pkm Python package — copy sources from the repo tree. Source is the
    # canonical pkm/ directory in /mnt/intergenos, which is staged into
    # the chroot by the build orchestrator (scripts/build-intergenos.sh).
    install -dm755 "${DESTDIR}/usr/lib/python3.14/site-packages/pkm"
    cp -a /mnt/intergenos/pkm/*.py "${DESTDIR}/usr/lib/python3.14/site-packages/pkm/"

    # CLI shim — thin wrapper so `pkm ...` works from PATH
    install -Dm755 /dev/stdin "${DESTDIR}/usr/bin/pkm" << 'SHIM'
#!/bin/sh
exec /usr/bin/python3 -m pkm "$@"
SHIM

    # Default repo configuration — points at the bootstrapped VPS mirror
    # (Component 1). Components 2 and 3 are tracked under
    # project_vps_mirror_tracking.md for post-v1.0.
    install -Dm644 /dev/stdin "${DESTDIR}/etc/pkm/repos.conf" << 'REPOS'
# InterGenOS package-manager repository configuration.
#
# Each repository is a stanza with a name, a base URL, and optional
# trust settings. The default shipped repo is the official mirror at
# intergenstudios.com. User-added repos can be appended below.

[intergenos-current]
url = https://intergenstudios.com/intergenos/sources/current/
enabled = true
# gpg_verify = true — enable once the signing-key ceremony completes
REPOS

    # Placeholder trust-store path — real keyring lands post-signing-key
    # ceremony; pkm treats absence as "no GPG verification available"
    # and the repo stanza gpg_verify flag (above) is the user-facing
    # opt-in.
    install -dm755 "${DESTDIR}/etc/pkm"

    # Runtime data directories pkm expects to exist. pkm itself will
    # initialise /var/lib/igos/pkm.db on first invocation, so no
    # schema bootstrap is required at install time.
    install -dm755 "${DESTDIR}/var/lib/igos/packages"
    install -dm755 "${DESTDIR}/var/lib/igos/archives"

    # Man page
    install -Dm644 /mnt/intergenos/packages/core/pkm/pkm.1 \
        "${DESTDIR}/usr/share/man/man1/pkm.1"
}

post_install() {
    # Nothing to do — directories exist, database self-initialises on
    # first invocation, no systemd service to enable.
    :
}
