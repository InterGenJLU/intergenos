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

In progress on `master` ahead of the v1.0 launch. Items here ship together as
v1.0.0 unless explicitly deferred to a later release.

### Added

- **GPU compute platform (`compute` tier).** A full ROCm 7.2.4 stack — math and
  solver libraries, collective communication, profiling and debug tooling, the
  HIP engine, and the SDPA attention kernels — built from source as an opt-in,
  mirror-only tier. Nothing in it ships on the installation image; installed
  systems pull it on demand. The universal GPU default remains the Vulkan-backed
  engine in the `ai` tier.
- **Training and model-tooling stack** in the `ai` tier, mirror-only: the
  PyTorch family with its ROCm/HIP build, plus the transformers, accelerate,
  triton and bitsandbytes chain and their pure-Python closure.
- **Multimodal assistant on every tier.** Each pinned model now ships a paired
  vision projector whose hash is pinned in the same signed manifest. A model
  declaring vision whose projector is unpinned is refused rather than served
  without it.
- **Hardware-tier selection redesigned** around discrete-GPU presence and VRAM
  only. System memory is no longer an input, unreadable VRAM fails *down* to the
  entry tier rather than up, and a machine whose store holds only a smaller model
  serves that model loudly instead of dead-ending.
- **Chronicle**, the backup utility: a content-addressed engine with hardlink
  rotation, restore points, retention and enumeration, a command-line client, a
  GTK4/libadwaita interface, and packaging with its units and assets.
- **Gaming and Windows-application groundwork**: the multilib m64/m32 toolchain,
  a new-WoW64 Wine build, the GE-Proton and Steam download-helpers, gamescope and
  gamemode, winetricks with its runtime closure, and a staged mingw-w64 cross
  toolchain.
- **Applications on the image**: GIMP, Inkscape and LibreOffice now ship with
  their runtime closure, alongside diagnostic tooling (smartmontools, dmidecode,
  lm-sensors with the matching kernel configuration) and network CLI utilities.
- **First-party icon theme** promoted to the system default, with the
  InterGenOS application identity — icons, labels, launchers and the
  app-overview folder — landing alongside it.
- **Build-integrity gates**, each fail-closed at the point it can still stop a
  bad artifact: archive-time ELF word-size auditing at all three payload
  chokepoints, a post-eviction NEEDED-closure sweep, staged-kernel exclusivity at
  every build entry point, ISO-closure preflight with a runtime-namespace check,
  a squashfs ownership gate, tmpfiles.d owner resolvability, and a runtime-dir
  gate at both archive chokepoints.
- **Mirror-only archive exclusion at squashfs**, so the archive corpus no longer
  ships in full on every image, plus a build-cache purge from the shipped root.
- **Incremental mirror publishing**: unchanged archives hardlink against every
  snapshot already on the volume instead of re-uploading, retention pruning runs
  inside the publish transaction, and a capacity preflight fails the publish
  closed before it starts.
- **Package-manager transparency work**: topological upgrade ordering with
  kernel-replace exclusion, an unprivileged dry-run preview, a reboot-required
  activation advisory, end-of-transaction next-steps output, and a unified
  install path for proprietary downloads.
- **Assistant safety and honesty controls**: a deterministic destructive-intent
  gate that does not depend on the model, tool failure made binding in synthesis
  so a denied dispatch cannot be reported as success, a route-to-tools guard for
  direct system-state questions, and per-turn provenance recording that checks
  each answer against the dispatches it claims.
- **Decision tracing** through the routing and synthesis path, with per-call
  latency spans and a per-turn telemetry panel.
- **Signing ergonomics**: the bootloader ceremony now answers its per-binary
  PIN prompts from a single capture, so the operator types the PIN once while
  per-operation authentication stays intact.
- **A fail-closed public-language gate** on push, driven by a term list held
  outside the repository.
- **Dynamic ISO naming** set at launch and persisted across the ceremony-resume
  chain, replacing post-creation renames.

### Changed

- Build phase order is now `desktop → extra → compute → ai`, so the AI tier's
  GPU-native builds can consume the compute SDKs and extra-tier libraries at
  build time. The candidate capture point moved with it and is defined by
  principle — the final package-building phase — rather than by tier name.
- Repository trust documentation updated for v1.0 launch: live mirror URL,
  canonical signing-key fingerprint cross-checked against the published
  `signing-key.md`, concrete signature-verification-failure guidance, and a
  cargo-vendor supply-chain reproducibility note.
- Getting-started documentation updated for v1.0 launch: live mirror URL,
  signing-key fingerprint, concrete `pkm sync` first-run behavior, and a
  pointer to the trust documentation for users who want the verification
  story in depth.
- Model licensing is read from the signed model manifest rather than inferred.
  Every model shipped today declares Apache-2.0, which the acceptance gate
  treats as permissive; a restrictive declaration still requires an explicit,
  recorded acceptance before download.

### Security

- Removed PyPI from the maturin and python-cryptography build path entirely in
  response to the active 2026-05-11/12 PyPI supply-chain attack window. Both
  packages now build from upstream GitHub source tarballs through a
  reproducible cargo-vendor pipeline.
- Vendored Rust crate archives standardized on POSIX `pax` format to remove
  the ustar 100-character path-length restriction class of failures.
- Verified boot is the sole boot-integrity path. The whole-file digest fallback
  was removed; the init script fails closed without a sealed root hash unless an
  explicit development marker is present, and an assembly gate asserts every
  kernel image seals the current root hash.
- Installer forensic traces redact positionally-passed secrets and their sinks
  open restricted, after install-time credentials were found landing in a
  world-readable trace.
- The user-selected locale is validated against a strict allowlist before it
  reaches a privileged shell in the target root.

### Completed since the previous entry

Items previously listed as remaining before the v1.0 tag, now done:

- Secure Boot validated on bare metal — the full signed chain runs end to end
  under enforced Secure Boot with per-machine key enrolment, across current and
  legacy hardware.
- `pkm` ships as a system tool on an installed target.
- Live ISO infrastructure: custom initramfs, squashfs builder, and the
  three-entry boot menu.
- The graphical installer frontend.
- Assistant integration for the entry and mid tiers, and the pluggable
  security-scanner layer with its local-only defaults.
- The public binary mirror is live and serving a signed index.

### Remaining before the v1.0 tag

- Microsoft `shim-review` submission, for an InterGenOS-owned signed shim that
  removes the first-boot key-enrolment step.
- Full v1.0 package coverage on the binary mirror, and ISO download
  infrastructure.
- Source-mirror completion: the upload path and the upstream-version poller.
- Validation of the largest assistant tier on high-end GPU hardware; its weights
  and vision projector are already pinned in the signed manifest.
- Additional desktop environments alongside GNOME.

---

## [1.0.0] — TBD

Finalized when v1.0 ships. This entry will summarize the complete from-source
build chain, the binary mirror first publish, the signed Secure Boot chain
(shim, GRUB, UKI, dm-verity), the local AI assistant (InterGen and InterGen
Sentinel), the GNOME 49 Wayland desktop, and the Forge installer flow that the
v1.0 image ships.

---

## Earlier history

Pre-2026 builds (`build_001`, `build_002`, `build_003`, 2015-2016) are archived
on GitHub under the `InterGenOS` organization. They are not part of this
changelog; the 2026 revival is a from-scratch rewrite that shares no code
with the original builds.

[Unreleased]: https://github.com/InterGenJLU/intergenos/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/InterGenJLU/intergenos/releases/tag/v1.0.0
