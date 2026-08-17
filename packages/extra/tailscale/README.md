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
large (~40 MB) because tailscale's module graph is large; that is the
reproducibility cost we accept (see "Why commit" below).

## Toolchain note

`tailscale`'s `go.mod` declares `go 1.26.3`. `packages/core/go` was bumped
1.26.2 → 1.26.4 expressly to clear that floor; `build.sh` sets
`GOTOOLCHAIN=local` so the build uses the in-tree go (1.26.4 ≥ 1.26.3)
instead of fetching a toolchain over the network. `CGO_ENABLED=0` → fully
static `tailscale` + `tailscaled` binaries.

## Daemon

`tailscaled` is a long-running network daemon. The package ships upstream's
own `tailscaled.service` (tracked) + `/etc/default/tailscaled`, but ships
**no systemd preset** — tailscaled is an unconditional network-facing
daemon (joins a mesh VPN, needs operator auth), so per the security-only
posture it stays **disabled** (the `99-*` catch-all wins) until the user
runs `systemctl enable --now tailscaled`.

## Refresh procedure (manual, on demand)

Refresh when `tailscale` ships a new upstream version, or a Go dep needs a
security update (Go module advisory).

```sh
# 1. Bump version + upstream sha256 in package.yml.
NEW_VER=1.98.6   # example
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

# 3. Deterministic tar+xz (the same flags as lego / cargo-vendor-gen.sh).
SDE=$(git -C /mnt/intergenos log -1 --format=%ct)
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime="@${SDE}" \
    -cf - tailscale-${NEW_VER} \
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
