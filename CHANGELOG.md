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

- **The embedding server takes the input it is sent.** Its physical batch is
  sized to its context, so an input between 512 tokens and the context no
  longer fails with an HTTP 500; a longer input is shortened on token
  boundaries using the server's own tokenizer, with a log line naming the
  count. The daemon asks the server only once it has answered its health
  check, and one request carries at most eight texts, so no request can
  outlive its own timeout. The sustained-corruption alarm reports the count
  that fired it, with its threshold and window, instead of the count after
  the window was cleared. Proven against the real engine binary and model on
  scratch ports; not yet run as the installed daemon.
- **The ledger-anchor gate runs from a git worktree.** The pre-push anchor
  step tested for a `.git` directory and refused a worktree, whose `.git` is
  a file; it now asks git. The public-language gate exempts the private
  repository's directory name only inside a double-quoted shell string in a
  script directly under `scripts/`; the same string still blocks in prose,
  comments, commit messages and ref names (controls in the suite).
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
  web-turn lifecycle, conversation resets, semantic selection, GPU offload,
  netfilter behavior, trace integrity, install-manifest completeness, secret
  redaction, wiki startup indexing and desktop keybindings. A signing publish
  requires a SHA-256 manifest of the build-chroot archives and refuses any
  difference between that supplied manifest and staging in either direction;
  the correspondence gate does not independently attest where the manifest was
  produced. Boot-order and write-policy fixtures no longer inherit the machine
  running the tests, and the public-content gates cover private and routable
  IPv4 addresses plus additional identifier spellings.

### Fixed

- **An upgrade that was abandoned reports it.** Three paths in the package
  manager's upgrade loop gave up on a package and moved on without recording
  anything: a dependency the new release introduces that could not be resolved,
  one that could not be downloaded or installed, and the upgrade target's own
  archive failing to download. The closing summary named nothing and the command
  exited zero while the package stayed at its old version. Each path is now
  recorded, so the summary names the package and the exit code is non-zero.
- **The systemd recipe's test step no longer overwrites the staged system
  identity file.** It now creates a fallback `/etc/os-release` only when the
  file is absent, preserving the staged `ID` field for systemd and later
  packages. A focused recipe test covers the guard; a full Chapter 8 build has
  not rerun this change.
- **Package test declarations now match the observed suite behavior.**
  Samba's suite is declared not run because enabling it compiles test-only
  behavior into the shipped server. Node.js's recipe now invokes the offline
  default test set instead of a target that stopped while fetching documentation
  tooling. CUPS records that its programming-interface tests run before the
  scheduler plan refuses the build user. Recipe-level tests pin these corrected
  policies and invocations; the three packages' suites will be rerun in the next
  build.
- **The virtual-machine manager recipe carries a current-glib startup patch.**
  Current releases removed a compatibility alias the application still called,
  so the unpatched application failed before its window appeared. The patch
  resolves the legacy name when it is present and falls back to the current
  `GLibUnix` namespace when it is not. The pre-fix failure was measured on two
  installed systems; a post-patch package build and GUI launch remain unproven.
- **The packaged wiki render was refreshed after release-reference and
  identity examples were updated in the separate wiki source repository.**
  The package tree carries a regenerated and re-signed manifest for the updated
  87-page render. The source-page diffs are not present in this repository, so
  the earlier 15-page and four-example counts are not independently recoverable
  here.
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
- **The live and installed GRUB build paths embed the menu font in the
  GRUB memdisk.** Scratch images and an unsigned OVMF boot validated the embedded
  path; signed Secure Boot evaluation remains pending for the next point
  release.
- **InterGen's privileged-action path launches the approved runner through a
  transient user-manager unit.** The request travels in an owner-only file
  addressed by an opaque identifier, package operations build one privilege
  transition, and failure messages name only measured conditions. Structural
  gates and real negative controls validate the boundary; an attended
  privileged action has not run end to end.
- **InterGen creates per-user state with owner-only permissions and tightens
  its existing state trees once.** Logs and their rotated copies, transcripts,
  personal facts, decision records, tokens, keys and the answer cache are
  created in mode 0700 directories as mode 0600 files. The migration is scoped
  to the four InterGen-owned trees, refuses symbolic-link roots, stops at
  mounted filesystems, reports unreadable paths and does not repeatedly undo
  later sharing choices.
- **Browser/server turn handling acknowledges receipt before routing and
  returns a timeout before the browser's failsafe.** The client disarms its
  whole-turn failsafe while a consent card is open. The timed-out worker is not
  cancelled; it may continue mutating state after the timeout and can overlap a
  later turn. No real browser was driven against a genuinely starved embedding
  server in this change.
- **Semantic intent selection keeps an eligible candidate, tool and score
  together.** An ineligible higher score can no longer displace an eligible
  intent or lend its score to a different candidate. The arithmetic correction
  is proved with supplied similarity values; semantic recall was 0/19 for the
  measured real-language corpus, so the corrected selection was not reachable on that
  measured corpus.
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
- **Automatic GPU-layer requests are planned independently of hardware tier.**
  The daemon derives a requested `--n-gpu-layers` value from reported video
  memory and model metadata; the server's startup banner remains the authority
  for how many layers actually reached the GPU. With readable inputs, the
  planner requests every layer for a full fit, otherwise a bounded uniform-per-
  layer estimate, or zero when that estimate leaves room for no layer. Unreadable
  video memory or model size requests every layer so failure is loud; a known
  non-fit with unreadable layer count requests zero. The package-shipped models
  manifest is parsed without verifying its detached signature, file sizes are a
  fallback, and an unreadable projector size currently contributes zero bytes.
  Planner arithmetic and real model headers were proved; a post-change load on
  an affected 3–7 GB card was not.
- **The plain-named `iptables` commands use the nftables backend the kernel
  supports.** The package pointed `iptables`, `ip6tables` and their save and
  restore commands at the legacy backend, which the shipped kernel does not
  provide, so the mesh client's packet-filter chains could not be created.
- **Recognized secret formats are redacted from the turn record and decision
  trace.** Private-key blocks, credential-bearing URLs, `crypt(3)` hashes, JSON
  web tokens and supported vendor-prefixed tokens are replaced in place, and
  both writers share one definition. Unstructured secrets without a recognized
  format or credential-shaped field name remain outside this detector.
- **The `/etc/cron.*` directories say what reads them.** A README beside the
  four directories states that nothing runs their scripts until `fcron.service`
  is enabled, gives the command, and states each directory's schedule. The
  installer's post-install checks report a certificate directory they could not
  read as unreadable rather than as absent.
- **Release tooling requires a sealed, green installed-system gate record.**
  The runner records the installed-system tier and the checker requires a named
  InterGenOS machine, an installed package path, matching installed InterGen release and
  the caller-declared content hash, no failed gates and only declared skips. The image builder,
  mirror publisher and promotion path refuse a missing, mismatched, failed or
  edited record. The record's SHA256SUMS must also carry a detached signature
  by the release key, which the checker verifies with gpgv against the pinned
  fingerprint before it reads anything else; physical-hardware provenance
  remains a release-process requirement rather than a fact the checker
  authenticates.
- **ROCm packages declare the providers of their linked libraries.**
  `libamdhip64.so` and `libhiprtc.so` from `rocm-hip`, plus
  `libhsa-runtime64.so` from `rocr-runtime`, need
  `librocprofiler-register.so.0`; `librocsparse.so` needs `libroctx64.so.4`,
  supplied by `roctracer`. Those providers were absent on the measured install
  while their packages were available on the mirror. An installed-system gate
  asks the loader directly. A companion authoring checker can audit a named
  root, but it is not wired into the build; without a package database it skips
  declaration checking, and a manual/base under-declaration is reported rather
  than failed.
- **A second package-manager operation waits for the first instead of failing.**
  When two `pkm` commands that change the system overlap, the later one now
  waits at a terminal for the earlier one to finish (announcing the holding
  process every few seconds), refuses immediately when run from a script, and
  honours `--wait`, `--no-wait` and `--wait-timeout` on every changing command.
  The lock path can be redirected so a test never takes the machine-wide lock.
- **A forget stops active recall and clears live relevance caches.** The
  subject is matched in the forms the store can have written, matching active
  rows are soft-deleted, the reply states the count and the turn record records
  `physical=false`. The database bytes remain pending the separate physical-
  erasure contract. Every live conversation in the shipped one-store daemon
  drops the matching cached vector; an atypical multi-store process may also
  evict identical text from another store, causing a later re-embed rather than
  a wrong recall.
- **Three installed-system gates decide from the shape of the installed code,
  not from matching text.** The privilege-boundary gate reads how the setuid
  helper is launched, the start-up embedding gate reads what the embedding
  call spans, and the restart-persistence gate counts index writes only inside
  the functions that build the index; a shape the reader cannot classify fails
  loudly instead of passing.
- **The affected recipes now carry several installed-system corrections.** The
  fcron package stages the ownership and PAM configuration `fcrontab` needs for
  an ordinary user; the kernel recipes give images, maps and configuration
  files the same release-stamped name; kernel install messages point at the
  paths and phases that exist; and base system files assign the PC-speaker
  alias to one driver.

### Changed

- **Wiki startup embedding now uses bounded batches.** Completed rows remain
  in memory during the startup pass, but partial rows are not persisted. A
  resume method exists without a production caller; see Known limits above for
  the resulting incomplete-index behavior.
- **A documentation accuracy pass updates selected desktop, database,
  package-management, ISO and operations claims.** Application labels and
  shortcut tables are checked against recipe or configuration data, and the
  package and database pages distinguish installed state from repository
  availability. Firmware-path wording is derived from staged UEFI payloads; no
  legacy-BIOS boot was performed in this pass.

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
