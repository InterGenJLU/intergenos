#!/bin/bash
# intergen-no-overview 1.0 -- Suppress the GNOME activities overview at
# every session startup (InterGenOS fleet-default behavior).
#
# Operator-direct directive 2026-05-21T~01:Z: "PLEASE MAKE THIS OUR
# DEFAULT". The activities overview that gnome-shell pops up at session
# start is operator-disliked as a default; this 5-line extension subscribes
# to Main.layoutManager 'startup-complete' + invokes Main.overview.hide(),
# delivering "land in the desktop, not the overview" behavior for every
# login of every user.
#
# Modeled on the canonical fthx/no-overview source. Pairs with
# packages/desktop/intergen-firstboot/ which uses the same startup-complete
# signal to fire the once-per-user branded animation. This extension is
# independent of the firstboot lifecycle -- runs every login (not gated by
# any marker), so the overview-suppression behavior persists after the
# firstboot animation marker is set.
#
# Source layout: assets/intergen-no-overview/intergen-no-overview@intergenos.org/
# in the canonical repo. Install layout:
#   /usr/share/gnome-shell/extensions/intergen-no-overview@intergenos.org/
#     extension.js
#     metadata.json
#
# Default-enabled via config/gsettings/91_intergenos-extensions.gschema.override
# per D-006 SSoT.

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

    local ext_dir="${DESTDIR}/usr/share/gnome-shell/extensions/intergen-no-overview@intergenos.org"
    install -dm755 "${ext_dir}"
    cp -a /mnt/intergenos/assets/intergen-no-overview/intergen-no-overview@intergenos.org/extension.js \
        "${ext_dir}/extension.js"
    cp -a /mnt/intergenos/assets/intergen-no-overview/intergen-no-overview@intergenos.org/metadata.json \
        "${ext_dir}/metadata.json"
    chmod 644 "${ext_dir}/extension.js" "${ext_dir}/metadata.json"
}

post_install() {
    set -e
    :
}
