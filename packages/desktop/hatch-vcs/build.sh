#!/bin/bash
# hatch_vcs 0.5.0 — Hatch plugin for VCS version source
# BLFS 13.0

configure() { : ; }

build() {
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    pip3 install --no-index --no-deps --find-links dist --no-user --root="$DESTDIR" hatch_vcs
}
