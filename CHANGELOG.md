# Changelog

All notable changes to InterGenOS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Releases are named by the InterGenOS release line: major releases — `R001`,
`R002`, … — are produced by a complete from-source bootstrap, and point
releases — `R001.1`, `R001.2`, … — deliver accumulated fixes and minor package
additions built against the proven substrate of the current major release. What
triggers each kind, and the single-supported-line model, are in
[docs/release-policy.md](docs/release-policy.md). Work that has landed on the
development branch but is not yet in a published release is listed under
`[Unreleased]`.

For the project's full design rationale see [docs/VISION.md](docs/VISION.md). For
security advisories see [SECURITY.md](SECURITY.md). Planned work that has not
landed is in the repository README, not here.

---

## [Unreleased]

Landed since R001, for the next point release.

### Added

- **Nineteen package recipes** — log rotation, VPN clients, container tooling
  and network diagnostics — published to the binary mirror.

### Fixed

- **A Secure Boot console message at every boot-menu display on installed
  systems.** The boot menu generated on an installed system loads GRUB's
  `bli` module, but the installed system's GRUB image did not embed it, and
  under Secure Boot the built-in verifier refuses to load a module from the
  EFI system partition — printing a policy refusal on the boot console each
  time the menu was drawn. The module is now embedded in that image. A test
  pins each GRUB image to the set of modules its own configuration loads, so a
  fix applied to one image can no longer miss the other.

### Changed

- Release policy published as [docs/release-policy.md](docs/release-policy.md):
  the two release types, the five conditions that force a complete from-source
  rebuild, and the support model.

---

## [R001] — 2026-08-16

The first public release, and the first major release of the InterGenOS release
line. It was produced by a complete from-source bootstrap — every package
rebuilt from an empty build root with the full validation gate set enforced —
then installed and evaluated on real hardware before publication.

R001 has no predecessor, so there is nothing to describe changes against. This
entry records what the release contains. The image, its checksum, the signature
over that checksum, the release key, the software bill of materials, the
provenance index and the full release notes are published alongside the image on
the project mirror; verification instructions are in
[README.md](README.md), [docs/getting-started.md](docs/getting-started.md) and
[SECURITY.md](SECURITY.md).

### What the release is

- x86_64, UEFI only, distributed as a live image with a graphical installer.
  Roughly 9.7 GiB.
- Over 1,100 package definitions in the source tree, of which over 800 ship on
  the installation image; the remainder are mirror-only and install on demand.
  Over 1,100 packages are published in the signed mirror index. Exact figures,
  and why the counts do not subtract into each other, are in the release notes
  published with the image.
- The desktop is GNOME 49 on Wayland with the InterGenOS shell theme.
- The boot chain is signed end to end — shim, GRUB and a unified kernel image —
  with a dm-verity root hash sealed into the signed kernel image on the live
  medium. Secure Boot works through a one-time machine-owner-key enrolment at
  first boot; the image also boots with Secure Boot disabled.
- `pkm` installs from the signed mirror index and verifies each package against
  it, and can fetch a small set of proprietary applications directly from their
  vendors on request.
- InterGen, the local assistant, runs entirely offline and selects its model
  tier from discrete-GPU presence and video memory. Every tier is multimodal,
  and a tier whose vision projector is not pinned in the signed model manifest
  is refused rather than served without vision.
- InterGen Sentinel, the assistant's pluggable security-scanner layer, ships
  with local-only defaults; routing a scan to any external provider is opt-in
  and names the provider.

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
- **Multimodal assistant on every tier.** Each pinned model ships a paired
  vision projector whose hash is pinned in the same signed manifest. A model
  declaring vision whose projector is unpinned is refused rather than served
  without it.
- **Hardware-tier selection** built around discrete-GPU presence and VRAM only.
  System memory is not an input, unreadable VRAM fails *down* to the entry tier
  rather than up, and a machine whose store holds only a smaller model serves
  that model loudly instead of dead-ending.
- **Chronicle**, the backup utility: a content-addressed engine with hardlink
  rotation, restore points, retention and enumeration, a command-line client, a
  GTK4/libadwaita interface, and packaging with its units and assets.
- **Gaming and Windows-application groundwork**: the multilib m64/m32 toolchain,
  a new-WoW64 Wine build, the GE-Proton and Steam download-helpers, gamescope and
  gamemode, winetricks with its runtime closure, and a staged mingw-w64 cross
  toolchain.
- **Applications on the image**: GIMP, Inkscape and LibreOffice with their
  runtime closure, alongside diagnostic tooling (smartmontools, dmidecode,
  lm-sensors with the matching kernel configuration) and network CLI utilities.
- **First-party icon theme** as the system default, with the InterGenOS
  application identity — icons, labels, launchers and the app-overview folder.
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
- **Signing ergonomics**: the bootloader ceremony answers its per-binary PIN
  prompts from a single capture, so the operator types the PIN once while
  per-operation authentication stays intact.
- **A fail-closed public-language gate** on push, driven by a term list held
  outside the repository.
- **Dynamic ISO naming** set at launch and persisted across the ceremony-resume
  chain, replacing post-creation renames.

### Changed

- Build phase order is `desktop → extra → compute → ai`, so the AI tier's
  GPU-native builds can consume the compute SDKs and extra-tier libraries at
  build time. The candidate capture point moved with it and is defined by
  principle — the final package-building phase — rather than by tier name.
- Repository trust documentation carries the live mirror URL, the canonical
  signing-key fingerprint cross-checked against the published `signing-key.md`,
  concrete signature-verification-failure guidance, and a cargo-vendor
  supply-chain reproducibility note.
- Getting-started documentation carries the live mirror URL, the signing-key
  fingerprint, concrete `pkm sync` first-run behavior, and a pointer to the
  trust documentation for readers who want the verification story in depth.
- Model licensing is read from the signed model manifest rather than inferred.
  Every model shipped today declares Apache-2.0, which the acceptance gate
  treats as permissive; a restrictive declaration still requires an explicit,
  recorded acceptance before download.

### Security

- Removed PyPI from the maturin and python-cryptography build path entirely in
  response to the active 2026-05-11/12 PyPI supply-chain attack window. Both
  packages build from upstream GitHub source tarballs through a reproducible
  cargo-vendor pipeline.
- Vendored Rust crate archives standardized on POSIX `pax` format to remove
  the ustar 100-character path-length restriction class of failures.
- Verified boot is the sole boot-integrity path. The whole-file digest fallback
  was removed; the init script fails closed without a sealed root hash unless an
  explicit development marker is present, and an assembly gate asserts every
  kernel image seals the current root hash.
- Four upstream kernel CVE backports are declared in both kernel recipes with
  their SHA-256 hashes, so the patched kernel is the one an installed system
  boots. The advisories are listed in [SECURITY.md](SECURITY.md).
- Installer forensic traces redact positionally-passed secrets and their sinks
  open restricted, after install-time credentials were found landing in a
  world-readable trace.
- The user-selected locale is validated against a strict allowlist before it
  reaches a privileged shell in the target root.

---

## Earlier history

Pre-2026 builds (`build_001`, `build_002`, `build_003`, 2015-2016) are archived
on GitHub under the `InterGenOS` organization. They are not part of this
changelog; the 2026 revival is a from-scratch rewrite that shares no code
with the original builds.

[Unreleased]: https://github.com/InterGenJLU/intergenos/compare/R001...HEAD
[R001]: https://github.com/InterGenJLU/intergenos/releases/tag/R001
