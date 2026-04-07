# Offline Rust/Cargo Builds in Chroot

**Date:** 2026-04-03
**Context:** InterGenOS desktop tier has 4 packages requiring network access during build. The chroot has no network by design.

## Packages Needing Network Access

| Package | Version (current -> BLFS 13.0) | Why | Solution |
|---------|-------------------------------|-----|----------|
| rust | 1.93.1 | Downloads bootstrap compiler during `x.py build` | Pre-download bootstrap tarballs |
| cbindgen | 0.27.0 -> **0.29.2** | `cargo build` fetches crates from crates.io | `cargo vendor` on host |
| rust-bindgen | 0.70.1 -> **0.72.1** | `cargo build` fetches crates from crates.io | `cargo vendor` on host |
| librsvg | 2.59.2 | meson invokes cargo internally | Verify GNOME tarball has vendored crates, or `cargo vendor` |

## Rust Bootstrap (1.92.0 for building 1.93.1)

### Required Bootstrap Tarballs

From `src/stage0` in `rustc-1.93.1-src.tar.xz`:
- **compiler_version:** 1.92.0
- **compiler_date:** 2025-12-11
- **Base URL:** `https://static.rust-lang.org/dist/2025-12-11/`

Files needed (x86_64-unknown-linux-gnu):

| File | SHA256 |
|------|--------|
| `rustc-1.92.0-x86_64-unknown-linux-gnu.tar.xz` | `78b2dd9c6b1fcd2621fa81c611cf5e2d6950690775038b585c64f364422886e0` |
| `cargo-1.92.0-x86_64-unknown-linux-gnu.tar.xz` | `e5e12be2c7126a7036c8adf573078a28b92611f5767cc9bd0a6f7c83081df103` |
| `rust-std-1.92.0-x86_64-unknown-linux-gnu.tar.xz` | `5f106805ed86ebf8df287039e53a45cf974391ef4d088c2760776b05b8e48b5d` |

### Offline Strategy

`x.py` checks for bootstrap binaries in `build/cache/YYYY-MM-DD/` before downloading. Steps:

1. Pre-download the three tarballs on the host
2. In build.sh configure(), create `build/cache/2025-12-11/` and copy tarballs there
3. `x.py build` finds them locally and skips the download

### bootstrap.toml Notes

The `locked-deps = true` setting only prevents checking for NEW dependency versions. It does NOT prevent the bootstrap download. The `[build] rustc = "/path/to/rustc"` option is an alternative but requires a pre-installed rustc.

## Cargo Vendor Approach (cbindgen, rust-bindgen, librsvg)

### How `cargo vendor` Works

1. Reads `Cargo.toml` and `Cargo.lock`
2. Downloads all dependency crates from crates.io into `vendor/` directory
3. Prints `.cargo/config.toml` snippet to stdout

The config.toml needed:
```toml
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
```

### Vendoring Workflow (run on host with network)

```bash
# Extract source
tar xf cbindgen-0.29.2.tar.gz
cd cbindgen-0.29.2

# Verify Cargo.lock exists (some crates don't ship it)
ls Cargo.lock

# Vendor dependencies
cargo vendor

# Create .cargo/config.toml
mkdir -p .cargo
cat > .cargo/config.toml << 'EOF'
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
EOF

# Create vendor-only tarball (keeps original source clean)
cd ..
tar cJf cbindgen-0.29.2-vendor.tar.xz cbindgen-0.29.2/vendor/ cbindgen-0.29.2/.cargo/
```

### Build.sh Changes

In the `configure()` function, extract vendor tarball and create config:
```bash
configure() {
    # Extract vendored dependencies (built offline on host)
    tar xf "${IGOS_SOURCES}/cbindgen-0.29.2-vendor.tar.xz" --strip-components=1

    # Cargo will find .cargo/config.toml and use vendored sources
}
```

### librsvg Special Case

GNOME release tarballs from `download.gnome.org` typically include vendored crates.
Git archive tarballs do NOT. Our current 2.59.2 tarball is a git archive with NO vendor directory.

**Options:**
1. Switch download URL to GNOME release tarball (preferred — comes pre-vendored)
2. `cargo vendor` on host (same as cbindgen/rust-bindgen)

## Packages NOT Needing Vendoring

- **spidermonkey** — Firefox ESR tarball ships its own vendored crates internally
- **All other desktop packages** — no cargo usage found in any of the 312 build.sh files

## Version Updates Needed

Before creating vendored tarballs, update to BLFS 13.0 versions:
- cbindgen: 0.27.0 -> 0.29.2
- rust-bindgen: 0.70.1 -> 0.72.1
- librsvg: verify version matches BLFS 13.0

## BLFS Reference

BLFS 13.0 explicitly states "An Internet connection is needed" for:
- rust (bootstrap download)
- cbindgen (cargo build)
- rust-bindgen (cargo build)  
- cargo-c (cargo build — not in our desktop tier)
- glycin (cargo vendor then meson — not in our desktop tier)
- librsvg (meson invokes cargo)

BLFS does NOT provide offline workarounds. The `cargo vendor` approach is standard practice in distro packaging (used by Fedora, openSUSE, Arch, etc.).
