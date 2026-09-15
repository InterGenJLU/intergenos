#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU

# Upstream ships interpreted Python; no configuration or compilation is needed.
# The builder's PATCH phase verifies and applies the package.yml patches first.
configure() { :; }
build() { :; }

do_install() {
    set -e
    install -Dm755 src/wsdd.py "${DESTDIR}/usr/bin/wsdd"
    install -Dm644 man/wsdd.8 "${DESTDIR}/usr/share/man/man8/wsdd.8"
    install -Dm644 etc/systemd/wsdd.service \
        "${DESTDIR}/usr/lib/systemd/system/wsdd.service"
    install -Dm644 LICENSE "${DESTDIR}/usr/share/licenses/wsdd/LICENSE"
}

# GVfs starts its own discovery client. The advertising service remains subject
# to the distribution's presets; this recipe does not enable it.
