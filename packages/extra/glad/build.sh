#!/bin/bash
# glad 2.0.8 — OpenGL/Vulkan/EGL loader generator
# BLFS 13.0

configure() {
    :
}

build() {
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    pip3 install --no-index --find-links dist --no-user \
         --root="$DESTDIR" --no-deps glad2
}
