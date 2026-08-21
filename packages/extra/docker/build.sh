#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# docker 29.5.2 — pack, ship, and run any application as a lightweight container
# Upstream: https://www.docker.com/
#
# Bundle layout (matches Arch + Fedora moby-engine pattern):
#   1. moby/moby v29.5.2     -> dockerd + docker-proxy
#   2. docker/cli v29.5.2    -> docker CLI
#   3. tini @ pinned commit  -> docker-init (PID 1 inside containers)
#
# Three sources land in $SRC_DIR after orchestrator extracts source[0]; we
# extract source[1] and source[2] ourselves in configure(). The pinned tini
# commit (de40ad007797e0dcd8b7126f27bb87401d224240) is what Arch + Fedora
# both ship; it is the upstream-recommended PID-1 for foreign-OS rootfses.
#
# Moby + CLI build via GOPATH mode (GO111MODULE=off) because the upstream
# Makefiles expect to be invoked from $GOPATH/src/github.com/{moby/moby/v2,
# docker/cli}. We replicate Arch's _fake_gopath_pushd helper inline. The
# vendor/ directories ship in the upstream tarballs — no network fetch.
#
# Build tags:
#   moby: seccomp journald apparmor — security tags + our journal backend +
#         our LSM. Matches Arch's DOCKER_BUILDTAGS.
#   cli:  no tags needed for the static binary (osusergo+netgo auto-applied)
#   tini: -DBUILD_TESTING=OFF (we don't have the test harness), static build
#
# Default-state posture (security-only alignment): docker.service + docker.socket ship
# DISABLED. We do NOT call `systemctl enable` in post_install. The package
# `iso_include: false` means it never lands without operator action anyway,
# but the secure-default belt-and-suspenders still applies.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

# Source-layout constants
TINI_COMMIT="de40ad007797e0dcd8b7126f27bb87401d224240"
MOBY_DIR_NAME="moby-docker-v29.5.2"
CLI_DIR_NAME="cli-29.5.2"
TINI_DIR_NAME="tini-${TINI_COMMIT}"

# Pinned short commits (synthetic — we have no .git tree in chroot). These
# are EMBEDDED in the binary via -X ldflags. Use the upstream tag's commit.
DOCKER_GITCOMMIT_MOBY="568f755"   # moby/moby docker-v29.5.2
DOCKER_GITCOMMIT_CLI="79eb04c"    # docker/cli v29.5.2

configure() {
    set -e

    # The orchestrator extracts source[0] (moby) into our cwd via
    # --strip-components=1 — we are inside the moby tree. Walk up one
    # level so all three source trees live as siblings under $SRC_PARENT.
    SRC_PARENT="$(dirname "$PWD")"
    MOBY_SRC="$PWD"

    # Extract docker/cli into a sibling directory
    cd "$SRC_PARENT"
    rm -rf cli && mkdir cli
    tar -xzf "${IGOS_SOURCES}/docker-cli-29.5.2.tar.gz" -C cli --strip-components=1
    CLI_SRC="$SRC_PARENT/cli"

    # Extract tini into a sibling directory
    rm -rf tini && mkdir tini
    tar -xzf "${IGOS_SOURCES}/tini-${TINI_COMMIT}.tar.gz" -C tini --strip-components=1
    TINI_SRC="$SRC_PARENT/tini"

    # Build a fake GOPATH with symlinks pointing to the real source trees.
    # moby's hack/make.sh + docker/cli's Makefile both assume they were
    # checked out into $GOPATH/src/github.com/{moby/moby/v2,docker/cli}.
    # Without this layout, version-embed ldflags + relative includes fail.
    export GOPATH="$SRC_PARENT/gopath"
    mkdir -p "$GOPATH/src/github.com/moby"
    mkdir -p "$GOPATH/src/github.com/docker"
    mkdir -p "$GOPATH/src/github.com/krallin"

    # moby's go.mod declares `module github.com/moby/moby/v2`; the v2 in the
    # path is the import path suffix per Go module versioning. The fake
    # GOPATH layout has to match.
    rm -rf "$GOPATH/src/github.com/moby/moby"
    mkdir -p "$GOPATH/src/github.com/moby/moby"
    ln -sfn "$MOBY_SRC" "$GOPATH/src/github.com/moby/moby/v2"

    ln -sfn "$CLI_SRC"  "$GOPATH/src/github.com/docker/cli"
    ln -sfn "$TINI_SRC" "$GOPATH/src/github.com/krallin/tini"

    # Stash the absolute paths for build() + do_install() to use.
    echo "$SRC_PARENT" > /tmp/.docker-build-src-parent
    echo "$MOBY_SRC"   > /tmp/.docker-build-moby-src
    echo "$CLI_SRC"    > /tmp/.docker-build-cli-src
    echo "$TINI_SRC"   > /tmp/.docker-build-tini-src
    echo "$GOPATH"     > /tmp/.docker-build-gopath
}

build() {
    set -e
    MOBY_SRC=$(cat /tmp/.docker-build-moby-src)
    CLI_SRC=$(cat /tmp/.docker-build-cli-src)
    TINI_SRC=$(cat /tmp/.docker-build-tini-src)
    export GOPATH=$(cat /tmp/.docker-build-gopath)
    export PATH="$GOPATH/bin:$PATH"

    # ---- moby (dockerd + docker-proxy) -------------------------------------
    # CGO mandatory for dockerd (libseccomp + libsystemd + libnftables +
    # libapparmor); CGO disabled for docker-proxy by hack/make/binary-proxy
    # itself. hack/make.sh dispatches both.
    cd "$GOPATH/src/github.com/moby/moby/v2"
    export GO111MODULE=off
    export GOTOOLCHAIN=local
    export DISABLE_WARN_OUTSIDE_CONTAINER=1
    export CGO_ENABLED=1
    export DOCKER_GITCOMMIT="$DOCKER_GITCOMMIT_MOBY"
    export DOCKER_BUILDTAGS='seccomp journald apparmor'
    export VERSION="29.5.2"
    # dynbinary (vs binary) links dynamically against system libs — we ship
    # the libs ourselves so no need for full-static. Matches Arch's choice.
    bash -x ./hack/make.sh dynbinary

    # ---- docker CLI ---------------------------------------------------------
    # CGO disabled for clean static binary; osusergo+netgo auto-applied.
    # The CLI uses vendor.mod (no go.mod). scripts/build/binary handles the
    # modfile internally in GOPATH mode; generate-man.sh needs module mode
    # forced (see below).
    cd "$GOPATH/src/github.com/docker/cli"
    export GO111MODULE=auto
    export CGO_ENABLED=0
    export GO_LINKMODE=static
    export VERSION="29.5.2"
    export GITCOMMIT="$DOCKER_GITCOMMIT_CLI"
    export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1748390400}"  # release date
    # scripts/build/.variables APPENDS /docker-${GOOS}-${GOARCH} to TARGET
    # and scripts/build/binary then creates a `docker` symlink alongside it.
    # So TARGET=build yields build/docker-linux-amd64 + a build/docker symlink
    # -> do_install's "$CLI_SRC/build/docker" resolves to the binary. (Setting
    # TARGET=build/docker instead makes build/docker a DIRECTORY -> install
    # omits-directory failure.)
    export TARGET="build"
    bash ./scripts/build/binary
    # docker/cli ships NO go.mod on purpose (renamed to vendor.mod), so
    # generate-man.sh's `go run -modfile=vendor.mod` has no module root to
    # anchor to ("cannot find main module ... -modfile cannot be used to set
    # the module root directory"). Upstream solves this with scripts/with-go-mod.sh:
    # it writes a temporary go.mod (module github.com/docker/cli), runs the
    # command under GO111MODULE=on GOTOOLCHAIN=local, then removes the go.mod on
    # exit. This is exactly how upstream's Makefile `manpages` target invokes it
    # (Makefile:127). Mirror it rather than reinventing the anchor.
    bash ./scripts/with-go-mod.sh ./scripts/docs/generate-man.sh

    # ---- tini (docker-init) -------------------------------------------------
    # cmake + make tini-static. CMake policy version min suppression matches
    # Arch's PKGBUILD. -DBUILD_TESTING=OFF drops the test harness which
    # needs network/python deps we don't want to drag in.
    cd "$TINI_SRC"
    cmake -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -DBUILD_TESTING=OFF \
          .
    make tini-static
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        true
}

do_install() {
    set -e
    MOBY_SRC=$(cat /tmp/.docker-build-moby-src)
    CLI_SRC=$(cat /tmp/.docker-build-cli-src)
    TINI_SRC=$(cat /tmp/.docker-build-tini-src)

    # ---- dockerd + docker-proxy --------------------------------------------
    install -Dm755 "$MOBY_SRC/bundles/dynbinary-daemon/dockerd"     "$DESTDIR/usr/bin/dockerd"
    install -Dm755 "$MOBY_SRC/bundles/dynbinary-daemon/docker-proxy" "$DESTDIR/usr/bin/docker-proxy"

    # ---- tini -> docker-init -----------------------------------------------
    install -Dm755 "$TINI_SRC/tini-static" "$DESTDIR/usr/bin/docker-init"

    # ---- docker CLI --------------------------------------------------------
    install -Dm755 "$CLI_SRC/build/docker" "$DESTDIR/usr/bin/docker"

    # CLI shell completions (generated by the freshly built binary itself)
    install -d -m 755 "$DESTDIR/usr/share/bash-completion/completions"
    "$CLI_SRC/build/docker" completion bash > "$DESTDIR/usr/share/bash-completion/completions/docker"
    chmod 644 "$DESTDIR/usr/share/bash-completion/completions/docker"

    install -d -m 755 "$DESTDIR/usr/share/zsh/site-functions"
    "$CLI_SRC/build/docker" completion zsh > "$DESTDIR/usr/share/zsh/site-functions/_docker"
    chmod 644 "$DESTDIR/usr/share/zsh/site-functions/_docker"

    install -d -m 755 "$DESTDIR/usr/share/fish/vendor_completions.d"
    "$CLI_SRC/build/docker" completion fish > "$DESTDIR/usr/share/fish/vendor_completions.d/docker.fish"
    chmod 644 "$DESTDIR/usr/share/fish/vendor_completions.d/docker.fish"

    # ---- man pages ----------------------------------------------------------
    # CLI man pages (generated by scripts/docs/generate-man.sh into man/man*/)
    install -d -m 755 "$DESTDIR/usr/share/man"
    cp -r "$CLI_SRC/man/man"* "$DESTDIR/usr/share/man/"
    # The engine has no man page. Decided 2026-08-21: state that plainly here
    # rather than leave a conditional install that cannot fire. moby ships the
    # page only as markdown (man/dockerd.8.md) plus a man/Makefile that renders
    # it; build() does not invoke that Makefile, so man/man8/dockerd.8 never
    # exists and the guarded install was always skipped. Measured on the
    # published 29.5.2 archive: 179 man1 pages, 2 man5 pages, 0 man8 pages.
    # `dockerd --help` is the engine's doc surface until the man/ Makefile is
    # invoked; enabling that is a build-step change and belongs to a cycle that
    # can compile-prove it, together with a man8 entry in verify_paths.

    # ---- systemd unit files ------------------------------------------------
    # Shipped from moby's upstream contrib/init/systemd/ verbatim.
    # docker.service references containerd.service in After= + Wants= — both
    # resolve via our packages/extra/containerd. firewalld.service is also
    # in After= but systemd silently ignores absent units; we ship nftables
    # instead and that's fine.
    install -Dm644 "$MOBY_SRC/contrib/init/systemd/docker.service" \
        "$DESTDIR/usr/lib/systemd/system/docker.service"
    install -Dm644 "$MOBY_SRC/contrib/init/systemd/docker.socket" \
        "$DESTDIR/usr/lib/systemd/system/docker.socket"

    # ---- sysusers.d --------------------------------------------------------
    # moby's contrib/systemd-sysusers/docker.conf carries the explicit
    # security warning about docker-group = root-equivalent (security-
    # aligned) plus `g docker -` (group only, no user). Ship verbatim.
    install -Dm644 "$MOBY_SRC/contrib/systemd-sysusers/docker.conf" \
        "$DESTDIR/usr/lib/sysusers.d/docker.conf"

    # ---- cli-plugins directory (empty placeholder) -------------------------
    # buildx / compose would land plugin binaries here. We don't ship either
    # in v1.0; the directory must exist for the CLI's plugin loader to scan
    # gracefully (it tolerates missing dirs but tools like docker-buildx
    # installers expect the canonical path to be present).
    install -d -m 755 "$DESTDIR/usr/libexec/docker/cli-plugins"

    # ---- /etc/docker config dir (empty placeholder) ------------------------
    # dockerd creates /var/lib/docker on first start; we don't pre-stage it.
    # /etc/docker/daemon.json is the user-edited config — directory present
    # but empty so operator-supplied config has a canonical home.
    install -d -m 755 "$DESTDIR/etc/docker"

    # Clean up scratch files
    rm -f /tmp/.docker-build-src-parent \
          /tmp/.docker-build-moby-src \
          /tmp/.docker-build-cli-src \
          /tmp/.docker-build-tini-src \
          /tmp/.docker-build-gopath
}

post_install() {
    set -e
    # Process this package's /usr/lib/sysusers.d/docker.conf entry so the
    # docker group exists. Idempotent; safe to re-run.
    systemd-sysusers /usr/lib/sysusers.d/docker.conf 2>/dev/null || true

    # Default-deny posture: do NOT auto-enable docker.service or docker.socket. The
    # operator who actually wants docker explicitly enables them after
    # install. Mirror-only distribution means the package never lands
    # without operator action; this is the belt-and-suspenders layer.
    #
    # No daemon-reload here either: pkm's canonical systemd-daemon-reload hook
    # owns it, armed by this package's own usr/lib/systemd/system/docker.service
    # and docker.socket, and it reports its own result instead of absorbing it.
}
