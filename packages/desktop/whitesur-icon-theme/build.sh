#!/bin/bash
# whitesur-icon-theme 2025-12-27 — macOS-inspired icon theme (3 brightness variants)
#
# Upstream: github.com/vinceliuice/WhiteSur-icon-theme
# License: GPL-3.0-or-later
#
# Installs three icon-theme directories at /usr/share/icons/:
#   * WhiteSur        (default)
#   * WhiteSur-light
#   * WhiteSur-dark   <-- referenced by the WhiteSur welcomer combo
#
# Build profile: shell installer install.sh -d <dest>. The installer's
# default invocation (no extra flags) selects the base theme + all three
# brightness variants — exactly what we want. The installer calls
# gtk-update-icon-cache mid-install, which would either fail in the
# chroot build env or pollute the build with cache binaries we don't
# want shipped; sed-strip those calls before invoking install.sh.

configure() {
    set -e
    # Neutralize gtk-update-icon-cache calls — defer caching to post_install.
    sed -i 's|gtk-update-icon-cache|true|g' install.sh
}

build() {
    set -e
    :  # No build step.
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/icons"
    bash install.sh -d "${DESTDIR}/usr/share/icons"
}

post_install() {
    set -e
    for d in WhiteSur WhiteSur-light WhiteSur-dark ; do
        gtk-update-icon-cache -q "/usr/share/icons/${d}" 2>/dev/null || true
    done
}
