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

### Known limits

- If the full wiki embedding index does not finish during the bounded startup
  pass, no current runtime path resumes it and wiki-grounded answering remains
  on keyword matching for that daemon run.
- Web-search requests remain phrasing-sensitive. Use `search the web for …` as
  the current workaround.

### Added

- **MariaDB gains NUMA memory placement, and PostgreSQL gains PL/Tcl and
  io_uring asynchronous I/O.** The MariaDB NUMA and PostgreSQL PL/Tcl flags
  incorrectly described their dependencies as absent. PostgreSQL's io_uring
  support was deliberately left for a follow-up after `liburing` landed. All
  three dependencies — `numactl`, Tcl and `liburing` — are now declared, and
  the recipes hard-enable the features. This makes
  `innodb-numa-interleave`, PL/Tcl stored procedures and PostgreSQL's io_uring
  asynchronous I/O part of the next package builds. A missing library now
  stops the build instead of quietly producing a server without the feature.
- **`gst-plugin-gtk4` — the GTK4 video sink element (`gtk4paintablesink`).**
  The camera application's live preview requires this GStreamer element and
  the application aborted at launch without it. The element lives in the
  Rust `gst-plugins-rs` project rather than the C GStreamer plugin sets, so
  it is packaged from there (version lockstep with the GStreamer stack),
  and the camera application now declares the dependency so the pairing
  cannot ship apart again.
- **An opt-in tier of red-first installed-system fixtures is now part of the
  tree.** It defines checks for privilege dispatch, per-user permissions,
  web-turn lifecycle, conversation resets,
  semantic selection, GPU offload, netfilter behavior, trace integrity,
  install-manifest completeness, secret redaction, wiki startup indexing and
  desktop keybindings. Publication refuses a staged package set whose bytes do
  not match the evaluated build corpus in both directions. Boot-order and
  write-policy test fixtures no longer inherit the machine running the tests,
  and the public-content gates cover private and routable IPv4 addresses plus
  additional identifier spellings.

### Fixed

- **The system identity file survives the Chapter 8 build.** While building
  systemd, the test step overwrote `/etc/os-release` with a single line,
  discarding the `ID` field that the build stages beforehand and that
  systemd's own configuration step reads. Every package built after systemd in
  that stage saw the reduced file, and the step that stages it only writes when
  the file is absent, so nothing put the field back until much later in the
  build. The test step now writes the file only when it is genuinely missing.
- **Package test declarations describe what their test suites actually do.**
  Three recipes carried a written policy stating that a suite ran and that its
  failures were understood, when the suite had not run at all or had stopped
  for a different reason than the one recorded. Samba's suite cannot run
  without a build option that compiles test-only behaviour into the shipped
  file server, so it is now declared as not run, with that reason stated.
  Node.js's suite stopped while trying to fetch documentation tooling from the
  network before reaching a single test; it now runs the test set the Node.js
  project itself designates for a run with no network access. CUPS's suite runs
  its programming-interface tests and then stops because the printing-scheduler
  test plan refuses to run as the build user; the record now says so instead of
  naming an unrelated cause.
- **The virtual-machine manager's startup path supports current glib.** Current
  releases removed a compatibility alias the application still called, so it
  failed before its window appeared. The startup call now resolves the legacy
  name when it is present and falls back to the current `GLibUnix` namespace
  when it is not.
- **The wiki's release references and identity examples match what images
  report.** Fifteen pages no longer use release-relative “current release”
  wording that can go stale; they point readers to the current download and
  describe how to read an installed system's own identity. Four examples that
  still showed the original R001 image's `1.0-dev (Revival)` string now show
  the current shape, name the two fields that stay stable across releases, and
  explain that a system installed from the original image keeps its old string
  until the updated file is adopted as a configuration update. The wiki's
  signed page manifest was regenerated and re-signed after each content pass.
- **Upgrade rollback copies are actually kept, and the upgrade output
  tells the truth about rollback.** When an outgoing archive is present in the
  package cache, the package manager keeps a rollback copy before upgrading so
  a failed install can restore the previous version — but the copy was looked
  up under a filename shape the cache never contains, so it was never found and
  every upgrade printed a per-package "rollback unavailable" warning whose
  suggested remedy could not help. The lookup now matches the cache's real
  naming. The per-package warning is replaced by one line before the
  transaction stating the protection that actually applies: a captured backup
  restore point, a kept rollback copy, or — normal for the first upgrade after
  installation — neither.
- **GRUB images carry their menu font inside the signed image.** Both the live
  and installed boot paths embed the font in GRUB's memdisk instead of asking
  the Secure Boot verifier to approve an external font read from the EFI
  system partition.
- **InterGen's privileged-action path no longer inherits the daemon's
  `NoNewPrivileges` setting.** The daemon now hands an approved action to a
  short-lived unit launched through the user service manager instead of
  starting `pkexec` as its own child. The request travels in an owner-only file
  addressed by an opaque identifier, package operations build one privilege
  transition, and failure messages name only conditions the path measured.
- **InterGen creates per-user state with owner-only permissions and tightens
  its existing state trees once.** Logs and their rotated copies, transcripts,
  personal facts, decision records, tokens, keys and the answer cache are
  created in mode 0700 directories as mode 0600 files. The migration is scoped
  to the four InterGen-owned trees, refuses symbolic-link roots, stops at
  mounted filesystems, reports unreadable paths and does not repeatedly undo
  later sharing choices.
- **Browser/server turn handling now sends an acknowledgement before routing
  starts.** The client code disarms its whole-turn failsafe while a consent card
  is open, and the server code returns a truthful timeout when routing exceeds
  its deadline instead of closing the connection in silence.
- **Semantic intent selection reports the candidate it actually selects.**
  An ineligible higher score can no longer displace an eligible intent or lend
  its score to a different candidate; the selected name, tool and score now
  describe the same threshold-clearing result.
- **The boot-order guard finds `efibootmgr` where the package installs it.** It
  can measure a demoted InterGenOS entry instead of reporting that boot order
  is indeterminate while the executable is present.
- **Installer integrity checks include promised archives that are absent from
  the medium.** Missing signed-manifest entries are presented as one explicit
  decision before disk writes and are carried into the audit record and final
  warning. On encrypted installs, the boot menu withholds fallback entries that
  have no usable unlock initramfs, names the unified-kernel default instead of
  relying on its row number, and adds the encrypted-root identifier when it can
  resolve one.
- **The installer seeds an extended monitor layout for a new user when it can
  read the live display state.** It enables each connected output that reports
  a current mode instead of copying the greeter's single-display layout and
  marking every secondary output disabled.
- **Each InterGen conversation owns its history, consent, pending offers and
  turn state.** Browser tabs, the console and the desktop bus no longer share
  one mutable conversation; starting or switching a session ends only the
  conversation being left, and a shared daemon refuses a turn that names none.
- **InterGen's decision trace is joinable across threads and process restarts.**
  Every turn has exactly one terminal outcome, off-thread work keeps its turn
  identity, sequence numbers continue across restarts with a per-run marker,
  and the derived retention ceiling cannot be defeated by one oversized row.
- **Desktop defaults match the files and actions the image ships.** The
  configured family is `Inter Variable`; `Ctrl+Alt+T` is bound to the shipped
  terminal; and `Super+D` is bound to show the desktop. Build-time and
  installed-system checks hold the settings to the packaged executable and
  installed configuration.
- **The first-run software offers and package output report one coherent
  transaction.** Offers already confirmed installed are withdrawn, a
  multi-item selection runs as one package transaction, outcomes distinguish
  installed, missing and unknown states, and reboot or restart guidance stays
  visible. Model download sizes come from the shipped models manifest, package
  install phases report progress, and `pkm info` reads the repository index for
  an available package that is not installed. The provider page reveals its
  Apply button after a changed selection, and the offer layout follows the
  user's text size and stays inside the page width.
- **Discrete graphics cards are used when the model fits their memory.** Offload
  is decided by whether the resolved model, with its vision projector, fits the
  detected video memory — every layer when it fits, as many layers as fit
  otherwise, and zero only when not one layer fits or a needed value could not
  be read. The hardware tier still chooses the model but no longer decides the
  offload, which had left 3–7 GB cards serving on the processor.
- **The plain-named `iptables` commands use the nftables backend the kernel
  supports.** The package pointed `iptables`, `ip6tables` and their save and
  restore commands at the legacy backend, which the shipped kernel does not
  provide, so the mesh client's packet-filter chains could not be created.
- **Secret-shaped content is redacted from the turn record and the decision
  trace.** Private-key blocks, URLs carrying a password, `crypt(3)` hashes, JSON
  web tokens and vendor-prefixed API tokens found inside prompts, commands, tool
  results or file contents are replaced in place with a placeholder naming the
  shape; both writers share one definition.
- **The `/etc/cron.*` directories say what reads them.** A README beside the
  four directories states that nothing runs their scripts until `fcron.service`
  is enabled, gives the command, and states each directory's schedule. The
  installer's post-install checks report a certificate directory they could not
  read as unreadable rather than as absent.
- **A release is refused without a sealed, green installed-system gate run on
  real hardware.** A runner records the installed-system gate tier's results
  as a sealed record; the image builder, the mirror publisher and the promotion
  path refuse a release whose record is missing, belongs to another release,
  carries failed gates, or was edited after sealing. The gate tier itself now
  imports the installed package rather than the source checkout, so its
  results describe the installed system.
- **The ROCm serving engine's linked libraries are declared by the packages
  that link them.** `rocm-hip`, `rocr-runtime` and `rocsparse` link
  `librocprofiler-register` and `libroctx64` but did not declare their
  providers as runtime dependencies, so an installed ROCm engine failed at the
  dynamic linker while both providers sat on the mirror. A build-time check now
  fails a package whose linked library is not in its declared runtime closure,
  and an installed-system check asks the loader directly.
- **The affected recipes now carry several installed-system corrections.** The
  fcron package stages the ownership and PAM configuration `fcrontab` needs for
  an ordinary user; the kernel recipes give images, maps and configuration
  files the same release-stamped name; kernel install messages point at the
  paths and phases that exist; and base system files assign the PC-speaker
  alias to one driver.

### Changed

- **Wiki embedding runs in bounded batches during startup.** Completed rows
  remain in memory during the startup pass, but partial rows are not persisted
  and no current runtime path resumes an incomplete index. If the full index
  does not finish in that pass, keyword matching remains in use for the daemon
  run.
- **A documentation accuracy pass aligns the desktop, database,
  package-management, ISO and operations pages with the packaged image and the
  code paths that implement them.** Application labels are checked against
  their recipe data, shortcut tables reflect the configured settings, and the
  package and database pages distinguish installed state from repository
  availability.

---

## [R001.1] — 2026-08-20

The first point release. Point releases deliver accumulated fixes and minor
package additions built against the proven substrate of the current major
release ([docs/release-policy.md](docs/release-policy.md)). R001.1 was produced
as a targeted rebuild against R001's substrate — every changed package
recompiled with the full validation gate set enforced — then installed and
evaluated on real hardware before publication. The image, its checksum, the
signature over that checksum, and the release key are published together on the
project mirror; verification instructions are unchanged from R001.

### Known limits in R001.1

- Privileged actions through InterGen do not work in R001.1. The message
  `runner not found / package may be misinstalled` is incorrect; do not
  reinstall packages in response.
- In R001.1, wiki-grounded answering uses keyword matching.
- On machines with more than one local account, InterGen's activity log
  (`~/.local/state/intergen/intergen.log`, which records web-search queries)
  is readable by other local accounts on a standard install. The personal-facts
  database and transcripts are also created with loose permissions, but on a
  standard install they sit behind a private `~/.local/share` directory and are
  not reachable by other accounts. Run
  `chmod 700 ~/.local/state/intergen ~/.local/share/intergen` now to close both.
- Some discrete GPUs with 3–7 GB of VRAM are not yet used for inference; replies
  are slower than intended on that hardware.
- Web-search requests are phrasing-sensitive. Use `search the web for …` as
  the workaround in R001.1.

### Added

- **Nineteen package recipes — the first post-release package additions.**
  Five ship on the installation image: automatic log rotation on every install
  (`logrotate`, ending unbounded log growth), USB device tooling (`usbutils`),
  NVMe drive management (`nvme-cli`), ethernet diagnostics (`ethtool`), and
  hybrid-graphics switching (`switcheroo-control`). Fourteen are published to
  the signed mirror and install on demand: the VPN client set — OpenVPN,
  OpenConnect and WireGuard tooling with their NetworkManager plugins — the
  container-tooling completion (`buildah`, `skopeo`, `docker-buildx`), and a
  network-diagnostics suite (`nmap`, `tcpdump`, `iperf3`, `mtr`, `socat`, with
  a meta-package that installs the set in one command).
- **Package pre-remove hooks.** The package manager runs a package's declared
  pre-remove hook before removal, and lifecycle hooks now report what they
  actually did rather than only that they ran.
- **Discovered-name resolution.** When the first-boot welcomer's Network
  Discovery option is enabled, discovered `.local` hostnames also become
  resolvable, so a discovered machine can be reached by name, not only seen.
- **New fail-closed build-integrity gates**: an aspirational-reference check
  spanning services, autostart entries, documentation and package lifecycle
  hooks — a referenced path that nothing in the tree produces refuses the
  build; a build-root-versus-archive coverage gate — every built file must be
  carried by a sealed package archive or a reviewed allowlist entry; a
  source-tree coverage gate — every source root the build stages must be
  declared by the recipes that read it; an autostart-condition gate — a shipped
  autostart entry whose condition nothing honours refuses; and an
  image-preparation outcome assertion — no pruned package's payload may
  survive into the image.

### Fixed

- **A Secure Boot console message at every boot-menu display on installed
  systems.** The boot menu generated on an installed system loads GRUB's
  `bli` module, but the installed system's GRUB image did not embed it, and
  under Secure Boot the built-in verifier refuses to load a module from the
  EFI system partition — printing a policy refusal on the boot console each
  time the menu was drawn. The module is now embedded in that image. A test
  pins each GRUB image to the set of modules its own configuration loads, so a
  fix applied to one image can no longer miss the other.
- **Service enablement has a single owner.** The systemd preset policy now
  states the default for every shipped service, including six that previously
  had none; seven package recipes and the disk-image script stopped enabling
  or disabling units themselves; presets apply on a package's first install
  only, so an administrator's later enablement choices survive package
  upgrades; and the written default for the realtime scheduling daemon
  (`rtkit-daemon`) now matches what the machine actually does.
- **The first-boot welcomer no longer relaunches after completion.** Its
  autostart entry is skipped once the user has finished with it.
- **Package removal consults the full install record.** Removal consumes the
  union of the package database and the on-disk package manifest, so a path
  whose database row was lost — a symlinked install root, in the case that
  surfaced — is still removed cleanly, and image preparation asserts the
  outcome.
- **Rebuilt packages re-bundle their license texts.** A package rebuilt in
  place no longer inherits the prior build's on-disk license bundle; the gate
  that requires every shipped package to carry its licenses verifies the
  result.
- **Coredump symbolization is built in.** systemd now declares its elfutils
  dependency explicitly and pins the feature on, so crash reports resolve
  symbols out of the box.
- **The scheduler helper `fcronsighup` regains its intended group and setuid
  mode** on installed systems.
- **AppArmor profile loading has one critical owner**, and the HIP compute
  probes (`hipcc`, `hipconfig`) resolve by bare name.
- **The Python `cryptography` package stages only its library** into the
  Python module directory, no longer carrying extra build artifacts.

### Changed

- **The release identity is authored once** — in the base system files — and
  read everywhere else; the installer no longer writes a second copy.
- **Test-suite policies are declared instead of masked** for cups, samba,
  SpiderMonkey, Node.js and MIT Kerberos: their suites run and report status,
  with expected failures dispositioned by a written per-package policy rather
  than hidden.
- **A documentation accuracy pass**: drifting counts rounded or corrected,
  published claims the code contradicted fixed, signing-key location lists
  updated to the published state, and the third-party notices regenerated.
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
  prompts from a single capture, so the key holder types the PIN once while
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

[Unreleased]: https://github.com/InterGenJLU/intergenos/compare/R001.1...HEAD
[R001.1]: https://github.com/InterGenJLU/intergenos/compare/R001...R001.1
[R001]: https://github.com/InterGenJLU/intergenos/releases/tag/R001
