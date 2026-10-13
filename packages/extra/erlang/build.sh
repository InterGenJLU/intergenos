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

    # /usr/bin/typer is the command of the Python typer package
    # (packages/ai/typer), which ships a regular file there. Upstream's install
    # links the same path to Erlang's own type checker, and two packages
    # cannot ship one path as two kinds of file: the second install would
    # write through the link into the first package's tool, and pkm refuses
    # it. The link is not installed; the Erlang tool stays at
    # /usr/lib/erlang/bin/typer. Both checks are gates: an upstream install
    # that stops making the link, or stops shipping the tool, stops the build
    # here so this step is read again.
    if [ ! -L "${DESTDIR}/usr/bin/typer" ]; then
        echo "erlang: upstream install no longer links /usr/bin/typer; review this step" >&2
        return 1
    fi
    rm -f "${DESTDIR}/usr/bin/typer"
    if [ ! -f "${DESTDIR}/usr/lib/erlang/bin/typer" ]; then
        echo "erlang: /usr/lib/erlang/bin/typer is missing from the install" >&2
        return 1
    fi
}
