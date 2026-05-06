#!/bin/bash
# containers-common 0.64.1 — Common container configuration files
# Not in BLFS — InterGenOS extra tier
#
# Provides default registries.conf, storage.conf, and other container
# configuration files shared by Podman, Buildah, and Skopeo.
# This is a config-only package with no compiled code.

configure() { : ; }
build() { : ; }

do_install() {
    install -d "$DESTDIR/etc/containers"
    install -d "$DESTDIR/usr/share/containers"

    # registries.conf — default registry search list
    if [ -f pkg/config/containers.conf ]; then
        install -v -m644 pkg/config/containers.conf "$DESTDIR/usr/share/containers/containers.conf"
    fi
    if [ -f pkg/config/registries.conf ]; then
        install -v -m644 pkg/config/registries.conf "$DESTDIR/etc/containers/registries.conf"
    fi
    if [ -f pkg/config/storage.conf ]; then
        install -v -m644 pkg/config/storage.conf "$DESTDIR/etc/containers/storage.conf"
    fi
    if [ -f pkg/config/policy.json ]; then
        install -v -m644 pkg/config/policy.json "$DESTDIR/etc/containers/policy.json"
    fi

    # seccomp profiles
    if [ -d pkg/seccomp ]; then
        install -v -m644 pkg/seccomp/*.json "$DESTDIR/usr/share/containers/" 2>/dev/null || true
    fi
}
