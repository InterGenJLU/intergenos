#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# intergenos-launch-monitor 1.0 — first-party GNOME Shell extension that moves a
# launched game window to the user-declared monitor before its first frame.
#
# WHY (design item, operator-proposed 2026-07-08 at the Limbo session): under
# GNOME Wayland only the compositor may place another client's window, so the
# mover lives IN the compositor as a Shell extension (the shipped
# intergenos-settings-arrow is the precedent + infrastructure). It watches
# window-created, matches game window classes (steam_app_*/gamescope by default,
# derived from the measured GE launch path — all titles run through Steam Linux
# Runtime — and user-extensible), and calls Mutter move_to_monitor(declared).
# The user DECLARES the monitor in the extension preferences (gsettings-backed
# picker); the system obeys. Inert until declared; fail-safe on an unresolved
# output. gamescope (the sibling package) is the per-title scaling/output layer.
#
# Source layout: assets/intergenos-launch-monitor/intergenos-launch-monitor@intergenos.org/
#   extension.js  prefs.js  metadata.json  schemas/<id>.gschema.xml
# Install layout: /usr/share/gnome-shell/extensions/intergenos-launch-monitor@intergenos.org/
#   (+ schemas/gschemas.compiled, compiled here at build time)
#
# Default-enabled (inert until a monitor is declared) via
# config/gsettings/91_intergenos-extensions.gschema.override per the D-006 SSoT.

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

    local uuid="intergenos-launch-monitor@intergenos.org"
    local src="/mnt/intergenos/assets/intergenos-launch-monitor/${uuid}"
    local ext_dir="${DESTDIR}/usr/share/gnome-shell/extensions/${uuid}"

    install -dm755 "${ext_dir}/schemas"
    install -m644 "${src}/extension.js"   "${ext_dir}/extension.js"
    install -m644 "${src}/prefs.js"       "${ext_dir}/prefs.js"
    install -m644 "${src}/metadata.json"  "${ext_dir}/metadata.json"
    install -m644 "${src}/schemas/"*.gschema.xml "${ext_dir}/schemas/"

    # Compile the extension's PRIVATE schema in place so the extension + prefs
    # resolve their settings from the extension dir (getSettings() reads
    # schemas/gschemas.compiled here — no system schema install needed).
    glib-compile-schemas --strict "${ext_dir}/schemas"
    if [ ! -f "${ext_dir}/schemas/gschemas.compiled" ]; then
        echo "ERROR: gschemas.compiled not produced — extension schema compile failed" >&2
        exit 1
    fi
}

post_install() {
    set -e
    :
}
