#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# intergenos-settings-arrow 1.0 -- GNOME Shell extension that re-routes the
# Wi-Fi and Bluetooth QuickSettings menu arrows to open their Settings panel
# directly, instead of the inline submenu.
#
# WHY (PI-Welcome-2, root-caused 2026-06-19 on the development machine GBC004.1 install):
# GNOME 49's QuickSettings renders an expanding quick-toggle submenu
# (.quick-toggle-menu) at full natural height with NO scroll area in the
# subtree. On a short panel (1366x768) the Wi-Fi network list is taller than
# the screen, so clicking the arrow overflows it off-screen (operator-observed:
# the list runs past the bottom edge; earlier the whole menu slid off). A
# scroll/height-clamp was attempted (dash-to-panel _getBoxPointerPreferredHeight
# clamp) and dropped -- the menu ignores the clamp with no scroll area; the
# theme CSS trim (9aa132a5) only fits the SHORT power submenu. The non-scroll
# fix (operator-chosen): re-route the Wi-Fi/Bluetooth arrow to launch the
# Settings panel, which has its own scrolling. Bluetooth included proactively
# (same overflow class once many devices are paired). Other toggles
# (power/wired/VPN) keep their short inline menus untouched.
#
# Validated live on a development machine (1366x768): the arrows open Settings -> Wi-Fi /
# Bluetooth; the toggles + power submenu are unaffected.
#
# Source layout: assets/intergenos-settings-arrow/intergenos-settings-arrow@intergenos.org/
# in the canonical repo. Install layout:
#   /usr/share/gnome-shell/extensions/intergenos-settings-arrow@intergenos.org/
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

    local ext_dir="${DESTDIR}/usr/share/gnome-shell/extensions/intergenos-settings-arrow@intergenos.org"
    install -dm755 "${ext_dir}"
    cp -a /mnt/intergenos/assets/intergenos-settings-arrow/intergenos-settings-arrow@intergenos.org/extension.js \
        "${ext_dir}/extension.js"
    cp -a /mnt/intergenos/assets/intergenos-settings-arrow/intergenos-settings-arrow@intergenos.org/metadata.json \
        "${ext_dir}/metadata.json"
    chmod 644 "${ext_dir}/extension.js" "${ext_dir}/metadata.json"
}

post_install() {
    set -e
    :
}
