#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# ghc 9.14.1 — The Glasgow Haskell Compiler
# InterGenOS extra-tier language toolchain (RC001 unlock lane), built FROM
# SOURCE against a pinned binary seed.
#
# HEAVY-BOOTSTRAP. ghc is written in Haskell, so compiling it requires an
# existing ghc. Strategy (the in-tree rust/go bootstrap precedent; build-rules
# §2.5 pinned-source form):
#   1. Install the sha256-pinned upstream binary seed (ghc-9.10.2) to a scratch
#      prefix — used ONLY to compile the shipped compiler.
#   2. ./configure the 9.14.1 source with the seed ghc.
#   3. Bootstrap Hadrian (ghc's build system) OFFLINE, then build the release
#      flavour with it and emit a binary-dist tree.
#   4. Install that binary-dist via its own DESTDIR-clean configure+make.
# The shipped artifact is built entirely from ghc-9.14.1-src.tar.xz; the seed
# contributes no bytes to it.
#
# WALL-4 RE-AUTHOR (documented no-cabal Hadrian bootstrap):
#   Hadrian is itself Haskell and normally builds via cabal-install, which this
#   system does not provide (the ./hadrian/build wrapper's build-cabal path
#   fails). GHC ships hadrian/bootstrap/bootstrap.py for exactly this packager
#   case — it builds Hadrian from a fixed Hackage dependency set using ONLY the
#   seed ghc. bootstrap.py downloads those deps by default; for the offline
#   chroot every dep (source tarball + revised .cabal) is pinned in package.yml
#   and fed via bootstrap.py's `-s <sources-tarball>` route, assembled below.
#   The dep set + all sha256s come from the in-tree
#   hadrian/bootstrap/plan-bootstrap-9_10_2.json (matched to the 9.10.2 seed);
#   the 23 "builtin" bootstrap deps are provided by the seed's own package db.
#   VERIFIED (not assumed): the GHC compiler source ships pre-generated parsers,
#   so no external alex/happy is needed to build GHC; `alex` DOES appear as a
#   Hadrian bootstrap dependency and is built from its own pinned tarball during
#   the bootstrap — a disclosed pinned input, never a silent vendor.
#
# WALL-5 RE-AUTHOR (system libffi for the RTS):
#   With Hadrian bootstrapped, its release build next builds an in-tree copy of
#   libffi and installs it to _build/stage1/libffi/build/inst/lib/libffi.a. That
#   install is DESTDIR-redirected (the recipe env carries do_install's DESTDIR
#   into every phase), so the archive never lands where Hadrian's rule reads it
#   and the build fails "rule failed to produce libffi.a". Rather than guard the
#   symptom, the release is configured --with-system-libffi so Hadrian skips the
#   bundled libffi entirely and links the pinned from-source core libffi — the
#   route every mainstream distro takes. Full rationale + the configure form are
#   in configure() step 2. (See core/libffi; the RTS runtime-links libffi.so, so
#   libffi is a declared build + runtime dependency in package.yml.)
#
# Seed provenance (pinned in package.yml source:):
#   ghc-9.10.2-x86_64-deb11-linux.tar.xz
#   https://downloads.haskell.org/ghc/9.10.2/ghc-9.10.2-x86_64-deb11-linux.tar.xz
#   sha256 2fe2c3e0a07e4782530e8bf83eeda8ff6935e40d5450c1809abcdc6182c9c848
#
# BUILD-VERIFY items (confirmed on the chroot build-verify leg):
#   * seed floor is CLEARED (walls 1-2 probe-proven: env -u DESTDIR install +
#     the scoped libtinfo shim; seed ghc --version rc=0). The older ghc-9.10.2-src
#     fallback chain is NOT needed and is not carried.
#   * confirm the bootstrapped hadrian appears at
#     hadrian/bootstrap/_build/bin/hadrian, and the emitted binary-dist dir name
#     (target triple) matches the do_install glob.
#   * confirm the release build takes Hadrian's useSystemFfi path — the log
#     prints "System supplied FFI library will be used" and the bundled
#     libffi Make/install rule (inst/lib/libffi.a) never runs (wall 5).

# Hadrian offline-bootstrap dependency set (pkg version) — MUST match the
# package.yml source: pins and hadrian/bootstrap/plan-bootstrap-9_10_2.json.
_BOOT_DEPS="\
alex 3.5.2.0
base16-bytestring 1.0.2.0
Cabal 3.14.1.1
Cabal-syntax 3.14.1.0
clock 0.8.4
cryptohash-sha256 0.11.102.1
directory 1.3.9.0
extra 1.8
file-io 0.1.5
filepattern 0.1.3
hashable 1.5.0.0
heaps 0.4.1
js-dgtable 0.5.2
js-flot 0.8.3
js-jquery 3.3.1
primitive 0.9.0.0
process 1.6.25.0
random 1.3.0
shake 0.19.8
splitmix 0.1.1
unordered-containers 0.2.20
utf8-string 1.0.2"

_seed_dir="$PWD/.boot-ghc"

configure() {
    set -e
    # 1. Install the pinned binary seed to a scratch prefix.
    rm -rf "$_seed_dir"
    mkdir -pv "$_seed_dir/src"
    tar xf "$IGOS_SOURCES/ghc-9.10.2-x86_64-deb11-linux.tar.xz" \
        -C "$_seed_dir/src" --strip-components=1
    # env -u DESTDIR: the builder exports DESTDIR for do_install staging, and
    # the seed's make install prepends it — the boot compiler lands under the
    # staging root instead of the scratch prefix and 'ghc' is never on PATH
    # (build-verify wall 1; the documented DESTDIR-redirect class).
    ( cd "$_seed_dir/src" && ./configure --prefix="$_seed_dir/inst" && env -u DESTDIR make install )

    # Scoped libtinfo shim (build-verify wall 2, probe-proven): the deb11 seed
    # links Debian's libtinfo.so.6 split, which this system does not ship
    # (terminfo lives in libncursesw). A PRIVATE compat dir on LD_LIBRARY_PATH
    # satisfies the seed only — nothing system-wide changes, and the shim dies
    # with the scratch dir. Probe: seed ghc --version rc=0 under exactly this
    # shim (versioned-symbol warnings are non-fatal).
    mkdir -pv "$_seed_dir/compat"
    ln -sfv /usr/lib/libncursesw.so.6 "$_seed_dir/compat/libtinfo.so.6"
    export LD_LIBRARY_PATH="$_seed_dir/compat${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export PATH="$_seed_dir/inst/bin:$PATH"
    ghc --version

    # 2. Configure the real source against the seed compiler. ghc 9.14 retired
    # --with-ghc; the compiler is passed as the GHC configure var (build-verify
    # wall 3; upstream's own error message prescribes exactly this form).
    #
    # --with-system-libffi (build-verify wall 5): link the RTS against the
    # system libffi — a pinned, from-source core package (ffi.h at /usr/include,
    # libffi.so at /usr/lib) — instead of GHC's bundled in-tree copy. This makes
    # Hadrian's Rules/Libffi.hs take its useSystemFfi branch, which copies the
    # system ffi.h/ffitarget.h into the RTS and links -lffi, and NEVER runs the
    # bundled libffi Make/install rule. That rule's output (inst/lib/libffi.a)
    # failed to materialize where Hadrian expects because the recipe env carries
    # DESTDIR (exported once for the whole package run, for do_install staging)
    # into the build phase, so the in-tree libffi's `make install` was
    # DESTDIR-redirected out of the tree the rule reads — the same DESTDIR class
    # the seed install guards with `env -u DESTDIR` above. The in-tree libffi
    # install is a long-standing upstream fragility area regardless (GHC
    # #9620 / #11109 / #16020); system libffi removes the class, not just the
    # symptom. --with-ffi-includes is passed EXPLICITLY because
    # --with-system-libffi WITHOUT it is a documented Hadrian failure (GHC
    # #21487 / #20579); --with-ffi-libraries is passed for symmetry.
    #   Decided 2026-07-23 under research: every mainstream from-source distro
    #   surveyed — Debian, Fedora, Arch, Alpine, Gentoo — builds GHC against
    #   system libffi (a second bundled copy is contrary to distro policy and to
    #   the single-verified-source posture). Shared-only libffi is sufficient:
    #   the useSystemFfi branch links -lffi dynamically and needs no static
    #   libffi.a, so no change to the core libffi package is required.
    # Fail loud if the header is absent so a missing/moved libffi can never fall
    # through to the empty-ffi-includes Hadrian failure above.
    [ -f /usr/include/ffi.h ] || { echo "FATAL: system libffi header /usr/include/ffi.h absent — cannot configure --with-system-libffi" >&2; exit 1; }
    ./configure --prefix=/usr GHC="$_seed_dir/inst/bin/ghc" \
        --with-system-libffi                                \
        --with-ffi-includes=/usr/include                    \
        --with-ffi-libraries=/usr/lib
}

build() {
    set -e
    # GHC/Hadrian REQUIRE a UTF-8 locale (the distro-standard export: Debian/
    # Fedora/Alpine all set one in their ghc recipes). The chroot default is
    # POSIX/ASCII, under which hadrian both misreads haskeline's deliberately
    # unicode-named test file (tests/dummy-μασ → replacement chars → "no rule
    # available") AND crashes printing the error (hPutChar: cannot encode
    # '\65533'). C.UTF-8 ships in the chroot glibc.
    export LC_ALL=C.UTF-8
    # Phases run in separate shells — re-establish the seed PATH and the scoped
    # libtinfo shim (see configure) for every boot-ghc invocation.
    export LD_LIBRARY_PATH="$_seed_dir/compat${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export PATH="$_seed_dir/inst/bin:$PATH"
    local seed_ghc="$_seed_dir/inst/bin/ghc"

    # 3a. Assemble the offline bootstrap-sources tarball bootstrap.py -s expects:
    # every pinned dep tarball + its revised .cabal + the in-tree plan renamed to
    # plan-bootstrap.json, all flat at the tarball root (the layout bootstrap.py's
    # `fetch` command would have produced online).
    local boot_stage="$PWD/.hadrian-boot-src"
    rm -rf "$boot_stage"; mkdir -pv "$boot_stage"
    local pkg ver
    while read -r pkg ver; do
        [ -n "$pkg" ] || continue
        cp -v "$IGOS_SOURCES/${pkg}-${ver}.tar.gz" "$boot_stage/${pkg}-${ver}.tar.gz"
        cp -v "$IGOS_SOURCES/${pkg}.cabal"         "$boot_stage/${pkg}.cabal"
    done <<< "$_BOOT_DEPS"
    cp -v hadrian/bootstrap/plan-bootstrap-9_10_2.json "$boot_stage/plan-bootstrap.json"
    local boot_tar="$PWD/.hadrian-boot-src.tar.gz"
    ( cd "$boot_stage" && tar czf "$boot_tar" . )

    # 3b. Bootstrap Hadrian OFFLINE with the seed ghc — no network, no
    # cabal-install. Run from hadrian/bootstrap so its _build stays isolated from
    # the main GHC build's _build. Result: hadrian/bootstrap/_build/bin/hadrian.
    # --no-archive: skip bootstrap.py's post-success redistribution tarball —
    # this build consumes _build/bin/hadrian directly, and the archive step is
    # the only lsb_release consumer in the bootstrap (its output lands in the
    # archive filename).
    ( cd hadrian/bootstrap && python3 ./bootstrap.py -w "$seed_ghc" -s "$boot_tar" --no-archive )
    local hadrian_bin="$PWD/hadrian/bootstrap/_build/bin/hadrian"
    [ -x "$hadrian_bin" ] || { echo "bootstrapped hadrian not found at $hadrian_bin" >&2; exit 1; }

    # 3c. Drive the release build with the bootstrapped Hadrian (replaces the
    # cabal-requiring ./hadrian/build wrapper; same flavour/args as before).
    # Emit a binary-dist tree for a DESTDIR-clean install.
    "$hadrian_bin" -j"$(nproc)" --flavour=release --docs=none binary-dist-dir
}

do_install() {
    set -e
    # Same UTF-8 requirement as build() — the bindist configure runs GHC tools.
    export LC_ALL=C.UTF-8
    export PATH="$_seed_dir/inst/bin:$PATH"
    # 4. Install from the emitted binary-dist (its configure+make honor DESTDIR).
    local bindist
    bindist=$(echo _build/bindist/ghc-*-x86_64-*linux*/)
    cd "$bindist"
    ./configure --prefix=/usr
    make install DESTDIR="${DESTDIR}"
}
