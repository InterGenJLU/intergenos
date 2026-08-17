# lego — Go-based ACME client (vendored)

## Why one source is `file:///`

`lego` is a Go program. Go programs need their module dependency graph
available at build time — either fetched from module proxies (online) or
pre-vendored into a `vendor/` directory (offline). InterGenOS builds in
an OFFLINE chroot, so all Go deps must be vendored before the chroot
build phase begins.

The package therefore has **two** source entries:

1. **Upstream source archive** — `https://github.com/go-acme/lego/archive/
   refs/tags/v${version}.tar.gz`, pinned by sha256. Fetched normally by
   `download-sources.py` like any other upstream tarball.
2. **Vendored dependencies archive** — `file:///lego-${version}-vendor.tar.xz`,
   pinned by sha256. This is a LOCAL artifact produced by running
   `go mod vendor` against the upstream source. Lives in tree at
   `build/sources/lego-<version>-vendor.tar.xz` (selectively un-gitignored
   — see top-level `.gitignore`).

The vendor archive does not exist upstream — it's an artifact we generate
ourselves so the offline chroot can compile `lego` without network access.

## Source-of-truth

- Upstream tree: <https://github.com/go-acme/lego>
- Each Go dep in the vendor tree is sha256-pinned in lego's `go.sum`
  (Go's content-addressable module model). The vendor archive is
  deterministic against `upstream lego-v<version>.tar.gz + go.sum`.
- License: MIT (lego itself; vendored deps carry their own licenses,
  recorded in the resulting `vendor/modules.txt`)

## Refresh procedure (manual, on demand)

Refresh happens when:
- lego ships a new upstream version, OR
- a Go dep needs a security update (Go module advisory)

```sh
# 1. Bump lego version in package.yml. Update the upstream sha256 by
#    fetching the new tarball and recording its sha256.
NEW_VER=5.0.3   # example
curl -fSLO https://github.com/go-acme/lego/archive/refs/tags/v${NEW_VER}.tar.gz
sha256sum lego-${NEW_VER}.tar.gz   # → paste into package.yml's first source sha256
mv lego-${NEW_VER}.tar.gz /mnt/intergenos/build/sources/

# 2. Regenerate the vendor archive.
WORK=$(mktemp -d) && cd "$WORK"
tar -xzf /mnt/intergenos/build/sources/lego-${NEW_VER}.tar.gz
cd lego-${NEW_VER}
go mod vendor       # requires go installed + network to module proxies

# 3. Tar the source + vendor/ tree with deterministic flags.
cd ..
SDE=$(git -C /mnt/intergenos log -1 --format=%ct)
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime="@${SDE}" \
    -cf - lego-${NEW_VER} \
    | XZ_OPT='-9 -T1 --no-warn' xz -c \
    > /mnt/intergenos/build/sources/lego-${NEW_VER}-vendor.tar.xz

# 4. Remove the stale vendor archive for the previous version.
rm -f /mnt/intergenos/build/sources/lego-<OLD_VER>-vendor.tar.xz

# 5. Update package.yml's vendor archive sha256.
sha256sum /mnt/intergenos/build/sources/lego-${NEW_VER}-vendor.tar.xz

# 6. git diff + commit. The blob change in build/sources/ + the .yml
#    diff land together so the refresh is one auditable commit.
```

## User-facing refresh (installed system)

End users get refreshed `lego` (and its vendored deps, since they're
baked into the static binary at build time) through the normal pkm
package flow — `pkm update lego` after the operator publishes a new
build. Same model as every other package.

## Why not auto-regenerate every build?

Per-build vendor regen was considered and rejected. Reasons:

1. **Reproducibility from clean clone**: a committed vendor archive means
   anyone with the repo can produce byte-identical lego binaries forever.
   Per-build regen would tie build reproducibility to upstream module
   proxy availability + Go version on the build host.
2. **No special-casing**: every other package in the tree gets refreshed
   on manual, on demand version bumps. Treating lego differently would
   create an inconsistency to remember.
3. **`go mod vendor` is non-trivial**: it's slow (~30s warm, much longer
   cold), needs network, and produces ~10K files. Running it every build
   wastes time without delivering a real benefit.

The vendor archive being a committed artifact is the same posture every
other package's source tarball gets — just one whose origin is local
generation rather than an upstream URL.
