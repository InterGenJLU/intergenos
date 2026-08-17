#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gstreamer 1.28.1 — GStreamer multimedia framework
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=disabled \
          -Dexamples=disabled \
          -Ddoc=disabled
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # gstreamer 1.28's introspection ships gst_init() / gst_init_check() with
    # the `argv` parameter NOT marked nullable, so gjs rejects the canonical
    # `Gst.init_check(null)` / `Gst.init(null)` calls ("Expected type utf8 for
    # Argument 'argv' but got type 'null'"). That breaks EVERY gjs GStreamer
    # consumer — notably GNOME Shell 49's screencast service, which dies on load
    # so screen recording is broken (G3-13, found on a development machine). gst_init(NULL,NULL)
    # is valid C, so the nullable annotation is correct; restore it on the
    # installed GIR and recompile the typelib. Fixes all consumers at the root.
    # Validated: `Gst.init_check(null)` returns true against the patched typelib.
    local _gir="${DESTDIR}/usr/share/gir-1.0/Gst-1.0.gir"
    local _typelib="${DESTDIR}/usr/lib/girepository-1.0/Gst-1.0.typelib"
    if [ ! -f "$_gir" ] || [ ! -f "$_typelib" ]; then
        echo "gstreamer: Gst-1.0 gir/typelib missing in DESTDIR — cannot apply the gjs init(null) fix" >&2
        exit 1
    fi
    python3 - "$_gir" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
def fix(m):
    t = m.group(0)
    if 'nullable' in t:
        return t
    # The argv tag already carries optional="1"; only add it when it is absent,
    # otherwise we emit a duplicate attribute (malformed XML — see the ET.parse
    # well-formedness assertion below). Always add the nullable annotation.
    ins = ' nullable="1"' + ('' if 'optional=' in t else ' optional="1"')
    return t[:-1] + ins + '>'
s2 = re.sub(r'<parameter name="argv"[^>]*>', fix, s)
# Verify the patch took on every argv parameter. The GIR's argv tags are
# multi-line and carry other attributes (direction, caller-allocates,
# transfer-ownership), so fix() appends the nullable annotation at the END of
# each tag — a naive count('name="argv" nullable') substring check false-fails
# because nullable does not sit immediately after name="argv". Re-parse the
# argv tags and assert each now carries the annotation.
_argv_tags = re.findall(r'<parameter name="argv"[^>]*>', s2)
if not _argv_tags or not all('nullable' in t for t in _argv_tags):
    sys.exit('gstreamer: no argv parameters marked nullable — GIR shape changed?')
open(p, 'w').write(s2)
# Assert the patched GIR is well-formed XML. g-ir-compiler (below) tolerates a
# duplicate attribute, but the downstream consumer's g-ir-scanner parses the
# installed Gst-1.0.gir with strict xml.etree — a malformed GIR silently passes
# here and then explodes in gst-plugins-base ("duplicate attribute"). Validate
# the produced XML at the root so a future patch regression fails loud, here.
import xml.etree.ElementTree as ET
ET.parse(p)
print('gstreamer: argv params marked nullable in Gst-1.0.gir')
PY
    g-ir-compiler "$_gir" -o "$_typelib"
    echo "gstreamer: recompiled Gst-1.0.typelib with nullable argv (gjs init(null) fix)"
}
