#!/bin/bash
# intergen-pkm-notifier 1.0 — pkm Notifier GNOME Shell extension (Q8 Phase C)
#
# Tray indicator for available pkm package upgrades. Reads
# /var/lib/pkm/available-updates.json written by the pkm-check-updates
# systemd timer-driven service (Q8 Phase B; lives in packages/core/pkm/)
# and surfaces an icon in the top bar when count > 0. Click opens a
# terminal pre-typed with `pkm upgrade --all --dry-run` for review.
#
# NEVER auto-upgrades — informational only per the operator-greenlit Q8
# design. User explicitly invokes the upgrade to act on the notification.
#
# Source layout: assets/intergen-pkm-notifier/pkm-notifier@intergenos.org/
# in the canonical repo. Install layout:
#   /usr/share/gnome-shell/extensions/pkm-notifier@intergenos.org/
#     extension.js
#     metadata.json
#
# Default-enabled via config/gsettings/91_intergenos-extensions.gschema.override
# (per D-006 SSoT — the intergenos-default-settings gschema-override
# package is the canonical source for default-enabled extensions).

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e

    # Copy extension files directly from canonical source. Same pattern
    # as packages/core/pkm/build.sh — first-party authored content lives
    # in /mnt/intergenos/ + ships via cp -a without tarball assembly.
    local ext_dir="${DESTDIR}/usr/share/gnome-shell/extensions/pkm-notifier@intergenos.org"
    install -dm755 "${ext_dir}"
    cp -a /mnt/intergenos/assets/intergen-pkm-notifier/pkm-notifier@intergenos.org/extension.js \
        "${ext_dir}/extension.js"
    cp -a /mnt/intergenos/assets/intergen-pkm-notifier/pkm-notifier@intergenos.org/metadata.json \
        "${ext_dir}/metadata.json"
    chmod 644 "${ext_dir}/extension.js" "${ext_dir}/metadata.json"
}

post_install() {
    set -e
    # No glib-compile-schemas needed here — the extension's settings-schema
    # reference is forward-looking (v1.1 may add a prefs dialog with its
    # own schema). For v1.0 the schema is omitted entirely; the extension
    # works without it. Default-enabled state is set by the
    # intergenos-default-settings gschema-override package.
    :
}
