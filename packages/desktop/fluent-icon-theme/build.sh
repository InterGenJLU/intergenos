#!/bin/bash
# fluent-icon-theme 2025-08-21 — Fluent icon theme (3 brightness variants of the standard color)
#
# Upstream: github.com/vinceliuice/Fluent-icon-theme
# License: GPL-3.0-or-later
#
# Installs three icon-theme directories at /usr/share/icons/:
#   * Fluent        (default)
#   * Fluent-light
#   * Fluent-dark   <-- referenced by the Fluent welcomer combo
#
# Build profile: shell installer install.sh -d <dest>. The installer's
# default invocation selects the standard color + all 3 brightness
# variants. Fluent's installer is the newest of the four (refactored
# version per its own header comment) — it already gates one of its
# gtk-update-icon-cache calls behind `|| true`, but a second
# unconditional call exists; the sed-strip catches both for consistency.

configure() {
    set -e
    sed -i 's|gtk-update-icon-cache|true|g' install.sh
}

build() {
    set -e
    :
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/icons"
    bash install.sh -d "${DESTDIR}/usr/share/icons"
}

post_install() {
    set -e
    for d in Fluent Fluent-light Fluent-dark ; do
        gtk-update-icon-cache -q "/usr/share/icons/${d}" 2>/dev/null || true
    done
}
