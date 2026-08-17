#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# attrs 26.1.0 — Classes without boilerplate.
#
# RUNTIME dependency of aiohttp (aiohttp's METADATA: "attrs>=17.3.0",
# unconditional). aiohttp's do_install resolves its runtime deps against the
# chroot, so attrs must be built+installed before aiohttp or the install fails
# "No matching distribution found for attrs". (aiohttp's other runtime deps —
# aiohappyeyeballs/aiosignal/frozenlist/multidict/propcache/yarl — are already
# packaged; async-timeout is gated python_version < 3.11 so N/A on py3.14.)
#
# Pure-Python; build-backend = hatchling.build with hatch-vcs +
# hatch-fancy-pypi-readme plugins (all already present in the chroot).

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-cache-dir --no-user --root="$DESTDIR" attrs
}
