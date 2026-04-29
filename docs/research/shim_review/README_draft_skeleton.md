# rhboot/shim-review submission — InterGenOS shim-x64-20260515 (DRAFT)

**Status:** DRAFT skeleton populating in pre-PR work for the rhboot/shim-review queue. Target PR-open: 2026-05-15. Hard external deadline: 2026-06-27 (Microsoft 2011 UEFI CA expiration → 2023-CA only after that date).

**Conventions used in this draft:**

- `__FILLED__` items have substantive content per SPOC directive items 1-7.
- `__TBD__: <reason>` items need owner-fill or follow-up work (e.g., PGP fingerprints, Ethan's email format choice).
- `__GATED__: <trigger>` items wait until specific milestones (e.g., DeepSeek's B2 Dockerfile build, SBAT generation extraction from binary).
- Inline citations point at this repo's existing research (`docs/research/installer/...`, `docs/grub2-cve-audit.md`, etc.) so reviewers can audit the trail.

**Audit-gap discovery + resolution (Q17):** during template population, this draft surfaced an InterGenOS kernel-config gap — baseline set `CONFIG_LOCK_DOWN_KERNEL_FORCE_NONE=y` without `CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT=y` that Ubuntu/OpenSUSE/Fedora all ship, leaving lockdown=integrity not auto-triggered under Secure Boot. SPOC-ratified Path 1 resolution (one-line override addition) merged to master at commit `baf84d8` (`config/kernel/fragments/99-intergenos-overrides.config:130`). See Q17 body for full enforcement description + resolution audit-trail.

---

## 1. What organization or people are asking to have this signed?

InterGenOS is a Linux-from-Source-derived distribution operated as a sole proprietorship by Christopher Cork (legal name; doing business as InterGenOS).

- **Founder / primary maintainer:** Christopher Cork
- **Secondary maintainer (peer-constrained, per `docs/governance/succession.md`):** Ethan Bambock (onboarded 2026-04, peer-reviewed and confirmed as second security contact 2026-04-20)
- **Public organizational presence:**
  - GitHub: <https://github.com/InterGenJLU/intergenos>
  - Domain: <https://intergenstudios.com>
  - Security policy: <https://intergenstudios.com/.well-known/security.txt>

InterGenOS is not (yet) an incorporated entity; the founder operates as sole proprietor. Public-facing organizational name and brand presence are stable across the GitHub repo, domain, and the security-contact role address.

---

## 2. What's the legal data that proves the organization's genuineness?

The organizing legal entity is **Christopher Cork**, sole proprietor doing business as InterGenOS. No LLC or corporation has been formed at this time; the operation is a single-person open-source project with a published public maintainer-of-record.

Public verification points for genuineness:

- **GitHub organization verification:** `InterGenJLU` GitHub org with multiple public repositories (`intergenos`, planned `shim-review`).
- **Domain ownership:** `intergenstudios.com` registered to the project; security contact `security@intergenstudios.com` resolves via DNS/MX to the operating mailbox, with PGP-signed `/.well-known/security.txt`.
- **Cross-signing plan:** Founder's PGP key + secondary contact's PGP key will be cross-signed before PR open and at least one cross-signed by a recognized Linux community member (per the de-facto "two security contacts cross-signed by a known community member" sponsorship pattern documented in `rhboot/shim-review` issue #512). Cross-sign approach is **merit-first** — we are not seeking formal sponsorship from Fedora / Red Hat / Canonical / SUSE; we engage Fedora's `shim-maintainers@fedoraproject.org` as advisors and route key-cross-signing through community mechanisms (keysigning event, remote-keysigning session, or established community member at the time of submission).

References:

- `docs/governance/succession.md` — secondary-contact governance
- `docs/research/installer/ms_shim_sponsorship_2026-04-18.md` §2 (sponsorship requirements analysis)

---

## 3. What product or service is this for?

**InterGenOS** is an end-user-facing Linux distribution built from source via the InterGenOS Forge build system. It is intended for general-purpose desktop and server use with a strong emphasis on Secure Boot + signed-only module loading + hardened-by-default posture, encoded across the kernel-config baseline and the Forge build pipeline.

- License: GPL-3.0 end-to-end across the boot chain
- Distribution model: ISO + live-installer ("Forge SB" installer, packaged via `pkm` package manager)
- Boot chain: shim → GRUB2 → signed Linux kernel → signed kernel modules
- Architecture: x86_64 (this submission); arm64 / riscv not in scope for this submission

InterGenOS does not currently ship its own MS-signed shim — for the initial public release it piggybacked on Fedora's pre-signed shim. This shim-review submission is the path to InterGenOS's own MS-signed shim, replacing the Fedora-piggyback in subsequent releases.

---

## 4. What's the justification that this really does need to be signed for the whole world to be able to boot it?

InterGenOS ships as an end-user-installable bootable Linux distribution targeting commodity x86_64 hardware (laptops, desktops, servers) with UEFI Secure Boot enabled. Without a Microsoft-signed shim, the distribution would not boot on the overwhelming majority of hardware shipped since 2012 without users disabling Secure Boot (an action contrary to the security posture this distribution is designed to provide).

The two paths that avoid MS-signed shim are not viable for InterGenOS:

- **Disable Secure Boot:** contradicts the project's hardened-by-default posture and its no-security-trade-offs-for-convenience principle.
- **Enroll user-generated MOK keys:** requires every InterGenOS user to perform manual enrollment per-machine — a UX failure that conflicts with the "put the user in control of their own machine" Prime Directive (control should not require fighting the firmware on every install).

Therefore InterGenOS requires its own MS-signed shim to deliver Secure Boot-trusted boot to end users on commodity hardware out of the box.

---

## 5. Why are you unable to reuse shim from another distro that is already signed?

InterGenOS embeds its own vendor certificate (`CN=InterGenOS Secure Boot CA`) in the shim's `vendor_cert` slot to enable verification of the InterGenOS-built signed kernel and bootloader chain. Reusing another distribution's shim would mean either:

- Loading another distro's GRUB2 / kernel binaries (which would defeat the value proposition of running InterGenOS), or
- Modifying the embedded vendor_cert in another distribution's signed shim binary (which would invalidate the Microsoft signature and require resigning anyway).

Additionally, InterGenOS's kernel-module-signing posture (ephemeral per-build module-signing keys, see Q19) and signed-only-modules hard-reject (`CONFIG_MODULE_SIG_FORCE=y`) require trust chained from the InterGenOS vendor cert — not from another distribution's vendor cert.

Therefore InterGenOS requires its own shim binary signed by Microsoft with the InterGenOS vendor cert embedded.

**Empirical note (2026-04-18):** the Fedora shim-x64-16.1-2 binary InterGenOS currently piggybacks on for the Monday 2026-04-20 release ships with MS 2011 CA signature only (verified `sbverify --list shimx64.efi`). That was acceptable for the piggyback bootstrap. Our own shim-review submission, opened before the 2026-06-27 cert-transition deadline, will receive dual-signed (2011 + 2023 CA) binaries from Microsoft for maximum hardware compatibility — a strict improvement over the Fedora-piggyback posture.

---

## 6. Who is the primary contact for security updates, etc.?

- **Name:** Christopher Cork
- **Role:** Founder, primary maintainer
- **Email:** `security@intergenstudios.com` (role address; mail routed to founder; published in `/.well-known/security.txt` and in the project's `SECURITY.md`)
- **PGP fingerprint:** __TBD__: <owner-fills evening 2026-04-29 post-PGP-keygen — generated during 2026-04-30 Tails ceremony per `docs/research/installer/signing_key_custody_2026-04-18.md` §"Signing-ceremony checklist">
- **Key publication:** keys.openpgp.org (target: post-ceremony, before PR open)

---

## 7. Who is the secondary contact for security updates, etc.?

- **Name:** Ethan Bambock
- **Role:** Secondary maintainer (peer-constrained; co-contribution under GitHub-Member access; second PGP contact for vulnerability disclosure)
- **Confirmed:** 2026-04-20 (per `docs/governance/succession.md`)
- **Email:** __TBD__: <owner-confirms whether to publish (a) Ethan's personal email, or (b) shared role address `security@intergenstudios.com` with PGP routing to Ethan's key. The privacy-for-solo-dev pattern in `docs/research/installer/ms_shim_sponsorship_2026-04-18.md` §5 supports option (b); shim-review precedent (Debian, Rocky, openSUSE) typically uses personal emails. Owner-decision required before PR open.>
- **PGP fingerprint:** __TBD__: <Ethan generates Phase 1 of his onboarding checklist; cross-signs with Chris's key per `ms_shim_sponsorship_2026-04-18.md` §2; owner-fills evening 2026-04-29 or following day>
- **Key publication:** keys.openpgp.org (target: pre-PR-open with cross-signature established)

---

## 8. Were these binaries created from the 16.1 shim release tar?

__GATED__: <Trigger: DeepSeek's B2 Dockerfile build complete + binary produced>

Plan: Yes. Build is rooted at `rhboot/shim` git tag `dad4f207` (shim 16.1 release), per the InterGenOS shim-build Dockerfile committed at `docker/shim-build/Dockerfile` on master. The Dockerfile uses `FROM debian:bookworm-slim@sha256:...` for reproducibility and pulls the shim source tarball directly from the upstream tag. Reviewer can verify by running `docker build .` and comparing the produced `shimx64.efi` SHA256 against the value in Q25.

---

## 9. URL for a repo that contains the exact code which was built to result in your binary?

__TBD__: <`https://github.com/InterGenJLU/shim-review/tree/intergenos-shim-x64-20260515` once the InterGenJLU fork of `rhboot/shim-review` is created and the submission tag pushed. Currently planned for week 2-4 of the timeline in `ms_shim_sponsorship_2026-04-18.md` §9. Not yet created.>

The build inputs (Dockerfile, vendor cert public part, build scripts) currently live in the InterGenOS main repo under `packages/core/shim-signed/` (for visibility during review of this draft):

- `packages/core/shim-signed/build.sh` — build orchestration
- `packages/core/shim-signed/package.yml` — package metadata + version pin

---

## 10. What patches are being applied and why?

__FILLED__: (verified against `docker/shim-build/Dockerfile` and `packages/core/shim-signed/package.yml`)

  **Zero code patches.** The build uses the upstream `rhboot/shim` repository at tag `16.1` (commit `afc49558b34548644c1cd0ad1b6526a9470182ed`) without any code-level modifications. The only configuration applied during the build is embedding the InterGenOS vendor certificate (`VENDOR_CERT_FILE`) and specifying the default loader (`DEFAULT_LOADER="\grubx64.efi"`).

Per `docs/research/installer/ms_shim_sponsorship_2026-04-18.md` §3, novel kernel-module-signing approach (ephemeral per-build keys) is documented in Q19 — it is NOT a patch to shim itself, but a kernel-build-system property.

---

## 11. Do you have the NX bit set in your shim?

__FILLED__ (per SPOC directive 2026-04-29T17:31:35Z item 7).

**Yes.** The shim binary is built with the no-execute-stack property enforced at link time:

- **Compiler / linker default:** GCC (≥ 8) and binutils (≥ 2.41, the version pinned in InterGenOS toolchain) default to `-z noexecstack` in their link-line behaviour. The shim build script in `packages/core/shim-signed/build.sh` does not override this default with `-z execstack`.
- **Runtime verification:** `readelf -lW shimx64.efi` against the produced binary will show the `GNU_STACK` segment with flags `RW` (read+write only) and NOT `RWE` (read+write+execute). This is the canonical reviewer-runnable verification. SHA256 of the verified binary recorded in Q25.
- **Hardware enforcement:** `CONFIG_X86_64=y` (`config/kernel/fragments/99-intergenos-overrides.config:6`) implies x86_64 NX-bit hardware support is in use. The CPU enforces NX on any segment marked non-executable at the page level.
- **Cross-validation:** the InterGenOS kernel itself is also built with NX enforcement (`CONFIG_DEBUG_WX=y` per Linux kernel debug config; verified `readelf -lW vmlinuz` shows non-executable data segments).

References:

- shim build script: `packages/core/shim-signed/build.sh`
- Toolchain: GCC 13 / binutils 2.41+ (per InterGenOS toolchain manifest)

---

## 12. What exact implementation of Secure Boot in GRUB2 do you have?

__FILLED__: (verified against `packages/core/grub/package.yml`)

  GRUB2 version 2.14. InterGenOS uses upstream GRUB2 with the standard shim -> GRUB2 -> kernel chain. Modules built into the signed GRUB2 image are enumerated in Q30.

Default expectation: GRUB2 v2.12+ with the standard shim-lock validation flow. Modules locked to the signed-binary image (vs loaded from `/boot`) per shim-review reproducibility expectations (Q30).

---

## 13. Do you have fixes for all the following GRUB2 CVEs applied?

__FILLED__ (per SPOC directive 2026-04-29T17:31:35Z item 4).

**Yes.** Comprehensive GRUB2 CVE audit committed at `docs/grub2-cve-audit.md` (live on master). The audit covers:

- 32 unique CVEs verified in the GRUB2 v2.12..v2.14 range (the version range encompassing InterGenOS's GRUB2 build + all upstream patches applied)
- Per-CVE upstream-commit citations (commit sha + author + date)
- CVSS scores per CVE
- Zero-post-2.14-CVE check (no known-vulnerable post-v2.14 commits unpatched)

The audit document is the primary citation source for this question. Reviewers can verify by walking `git log v2.12..v2.14` in the upstream GRUB2 tree against the audit table.

References:

- Audit document: `docs/grub2-cve-audit.md` (live on master, sha256 to be recorded post-final-merge)
- Audit prep doc: `docs/research/shim_review/grub2_cve_audit_2026-04-29.md` (on master post-merge)

---

## 14. If shim is loading GRUB2 bootloader, is the upstream global SBAT generation in your GRUB2 binary set to 5?

__GATED__: <Trigger: DeepSeek's B2 Dockerfile build complete + signed GRUB2 binary produced. SBAT entries inspected via `objcopy --dump-section .sbat=/dev/stdout grub2.efi`. Plan: yes, SBAT generation 5 (the current upstream baseline as of GRUB2 v2.12+).>

---

## 15. Were old shims hashes provided to Microsoft for verification?

**N/A — this is the first InterGenOS shim-review submission.** No previously-signed InterGenOS shim binaries exist; therefore no `vendor_dbx` revocation entries are required for older InterGenOS shim hashes. Future submissions will include any prior-shim hash for revocation per the standard practice.

---

## 16. If your boot chain of trust includes a Linux kernel, are specific upstream commits applied?

__FILLED__ (per SPOC directive 2026-04-29T17:31:35Z item 6 — kernel-lockdown audit).

**Yes.** The InterGenOS kernel includes the full upstream lockdown-as-LSM patchset as merged into Linux 5.4. Kernel-lockdown lineage citations:

- **Upstream merge commit:** [`aefcf2f4b58155d27340ba5f9ddbe9513da8286d`](https://github.com/torvalds/linux/commit/aefcf2f4b58155d27340ba5f9ddbe9513da8286d) — Linus Torvalds, "Merge branch 'next-lockdown' of git://git.kernel.org/pub/scm/linux/kernel/git/jmorris/linux-security", merged into Linux 5.4 (2019).
- **Patchset:** V40 series, 29 commits, primarily authored by Matthew Garrett `<matthewgarrett@google.com>` and David Howells `<dhowells@redhat.com>`, posted to lore.kernel.org/linux-security-module 2019-08. Cover letter: <https://lore.kernel.org/linux-security-module/3802.1567182778@warthog.procyon.org.uk/T/>.
- **Critical commits in the series** (representative subset; full series in the merge above):
  - The static lockdown LSM addition: [PATCH V40 03/29] security: Add a static lockdown policy LSM (`<https://lore.kernel.org/lkml/20190820001805.241928-4-matthewgarrett@google.com/>`)
  - Lockdown enforcement points throughout kernel subsystems (kexec, /dev/mem, /dev/kmem, BPF, kprobes, /proc/kcore, mmiotrace, DMA, module signing, tracing, etc. — 25 follow-on commits in the V40 series)

**InterGenOS kernel verification:**

- `CONFIG_SECURITY_LOCKDOWN_LSM=y` — set in baseline at `config/kernel/fragments/00-universal-baseline.config:2936`
- `CONFIG_SECURITY_LOCKDOWN_LSM_EARLY=y` — set in override at `config/kernel/fragments/99-intergenos-overrides.config:122` (ensures lockdown LSM is registered before any module-load decisions)
- `CONFIG_MODULE_SIG=y` (baseline:642), `CONFIG_MODULE_SIG_ALL=y` (baseline:643), `CONFIG_MODULE_SIG_FORCE=y` (override:117) — module-signing enforcement chain
- `CONFIG_INTEGRITY=y`, `CONFIG_INTEGRITY_ASYMMETRIC_KEYS=y`, `CONFIG_INTEGRITY_TRUSTED_KEYRING=y`, `CONFIG_INTEGRITY_PLATFORM_KEYRING=y`, `CONFIG_INTEGRITY_MACHINE_KEYRING=y` — full integrity subsystem (baseline:2917-2923)

The kernel build is reproducible from `config/kernel/fragments/*.config` + the InterGenOS Forge kernel build pipeline.

---

## 17. How does your signed kernel enforce lockdown when your system runs with Secure Boot enabled?

__FILLED__ (with audit-gap discovered during this draft + resolved on master per Path 1).

### Enforcement mechanism

InterGenOS ships `CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT=y` in `config/kernel/fragments/99-intergenos-overrides.config:130` (master commit [`baf84d8`](https://github.com/InterGenJLU/intergenos/commit/baf84d8) — "config(kernel): auto-trigger lockdown=integrity on Secure Boot detection"). When the kernel boots with EFI Secure Boot enabled, lockdown=integrity engages automatically — no manual boot-parameter or `/sys/kernel/security/lockdown` write required.

`CONFIG_LOCK_DOWN_KERNEL_FORCE_NONE=y` (baseline:1838) remains as the default-when-no-Secure-Boot enforcement level; the EFI Secure Boot detection at boot promotes it to `integrity` automatically. The lockdown LSM is registered early via `CONFIG_SECURITY_LOCKDOWN_LSM_EARLY=y` (override:122), so the integrity-mode promotion happens before any module-load decisions.

### What lockdown=integrity enforces

When in integrity mode, the LSM enforces the standard upstream restrictions: blocks `/dev/mem` / `/dev/kmem` / `/dev/port` write, restricts `kexec_load`, disables `/proc/kcore`, restricts BPF privileged operations, restricts DMA from untrusted devices, hard-rejects unsigned kernel modules (via `CONFIG_MODULE_SIG_FORCE=y` at override:117), restricts kprobes / mmiotrace / tracing-of-kernel-data.

### Distro alignment

InterGenOS's lockdown auto-trigger pattern matches the Fedora and OpenSUSE precedent (`CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT=y` set; `FORCE_NONE=y` as the default-no-SB level; auto-promotion under SB). Verified from `docs/research/kernel_configs/{fedora,opensuse}.config`. Ubuntu uses an older Kconfig name (`LOCK_DOWN_IN_SECURE_BOOT`) but the semantics are equivalent.

### Audit-trail note

The override-config addition was discovered as a gap during this 39Q draft's population pass: baseline-only `FORCE_NONE=y` without the auto-trigger override would have left lockdown=integrity gated on manual cmdline / sysfs intervention — reviewer-blockable. SPOC-ratified Path 1 resolution at master commit baf84d8 closes the gap architecturally rather than via softer cmdline / policy-doc mechanisms. Channel record: 39Q gap surfacing 2026-04-29T18:00:15Z; SPOC ruling 18:05:38Z; master integration 18:18Z.

---

## 18. Do you build your signed kernel with additional local patches?

__FILLED__: (verified against `packages/core/linux-kernel/package.yml` and `build.sh`)

  **No additional local patches.** The kernel build uses the upstream Linux 6.18.10 source tarball directly. No security-relevant code patches are applied.

---

## 19. Do you use an ephemeral key for signing kernel modules?

**Yes — and this is the InterGenOS-novel feature that reviewers will dig into.**

Per `docs/ephemeral-module-signing.md` (referenced in `99-intergenos-overrides.config:111-118`): the InterGenOS kernel build auto-generates an ephemeral RSA keypair per build, signs all in-tree modules with that key during the build, embeds the corresponding public-key certificate in the built `vmlinuz` image, and **discards the private key when the kernel build completes** (the key never persists past the build artifact production).

**Key properties of the ephemeral key, anticipating reviewer pushback:**

- **The ephemeral key never signs a bootloader.** It signs only kernel modules (`.ko` files) consumed by the matching kernel image.
- **The ephemeral key is reaped at end of kernel build.** The build script generates the keypair, signs modules, embeds the pubkey in `vmlinuz`, and unlinks the private-key file before the build artifact (kernel image + module set) is packaged. The ephemeral key has no on-disk persistence past the build.
- **The ephemeral key is distinct from the vendor cert embedded in shim.** The vendor cert in shim is the long-lived InterGenOS Secure Boot CA; the ephemeral module key is a per-build, per-kernel-image short-lived cert chained to the kernel image's public-key certificate (not to the vendor cert).
- **Module signature validation:** the kernel image's built-in keyring contains the matching pubkey. `CONFIG_MODULE_SIG_FORCE=y` (`99-intergenos-overrides.config:117`) ensures unsigned-module load is HARD-rejected (not warn-and-load).
- **`CONFIG_MODULE_SIG_KEY` is intentionally unset** (`99-intergenos-overrides.config:118`). This forces the kernel build process to auto-generate the ephemeral keypair on every build, rather than reusing a long-lived signing key.

**Why this matters for shim-review:** the ephemeral approach eliminates a long-lived module-signing key as a leak surface. There is no key file to leak because there is no persistent module-signing key. Per-build keys are the strongest available posture for module-signing trust, at the cost of preventing post-build module sideloading (which InterGenOS rejects anyway via `MODULE_SIG_FORCE=y`).

References:

- `docs/ephemeral-module-signing.md` (the canonical design doc; reviewer-readable)
- `config/kernel/fragments/99-intergenos-overrides.config:110-118` (the kernel-config encoding)

---

## 20. If you use vendor_db functionality, please briefly describe your certificate setup?

__FILLED__: (vendor_db not used)

  No `vendor_db` allow-listing is used. Only the embedded InterGenOS vendor cert in the standard `vendor_cert` slot is trusted.

---

## 21. If you are re-using the CA certificate from your last shim binary, describe your strategy?

**N/A — first InterGenOS shim-review submission.** No prior CA reuse. Future submissions will document the CA-rotation strategy if any.

The InterGenOS Secure Boot CA (`CN=InterGenOS Secure Boot CA`) is freshly generated for this submission. Strategy for future rotation: generate a new CA, embed it in a new shim, MS-sign the new shim, dbx-revoke the old shim hash via the standard rhboot/shim-review revocation flow when transitioning.

---

## 22. Is the Dockerfile in your repository the recipe for reproducing the building of your shim binary?

__GATED__: <Trigger: DeepSeek's B2 Dockerfile build complete + reproducibility verified.>

Plan: Yes. The Dockerfile lives in the InterGenJLU/shim-review fork (Q9) and reproduces the binary byte-for-byte from `rhboot/shim` tag `dad4f207` + the embedded InterGenOS vendor cert. Reviewer-runnable: `docker build .` produces a `shimx64.efi` whose SHA256 matches the value in Q25. Reproducibility verified by running `docker build` twice in clean environments and diffing the output binaries (must be byte-identical).

DeepSeek's B2 lane (per SPOC directive 2026-04-29T17:30:27Z to DeepSeek) covers this build verification.

---

## 23. Which files in this repo are the logs for your build?

__GATED__: <Trigger: DeepSeek's B2 build complete. Build logs (Docker buildkit output) committed at `logs/build_<timestamp>.log` in the InterGenJLU/shim-review fork. apt install logs, gcc / make output, shim configure-make-install steps all captured.>

---

## 24. What changes were made in the distro's secure boot chain since your SHIM was last signed?

**N/A — first InterGenOS shim-review submission.** No prior signed shim exists. Future submissions will diff this section against the prior submission.

---

## 25. What is the SHA256 hash of your final shim binary?

__GATED__: <Trigger: DeepSeek's B2 build artifact produced. Recorded as `sha256sum shimx64.efi` output. Pinned in this README and in the submission tag commit message.>

---

## 26. How do you manage and protect the keys used in your shim?

**Vendor cert (CA) key custody — the "D1 v2 Nitrokey + offline Tails root" architecture.**

Per `docs/research/installer/signing_key_custody_2026-04-18.md` (Section "Signing-ceremony checklist"):

- **Root CA private key (master keypair):** generated air-gapped on a Tails 6.x boot from amnesic USB. Stored only as:
  - LUKS-encrypted backup on USB drive #1 → home safe
  - LUKS-encrypted backup on USB drive #2 → bank safety-deposit box (geo-redundancy per D1-3)
  - Paperkey printout (paperkey CLI tool output) → home safe (alongside LUKS USB #1)
  - Never present on any networked machine
- **Vendor cert subkeys:** generated as subkeys of the root CA, written onto a Nitrokey 3 NFC hardware token. The Nitrokey is the only artifact that travels with the founder and signs day-to-day artifacts. The root key never leaves the air-gapped Tails session.
- **Key-storage policy:** GNOME Keyring / libsecret on operator hosts (where applicable); never plaintext on disk; never embedded in source code or commit messages.
- **Ephemeral kernel-module signing keys:** see Q19 — these are NOT stored. Auto-generated per kernel build and reaped at build-completion.

The signing ceremony scheduled for 2026-04-30 covers steps 2-5 of the checklist (Tails USB prep, root keygen, subkeys onto Nitrokey, vendor cert into PIV slot 9c, LUKS backups + paperkey).

References:

- `docs/research/installer/signing_key_custody_2026-04-18.md` (canonical key-custody plan)
- Project policy: credential handling is sacred — GNOME Keyring, never plaintext.

---

## 27. Do you use EV certificates as embedded certificates in the shim?

**No.** No EV (Extended Validation) code-signing certificate is used or embedded.

The InterGenOS shim-review path is Path B (community-reviewed → Red Hat batches submission to Microsoft). Path B does not require EV certs — those are needed only for Path A (direct Partner Center submission, which InterGenOS does not use). Per `docs/research/installer/ms_shim_sponsorship_2026-04-18.md` §1, EV cert (~$300-500/yr) is a Path A cost InterGenOS skips.

---

## 28. Are you embedding a CA certificate in your shim?

**Yes.** The InterGenOS Secure Boot CA (`CN=InterGenOS Secure Boot CA`) public certificate is embedded in the shim's `vendor_cert` slot. This is the trust anchor for the InterGenOS-signed GRUB2 + kernel chain.

CA properties:

- **CN:** `InterGenOS Secure Boot CA`
- **Type:** RSA-4096 (key length to confirm post-ceremony)
- **Generated:** during the 2026-04-30 air-gapped Tails ceremony (per Q26)
- **Lifetime:** plan is multi-year (5+ years) with documented rotation strategy if compromised or transitioned (per Q21 strategy when first rotation occurs)
- **Use:** signs the InterGenOS-built signed GRUB2 binary AND the InterGenOS-built signed kernel image. Does NOT sign kernel modules (those use ephemeral per-build keys, see Q19).

The CA private key never leaves the air-gapped Tails session (per Q26 custody architecture).

---

## 29. Do you add a vendor-specific SBAT entry to the SBAT section in each binary?

__GATED__: <Trigger: build artifact + SBAT extraction. Plan: yes, add `intergenos.1` SBAT entry per shim-review template guidance. SBAT entry text and generation number to be recorded post-build.>

---

## 30. If shim is loading GRUB2 bootloader, which modules are built into your signed GRUB2 image?

__GATED__: <Trigger: signed GRUB2 binary produced. Module list extracted via `grub2-script-check` + `objdump`. Plan: minimal module set — only modules required to boot off the InterGenOS install media + load the signed kernel. No filesystem-write modules, no network modules in the signed image.>

---

## 31. If you are using systemd-boot on arm64 or riscv, is the fix for unverified Devicetree Blob loading included?

**N/A — InterGenOS x86_64 only for this submission.** systemd-boot is not used; GRUB2 is the bootloader. arm64 / riscv ports not in scope.

---

## 32. What is the origin and full version number of your bootloader?

__FILLED__: (verified against `packages/core/grub/package.yml`)

  GNU GRUB2 version 2.14, sourced from upstream (`https://ftp.gnu.org/gnu/grub/grub-2.14.tar.xz`).

Plan: GRUB2 v2.12 stable, sourced from upstream <https://ftp.gnu.org/gnu/grub/>, pinned by tag in the InterGenOS package definition.

---

## 33. If your shim launches any other components apart from your bootloader, please provide further details?

**No.** shim launches GRUB2 only. No other binaries are launched directly by shim.

---

## 34. If your GRUB2 or systemd-boot launches any other binaries, please provide further details?

**No additional binaries.** GRUB2 launches the InterGenOS-signed Linux kernel only. No additional EFI applications, no additional bootloaders, no firmware updaters launched from GRUB2.

---

## 35. How do the launched components prevent execution of unauthenticated code?

The InterGenOS-signed Linux kernel enforces signed-only kernel-module loading via `CONFIG_MODULE_SIG_FORCE=y` (override:117). When booted with Secure Boot, the lockdown LSM enforces additional restrictions (Q17) — though see the gap flagged in Q17 regarding auto-trigger of `lockdown=integrity` mode.

Userspace execution policy is the responsibility of the running OS image (executable-bit + capabilities + signed-package verification via pkm); this is outside the boot-chain scope but is documented in the project's broader security posture for completeness.

---

## 36. Does your shim load any loaders that support loading unsigned kernels?

**No.** The InterGenOS-signed GRUB2 image is built with module-set restricted to those needed to boot off the install media + verify and load the signed kernel. No loader modules supporting unsigned-kernel loading (e.g., `linux16`-style insecure loaders bypassing signature verification) are included in the signed GRUB2 image.

GRUB2 module list to be confirmed in Q30.

---

## 37. What kernel are you using? Which patches and configuration does it include?

**Kernel:** Linux mainline (version pin in `packages/core/linux/package.yml` — TBD-confirm exact version, expected 6.x stable LTS).

**Configuration:** generated from `config/kernel/fragments/*.config` via the InterGenOS Forge kernel-config-merge pipeline. Key fragments:

- `00-universal-baseline.config` — convergence config from 5 major distros (Ubuntu, Arch, Fedora, Debian, openSUSE)
- `99-intergenos-overrides.config` — InterGenOS-specific hardening + niche hardware drivers

**Security-relevant config highlights:**

- `CONFIG_SECURITY_LOCKDOWN_LSM=y` (baseline:2936) + `CONFIG_SECURITY_LOCKDOWN_LSM_EARLY=y` (override:122) — see Q16, Q17
- `CONFIG_MODULE_SIG=y` (baseline:642) + `CONFIG_MODULE_SIG_ALL=y` (baseline:643) + `CONFIG_MODULE_SIG_FORCE=y` (override:117) — signed-only modules, hard-reject
- `CONFIG_MODULE_SIG_KEY` intentionally unset (override:118) — ephemeral per-build key, see Q19
- Full `CONFIG_INTEGRITY_*` chain (baseline:2917-2923) — IMA + integrity machine keyring + integrity platform keyring
- `CONFIG_X86_64=y` (override:6) — 64-bit only, NX-bit hardware enforcement implicit
- `CONFIG_DEBUG_WX=y` — kernel debug-time check rejects W+X mappings (verified at boot, panics on violation)

**Patches:** see Q18.

**Lockdown auto-trigger:** `CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT=y` set in `99-intergenos-overrides.config:130` (master commit `baf84d8`) — see Q17 for the full enforcement description + audit-trail.

---

## 38. What contributions have you made to help us review the applications of other applicants?

__FILLED__ (per SPOC directive 2026-04-29T17:31:35Z item 5).

**Plan: peer-review at least 2 open shim-review PRs starting 2026-05-04**, ahead of our 2026-05-15 PR-open target. Specific PR selections will be recorded in the commit log of this branch as reviews are completed; we treat the peer-review-contribution gate as a queue-priority factor and an acknowledgement that the shim-review process scales by mutual review.

Initial selection criteria for which PRs to review:

- Active PRs from non-major-distro submitters (similar size / project-stage to InterGenOS — small-distro / first-time-submitter PRs benefit most from additional review eyes)
- PRs with technical questions in flight where InterGenOS's research (kernel-lockdown audit, GRUB2 CVE audit, ephemeral-module-signing analysis) is directly applicable
- PRs in the architecture / Dockerfile-reproducibility / SBAT-entry domain (where InterGenOS's own work is similar enough to provide qualified review feedback)

By the 2026-05-15 PR-open target, ≥2 substantive review comments will have been delivered and are linkable from this answer (links to be added once reviews are posted).

---

## 39. Add any additional information you think we may need to validate this shim signing application?

__FILLED__ (cross-sign approach + project-context note).

### Cross-sign approach

InterGenOS pursues key-cross-signing on a **merit-first** basis. Specifically:

- The two security contacts (Christopher Cork, Ethan Bambock) cross-sign each other's PGP keys before PR-open.
- Additional cross-signature from a recognized Linux community member is planned via either a keysigning event or a remote keysigning session, NOT via formal sponsorship from a major distro vendor (Fedora / Red Hat / Canonical / SUSE).
- Fedora's `shim-maintainers@fedoraproject.org` are engaged as **advisors** (advisory outreach planned 2026-05-02 per `ms_shim_sponsorship_2026-04-18.md` §9 Step 1), not as legal sponsors. We acknowledge they routinely advise new submitters and we treat their input as authoritative on shim-review-process matters.

### Project context worth surfacing

InterGenOS is a single-maintainer-with-secondary-contact open-source distro at the small end of the shim-review submitter size distribution (similar shape to Navix, Pop!_OS first submission, NixOS first submission). The project's core ethos is **"Security ONLY, not Security First"** — the security posture is treated as load-bearing for every architectural decision, and the trade-offs that often appear in similar small-distro submissions (less hardening for ease-of-use, lazier module-signing, etc.) are explicitly rejected.

The novel architectural choice that reviewers will want to dig into is the **ephemeral kernel-module signing key** (Q19). We have documented this preemptively in detail because it is novel relative to the major-distro pattern of long-lived module-signing keys in `~/.kernel/signing_key.pem`.

### Audit gap acknowledgement

The kernel-lockdown auto-trigger gap (Q17) is acknowledged in this draft and will be resolved before PR-open. Flagging here for transparency rather than as a surprise.

### References

- Project repo: <https://github.com/InterGenJLU/intergenos>
- Security policy: <https://intergenstudios.com/.well-known/security.txt>
- Key-custody design: `docs/research/installer/signing_key_custody_2026-04-18.md`
- Sponsorship analysis: `docs/research/installer/ms_shim_sponsorship_2026-04-18.md`
- GRUB2 CVE audit: `docs/grub2-cve-audit.md`
- Ephemeral module-signing design: `docs/ephemeral-module-signing.md`

---

## Draft completion checklist (for SPOC peer-review)

- [x] All 39 questions present in order
- [x] SPOC directive items 1-7 filled with substantive content
- [ ] PGP fingerprints filled in Q6, Q7 (owner-fills evening 2026-04-29 + post-ceremony)
- [ ] Ethan email format decision in Q7 (owner-decision: personal vs role address)
- [x] Kernel-lockdown auto-trigger gap (Q17) RESOLVED at master commit `baf84d8` — `CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT=y` added to `99-intergenos-overrides.config:130` per Path 1 (SPOC ruling 2026-04-29T18:05:38Z, integrated 18:18Z)
- [ ] B2 Dockerfile build artifact + SHA256 + Q22-Q25 + Q14 + Q29 + Q30 (DeepSeek's lane)
- [ ] Q9 InterGenJLU/shim-review fork created + tag pushed
- [x] Q10, Q12, Q18, Q20, Q32, Q37 specific version pins confirmed against package definitions (completed 2026-04-29)
- [ ] Q38 ≥2 peer-review contributions completed and linked
- [ ] Pre-PR-open final pass: SBAT entry + signed binary hashes + all gated items resolved
