#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# neovim 0.12.3 — Hyperextensible Vim-based text editor
#
# Bundled-deps CMake build (upstream USE_BUNDLED=ON default). Two stages:
#   1. cmake.deps/  builds Neovim's pinned dependency set (libuv, luajit, luv,
#      lpeg, lua-compat-5.3, unibilium, utf8proc, tree-sitter + 6 grammars)
#      via ExternalProject into ${DEPS_PREFIX}.
#   2. the main tree links against ${DEPS_PREFIX} and produces bin/nvim +
#      share/nvim/ runtime files.
#
# Offline discipline (upstream BUILD.md "Build offline"): each dep tarball is
# pre-staged into ${IGOS_SOURCES} under a distinct filename (see package.yml)
# and EXTRACTED below into .deps/build/src/<name>/ — the ExternalProject source
# dir (EP_PREFIX = ${CMAKE_BINARY_DIR}/build, per cmake/Deps.cmake). The deps
# build is then run with -DUSE_EXISTING_SRC_DIR=ON, which makes cmake.deps leave
# every dep's _URL unset (cmake/Deps.cmake) so ExternalProject uses the existing
# source dir and never touches the network. This is the documented,
# upstream-supported offline build (the same mechanism Debian/PPA use with the
# neovim/deps src snapshot). The prior copy-tarball-to-DEPS_DOWNLOAD_DIR approach
# depended on each dep's URL basename matching exactly and silently fell through
# to a network fetch for utf8proc.
#
# Pinned versions are read from cmake.deps/deps.txt of the 0.12.3 source.

# Map: <staged filename in $IGOS_SOURCES> -> <ExternalProject src-dir name under .deps/build/src/>
_stage_deps() {
    set -e
    local srcroot="$1"   # = .deps/build/src
    # Each line: "<staged-name>|<src-dir name>"
    local map=(
        "neovim-${PKG_VERSION}-dep-libuv-v1.52.1.tar.gz|libuv"
        "neovim-${PKG_VERSION}-dep-luajit-fbb36bb.tar.gz|luajit"
        "neovim-${PKG_VERSION}-dep-luv-1.52.1-0.tar.gz|luv"
        "neovim-${PKG_VERSION}-dep-lpeg-1.1.0.tar.gz|lpeg"
        "neovim-${PKG_VERSION}-dep-lua_compat53-v0.13.tar.gz|lua_compat53"
        "neovim-${PKG_VERSION}-dep-unibilium-v2.1.2.tar.gz|unibilium"
        "neovim-${PKG_VERSION}-dep-utf8proc-v2.11.3.tar.gz|utf8proc"
        "neovim-${PKG_VERSION}-dep-treesitter-v0.26.7.tar.gz|treesitter"
        "neovim-${PKG_VERSION}-dep-treesitter_c-v0.24.1.tar.gz|treesitter_c"
        "neovim-${PKG_VERSION}-dep-treesitter_lua-v0.5.0.tar.gz|treesitter_lua"
        "neovim-${PKG_VERSION}-dep-treesitter_vim-v0.8.1.tar.gz|treesitter_vim"
        "neovim-${PKG_VERSION}-dep-treesitter_vimdoc-v4.1.0.tar.gz|treesitter_vimdoc"
        "neovim-${PKG_VERSION}-dep-treesitter_query-v0.8.0.tar.gz|treesitter_query"
        "neovim-${PKG_VERSION}-dep-treesitter_markdown-v0.5.3.tar.gz|treesitter_markdown"
    )
    local entry staged name
    for entry in "${map[@]}"; do
        staged="${entry%%|*}"
        name="${entry##*|}"
        mkdir -p "${srcroot}/${name}"
        # Extract the source files directly into src/<name>/ (strip the tarball's
        # single top-level wrapper dir) — the form USE_EXISTING_SRC_DIR expects.
        tar -xf "${IGOS_SOURCES}/${staged}" -C "${srcroot}/${name}" --strip-components=1
    done
}

configure() {
    set -e
    DEPS_PREFIX="${PWD}/.deps/usr"
    # Pre-place each dep's EXTRACTED source at .deps/build/src/<name>/ so the
    # deps build (USE_EXISTING_SRC_DIR=ON) uses them and never downloads.
    DEPS_SRC_DIR="${PWD}/.deps/build/src"
    mkdir -p "$DEPS_SRC_DIR"
    _stage_deps "$DEPS_SRC_DIR"

    # Stage 1: build the bundled dependency set offline.
    cmake -S cmake.deps -B .deps -G Ninja                  \
          -D CMAKE_BUILD_TYPE=Release                      \
          -D CMAKE_INSTALL_PREFIX="$DEPS_PREFIX"           \
          -D USE_BUNDLED=ON                                \
          -D USE_EXISTING_SRC_DIR=ON                       \
          -D ENABLE_WASMTIME=OFF
    cmake --build .deps

    # Stage 2: configure the main tree against the bundled deps.
    cmake -B build -G Ninja                                \
          -D CMAKE_BUILD_TYPE=Release                      \
          -D CMAKE_INSTALL_PREFIX=/usr                     \
          -D CMAKE_PREFIX_PATH="$DEPS_PREFIX"
}

build() {
    set -e
    cmake --build build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
