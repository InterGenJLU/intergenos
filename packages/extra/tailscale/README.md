# tailscale — WireGuard-based mesh VPN (vendored)

## Why one source is `file:///`

`tailscale` is a Go program. Go programs need their module dependency
graph available at build time — either fetched from module proxies
(online) or pre-vendored into a `vendor/` directory (offline). InterGenOS
builds in an OFFLINE chroot, so all Go deps must be vendored before the
chroot build phase begins. Same posture as `packages/extra/lego`.

The package has **two** source entries:

1. **Upstream source archive** — `https://github.com/tailscale/tailscale/
   archive/refs/tags/v${version}.tar.gz`, pinned by sha256. Fetched by
   `download-sources.py` like any other upstream tarball.
2. **Vendored dependencies archive** — `file:///tailscale-${version}-vendor.tar.xz`,
   pinned by sha256. A LOCAL artifact produced by `go mod vendor` against
   the upstream source. Lives in tree at
   `build/sources/tailscale-<version>-vendor.tar.xz` (selectively
   un-gitignored — see top-level `.gitignore`). The wrapper dir
   `tailscale-<version>/` inside the archive holds `vendor/` + `go.mod` +
   `go.sum`; `build.sh` extracts it with `--strip-components=1`.

The vendor archive does not exist upstream — we generate it so the offline
chroot can compile `tailscale` without network access. This archive is
large (about 61 MB for 1.102.4) because tailscale's module graph is large;
that is the reproducibility cost we accept (see "Why commit" below).

## Toolchain note

`tailscale`'s `go.mod` declares `go 1.26.6`. `packages/core/go` was bumped
1.26.4 → 1.26.8 expressly to clear that floor; `build.sh` sets
`GOTOOLCHAIN=local` so the build uses the in-tree go (1.26.8 ≥ 1.26.6)
instead of fetching a toolchain over the network, and refuses to build with a
go below the floor. `CGO_ENABLED=0` → fully static `tailscale` +
`tailscaled` binaries.

## Daemon

`tailscaled` is a long-running network daemon. The package ships upstream's
own `tailscaled.service` (tracked) + `/etc/default/tailscaled`, but ships
**no systemd preset** — tailscaled is an unconditional network-facing
daemon (joins a mesh VPN, needs operator auth), so per the security-only
posture it stays **disabled** (the `99-*` catch-all wins) until the user
runs `systemctl enable --now tailscaled`.

## Updates: the package manager is the installer of record

`tailscaled` carries its own updater. When the coordination server asks, it
downloads the vendor's build and replaces `/usr/bin/tailscale` and
`/usr/sbin/tailscaled` in place; on a distribution it does not recognize it
uses the vendor's tarball updater. The package database would then go on
describing the build `pkm` installed while different binaries run, and
`pkm verify tailscale` would report both files modified. So the package ships
`tailscaled`'s device policy file, `/etc/tailscale/syspolicy.json`:

```json
{
  "CheckUpdates": "never",
  "InstallUpdates": "never"
}
```

`tailscaled` reads that path by default at start (its `--syspolicy-file`
flag; the shipped unit passes none) and enforces both settings over the
node's own preference (`tailscale set --auto-update`) and the tailnet's
default, so the node neither checks for nor applies updates of its own, and
an update request from the coordination server is answered "not enabled".
New releases arrive with `pkm upgrade tailscale`. `tailscale update` run by
hand does not consult this file: it is an administrator's explicit act and
still replaces both binaries outside the package manager, so use
`pkm upgrade tailscale` instead. The file is plain JSON so it parses whether
or not the daemon was built with its comment-tolerant reader. It is a configuration file: an administrator who wants the vendor's
updater back on a particular machine can change it, and restart
`tailscaled` for the change to take effect.

## Refresh procedure (manual, on demand)

Refresh when `tailscale` ships a new upstream version, or a Go dep needs a
security update (Go module advisory).

```sh
# 1. Bump version + upstream sha256 in package.yml.
NEW_VER=1.102.6   # example
curl -fSLO https://github.com/tailscale/tailscale/archive/refs/tags/v${NEW_VER}.tar.gz
sha256sum v${NEW_VER}.tar.gz   # → package.yml's first source sha256
mv v${NEW_VER}.tar.gz /mnt/intergenos/build/sources/tailscale-${NEW_VER}.tar.gz

# 2. Regenerate the vendor archive (needs go + network to module proxies).
WORK=$(mktemp -d) && cd "$WORK"
tar -xzf /mnt/intergenos/build/sources/tailscale-${NEW_VER}.tar.gz
mv tailscale-${NEW_VER} tailscale-${NEW_VER}.src && mv tailscale-${NEW_VER}.src tailscale-${NEW_VER}
cd tailscale-${NEW_VER}
go mod vendor
cd ..

# 3. Deterministic tar+xz (the same flags as lego / cargo-vendor-gen.sh) of
#    the wrapper dir's go.mod, go.sum and vendor/ only — the layout the
#    committed archives carry; build.sh extracts it over the upstream source.
SDE=$(git -C /mnt/intergenos log -1 --format=%ct)
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mode=u+rwX,go+rX,go-w --mtime="@${SDE}" \
    -cf - tailscale-${NEW_VER}/go.mod tailscale-${NEW_VER}/go.sum \
          tailscale-${NEW_VER}/vendor \
    | XZ_OPT='-9 -T1 --no-warn' xz -c \
    > /mnt/intergenos/build/sources/tailscale-${NEW_VER}-vendor.tar.xz

# 4. Remove the stale vendor archive for the previous version.
rm -f /mnt/intergenos/build/sources/tailscale-<OLD_VER>-vendor.tar.xz

# 5. Update package.yml's vendor archive sha256.
sha256sum /mnt/intergenos/build/sources/tailscale-${NEW_VER}-vendor.tar.xz

# 6. git diff + commit — the blob change in build/sources/ + the .yml diff
#    land together so the refresh is one auditable commit.
```

(The upstream tarball already extracts to `tailscale-<version>/`, so the
wrapper-dir rename in step 2 is a no-op kept for parity with the gh recipe.)

## Why commit the vendor archive (not regenerate per build)?

Same rationale as lego: a committed, sha-pinned vendor archive means
anyone with the repo produces byte-identical `tailscale`/`tailscaled`
binaries forever, without tying build reproducibility to module-proxy
availability or the build host's Go version. The committed archive is the
same posture every other package's source tarball gets, just one whose
origin is local generation.

> A bulk `go-vendor-gen.sh` (the Go analog of `cargo-vendor-gen-all.sh`,
> auto-discovering `build_artifacts: generated_by: go-vendor`) is the
> intended future automation; until it lands, this procedure is manual.
