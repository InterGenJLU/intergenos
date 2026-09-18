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

### Known limits in R001.3

- The small (2B) tier states wrong facts with confidence and answers some direct
  questions with a template; the model's floor, not the tree's.
- A web-search request phrased without a search verb or a subject the assistant
  can extract still goes to the model. `search the web for …` always reaches
  the tool. (A clause governed by "do not", "never" or "without" is no longer
  cut into an affirmative request; see Fixed.)
- The wiki embedding index can still stay keyword-only for a daemon run: the
  after-turn catch-up pass now waits up to fifteen seconds for the embedding
  slot and says so when it gives up, so the case is rarer and no longer silent.
- The greeter shows the Qwen attribution only when the installed `intergen`
  carries `--version`; a machine upgraded package by package renders nothing
  until intergen updates.
- Stopping the assistant service by hand does not keep it stopped: the desktop
  panel re-activates it over the desktop bus.
- The older GPU power rule (`70-intergen-compute-gpu-pm.rules`) still holds
  every secondary AMD card awake permanently.
- The NVIDIA driver helper's silent minute after the EULA is only partly fixed.
- No Thunderbolt device-authorization daemon is shipped; Thunderbolt devices
  behave as the firmware's default policy dictates.
- The scenario harness sits to its timeout when the assistant's bus name is
  already owned (a test-instrument limit).
- The canonical test suite cannot complete on an installed machine.

### Added

- **The URI perl distribution is complete.** `URI/otpauth.pm`, one of the URI
  modules the project ships, loads `MIME::Base32` when it is used, and the
  project's perl did not carry that module, so that one documented URI scheme
  died at load with `Can't locate MIME/Base32.pm in @INC`. The module is now
  packaged from source (`perl-mime-base32`) and declared a runtime dependency
  of `perl-uri`, so the whole distribution loads.
- **An encrypted install offers a recovery key.** Until now an encrypted install
  ended with exactly one unlock credential. The installer now offers, on every
  encrypted install, to generate a recovery key on the machine, adds it as a
  second key slot, proves the slot opens the volume before it says so, and
  shows the key once on the completion page for the person to write down. The
  installer's record carries the fact that a key exists, never the key.
- **Every install ends with a restore point.** The backup engine used to take
  its first restore point only before a package transaction, so a machine on
  which nobody installed anything had no state to return to. The installer now
  takes one at the end of the install, and the shipped health check reports
  when a machine cannot be rolled back to itself.
- **The package manager can move an installed package forward from a local
  archive.** `pkm upgrade <name> --archive <file>` upgrades exactly one
  installed package from an archive whose own metadata names it, under the same
  trust modes, downgrade guard, dependency check, confirmation, restore point,
  rollback copy and configuration protection as a repository upgrade; the
  history row records the method. When the package manager replaces itself,
  every one of its own modules is loaded before the first file is replaced.
- **A TLS trust updater** (`update-ca-trust`, with its manual) that validates
  the configured anchors and publishes the generated certificate bundles
  atomically; `p11-kit` carries the regeneration helper.
- **A kernel hardening floor** shipped as `/usr/lib/sysctl.d/60-intergenos-hardening.conf`:
  kernel pointer restriction, full address-space randomization, strict
  reverse-path filtering, ICMP redirects neither accepted nor sent, martian
  packets logged, each applied to every interface so it wins over the systemd
  default. The kernel is built with the legacy heap layout switched off
  (`CONFIG_COMPAT_BRK` off), which the previous kernels carried on and which
  clamped address-space randomization. A shipped gate refuses a kernel
  configuration that turns it back on.
- **A kernel panic leaves a record the next boot can read.** On every installed
  system to date the kernel's panic-record backend loaded and recorded nothing:
  the EFI backend ships switched off by the kernel's own default, and once
  switched on it was still observed to write nothing from a panic on a Secure
  Boot machine. The installed system now reserves a small named memory region
  at boot through the kernel's own `reserve_mem` mechanism, and the RAM-backed
  recorder claims it by name — the kernel chooses the address, and Secure Boot
  lockdown, which refuses a recorder pointed at a fixed address, permits the
  named form. The configuration is delivered through the installer's
  command-line source (`/etc/kernel/cmdline.d/30-panic-record.conf`) and a
  module-load file, and a shipped gate refuses the address form so the silent
  regression cannot return. Proven on real hardware under Secure Boot: a
  deliberate panic left nothing before the change and a readable record
  (`/var/lib/systemd/pstore/dmesg-ramoops-0`, with the panic line and call
  trace) after it. The EFI backend option stays as the fallback.
- **Machine Owner Key precautions.** The installer stages the machine's own
  Secure Boot certificate on the boot partition beside shim
  (`EFI/InterGenOS/mok.der`, what MokManager's "Enroll key from disk" reads)
  and at `/etc/intergenos/mok.der`; every enrolment text states the ~10-second
  MokManager window and the from-disk recovery path; the firmware's signature
  database is read and the trusted Microsoft UEFI authorities are stated in
  plain language. The installer also reads the owner keys the firmware already
  trusts, names the ones earlier installs on this machine left behind, and
  offers to retire them during the install. The first-login page says when the
  key is staged but not enrolled, and when a retirement asked for during the
  install has not happened, with the recovery path in each case.
- **The installer's disk phase is on the record.** Every command the partition
  and mount steps issue runs through the install trace with its arguments,
  exit status, output and duration; the phase opens with the disk, its size and
  the chosen options and closes with the resulting layout. A passphrase or
  sealing secret fed to any command is withheld by the trace writer with only
  its byte count kept.
- **Installed file capabilities are restored and read back** after deployment
  for the programs that need them (`ping6`, the keyring daemon) instead of
  those programs shipping setuid; `libcap` ships `setcap` and `getcap` for the
  package hooks that restore them.
- `wsdd` 0.9, the WS-Discovery helper used by GNOME Files for Windows-network
  browsing. GVfs now requires it, and the ISO includes `defusedxml` for its
  XML parsing. The advertising service is installed but remains disabled.
  A compatibility patch initializes verbose diagnostics on the actual event
  loop when running with Python 3.14. Initial discovery probes retain their
  random delay without blocking the client API while interfaces initialize.
- `codex`: a download helper that installs OpenAI's Codex command-line coding
  agent from the npm registry (pinned version, registry signature, advisory
  check) and the Codex VS Code extension from a pinned, sha256-verified
  package, the same way the `claude-code` helper does. `sudo pkm install codex`.
- `chatgpt`: a download helper that installs the ChatGPT desktop app for Linux
  (ChatGPT, Work and Codex in one application) from OpenAI's signed Linux
  package repository, verified through the repository's signed metadata the
  same way the `chrome` and `vscode` helpers are. `sudo pkm install chatgpt`.
- `rsyslog` 8.2608.0 with its library family `librelp` 1.13.0, `libestr` 0.1.11
  and `libfastjson` 1.2609.0: the rsyslog log processor built with the RELP
  acknowledged-delivery transport (input and output), file input, statistics,
  systemd journal import and export, plain TCP input and JSON parsing. Ships a
  hardened service unit and a local-only default configuration whose
  compatibility default is `strict`, so an unclean configuration aborts
  start-up; the service is disabled by default and is enabled only where a
  host is deployed as a log receiver. Mirror-only (`sudo pkm install rsyslog`).
- Mobile broadband: the ModemManager daemon unit and its D-Bus activation file
  ship, and the preset enables only the activation alias, so the daemon starts
  on request from the desktop and never runs on a machine without a modem.
- `pkm history` shows the 50 newest entries by default, with `--limit N` and
  `--all` to reach an install's full record.

### Fixed

- **The engine check now asks about the card the assistant will actually use,
  not about the machine.** The HIP build of the inference engine carries device
  code only for the AMD architectures it was compiled for, and the check that
  keeps it from being chosen on anything else compared the architectures the
  MACHINE has against that list, accepting the engine when ANY of them
  overlapped. Which card serves is decided afterwards, and only one card
  serves. On a two-card machine — measured 2026-09-18 on a workstation with a
  gfx1100 card and a gfx1102 card, against a build covering gfx1102 and not
  gfx1100 — the check accepted the engine on the strength of the gfx1102 card
  and the assistant then launched it on the gfx1100 card, for which that build
  has no device code. That is the crash at model load the check exists to
  prevent, and it turns a machine that would have served correctly on the
  Vulkan engine into one that serves nothing. The check now reads the
  architecture of the card that would be pinned, taken from the same selection
  that produces the pin, and from the graphics driver's own topology records —
  no extra tool and no second copy of the hardware list. A card named by hand
  in configuration is the card asked about. Single-card machines answer exactly
  as before, and every unknown — an engine build that reports no card
  addresses, an unreadable topology, an unreadable architecture list — leaves
  the previous behaviour untouched: only a measured "this card is not covered"
  declines the engine, and the reason names the card and what the build
  declares. Choosing an engine happens in two places, and both are given the
  pinned card: the second one, the launcher's own fallback, was asking about
  whichever card the automatic selection would have taken, which wrote a
  contradicting decision into the log and would have refused a working engine to
  any caller that reached it.
- **A turn that runs no tool no longer says it is running one.** The browser
  server sent its "let me look that up" acknowledgement the instant the router
  routed a turn to the tool path. That route means tools were OFFERED to the
  model, not that the model called one, and on the shipped small-model tier most
  such turns are answered straight from the model with no tool at all. Measured
  against the running assistant on 2026-09-18: every tool-route question in the
  fourteen-question browser battery showed the line, and not one of them ran a
  tool. The plainest case is the battery's own no-action question — "Calculate
  17 times 23 mentally. Reply with the number only. Do not use tools, run
  commands, access files, or contact external services." — which was answered
  391, correctly and with no tool, underneath a line promising the person an
  action they had just forbidden. The line is now sent at the first real tool
  call instead, once per turn. It still says nothing about the outcome, so it
  still appears when the action is then refused or held for consent — a turn
  that asks for an action did ask for it. The browser panel and the terminal
  client read the same message, so both are corrected by the one change.
- **The decision about whether a model fits the graphics card now measures the
  card the model goes onto.** On a machine with more than one card the assistant
  serves from exactly one of them, chosen after the model is picked and
  overridable by hand in configuration — but whether the model fits, and how many
  of its layers are placed on the card, was worked out from the most capable card
  the hardware detection found. On a machine whose cards differ in size those are
  two different cards. Measured on a two-card workstation on 2026-09-18: with the
  model pinned to the smaller card, the recorded plan read "card 20464 MiB" and
  declared a comfortable fit while the model was placed on a card of 8176 MiB. It
  happened to fit, with 155 MiB to spare, and the fit was never checked against
  the card that received it. For the largest model the signed record describes,
  the same mistake would place 29 of 33 layers on a card with room for 9. The
  card's size now comes from the same reading that chooses the card, so its name,
  its address and its size can never describe different cards; this works for
  every engine build, because each one reports its own devices' sizes. When no
  card is pinned, or the engine reported no size, the previously detected figure
  still stands — and either way the plan now states which figure it used, in the
  log and in the recorded trace, so a stored plan can never name a size without
  saying whose it is.
- **A comment in the graphics-engine build's architecture record is no longer
  read as a list of architectures.** The HIP build of the inference engine ships
  a small file naming the AMD GPU architectures it carries device code for, and
  the assistant reads it to decide whether the card it is about to serve on is
  one of them. The reader split the whole file on spaces and discarded only the
  words that themselves began with a `#`, so every other word of a comment
  survived: a record opening `# written by the recipe` declared "by", "recipe",
  "the" and "written" as architectures next to the real ones. That is not merely
  untidy. The declared set is compared against the architecture of the card that
  will be used, and a comment that names an architecture the build deliberately
  dropped would have declared it as carried — the one direction that matters,
  because accepting the engine on a card whose device code the build does not
  contain crashes the engine at model load, which is exactly what this check
  exists to prevent. Comments are now removed line by line, from the `#` to the
  end of that line, before the rest is read. The file the recipe writes today
  carries no comment and reads exactly as before; the change can only ever leave
  an architecture out of the declared set, never put one in, and leaving one out
  simply serves on the Vulkan engine instead.
- **A refused archive install now exits non-zero.** `pkm install --archive`
  checks the archive's SHA256 against the signed repository index. When it did
  not match, the command printed `archive SHA256 does not match repository
  index!`, refused to install, changed nothing — and exited 0. Anything that
  reads the exit status, such as a build step or a script, was told the install
  had succeeded while the package was not installed. Both refusing trust modes
  (the default `strict` and `repo-only`) are now counted, the command ends with
  a line naming each refused package and why, and it exits 1. The `loose`
  override is unchanged: it is a deliberate choice, it still warns, and it still
  proceeds.
- **A staging build no longer opens the running system's package database.**
  The builder brackets a recipe's post-install hook with a baseline of the
  package's own file hashes and a comparison afterwards, so a file the hook
  rewrites is recorded as hook-managed rather than reported as damage. Both
  halves read and write the package's row in the live package database — and
  they ran on `--stage-only` builds too, which register no row at all. On an
  installed machine that showed up as `pkm DB open failed for hook baseline:
  attempt to write a readonly database`: the write failed, and nothing was
  changed, but only because the build was unprivileged. The pair now runs in
  tracked mode only, where there is a row to baseline.
- **The attribution gate reports how big the change really is.** When the
  generated `THIRD-PARTY-NOTICES.md` falls out of step with the recipes, the
  check refuses and says how much differs. It compared line 1 with line 1,
  line 2 with line 2, and so on, so adding a single package — whose entry is
  inserted in the middle and moves every line after it — was reported as
  "~768 differing lines". The message now reports what a real difference
  contains: lines added and lines removed. The refusal itself, and the
  instruction never to hand-edit the file, are unchanged.
- **The ROCm object-listing tools start.** `/opt/rocm/bin/roc-obj-ls` and
  `/opt/rocm/bin/roc-obj-extract`, the two tools that list and extract the GPU
  code objects inside a compiled binary, are perl scripts. Both need the
  `File::Which` and `URI::Escape` perl modules, which the project's perl does
  not carry, so on every installed machine they exited immediately with
  `Can't locate File/Which.pm in @INC` and listed nothing. Both modules are now
  packaged from source (`perl-file-which`, `perl-uri`) and declared as runtime
  dependencies of `rocm-hip`, which is the package that installs the tools.
- **Every web-chat turn that needs the model completes.** On every installed
  R001.2 machine, a browser-chat question that reached the model raised inside
  the daemon and showed the "Something went wrong on my end" banner, because
  the browser server built the prompt outside the connection's conversation
  binding once the daemon detached its own. Every router access after routing
  now runs inside that binding; an unbound access still refuses.
- The assistant's browser transcript records each exchange once and asks the
  model the question once; every second turn used to carry the question twice.
- The assistant's serving plan is computed with the video-memory figure the
  hardware detector actually read; a writeback connector is no longer counted
  as a monitor, so the serving model stays off the card painting the desktop;
  on an NVIDIA card behind the proprietary driver the CUDA engine serves first
  and Vulkan is the floor; and the engine ladder detects the card vendor itself
  and offers every engine not yet tried before it reports itself exhausted.
- A request clause governed by "do not", "don't", "never" or "without" is never
  cut into an affirmative sub-request (the tail of such a sentence used to open
  the consent dialog for a command the person had just forbidden). A
  definition question ("explain what a kernel is") reaches the model instead of
  being served from the system-fact cache.
- The wiki index catches up after a turn even when the turn's own memory
  embedding is still in flight; every trace row the memory index writes names
  the turn that produced it, so the installed trace-integrity gate no longer
  refuses a record after the turn it drove has been indexed. The assistant
  caches its computed documentation index on disk, keyed by the documentation
  hashes and the model identity, so a restart does not recompute it.
- The assistant's status output and the runtime manifest state what was
  measured: the chat model, its paired projector and the embedding model are
  recorded, their bytes verified before loading, and a manifest-write failure
  is reported; model records carry the package descriptor identity and an
  explicit license, and an existing-model setup re-verifies the installed
  artifact and reports the record rewritten, already correct or refused.
- **Root is no longer locked on an installed system.** The password package's
  post-install hook ran on every installed target after the installer had
  written the chosen root password, so every R001.2 install landed with root
  locked and no rescue credential. The locked root the shipped media requires
  is now written where the media is assembled, and the installer reads the
  root password field back and verifies after the hooks that it still holds
  the hash it wrote.
- **The install trace never records a credential.** The subprocess layer used
  to log the full `chpasswd -e` line with both account hashes; a credential fed
  to `chpasswd`, `passwd`, `cryptsetup`, `openssl`, `gpg`, `ssh-keygen`,
  `mokutil` and their kin is now recorded only as a byte count, a
  credential-shaped payload or output fed to any other command is withheld the
  same way, and the trace records only a description of the root password
  field, never the hash.
- **Every SSH public key the installer writes is one the person was shown and
  accepted.** The graphical screen validated only the first line of the key
  box and then stored and wrote the whole box, so a pasted `authorized_keys`
  file had one line checked and every line installed. One shared parser now
  decodes each line, and both the graphical and the text installer show what
  will be written.
- The Welcomer's SSH switch closes the port: the SSH opt-in writes the
  removable nftables fragment the Welcomer manages, and the shipped
  `/etc/nftables.conf` is never edited, so SSH OFF now drops the packet filter
  rule as well as the service. The serial login prompt is enabled only when the
  installer itself ran over a serial console, never on a merely working port.
- Four installer-backend corrections: the final cleanup never deletes the login
  the person chose; post-install hooks and the checksum reconcile run only for
  packages whose install succeeded; a target that stays mounted is reported
  instead of "install complete"; the boot-partition mount step unwinds the root
  mount it acquired before the original error leaves. The `sr_RS.UTF-8@latin`
  locale compiles.
- The install leaves the privilege configuration at the mode sudo's own syntax
  checker requires (`0440`); at `0644` `visudo -c` refused the whole
  configuration. The install writes the password format the machine's own
  password library prefers and records the format written, so an account made
  by the install and a password changed afterwards no longer sit in two formats
  on one machine; the shipped `login.defs` actually declares the hashing method.
- Remote login: forwarding ships off in both directions (a person who logs in
  cannot open tunnels through the machine, and the machine cannot reach back
  into the key agent on the computer they came from); the recipe's copy and
  the shipped copy of the drop-in are held equal by a test. The user guide
  states the posture and how to turn forwarding on knowingly.
- The three files the install mirrors into the removable-media fallback
  directory on the boot partition are declared as one set and held by a test;
  they are byte-identical signed copies of the originals, not leftovers.
- Installed AMD-only unified kernel images omit the Intel microcode image;
  mixed or unknown processor inventories keep both.
- The boot menu's unified-kernel entries are named: the newest carries a fixed
  id that `/etc/default/grub` pins as the default, so a later generator cannot
  take the default by sorting first; the theme hook regenerates an existing
  menu only and leaves initial installation to bootloader setup.
- The boot-entry cleanup reports a truthful count and never removes a foreign
  entry without an offer; the firmware's own fallback entry is classified as
  what it is.
- The package manager: `pkm info` exits 1 for a package that is not installed
  and 0 for an installed one, so a script can gate on the status; `pkm cache
  clean --keep-current` matches the installed archive by name, version and
  release; `restart-services --all` restarts only the running units of packages
  changed since this boot and never the units that carry the login session (a
  package that would need one is reported as REBOOT REQUIRED), and refuses
  without a readable boot time; `upgrade --all` upgrades the package manager
  first and re-executes under the new release for the rest of the queue; a
  hook's identical-byte rewrite of a file is classified hook-generated only
  when the pre-hook bytes matched the owner's own checksum; database write
  traces record statement execution truthfully instead of predicting the
  outcome of an enclosing transaction; upgrading a download-helper package
  keeps the application it installed; the `claude-code` helper's install-mode
  option reaches the helper and only as "0" or "1".
- The assistant answers a question about the conversation itself ("what was my
  first question") from a verbatim, in-order record it now keeps, and a question
  about this machine's memory from the machine's own reading (what is available,
  not what is free); a follow-up such as "and memory?" resolves only against a
  resource question. Before, both went to the model and it named a later turn
  and a wrong figure with confidence.
- The assistant reads the display adapter's identity once from the kernel's
  device tree instead of running a PCI listing every five minutes; that listing
  resumed every suspended PCI device on each pass. A launch no longer passes the
  prefix-reuse option to an engine context that cannot honour it, and says so.
- A download helper's manifest records the symbolic link a machine actually
  holds, not its resolved target.
- What a package hook says on a zero exit is reported as a NOTE line instead of
  being discarded (eight fontconfig diagnostics naming a real ordering defect had
  been dropped by an install that called every hook OK); a hook that is selected
  and then declines to run for a foreign install root says so with its reason.
  Identical NOTE output from one hook is shown once with its count, a block a
  hook already said in the same install is not printed again and the closing
  summary names what was folded; the install trace keeps every line.
- An installed gate states whether a kernel panic on this machine would leave a
  record: the reserved region, the recorder's registration, the backend, and the
  shipped configuration on disk, so a machine running a corrected kernel image
  it will lose at the next boot is reported as such.
- The package-manager hook that invokes the certificate-trust updater fires on
  the trust source p11-kit is actually configured with (`/etc/pki/anchors`,
  including its anchors and blocklist subdirectories); it matched a directory
  family no recipe in this tree installs into, so the updater was never invoked
  by a package operation. The test derives the expected directory from the
  p11-kit recipe's own configuration, so moving one without the other fails
  loudly.
- A font installed before fontconfig no longer reports its cache as built: the
  cache builder postpones with its reason until the target has
  `/etc/fonts/fonts.conf`, and that file joins the trigger, so installing
  fontconfig rebuilds the cache for every font installed before it. (`fc-cache`
  exits zero and complains only on stderr when no configuration is present,
  which the package manager did not show.)
- Every download helper's acceptance record names the person who ran it
  (`SUDO_USER` when supplied, otherwise the effective account with an explicit
  note that no consenting user was named); the CUDA helper stamps its release.
- The `claude-code` helper pins CLI 2.1.270 (2.1.218 could not run the current
  model family), prepares the dependency tree without lifecycle scripts,
  refuses on a critical advisory or a failed registry-signature check (the
  signature check was claimed before and never run), reads the installed
  version back from the exact executable placed, and pins the VS Code extension
  to 2.1.270 with its sha256; its refusals now say what happened and what the
  reader can do.
- `intergenos-backup` (the backup engine): the twelve defects found by review
  are corrected with regression tests — captures record directory symlinks;
  reclamation stops before deleting anything when any manifest is unreadable
  or malformed; a same-size edit inside one second is stored as new bytes; the
  manifest hashes the stored copy; scrub reports every integrity failure; an
  existing store object is verified before deduplication accepts it; a failed
  configuration capture is retried and journaled; integrity failures exit
  non-zero; the configuration fingerprint covers every watched path; capture
  work runs off the desktop application's main loop; separate engine instances
  serialize state with a process lock. Retention removes versions
  all-before-any, fail-loud and announced, with every plan recorded before and
  after it runs and shown by the status command and the window; the
  directory-class target's size cap is enforced; a persistently failing mirror
  no longer accumulates a manifest per attempt; a store whose user-data
  directory is a symlink is refused; an unchanged file is reused by hard link
  only after its bytes and the previous stored copy are verified; a source that
  changes while it is being copied is refused; an unreadable engine state file
  is refused and preserved instead of being replaced by an empty state; and a
  storage error while validating a pruning plan no longer leaves the plan's
  announcement in the record without an outcome.
- `nvidia`: the driver's install hook could not find the kernel when re-run
  by hand outside the package manager (its fallback looked for a module
  directory name InterGenOS never produces), and its module-signing fallback
  named a kernel source directory that does not exist. Both recovery paths now
  derive the paths from the real `<version>-igos-<release>` string; the normal
  kernel-upgrade path was never affected. The legacy `nvidiafb` framebuffer
  driver is prevented from loading alongside the NVIDIA DRM driver.
- The shipped smoke harness (`intergenos-smoke-test`) is a trustworthy health
  instrument: the package count comes from the listing header; a marker
  package that is not found fails; an unprivileged verify is a warning with the
  exact re-run command; a path the run merely cannot read is reported
  unreadable, never absent, while a truly absent boot component under Secure
  Boot fails; the initramfs stub no longer counts as a boot artifact; documented
  no-driver PCI classes are never reported unclaimed; a missing check module
  aborts the run instead of passing.
- The first-run guide's restart texts follow the model setup state and direct
  the person to the setup card; the installed-package check works with older
  and newer package managers alike.
- The `/etc/cron.README` shipped with `fcron` no longer describes the other
  scheduler as present; it states that it is not installed and gives the one
  command that installs it.
- The `tailscale` defaults select the native nftables backend.
- The `wpa_supplicant` nl80211 template unit and the `switcheroo-control`
  unit are conditioned on the hardware they need; neither starts on a machine
  without it.
- `shim-signed`: the source license metadata matches shim's two-clause BSD text.

### Changed

- Linux kernel 6.18.10 → 6.18.51, the current release of the 6.18 long-term
  series (`linux-kernel` and `linux-kernel-pass2`, release restarted at 1, so
  the kernel release string becomes `6.18.51-igos-<release>`). Five backport
  patches the recipes carried are retired because 6.18.51 carries each fix
  upstream: CVE-2026-31431, CVE-2026-43284, CVE-2026-43500 (the locally
  authored backport is replaced by upstream's own fix), CVE-2026-46300, and the
  ASUS keyboard probe fix. The one remaining local patch, the graphics-card
  display-wakeup patch, is re-based onto 6.18.51 with its hunks unchanged. The
  final kernel pass runs `depmod` through its own sealed post-install hook so
  the package manager's recorders observe its writes. The kernel updates
  through `pkm upgrade` like any package and takes effect at the next reboot.
- **Unneeded privileged programs are removed from the desktop set.** The
  terminal multiplexer (`screen`) and the system-information helper
  (`libgtop`) ship without their setuid bit, which nothing in this system
  needed; the Kerberos switch-user program (`ksu`) is no longer shipped, since
  this system configures no realm and the program could never succeed; each
  recipe halts if the bit is still set. The declared privileged-program
  inventory loses the entries in the same change.
- **Default-enabled third-party services run under systemd restrictions.**
  `avahi`, `bluez`, `cups`, `networkmanager`, `rtkit`, `switcheroo-control` and
  `udisks2` ship upstream's service hardening drop-ins, each checked to keep the
  access the service needs (network discovery, Bluetooth devices, printer
  configuration and device access, network and device access, realtime
  scheduling, host mounts).
- The package manager, the installer and the root-run helpers (the backup
  engine's pre-transaction handler, the NVIDIA EULA helper) run Python in
  safe-path mode, and every program the package manager's hooks execute is
  bound by absolute path; a bidirectional execution inventory and a
  pushed-content gate refuse new, missing or changed execution edges.
- The desktop's unavailable screen-reader control is kept off and unwritable
  until a supported implementation is deployed; Orca, Rygel and WebDAV sharing
  are suppressed in the default session.
- The live session's D-Bus policy for the installer is written only into the
  live-media boot overlay, not into the installed policy file.
- Ctrl+Alt+T opens a terminal and Super+D shows the desktop.
- `sudo` ships one secure-path setting (the duplicate line in the drop-in that
  dropped `/usr/local` from the path is removed).
- The user documentation states where the machine's kernel-signing key lives,
  what that costs, and what would change it; the desktop guide says InterGenOS
  runs Wayland.

### Security

- CVE-2026-53362 (IPv6 send path, a local flaw listed in the CISA Known
  Exploited Vulnerabilities catalogue): R001 through R001.2 ship an affected
  kernel. Closed by the move to 6.18.51, which carries the upstream fix.
  Advisory: `docs/security/advisories/CVE-2026-53362-ipv6-fraggap.md`. No
  interim mitigation exists for installed systems; the fix is the kernel update.
- R001.2 installs landed with root locked and no rescue credential, with the
  chosen root password never taking effect; see Fixed. Installed systems get
  the correction with the `shadow` and `forge` updates; an existing install's
  root stays as it is until the person sets it.
- R001.2's install trace (kept on the installed system) recorded the chosen
  account password hashes; see Fixed. A person who installed R001.2 should
  treat the trace file as sensitive or delete it.
- Remote login on R001.2 permitted TCP and agent forwarding for anyone who
  could log in; see Fixed.

---

## [R001.2] — 2026-09-03

The second point release. R001.2 was produced as a targeted rebuild against
R001's substrate — every changed package recompiled with the full validation
gate set enforced — then installed and evaluated on real hardware before
publication. The image, its checksum, the signature over that checksum, and the
release key are published together on the project mirror; verification
instructions are unchanged from R001.

### Known limits in R001.2

- The wiki embedding index can stay keyword-only for a daemon run if neither
  the between-turn pass nor a web-page turn completes it; rarer since this
  release, not impossible.
- A web-search request phrased without a search verb or a subject the assistant
  can extract still goes to the model. `search the web for …` always reaches
  the tool.
- The small (2B) tier states wrong facts with confidence and answers some direct
  questions with a template; the model's floor, not the tree's.
- The greeter shows the Qwen attribution only when the installed `intergen`
  carries `--version`; a machine upgraded package by package renders nothing
  until intergen updates.
- Stopping the assistant service by hand does not keep it stopped: the desktop
  panel re-activates it over the desktop bus.
- The older GPU power rule (`70-intergen-compute-gpu-pm.rules`) still holds
  every secondary AMD card awake permanently; the scoped hold below makes it
  unnecessary.
- The NVIDIA driver helper's silent minute after the EULA is only partly fixed.
- The scenario harness sits to its timeout when the assistant's bus name is
  already owned (a test-instrument limit).
- The canonical test suite cannot complete on an installed machine.

### Added

- **The package manager can install into a directory instead of into this
  system.** `pkm --root DIR install <package>` puts the package, its database
  record, its manifest and its caches under DIR, and writes nothing outside it.
  The machinery has been there for a long time — it is how the graphical
  installer installs a whole system onto a target disk — but it could only be
  reached by writing Python. Repository settings and the keys used to check
  signatures are still read from the running machine, because a directory being
  built has no keys of its own to check against, and one that did would be
  choosing what the package manager trusts while it is being filled. Anything
  that cannot be done for another directory is refused by name, with the
  reason, before anything is changed: a package that installs by running a
  vendor's own installer, and a package whose own post-install step could not
  run inside the directory, are both refused rather than half-installed.
  Fixed in the same change, all three found by doing it for real against the
  package mirror: rebuilding the font cache and rebuilding the certificate
  trust store both acted on the machine running the command rather than on the
  directory, and the record of the newest package index accepted — the thing
  that refuses an older index replayed at you — was written to the machine
  rather than to the directory.

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

- **Upgrading a download-helper package no longer deletes the application it
  installed.** A package such as the CUDA toolkit ships a small installer
  script; the application itself is fetched from the vendor by that script
  and recorded on the same package. Upgrading the package removed every file
  the package owned — the application included, seven gigabytes in the
  toolkit's case — installed the new script, and reported success, leaving
  the CUDA engine unable to start. An upgrade now keeps the application and
  its record in place and replaces only the script; when the application was
  never installed, the upgrade says so and does not start a download nobody
  asked for. (pkm r73)
- The CUDA toolkit's installer script now records its own package release in
  the footprint it leaves for the package manager, so the toolkit is recorded
  at the release that was installed on every package-manager release,
  including the one on the installed image. (cuda-toolkit r6)
- The package manager no longer records a proprietary-download helper's
  package (the CUDA toolkit) at release 1 over the archive's release, which
  produced a phantom same-version upgrade offer after every such install.
- **The Welcomer works after the NVIDIA driver reboot.** On an NVIDIA machine
  the first-boot greeter installs the vendor driver, asks for a reboot, and
  promises to come back so InterGen can be set up. It came back and crashed
  before it had a window, and the crash was recorded as the person having
  finished, so it never came back again; while the driver was still
  installing, it had also reported the terminal closed with nothing installed.
  All of that is fixed: the page builds, a run that cannot build its window
  exits as a failure and is shown again, the outcome is read only when the
  terminal's command has actually finished, the rows inside the amber
  advisory box sit on an opaque ground instead of taking an amber cast, and
  the last line in the terminal says the Welcomer returns after the reboot.
  (intergen-welcome r42)
- **The CUDA toolkit is downloaded when the CUDA engine is installed.** The
  toolkit is fetched from NVIDIA by its own installer package after the
  person accepts NVIDIA's license. Pulled in as a dependency of the engine,
  that installer package was recorded as installed and its download step
  never ran, so the engine could not start and the package database said the
  toolkit was there. The package manager now runs the download step for every
  such package a transaction installs, `pkm info` and `pkm verify` say plainly
  when a download has not run, the Welcomer names the toolkit on the command
  it runs, and the toolkit's installer takes the license answer from the
  terminal the person is at rather than from whatever is on its standard
  input. On the first full run, the installer then refused to record the
  toolkit's eleven 32-bit objects (NVIDIA ships both widths) and left seven
  gigabytes untracked while the package manager exited as if it had
  succeeded; the installer now declares the mixed widths, and a failed
  download step makes the command exit with an error. (pkm r69–r70,
  cuda-toolkit r4–r5, intergen-welcome r42)
- **A fresh installation passes `pkm verify`.** The step that clears stale
  compiled Python files on an upgrade ran after the package was written into
  place and deleted the compiled files the package itself ships; every
  installation then reported thousands of its own files missing. The step runs
  before the package is written, so it clears only what a previous version
  left behind. (pkm r69)
- **`pkm verify` no longer reports three existing systemd files as missing.**
  Three slice units whose file names contain a backslash were recorded in the
  build's manifest with a stray backslash before their hash, so every
  installation registered them under a name that does not exist. The build's
  manifest writer records the hash cleanly, and the package manager reads the
  manifests already in the field. (pkm r71)
- **The smoke harness's module-signing check no longer fails on a kernel that
  enforces signing.** The check read the kernel config through a pipe that
  broke under the harness's own shell options; it now reads the config
  directly. (forge r240)

- Every trace row the assistant daemon writes while starting and warming up names the boot that produced it; the warm-up generations and the engine offload check no longer write placeholder-identified rows (intergen r245).

- **The Welcomer's polkit action file ships in its source tarball.** The
  first-boot greeter installs `org.intergenos.welcome.policy` — the polkit action
  that names the application and the change in every privilege prompt — but the
  script that generates the greeter's source tarball never staged it, so the
  tarball-membership check refused the build. The generator stages it, and a
  commit-time test now checks that every file the greeter's install step takes
  from its tarball is staged by the generator. (intergen-welcome r41; the eight
  other packages that declare the shared generator as an input re-fingerprint
  with no change to their own content.)
- **The assistant runs a web search it was explicitly asked for.** Asked, in
  three wordings, to look something up on the internet, it answered that it
  could search and stopped, or offered to search and did not; two of its own
  answering paths reached those turns first. Both step aside for an explicit
  search request, and the search runs.
- **A web search looks up what the sentence asked about, not the sentence
  itself.** The query is the extracted subject; a sentence that names nothing
  keeps the sentence as its query.
- **A request reaches a tool when it clears that tool's own recognition bar.**
  A second, flat 0.85 floor in the router kept any intent with a lower bar (in
  the shipped corpus, web search) from being reached; such questions went to
  the model with no tool.
- **A request that asks for two things in one sentence is handled as two
  things, and the second half is done.** The sentence is split and each part
  reaches its tool. A hyphenated program kind (`note-taking app`), `get a …`,
  `get me a …`, `is there a …`, and screen-capture phrasings reach their
  carriers; a program named earlier in the sentence is what the second half
  acts on; a pronoun or bare determiner (`install it`, `restart the one that's
  stopped`) is never sent to a tool as a name.
- **A refused action is reported in the tool's own words.** When a step of a
  request is refused (an install that needs privilege, for instance), the
  refusal is the answer for that step and is marked as the tool's words; it was
  discarded and the model described a command as if it were the outcome.
- **When nothing matched, the assistant asks which one.** If the first half of
  `find me a pdf editor and install it` finds no candidate, the assistant says
  nothing was installed and asks which package (or service) was meant; it names
  no package and carries no command.
- **`find the hidden files in …` and `find the big files` run a command.** A
  recognised file-search request resolves to one bounded, depth-limited,
  read-only listing instead of being recognised and then dropped; a human place
  name or a path with shell characters is left to the model on purpose.
- **An invented command never reaches the person.** The reply screen checks a
  whole first-party command (tool, subcommand, every flag) for `pkm`, `forge`,
  `intergen` and the `igos-*` tools against each tool's interface, which is
  generated from the tools' own parsers; `pkm remove /tmp -s 80` is caught and
  the real `pkm vacuum` is no longer accused.
- **The assistant daemon holds its serving graphics card awake while a model
  is loaded on it.** A discrete card with no display sits at runtime power
  `auto` and is suspended when idle; each model start or stop woke it and the
  desktop was rebuilt under the person using it. The daemon writes `on` to the
  card it pins the model to before opening it and restores the previous value
  when the model is gone; the udev rule `71-intergen-gpu-runtime-pm.rules`
  grants the video group that write on display-controller devices only. With
  the kernel and compositor changes below this closes the wallpaper and
  windows-to-primary-monitor defect.
- **What a tool found reaches the answer you see.** A tool result was dropped
  from the reply, and the record of that problem could not be trusted; the
  reply is measured for the result's presence before it is sent.
- **The assistant no longer refuses an ordinary question.** A recipe, a long
  formal proof and a long contract were refused; the instruction to answer
  helpfully while noting that the question sits outside what this machine is
  for is enforced.
- **`don't forget X` keeps X.** A negated delete verb was executed as a delete
  and both stored rows were marked deleted. A negated delete is not a delete,
  and a sentence the keep check claims is never handed to the delete path.
- **The offer to pass a question to the larger model appears only when there
  is a reason to.** It was offered on every ordinary turn (a threshold written
  for a one-to-five scale compared against a 0/0.5/1 value); it is decided from
  the request itself.
- **A remembered fact is answered from the stored fact, by code.** `what's my
  printer?` reached the model with the fact beside it and the mid-size model
  ignored it seven times in ten; a recall question is recognised as a recall
  and answered from the store.
- **One stated fact is remembered once.** `remember that my backup drive is
  /dev/sdb1` stored two entries and counted twice in `what do you know about
  me?`; one reading is stored, keyed on the subject named.
- **A chat model server that fails to start says why, is tried again, and the
  failure is admitted.** The daemon kept only the first 500 characters of the
  dead server's output (less than its banner); it keeps the end; a transient
  failure is retried three times; an absent model file or binary still
  degrades at once; the person is told.
- **The engine-health alarm counts only served answers.** Two of its five
  window slots were filled by one-word replies to readiness pings, so one
  flagged real answer fired the corrupt-output alarm on a healthy machine; a
  coherent answer containing LaTeX no longer reads as corruption.
- **Stopping the assistant's service releases its desktop-bus connection,** so
  a restart inside the same process comes back reachable instead of running
  with no bus interface and one warning line.
- **A credential typed into a command no longer stays in the assistant's
  records.** `Authorization: Bearer …`, `--password` in its three spellings, and
  `PGPASSWORD=…` / `api_key=…` assignments are replaced in both records with the
  marker kept; the decision record's second write path, which removed nothing,
  is closed.
- **The wiki index finishes building, and long turns stay findable.** The
  daemon gave itself ten seconds at start-up to embed the installed wiki; on a
  cold boot that ran out with the index part-built and the wiki answered by
  keyword match for the daemon's life. Each composed turn, and each web-page
  turn, gives the index one short bounded pass; the session index sizes its
  inputs to the embedding server's reported context and scores a long exchange
  by its best piece.
- **Teaching answers are prepared in small batches,** so preparing them cannot
  block the first thing a person asks.
- **`intergen --version` is a command, and the Qwen attribution is shown where
  a person converses.** The license page said the command existed; typing it
  printed `Unknown command`. It prints the package version and, only when a
  Qwen-family model is on the machine, one line naming that model and the
  Tongyi Qianwen License. The same line appears under the web conversation
  view's composer (the desktop panel is a window onto it), in the terminal
  console, and on the first-boot greeter's assistant cards. Every intergen
  command names the log file it writes.
- **An ASUS laptop keyboard keeps its driver.** The keyboard's vendor-control
  USB interface carries no mappable usages; a use-after-free guard new in this
  kernel version treated that as a failed probe, reported `-ENOMEM` once per
  boot and tore the node down. The upstream fix is backported verbatim.
- **Setting the assistant up from the greeter no longer runs as the
  administrator.** The one-click button escalated the entire setup run —
  hardware detection, the license gate and a model download of up to about
  22.9 GB — to the administrator account, under the general permission that
  covers running any program as another user. Nothing in that run needs it: the
  model is downloaded and checksum-verified as you, and only the step that
  writes it into the system-wide model store asks for permission, through a
  dedicated helper whose permission entry names what is being installed and
  which re-checks the file before writing it. The button now runs setup as you,
  so the one password prompt you see is that named one. Because the run is no
  longer wrapped in a privileged process, a model that downloaded and was then
  not installed — because the password prompt was closed, because the installer
  program is missing, or because the installer refused the file — is now told
  apart and given its own sentence, and the first of those says the download
  does not have to be repeated. Before this, that case reported that the
  download had not finished when the file was already on disk. The exit codes
  the greeter reads from a permission prompt are also corrected: the code that
  means the permission was not obtained was being reported as a failed
  authentication, and the code that means the program could not be started was
  being reported as an authentication problem, which it is not.
- **A privileged action the Welcomer could not run now says what happened.**
  A first boot recorded PolicyKit refusing the greeter's request and pkexec
  reporting that the prompt had been dismissed; the greeter showed a switch
  sliding back to off and said nothing, and the one-click assistant setup
  answered a closed password prompt with the same sentence it uses for a
  failed download. A closed prompt, a refused authentication and an error
  from the helper are now three different sentences, shown where the user is
  looking and written to the journal beside PolicyKit's own line. The greeter
  also ships a PolicyKit action for its helper, so the prompt names the change
  being authorized instead of saying only that a program wants to run as
  another user; the strength of the check is unchanged.
- **The greeter's autostart no longer reports a failure once its work is
  done.** On an already-set-up machine the launcher exits within milliseconds
  and the session manager could not place the finished process in a control
  group, which surfaced as a resource failure in the journal on a normal
  login. The launcher now writes a per-user autostart entry marked hidden
  alongside its completion marker, so nothing is started at all on later
  logins, and removes it with the marker when a driver install asks for the
  greeter to return after a reboot.

- **A graphics card waking up no longer rebuilds the desktop.** On a machine
  with a card that drives no display — a second card serving compute work — the
  card powers down when nothing is using it and wakes when something opens it.
  Each wake was announced as a display hotplug even though nothing was plugged
  in or out, and the compositor, which had released that card's device file,
  could be refused once when it reopened it and treated the refusal as proof the
  card had changed. Windows were moved to the primary monitor and desktop
  backgrounds were re-created. Two patches fix it: the kernel now probes the
  connectors on a wake and reports only a real change, so a display connected
  while the card slept is still detected; and the compositor retries a refused
  reopen and, if it still fails on a card with no display attached, keeps what it
  knows instead of discarding the monitor configuration. The third part is the
  daemon's card hold above. Proven to apply and build against the shipped
  sources; the behaviour is proven by an install.

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

- The shipped release identity now states R001.2: `/etc/os-release`
  (`VERSION`, `VERSION_ID`, `PRETTY_NAME`), `/etc/lsb-release`, `/etc/igos-release`
  and the `/etc/issue` banner. The codename is unchanged. Systems installed
  from the R001.2 images minted on 2026-09-02 report R001.1 until they upgrade
  the base system files package; the image stamp (`IMAGE_VERSION`) was already
  correct on those images.
- **The scenario harness measures honestly; nothing a person uses behaves
  differently.** It reads the assistant's turn record as the run adds to it; a
  turn it could not drive is never graded and a dead engine stops the run; a
  scenario that means the same thing on every hardware tier declares every
  tier, so the mid-size and large tiers are tested by it; a two-part request is
  graded clause by clause and a reply is checked against itself; sixty-four
  scenarios written from the shapes of real first-use conversations were added;
  four test wordings that had drifted from the assistant's own were corrected;
  source comments that misdescribed model selection were corrected and pinned
  by a test.
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
