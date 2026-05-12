# Changelog

All notable changes to InterGenOS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and InterGenOS adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once v1.0 ships. Pre-1.0 development tracks as `[Unreleased]` against the `master`
branch.

For the project's full design rationale see [docs/VISION.md](docs/VISION.md). For
security advisories see [SECURITY.md](SECURITY.md).

---

## [Unreleased]

In flight on `master` ahead of the v1.0 launch. Items here ship together as
v1.0.0 unless explicitly deferred to a later release.

### Added

- Public binary mirror infrastructure at `repo.intergenos.org` (DNS, TLS via
  Let's Encrypt R12, SSH push path, docroot layout). Awaiting first signed
  publish from Build #9 output.
- Operational runbook for the first public-mirror publish, including pre-flight
  signing-key drift check, atomic-promote rollback, and validation against a
  fresh InterGenOS install.
- Safe rehearsal tool (`scripts/dry-run-first-publish.sh`) exercising the full
  sign + emit + index chain against synthetic fixtures with a throwaway test
  key — never touches the release key or the live mirror.

### Changed

- Repository trust documentation updated for v1.0 launch: live mirror URL,
  canonical signing-key fingerprint cross-checked against the published
  `signing-key.md`, concrete signature-verification-failure guidance, and a
  cargo-vendor supply-chain reproducibility note.
- Getting-started documentation updated for v1.0 launch: live mirror URL,
  signing-key fingerprint, concrete `pkm sync` first-run behavior, and a
  pointer to the trust documentation for users who want the verification
  story in depth.

### Security

- Active 2026-05-11/12 PyPI supply-chain attack window navigated by bypassing
  PyPI entirely for the maturin + python-cryptography build path; both
  packages now build from GitHub-source tarballs via a reproducible
  cargo-vendor pipeline.
- Vendored Rust crate archives standardized on POSIX `pax` format to remove
  the ustar 100-character path-length restriction class of failures.

### To-do before v1.0 tag (scaffold — filled when each lands)

- Forge Secure Boot bare-metal validation on first hardware target
- Microsoft `shim-review` submission (deadline 2026-06-27 for CA rotation)
- `pkm` packaged as a system tool installable on a fresh target
- VPS source mirror completion (Components 2 and 3)
- Live ISO infrastructure: custom initramfs, squashfs builder, GRUB menu
- Forge GUI frontend (GTK4 + libadwaita)
- InterGen Tier 1 integration: `intergen-console` + `intergen-daemon`
- InterGen Sentinel security scanning: Local-Rules + Local-Qwen defaults

---

## [1.0.0] — TBD

To be filled when v1.0 ships. Will summarize the complete from-source build
chain, the binary mirror first-publish state, the signed Secure Boot chain,
the local AI assistant, and the installer flow that the v1.0 image ships
with.

---

## Earlier history

Pre-2026 builds (`build_001`, `build_002`, `build_003`, 2015-2016) are archived
on GitHub under the `InterGenOS` organization. They are not part of this
changelog; the 2026 revival is a from-scratch rewrite that shares no code
with the original builds.

[Unreleased]: https://github.com/InterGenJLU/intergenos/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/InterGenJLU/intergenos/releases/tag/v1.0.0
