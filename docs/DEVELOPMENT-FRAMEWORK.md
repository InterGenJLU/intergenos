# How InterGenOS Is Built — The Development Framework

**A public companion to InterGenOS's internal development process.** It explains *how*
InterGenOS is developed and *why* the process is shaped the way it is — the principles, the
lifecycle, and the non-negotiable rules that decide what ships. The step-by-step mechanics
live in the operations guides under [`docs/operations/`](operations/) (referenced throughout)
and in the [wiki](https://wiki.intergenos.org); this document is the spine that ties them
together.

If you only read one section, read [the doctrine](#the-doctrine--what-iteration-prevents) — it
is the reason you can trust an InterGenOS build.

---

## The lenses — every choice is scrutinized through these

Before any development decision is made — a recipe edit, a fix, a build flag, a package choice,
a process step — it is run through three lenses, in order. They are not aspirational values;
they are the working filter for every choice. An option that fails any one of them is wrong, no
matter how convenient, conventional, fast, or precedented it is.

1. **Security is not first. It is only.** We build assuming adversaries have superhuman
   vulnerability-discovery capability. The question is always: does this *eliminate* silent
   failure and turn an unverified assumption into a checked gate? A masked error, a blanket
   "ignore failures," an unverified "known-good" claim, a degraded-but-shipped feature — these
   are exactly what such an adversary exploits. If an option *masks* rather than *verifies*, it
   is wrong; no further deliberation is needed.

2. **The user controls a machine they understand, can modify, and can trust.** We reject bespoke
   complexity that hides how the system works, and prefer the standard, transparent,
   built-from-source path even when a shortcut would be more convenient. The two lenses are
   complementary: a machine the user cannot trust is a machine they do not control.

3. **Logic and project intent.** Does the choice satisfy *everything* it is evaluated against —
   secure, from-source, reproducible — *and* the actual goal behind the work (rigor, validate
   rather than assume, no half-finished features, no quietly narrowed scope)? A locally clever
   answer that defeats the project's stated intent is wrong.

These lenses usually converge on a single answer. When they do, that *is* the answer.

---

## The doctrine — what iteration prevents

**A build whose packages all compiled is NOT a build that is known-good.** The expensive
failures of a from-source distribution are not compile errors — they are the class of defect
that compiles fine, packages fine, and only manifests when the artifact is *installed and booted
on real hardware*:

- a service unit locked down with `ProtectSystem=strict` and no writable path — builds clean,
  fails the moment the daemon tries to write its state on a real install;
- a directory omitted from a package's file list — builds clean; the component that depends on
  it silently initializes empty at runtime;
- a hardware-detection heuristic wrong for an integrated GPU — builds clean, then selects a
  model the target cannot actually run;
- a first-boot race — builds clean, fires on one machine and never on another.

None of these is caught by "all packages built, 0 failures." Every one is caught by *installing
the artifact and booting it on real hardware.* That is why the candidate loop **always includes
the bootloader → ISO → install → boot chain**, and why a candidate is never trusted on the
strength of a clean build alone. (Full treatment:
[`docs/operations/09-gbc-iteration-methodology.md`](operations/09-gbc-iteration-methodology.md).)

**Two laws follow from the doctrine:**

1. **Validate on a clean build and install, on hardware that reproduces the bug — never on a
   hand-patched development box.** A fix that only works because of a manual edit on the running
   target is *a note about a fix, not a fix.* Nothing is done until it lives in the source tree
   **and** a clean build reproduces the corrected behavior with zero manual intervention (the
   signing step is the one sanctioned human action).

2. **Fixing now is the default for trust-bearing, build-orchestration, and small gaps.** An
   unfixed gap compounds — it interacts with downstream changes, masks root causes, and
   resurfaces as a production-class failure instead of a build-time error. When immediate repair
   is genuinely not the right call, that exception is recorded with an explicit expiry condition
   and a way to measure it, never as a bare line on a list.

**Race-class fixes carry two extra requirements:** validate on the hardware that actually
*reproduces* the race (the slower, lower-end tier is the honest test), and require several
consecutive clean **cold** boots — a single clean boot does not clear a race, and a warm reboot
is not a substitute for a cold power-cycle for timing-sensitive firmware and graphics races.

---

## The lifecycle

InterGenOS is produced as an ordered, reproducible lifecycle — from lining up a build, through
handling build issues, signing, ISO assembly, pre- and post-install evaluation, issue tracking,
iteration, mirror publishing, and promotion to a stable release. Each stage names its decision
gates and points to the operations guide that holds its mechanics.

### 1. Build from source — the proving ground

A full from-scratch build is how a new release candidate is minted. It runs every tier from the
toolchain up through the desktop, extra, compute and AI package sets, in a fixed phase order —
the AI tier last, because its GPU-native builds consume the compute SDKs and extra-tier libraries
at build time — inside an isolated, reproducible build environment. Every source is pinned and checksum-verified before it
is used; the build is reproducible by construction (a fixed source date, deterministic ordering).
This is the *proving ground*, not the per-fix tool — minting a candidate is deliberately
expensive so that what comes out of it has earned trust. (See
[`docs/operations/02-running-the-builder.md`](operations/02-running-the-builder.md).)

### 2. Handle build issues — without cheating

When a package fails, the cause is fixed **in the source tree**, never worked around. The
forbidden shortcuts are explicit and absolute: no moving a package to a different tier to dodge a
wiring problem, no disabling a feature to bypass a missing dependency, no silently skipping a
failed step, no stub functions ("a stub is a lie"), and disabling a package's test suite is a
last resort, not a first response. After a small number of failed tactical attempts on one
problem, the process *stops* and switches to a research-first investigation rather than thrashing.
Cheap host-side validation gates run before the expensive build environment even exists, so most
misconfigurations fail in about a second. (See
[`docs/operations/11-resolving-validation-gates.md`](operations/11-resolving-validation-gates.md).)

### 3. Sign the boot chain

The build halts for a hardware-token signing step. One signing covers the bootloader and all of
the unified kernel images. The trust chain is sealed here: each kernel image's command line
carries the verified-integrity root hash of the read-only system image, so a tampered system
image cannot boot under a validly signed kernel. Signing only *appends* a signature — it never
alters the payload — so the inputs are re-staged and their root hashes re-checked against the
freshly built system image before every signing, and the signed outputs are verified afterward.
(See [`docs/operations/03-automating-signing.md`](operations/03-automating-signing.md).)

### 4. Assemble the ISO

The read-only system image is compressed, a verified-integrity hash tree is generated over it,
and the bootable ISO stages the signed bootloader and kernel images alongside it. The trust gap
is closed by integrity verification of the system image alone: the root hash is sealed in the
signed kernel command line, asserted non-empty at build time, and verified at boot. There is no
checksum fallback boot path. A whole-file sha256 digest still ships on the media, but the boot
path does not consult it — it is a user-facing media diagnostic, and the init script refuses to
boot when the sealed command line carries no root hash. (See
[`docs/operations/04-generating-squashfs.md`](operations/04-generating-squashfs.md) and
[`docs/operations/05-creating-iso.md`](operations/05-creating-iso.md).)

### 5. Pre-install evaluation

The ISO is booted on real hardware (or a Secure-Boot-capable test VM) and the live environment
is examined *before* an install is attempted: the Secure-Boot and integrity chain, the system and
user logs, failed units, the shipped security posture (the system ships locked — no remote access
enabled, default-deny firewall — by default), and the boot sequence end to end. A clean
structural smoke pass means the live system is healthy; it does **not** by itself guarantee a
clean install. (See
[`docs/operations/06-test-vm-and-evaluation.md`](operations/06-test-vm-and-evaluation.md).)

### 6. Install and post-install evaluation

The installer writes the system to the target disk, then the result is examined **exhaustively,
not as a spot-check.** An "install complete" banner is not a passing grade; the failures that
matter hide in the logs — a by-design-failing subprocess masking a real one, a degraded feature,
a silently skipped step. The full installer trace is read end to end (every step, every
non-success line), the install-run journals are read, the system is rebooted into the installed
target, and the installed system's logs and journals are read for every boot — confirming zero
failed units and a clean boot-chain trust record. Every operator observation and every log
anomaly becomes a tracked issue.

### 7. Track issues, then iterate

Findings are grouped into layers that are applied and validated together, so a regression
localizes to its layer. Each fix is tagged by *where it takes effect* — whether it is picked up
by re-running a late stage of the build, or requires a rebuild into the system image, or lives
outside the build entirely. For day-to-day iteration on one or a few packages against an
already-booted ISO, a single rebuilt package can be slipstreamed into the install set without
rebuilding the whole ISO — turning an hour-long rebuild into a one-file copy. **Every such
surgical edit is saved to the source tree and must prove itself on the next from-scratch build.**
No edit is allowed to ride un-reproduced; live-patching is allowed for diagnosis but is never
sufficient on its own. (See
[`docs/operations/10-iteration-resume-builds.md`](operations/10-iteration-resume-builds.md).)

### 8. Publish to the signed mirror

Built packages are published to the package mirror with a fully signed index: the index is the
signed manifest of the *whole* repository, so every publish regenerates and re-signs the complete
index, and clients verify the entire index against one signature. Data transfer is incremental —
unchanged packages are not re-sent — but the signed index is never partial. The live repository
is promoted by an atomic swap, so clients never see a half-published state. (See
[`docs/operations/first-publish-runbook.md`](operations/first-publish-runbook.md).)

### 9. Promote to a stable release

A candidate is promoted to a stable, golden release **only** when a full from-scratch cycle runs
end to end with **zero triggers** — nothing required a fix: the build completes, the ISO is
assembled, the live image boots, the installer boots, it installs cleanly on real hardware, and
first boot is correct end to end, with the signing step as the only human action. All boot and
install legs are validated on representative (lower-end) hardware, with race-class items cleared
over several consecutive cold boots. Anything short of that bar is a trigger: fix it in the tree
and keep iterating. (See
[`docs/operations/09-gbc-iteration-methodology.md`](operations/09-gbc-iteration-methodology.md)
and [`docs/operations/07-golden-builder-snapshot.md`](operations/07-golden-builder-snapshot.md).)

---

## The non-negotiables

- **Built from source.** The standard, transparent, from-source path — not opaque shortcuts.
- **No silent failures.** Every error surfaces; an unverified assumption is turned into a checked
  gate or it is not trusted.
- **No stubs.** A stub is a lie; placeholder code that pretends to work does not ship.
- **Reproducible.** Pinned, checksum-verified sources; deterministic builds.
- **Only a clean build counts.** A fix is done when it lives in the tree and a clean build
  reproduces the corrected behavior with zero manual steps — never on the strength of a
  hand-patched box.
- **Signed end to end.** A signed Secure-Boot chain, integrity-verified system image, and a
  signed package index — verification you can check yourself.
- **Decide with the lenses; don't hand off a false choice.** When the lenses converge, that is
  the decision.

---

## Where the mechanics live

| Concern | Guide |
|---|---|
| Why iteration exists | [`docs/operations/09-gbc-iteration-methodology.md`](operations/09-gbc-iteration-methodology.md) |
| Build-environment setup | [`docs/operations/01-build-vm-setup.md`](operations/01-build-vm-setup.md) |
| Running the builder (flags, phases, resume) | [`docs/operations/02-running-the-builder.md`](operations/02-running-the-builder.md) |
| Signing | [`docs/operations/03-automating-signing.md`](operations/03-automating-signing.md) |
| System image (squashfs) | [`docs/operations/04-generating-squashfs.md`](operations/04-generating-squashfs.md) |
| ISO assembly | [`docs/operations/05-creating-iso.md`](operations/05-creating-iso.md) |
| Test VM and evaluation | [`docs/operations/06-test-vm-and-evaluation.md`](operations/06-test-vm-and-evaluation.md) |
| Golden release snapshot | [`docs/operations/07-golden-builder-snapshot.md`](operations/07-golden-builder-snapshot.md) |
| Adding a package | [`docs/operations/08-adding-packages.md`](operations/08-adding-packages.md) |
| Resume / iteration builds | [`docs/operations/10-iteration-resume-builds.md`](operations/10-iteration-resume-builds.md) |
| Validation gates | [`docs/operations/11-resolving-validation-gates.md`](operations/11-resolving-validation-gates.md) |
| Mirror publishing | [`docs/operations/first-publish-runbook.md`](operations/first-publish-runbook.md) |

For the user-facing presentation of this methodology, see the **Developer & Contributor Guide**
in the [InterGenOS Wiki](https://wiki.intergenos.org).
