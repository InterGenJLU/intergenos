#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# erlang 29.0.3 — Erlang/OTP concurrent language + runtime.
#
# Not in the BLFS book (13.x). Recipe grounded on the upstream INSTALL guide
# (github.com/erlang/otp HOWTO/INSTALL.md) and cross-checked against the Arch
# and Alpine recipes: from-source configure/make against system OpenSSL +
# ncurses; the GUI (wx/observer), ODBC and jinterface-javac apps are disabled
# so the toolchain carries no display, unixODBC or JDK build-time coupling.

configure() {
    set -e
    export ERL_TOP="$PWD"

    # System OpenSSL for crypto/ssl/public_key; system ncurses for the shell.
    ./configure --prefix=/usr            \
                --enable-threads         \
                --enable-kernel-poll     \
                --enable-dynamic-ssl-lib \
                --with-ssl               \
                --without-wx             \
                --without-odbc           \
                --without-javac
}

build() {
    set -e
    export ERL_TOP="$PWD"
    make -j${IGOS_JOBS}
}

check() {
    set -e
    export ERL_TOP="$PWD"
    # Smoke-verify the freshly-built runtime. The upstream conformance suite
    # (make release_tests + ts:run) needs an installed release and hours of
    # runtime and is not run in-chroot; this proves erl/eval works.
    "$ERL_TOP/bin/erl" -noshell \
        -eval 'io:format("hello, InterGenOS~n"), halt().'
}

do_install() {
    set -e
    export ERL_TOP="$PWD"
    make DESTDIR="$DESTDIR" install
}
