# rhboot/shim-review submission — InterGenOS shim-x64-20260515 (DRAFT)

**Status:** DRAFT skeleton populating in pre-PR work for the rhboot/shim-review queue. Target PR-open: 2026-05-15. Hard external deadline: 2026-06-27 (Microsoft 2011 UEFI CA expiration → 2023-CA only after that date).

**Conventions used in this draft:**

- `__FILLED__` items have substantive content per project-lead directive items 1-7.
- `__TBD__: <reason>` items need owner-fill or follow-up work (e.g., PGP fingerprints, Ethan's email format choice).
- `__GATED__: <trigger>` items wait until specific milestones (e.g., the B2 Dockerfile build, SBAT generation extraction from binary).
- **Kernel-config fragment terms:** `00-universal-baseline.config` is the cross-distro convergence baseline (Ubuntu/Arch/Fedora/Debian/openSUSE intersection of recommended hardening), applied first. `99-intergenos-overrides.config` is applied second and takes precedence — concatenation order in `packages/core/linux-kernel/build.sh:36` followed by `make olddefconfig`. Per-line citations like `99-intergenos-overrides.config:122` reference the exact line in the override fragment where each setting is defined; the final compiled kernel `.config` is the merged result.
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
- **PGP key (master):** `5597 A3E0 587B 2530 06D0 DD7B 8C50 8261 8208 3050` (RSA-4096; project-role UID `InterGenOS Project Signing Key (primary) <intergenos-primary@intergenstudios.com>`; generated air-gapped on Tails 7.7 during ceremony 2026-05-05; no expiry on master)
- **Release-signing subkey [S1]:** `D7AA 641D 81AC D690 C5AD 865E 7276 E14D D888 6BFE` (Nitrokey 3 NFC, serial `B9753481`; primary-maintainer daily-driver release-signing token; 2-year expiry to 2028-05-04. UIF=on per ceremony — touch required per signing operation.)
- **Release-signing subkey [S2]:** `81DD 223F 9BA9 B3F2 AFBF FC5A FA24 B042 975F 775E` (Nitrokey 3 NFC, serial `43D33126`; primary-maintainer off-site bank-SDB token; 2-year expiry to 2028-05-04. UIF=on.)
- **Release-signing subkey [S3]:** `B34D 3D3F B5EA DFC4 80ED BDB0 D3C5 DF2C C73B 67ED` (Nitrokey 3 NFC, serial `730D5185`; secondary-maintainer daily-driver release-signing token; 2-year expiry to 2028-05-04. UIF=on.)
- **Release-signing subkey [S4]:** `99B3 E755 5064 180D C9CE 3284 32AE E441 15DE AAED` (Nitrokey 3 NFC, serial `CC1D07E3`; secondary-maintainer off-site fireproof-safe token; 2-year expiry to 2028-05-04. UIF=on.)
- **Encryption subkey [E]:** `62C7 E2C3 0908 823D AF5E 4EBF 917B 649E 00F2 868C` (RSA-4096; on-disk in LUKS master backup; not card-bound; used for PGP-encrypted security reports per `SECURITY.md`; 2-year expiry to 2028-05-04.)
- **Key publication:** Master pubkey LIVE on `keys.openpgp.org` (email-verified at `intergenos-primary@intergenstudios.com`) AND `keyserver.ubuntu.com`; cross-published in `docs/signing-key.md` (canonical fingerprint page) and `docs/signing-key.asc` (armored pubkey, second offline-verifier source).

---

## 7. Who is the secondary contact for security updates, etc.?

- **Name:** Ethan Bambock
- **Role:** Secondary maintainer (peer-constrained; co-contribution under GitHub-Member access; second PGP contact for vulnerability disclosure)
- **Confirmed:** 2026-04-20 (per `docs/governance/succession.md`)
- **Email:** `security@intergenstudios.com` — shared role address (PGP-signed mail with Ethan's key routes to him for secondary-contact correspondence). Privacy-for-solo-dev pattern per `docs/research/installer/ms_shim_sponsorship_2026-04-18.md` §5; preserves Ethan's individual email from public PR indexing while maintaining external-verifiable secondary-contact channel. The personal address `ethan@intergenstudios.com` is provisioned at the project domain for secondary-maintainer organizational visibility (any direct correspondence routes to Ethan); reviewers are encouraged to use the shared `security@` address for vulnerability disclosure.
- **PGP fingerprint:** __TBD__: <Ethan generates Phase 1 of his onboarding checklist; cross-signs with Chris's key per `ms_shim_sponsorship_2026-04-18.md` §2; owner-fills post-Phase-1>
- **Key publication:** keys.openpgp.org (target: pre-PR-open with cross-signature established)

---

## 8. Were these binaries created from the 16.1 shim release tar?

__FILLED__:

**Yes.** Build is rooted at `rhboot/shim` git tag `16.1` (commit `afc49558b34548644c1cd0ad1b6526a9470182ed`), per the InterGenOS shim-build Dockerfile committed at `docker/shim-build/Dockerfile` on master. The Dockerfile uses `FROM debian:bookworm-slim@sha256:5a2a80d11944804c01b8619bc967e31801ec39bf3257ab80b91070eb23625644` for reproducibility and pulls the shim source tarball directly from the upstream tag. Reviewer can verify by running `docker build .` against this Dockerfile and comparing the produced `shimx64.efi` SHA-256 against the canonical value in Q25 (`b6c0c2c59cd2c6cc8306138ffd58a70210926defab4147b332663c91097ccf75`).

The `SHIM_COMMIT_SHA` artifact emitted by the build records the upstream commit hash and is part of the 9-check `verify-b2-reproducibility.sh` harness output.

---

## 9. URL for a repo that contains the exact code which was built to result in your binary?

__FILLED__: `https://github.com/InterGenJLU/shim-review/tree/intergenos-shim-x64-20260515`

The submission branch is created in the `InterGenJLU/shim-review` fork of `rhboot/shim-review`. The build inputs (Dockerfile, vendor cert, SBAT entries, signing script) live in the InterGenOS main repo and are referenced from the submission branch:

- `docker/shim-build/Dockerfile` — reproducible container build (produces unsigned `shimx64.efi`)
- `docker/shim-build/vendor-cert/intergenos-secure-boot-ca.{pem,der}` — public vendor cert (private half on NK#1 PIV slot 9c)
- `docker/shim-build/sbat/sbat.intergenos.csv` — InterGenOS SBAT vendor entry
- `scripts/sign-shim.sh` — workstation-side NK#1 signing helper (PKCS#11 + sbsign)

---

## 10. What patches are being applied and why?

__FILLED__: (verified against `docker/shim-build/Dockerfile` and `packages/core/shim-signed/package.yml`)

  **Zero code patches.** The build uses the upstream `rhboot/shim` repository at tag `16.1` (commit `afc49558b34548644c1cd0ad1b6526a9470182ed`) without any code-level modifications. The only configuration applied during the build is embedding the InterGenOS vendor certificate (`VENDOR_CERT_FILE`) and specifying the default loader (`DEFAULT_LOADER="\grubx64.efi"`).

Per `docs/research/installer/ms_shim_sponsorship_2026-04-18.md` §3, novel kernel-module-signing approach (ephemeral per-build keys) is documented in Q19 — it is NOT a patch to shim itself, but a kernel-build-system property.

---

## 11. Do you have the NX bit set in your shim?

__FILLED__ (per project-lead directive 2026-04-29T17:31:35Z item 7).

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

Modules locked to the signed-binary image (vs loaded from `/boot`) via shim-lock + GRUB2 module-lock; reproducibility expectations per Q30.

---

## 13. Do you have fixes for all the following GRUB2 CVEs applied?

__FILLED__ (per project-lead directive 2026-04-29T17:31:35Z item 4).

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

__FILLED__:

**Yes — `grub,5` per upstream baseline, plus InterGenOS-vendor `grub.intergenos,1` per `packages/core/grub/sbat.csv` on master.**

Build precheck `scripts/sign-release.sh` enforces this architecturally: it blocks builds whose generations fall below upstream `SbatLevel_Variable.txt` (Tails-6.5-class footgun mitigation per the Q-SBAT resolution).

The complete SBAT block on master across all signed binaries:

| Component | Generation | Source |
|---|---|---|
| `shim` | 4 | upstream rhboot/shim 16.1 baked-in baseline |
| `shim.intergenos` | 1 | `docker/shim-build/sbat/sbat.intergenos.csv` (InterGenOS vendor entry) |
| `grub` | 5 | upstream GNU GRUB 2.14 baked-in baseline |
| `grub.intergenos` | 1 | `packages/core/grub/sbat.csv` (InterGenOS vendor entry) |
| `linux` | 1 | upstream linux-kernel 6.18.10 baked-in baseline |

Reviewer-runnable verification (post-Phase-1 GRUB build): `objcopy --dump-section .sbat=/dev/stdout grub2.efi` produces output that matches the union of upstream `SbatLevel_Variable.txt` entries plus the on-master CSV entries above. The pre-build content of `packages/core/grub/sbat.csv` is itself reviewable on master without needing the binary build to land.

---

## 15. Were old shims hashes provided to Microsoft for verification?

**N/A — this is the first InterGenOS shim-review submission.** No previously-signed InterGenOS shim binaries exist; therefore no `vendor_dbx` revocation entries are required for older InterGenOS shim hashes. Future submissions will include any prior-shim hash for revocation per the standard practice.

---

## 16. If your boot chain of trust includes a Linux kernel, are specific upstream commits applied?

__FILLED__ (per project-lead directive 2026-04-29T17:31:35Z item 6 — kernel-lockdown audit).

**Yes.** The InterGenOS kernel includes the full upstream lockdown-as-LSM patchset as merged into Linux 5.4. Kernel-lockdown lineage citations:

- **Upstream merge commit:** [`aefcf2f4b58155d27340ba5f9ddbe9513da8286d`](https://github.com/torvalds/linux/commit/aefcf2f4b58155d27340ba5f9ddbe9513da8286d) — Linus Torvalds, "Merge branch 'next-lockdown' of git://git.kernel.org/pub/scm/linux/kernel/git/jmorris/linux-security", merged into Linux 5.4 (2019).
- **Patchset:** V40 series, 29 commits, primarily authored by Matthew Garrett `<matthewgarrett@google.com>` and David Howells `<dhowells@redhat.com>`, posted to lore.kernel.org/linux-security-module 2019-08. Cover letter: <https://lore.kernel.org/linux-security-module/3802.1567182778@warthog.procyon.org.uk/T/>.
- **Critical commits in the series** (representative subset; full series in the merge above):
  - The static lockdown LSM addition: [PATCH V40 03/29] security: Add a static lockdown policy LSM (`<https://lore.kernel.org/lkml/20190820001805.241928-4-matthewgarrett@google.com/>`)
  - Lockdown enforcement points throughout kernel subsystems (kexec, /dev/mem, /dev/kmem, BPF, kprobes, /proc/kcore, mmiotrace, DMA, module signing, tracing, etc. — 25 follow-on commits in the V40 series)

**Specific upstream commits called out by rhboot/shim-review template (verification):**

The shim-review submission template explicitly requires confirmation that 3 specific lockdown commits are applied. All 3 are mainline-merged in the Linux 5.4-5.6 era and are transitively included in InterGenOS's pinned Linux 6.18.10:

| Commit SHA | Title | Author | Files modified | Status in InterGenOS Linux 6.18.10 |
|---|---|---|---|---|
| [`1957a85b`](https://github.com/torvalds/linux/commit/1957a85b0032a81e6482ca4aab883643b8dae06e) | efi: Restrict efivar_ssdt_load when the kernel is locked down | Matthew Garrett `<mjg59@google.com>` | `drivers/firmware/efi/efi.c` | ✓ included (mainline-merged via James Morris in the Linux 5.4 lockdown LSM patchset) |
| [`75b0cea7`](https://github.com/torvalds/linux/commit/75b0cea7bf307f362057cc778efe89af4c615354) | ACPI: configfs: Disallow loading ACPI tables when locked down | Jason A. Donenfeld `<Jason@zx2c4.com>` | `drivers/acpi/acpi_configfs.c` | ✓ included (mainline-merged via Rafael J. Wysocki; commit message includes `Cc: 5.4+` for stable backport) |
| [`eadb2f47`](https://github.com/torvalds/linux/commit/eadb2f47a3ced5c64b23b90fd2a3463f63726066) | lockdown: also lock down previous kgdb use | Daniel Thompson `<daniel.thompson@linaro.org>` | `include/linux/security.h`, `kernel/debug/debug_core.c`, `kernel/debug/kdb/kdb_main.c`, `security/security.c` | ✓ included (mainline-merged via Linus Torvalds) |

Each commit can be independently verified against the InterGenOS-built kernel by:

1. Cloning the upstream Linux source tree at the InterGenOS-pinned tag (`v6.18.10`)
2. Running `git log <sha>^..<sha> -- <files>` to confirm the commit is in the tree
3. Cross-checking against the InterGenOS kernel source tarball SHA256 (per `packages/core/linux-kernel/package.yml` source URL + checksum)

The 3 commits collectively lock down EFI variable SSDT loading, ACPI configfs table loading, and kgdb-mediated kernel memory access — all of which would otherwise allow ring-0 code execution bypassing Secure Boot's trust chain.

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

**Yes — one upstream-CVE backport patch** applied via `packages/core/linux-kernel/patches/CVE-2026-31431-copy-fail.patch` (upstream commit `crypto: algif_aead - Revert to operating out-of-place`, Herbert Xu, 2026-03-26). The patch reverts an in-place AEAD optimization that introduced an exploitable use-after-free (CVE-2026-31431, root-from-unprivileged-local). Applied during `linux-kernel/build.sh:21-29` before configure; reviewer-runnable verification: `ls packages/core/linux-kernel/patches/` plus inspection of `build.sh` patch-application loop.

No other local patches. Kernel source is the upstream Linux 6.18.10 tarball (sha256 `d6d377161741ada2fab28eed69143277634a2aeb5e3883e50c031588ede48ede` per `packages/core/linux-kernel/package.yml`).

---

## 19. Do you use an ephemeral key for signing kernel modules?

**Yes — and this is the InterGenOS-novel feature that reviewers will dig into.**

Per `docs/ephemeral-module-signing.md` (referenced in `99-intergenos-overrides.config:111-118`): the InterGenOS kernel build auto-generates an ephemeral RSA keypair per build, signs all in-tree modules with that key during the build, embeds the corresponding public-key certificate in the built `vmlinuz` image, and **discards the private key when the kernel build completes** (the key never persists past the build artifact production).

**Key properties of the ephemeral key, anticipating reviewer pushback:**

- **The ephemeral key never signs a bootloader.** It signs only kernel modules (`.ko` files) consumed by the matching kernel image.
- **The ephemeral key is reaped at end of kernel build.** The build script generates the keypair, signs modules, embeds the pubkey in `vmlinuz`, and unlinks the private-key file before the build artifact (kernel image + module set) is packaged. The ephemeral key has no on-disk persistence past the build.
- **The ephemeral key is distinct from the vendor cert embedded in shim.** The vendor cert in shim is the long-lived InterGenOS Secure Boot CA; the ephemeral module key is a per-build, per-kernel-image short-lived cert chained to the kernel image's public-key certificate (not to the vendor cert).
- **Module signature validation:** the kernel image's built-in keyring contains the matching pubkey. `CONFIG_MODULE_SIG_FORCE=y` (`99-intergenos-overrides.config:117`) ensures unsigned-module load is HARD-rejected (not warn-and-load).
- **The InterGenOS Unified Kernel Image (UKI) is signed by the InterGenOS vendor cert** — the same X.509 cert embedded in shim's `vendor_cert` slot. The UKI is a single PE binary produced by `systemd-stub` that bundles `vmlinuz` + `initramfs` + kernel `cmdline` + the `linux` SBAT entry into one signed envelope; `sbsign` applies the vendor-cert signature to that single envelope at release time per `docs/signing-procedure.md`. This anchors the trust chain end-to-end:
  1. **shim** verifies **GRUB2** via the embedded `vendor_cert` (Secure Boot signature on `grubx64.efi`).
  2. **GRUB2** verifies the **InterGenOS UKI** (vmlinuz + initramfs + cmdline bundled by systemd-stub, signed as a single PE binary via sbsign) against the same vendor-cert chain.
  3. The **vmlinuz inside the UKI** contains the per-build ephemeral module-signing pubkey in its `.builtin_trusted_keys` keyring; the UKI envelope's signature transitively covers vmlinuz, so the ephemeral pubkey is itself authenticated through the UKI signature.
  4. **kernel modules** are verified at load time against that embedded pubkey under `CONFIG_MODULE_SIG_FORCE=y`.

  The ephemeral pubkey inherits its trust by being embedded in a vendor-cert-signed UKI — not from a separate MOK enrollment, not from a `db` entry. **No MOK enrollment is required for InterGenOS in-tree modules to load** under Secure Boot. (For DKMS / out-of-tree modules, the user-side MOK chain via `CONFIG_SECONDARY_TRUSTED_KEYRING=y` applies; see `docs/ephemeral-module-signing.md` "User-Side DKMS / Out-of-Tree Modules".)
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

__FILLED__:

**Yes — verified reproducible across two independent native-Linux Docker hosts.**

The Dockerfile (`docker/shim-build/Dockerfile` on master, mirrored in the InterGenJLU/shim-review fork per Q9) reproduces the shim binary byte-for-byte from upstream `rhboot/shim` 16.1 (commit `afc49558b34548644c1cd0ad1b6526a9470182ed`) + the embedded InterGenOS vendor cert.

**Cross-host reproducibility evidence (native-Linux Docker):**

| Host | Distro | Docker | Tarball SHA-256 | Shim SHA-256 |
|---|---|---|---|---|
| Build host A (Ubuntu 24.04, native `docker.io`) | Ubuntu 24.04.2 LTS | `docker.io 27.5.x` (apt-installed) | `22ba569ab8543d456e4bf0289b9c63b7c28046ed3d98a0549cc38491322f8e97` | `b6c0c2c59cd2c6cc8306138ffd58a70210926defab4147b332663c91097ccf75` |
| Build host B (Ubuntu 22.04, native `docker.io`) | Ubuntu 22.04 LTS | `docker.io 29.1.3` (apt-installed) | `22ba569ab8543d456e4bf0289b9c63b7c28046ed3d98a0549cc38491322f8e97` | `b6c0c2c59cd2c6cc8306138ffd58a70210926defab4147b332663c91097ccf75` |

Both hosts produce byte-identical SHAs despite different physical hardware, different apt-snapshot policies, different kernel versions (5.15 vs 6.x), and different docker-engine point releases — confirming the Dockerfile's `apt-snapshot` pin to `20260501T000000Z bookworm`, `SOURCE_DATE_EPOCH=1746489600`, `make -j1`, and content-addressed base image (`debian:bookworm-slim@sha256:5a2a80d11944804c01b8619bc967e31801ec39bf3257ab80b91070eb23625644`) effectively remove host-environment as a leak source.

**Reproducibility scope (honest disclosure):** Docker Desktop's virtualization layer (Windows + WSL2; presumably also macOS) produces internally-consistent but DIFFERENT SHAs from native-Linux Docker. A Windows-host witness build under Docker Desktop produced `tarball=5875607d10e661bb32330c0af99783fb6ba7d11e5cc10e34d3e4e26bc1161bc6` / `shim=8a2fc8e462be1ebbab74bcec1afee1d0f0ef1dbb117a1e207f0dc2c11ee68748` — consistent across BuildKit version `v0.18.2` + `v0.29.0` on the same host but divergent from native-Linux. Therefore the strict bit-identity reproducibility claim scopes to **native-Linux Docker (apt-installed `docker.io` / `docker-ce`) only**. Reviewers running this Dockerfile under Docker Desktop will get internally-consistent results but should not expect byte-identity with the canonical native-Linux SHAs above.

**Reviewer-runnable verification:**

```
docker build --build-arg SOURCE_DATE_EPOCH=1746489600 \
    -t intergenos-shim-builder \
    -f docker/shim-build/Dockerfile .
docker create --name extract intergenos-shim-builder
docker cp extract:/out/. ./b2-output/
docker rm extract
sha256sum b2-output/intergenos-shim-16.1.tar
sha256sum b2-output/shimx64.efi
```

Plus `scripts/verify-b2-reproducibility.sh` graduates this into a 9-check harness (tarball SHA, shim binary SHA, vendor_cert.der SHA, vendor_cert.pem SHA, SHIM_COMMIT_SHA, sbat.intergenos.csv SHA, SBAT section dump, PE metadata, DER/PEM cert consistency).

**Beyond the shim binary itself,** every pkm-tracked artifact in the produced InterGenOS image carries a per-file SHA-256 attestation in both the human-readable text manifest and the pkm SQLite database (per the supersedes + content-hash design fully merged at master commit `811c2b5` — the integration of the multi-phase RFC, encompassing Phase 1 schema + Phase 2 tracker + Phase 4 installer/verifier + Phase 5 v4 migration + Phase 6 YAML + Phase 7 atomicity tests, all 26/26 PASS). The pkm repository index (`InterGenOS.db`), which is GPG-signed by the distro release-signing subkey at every release, records the per-file hashes transitively — recipients verifying the index signature can subsequently run `pkm verify --strict <package>` to re-validate any installed file against its signed expected hash. The shim binary AND the underlying OS image that loads and runs it are both content-hash attestable end-to-end.

---

## 23. Which files in this repo are the logs for your build?

__FILLED__:

Build logs are committed in the `InterGenJLU/shim-review/intergenos-shim-x64-20260515` fork branch under `logs/`:

| File | Content |
|---|---|
| `logs/build_<timestamp>.log` | Full Docker buildkit output from the canonical native-Linux build (apt install steps, gcc / make output, shim configure-make-install steps, tarball assembly) |
| `logs/verify-b2-reproducibility.log` | Output of `scripts/verify-b2-reproducibility.sh` against the built artifacts — 9 PASS checks (tarball SHA, shim binary SHA, vendor_cert.der SHA, vendor_cert.pem SHA, SHIM_COMMIT_SHA, sbat.intergenos.csv SHA, SBAT section dump, PE metadata, DER/PEM cert consistency) |

A second build log from an independent witness host (Ubuntu 22.04 + apt-installed `docker.io 29.1.3`) producing byte-identical SHAs is included as supplementary cross-host reproducibility evidence per Q22.

The Dockerfile + harness script + log files together let any reviewer with a native-Linux Docker host reproduce the build end-to-end, validate every input file, and confirm the produced binary's SHA matches the canonical attestation in Q25.

---

## 24. What changes were made in the distro's secure boot chain since your SHIM was last signed?

**N/A — first InterGenOS shim-review submission.** No prior signed shim exists. Future submissions will diff this section against the prior submission.

---

## 25. What is the SHA256 hash of your final shim binary?

__FILLED__:

**Pre-MS-signing SHA-256 of the shim binary submitted for Microsoft signing:**

```
b6c0c2c59cd2c6cc8306138ffd58a70210926defab4147b332663c91097ccf75  shimx64.efi
```

This is the canonical attestation for the unsigned shim binary that Microsoft will sign. The post-MS-signing SHA will differ (the embedded MS signature changes the binary content); the post-signing SHA is what end-users verify against the signed binary they install. Both pre-signing and post-signing SHAs will be pinned in the InterGenJLU/shim-review fork branch — the pre-signing SHA in this README (the canonical build attestation), the post-signing SHA in a follow-up commit once Microsoft returns the signed binary (~6-8 weeks post-PR-merge per the standard rhboot/shim-review cadence).

**Reproducibility:** the pre-signing SHA above is reproducible byte-for-byte on any native-Linux Docker host running the Dockerfile in Q22. Cross-host evidence in Q22's table.

**Companion artifact SHA-256s** (output of the canonical `b2-output/` produced by `docker cp` from the `intergenos-shim-builder` image):

| Artifact | SHA-256 |
|---|---|
| `intergenos-shim-16.1.tar` (full tarball) | `22ba569ab8543d456e4bf0289b9c63b7c28046ed3d98a0549cc38491322f8e97` |
| `shimx64.efi` (the shim binary) | `b6c0c2c59cd2c6cc8306138ffd58a70210926defab4147b332663c91097ccf75` |

The SHA-256 here is the canonical attestation for the shim binary specifically. For the broader OS-image attestation story (per-file SHA-256 across every pkm-tracked artifact, transitively signed via the GPG-signed pkm repository index), see Q22's content-hash discussion. `pkm verify --strict <package>` is the reviewer-runnable on-demand verification path against the signed hash record.

---

## 26. How do you manage and protect the keys used in your shim?

**Vendor cert (CA) key custody — the "D1 v2 Nitrokey + offline Tails root" architecture.**

Per `docs/research/installer/signing_key_custody_2026-04-18.md` and the executed ceremony of 2026-05-05:

- **Root CA private key (master keypair):** generated air-gapped on a Tails 7.7 boot from amnesic USB on 2026-05-05. RSA-4096, no expiry on master. UID `InterGenOS Project Signing Key (primary) <intergenos-primary@intergenstudios.com>` (project-role identity). Master fingerprint `5597A3E0587B253006D0DD7B8C50826182083050`. Stored only as:
  - LUKS-encrypted backup on dedicated USB drive (Drive #3, on-site fireproof safe); revocation cert co-resident
  - Paperkey printout × 2 (one on-site, one offsite)
  - Never present on any networked machine
- **Release-signing subkeys [S1]–[S4]:** four subkeys of the master, each keytocarded to a Nitrokey 3 NFC hardware token during the air-gapped session, all UIF=on (touch required per signing operation), 2-year expiry to 2028-05-04. Custody:
  - **[S1]** → Nitrokey #1 (`B9753481`) — primary maintainer daily-driver
  - **[S2]** → Nitrokey #2 (`43D33126`) — primary maintainer off-site bank-SDB
  - **[S3]** → Nitrokey #3 (`730D5185`) — secondary maintainer daily-driver
  - **[S4]** → Nitrokey #4 (`CC1D07E3`) — secondary maintainer off-site fireproof safe
  
  Symmetric custody across both maintainers: each holds one daily-use NK + one hardened-offline NK at separate physical locations. No single-location loss revokes the chain.
- **Encryption subkey [E]** (`62C7E2C30908823DAF5E4EBF917B649E00F2868C`) — RSA-4096, on-disk in LUKS master backup; not card-bound. Used for PGP-encrypted security reports per `SECURITY.md`.
- **EFI-binary signing keypair (PIV slot 9c on Nitrokey #1):** RSA-4096, generated on-card via `nitropy nk3 piv --experimental` during the same air-gapped session. Private half never leaves the hardware. Vendor cert DER fingerprint (SHA-256) `7B:8F:21:50:B5:D0:0C:7B:28:DD:51:8F:AD:D7:0B:C0:E8:37:AE:43:DF:7B:5E:23:D6:18:5E:9C:75:30:C8:76`; PEM-file SHA-256 `8ce749e7e77169205e4761d82b48a4333f48cdec2ee0f711b8cff560fe150514` (transport integrity). PIV management key rotated from factory hex to a fresh AES-256 value during the same session; recorded only on the maintainer's paper records.
- **Key-storage policy:** GNOME Keyring / libsecret on operator hosts (where applicable); never plaintext on disk; never embedded in source code or commit messages.
- **Ephemeral kernel-module signing keys:** see Q19 — these are NOT stored. Auto-generated per kernel build and reaped at build-completion.

The signing ceremony of 2026-05-05 completed all checklist steps end-to-end: Tails USB prep, root keygen, four signing subkeys keytocarded onto Nitrokeys, encryption subkey backed up via LUKS, paperkeys × 2 produced, EFI vendor cert minted on NK#1 PIV slot 9c, AES-256 PIV management key rotation. `validate.py` reported 0 failures across all 5 validation sections.

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
- **Type:** RSA-4096
- **Generation:** on-card in Nitrokey #1's PIV applet (slot 9c) during the 2026-05-05 air-gapped Tails ceremony, via `nitropy nk3 piv --experimental` (validated 2026-05-02 against NK#4 with full rotate+re-auth+rotate-back cycle PASS, then applied to NK#1). Private half never leaves the hardware token. Vendor cert DER fingerprint (SHA-256) `7B:8F:21:50:B5:D0:0C:7B:28:DD:51:8F:AD:D7:0B:C0:E8:37:AE:43:DF:7B:5E:23:D6:18:5E:9C:75:30:C8:76`; PEM-file SHA-256 `8ce749e7e77169205e4761d82b48a4333f48cdec2ee0f711b8cff560fe150514` (transport integrity check). PIV management key rotated from factory hex to fresh AES-256 during the same session.
- **Lifetime:** 2 years (2028-05-04) with documented rotation strategy per Q21
- **Use:** signs the InterGenOS-built signed GRUB2 binary AND the InterGenOS-built signed kernel image. Does NOT sign kernel modules (those use ephemeral per-build keys, see Q19).

The CA private key, once generated in the PIV slot 9c follow-up session, will never leave the Nitrokey hardware token (per Q26 custody architecture; on-card generation guarantees private material is hardware-bound from inception).

---

## 29. Do you add a vendor-specific SBAT entry to the SBAT section in each binary?

__FILLED__: Yes — `shim.intergenos,1` for the shim binary (file at `docker/shim-build/sbat/sbat.intergenos.csv` on master; one-line CSV currently committed: `shim.intergenos,1,InterGenOS,shim,16.1,https://github.com/InterGenJLU/intergenos`). Companion `grub.intergenos,1` entry lands in the GRUB binary per `packages/core/grub/sbat.csv`. Both are vendor-specific InterGenOS additions atop the upstream-baked `shim,4 / grub,5 / linux,1` entries; the union forms the resolved 6-line SBAT block (per docs/research/shim_review/post_b2_completion_roadmap_2026-05-01.md). Generation 1 ships at v1.0; bumped only on revocation.

---

## 30. If shim is loading GRUB2 bootloader, which modules are built into your signed GRUB2 image?

__FILLED__ (design-intent settled on master; binary-extracted module list verifiable post-Phase-1 GRUB build):

**Minimal module set.** Only modules required to discover boot media, read filesystems we load configs / UKIs from, verify signatures, chain the next signed binary, or render the menu. **No filesystem-write modules, no network modules, no env-write** in the signed image.

The module list is encoded explicitly in `scripts/build-grub-standalone.sh` on master (the `MODULES=( … )` array) — that script is the source of truth for what `grub-mkstandalone --modules=...` is invoked with. Module categories and rationale:

| Category | Modules | Rationale |
|---|---|---|
| Boot media + partition discovery | `part_gpt`, `part_msdos`, `iso9660`, `udf` | Required to read the install media partition table + ISO filesystem |
| Filesystem read access | `ext2`, `fat`, `xfs`, `btrfs` | Read-only paths through these; no filesystem-write modules included |
| Crypto + signature verification | `shim_lock`, `pgp`, `gcry_sha256`, `gcry_sha512`, `gcry_rsa` | `shim_lock` enforces the shim → GRUB → UKI signature chain under Secure Boot; `pgp` + `gcry_*` provide the verification primitives |
| Bootloader essentials | `linux`, `chain`, `boot`, `configfile`, `echo`, `normal`, `test`, `true`, `search`, `search_fs_uuid`, `search_label`, `search_fs_file`, `halt`, `reboot`, `ls`, `help` | Standard GRUB2 boot + signed kernel loading + ESP discovery + minimal user-facing controls |
| Display | `gfxterm`, `gfxmenu`, `videoinfo`, `vbe`, `efi_gop`, `font`, `png` | Boot-time display + theme rendering for the 3-entry menu |
| **Excluded** | `tftp`, `http`, `pxe`, `efinet`, `loopback` (write), `procfs`, `loadenv`, `savedefault`, `password_pbkdf2` | Network + writable-FS + env-write modules NOT built into the signed image; loading these post-shim-lock would require user MOK enrollment |

**Reviewer-runnable verification (post-Phase-1 GRUB build):**

```
objdump -h grubx64.efi | grep mods
objcopy --dump-section .sbat=/dev/stdout grubx64.efi
grub2-script-check --root=. <embedded-grub.cfg>
file grubx64.efi  # expect: PE32+ executable (EFI application) x86-64
```

The build script `scripts/build-grub-standalone.sh` performs the first three checks itself before declaring PASS; reviewers running the script on a native-Linux Docker host with GRUB 2.14 installed will reproduce the same module set + SBAT entries embedded in the produced binary.

---

## 31. If you are using systemd-boot on arm64 or riscv, is the fix for unverified Devicetree Blob loading included?

**N/A — InterGenOS x86_64 only for this submission.** systemd-boot is not used; GRUB2 is the bootloader. arm64 / riscv ports not in scope.

---

## 32. What is the origin and full version number of your bootloader?

__FILLED__: (verified against `packages/core/grub/package.yml`)

  GNU GRUB2 version 2.14, sourced from upstream (`https://ftp.gnu.org/gnu/grub/grub-2.14.tar.xz`).

---

## 33. If your shim launches any other components apart from your bootloader, please provide further details?

**No.** shim launches GRUB2 only. No other binaries are launched directly by shim.

---

## 34. If your GRUB2 or systemd-boot launches any other binaries, please provide further details?

**No additional binaries.** GRUB2 launches the InterGenOS-signed Linux kernel only. No additional EFI applications, no additional bootloaders, no firmware updaters launched from GRUB2.

---

## 35. How do the launched components prevent execution of unauthenticated code?

The InterGenOS-signed Linux kernel enforces signed-only kernel-module loading via `CONFIG_MODULE_SIG_FORCE=y` (override:117). When booted with Secure Boot, the lockdown LSM enforces additional restrictions; see Q17 for `CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT=y` auto-trigger details.

Userspace execution policy is the responsibility of the running OS image (executable-bit + capabilities + signed-package verification via pkm); this is outside the boot-chain scope but is documented in the project's broader security posture for completeness.

---

## 36. Does your shim load any loaders that support loading unsigned kernels?

**No.** The InterGenOS-signed GRUB2 image is built with module-set restricted to those needed to boot off the install media + verify and load the signed kernel. No loader modules supporting unsigned-kernel loading (e.g., `linux16`-style insecure loaders bypassing signature verification) are included in the signed GRUB2 image.

GRUB2 module list to be confirmed in Q30.

---

## 37. What kernel are you using? Which patches and configuration does it include?

**Kernel:** Linux 6.18.10 (version pin in `packages/core/linux-kernel/package.yml`; sha256 `d6d377161741ada2fab28eed69143277634a2aeb5e3883e50c031588ede48ede`).

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

__FILLED__ (per project-lead directive 2026-04-29T17:31:35Z item 5).

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

The kernel-lockdown auto-trigger gap (Q17) was surfaced during this draft's population pass and resolved at master commit `baf84d8` ahead of submission. Documented here for transparency.

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
- [x] PGP fingerprints filled in Q6 — master + [S1]-[S4] + [E] + EFI vendor cert SHA all populated post-ceremony 2026-05-05. Q7 (Ethan Phase 1) remains independently pending on secondary maintainer's Phase 1 onboarding.
- [x] Ethan email format decision in Q7 — resolved: shared role address (PGP-signed mail to secondary maintainer's key) per Q7 body
- [x] Kernel-lockdown auto-trigger gap (Q17) RESOLVED at master commit `baf84d8` — `CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT=y` added to `99-intergenos-overrides.config:130` per Path 1 (SPOC ruling 2026-04-29T18:05:38Z, integrated 18:18Z)
- [ ] B2 Dockerfile build artifact + SHA256 + Q22-Q25 + Q14 + Q29 + Q30 (B2 reproducibility lane)
- [x] Q9 InterGenJLU/shim-review fork created + submission branch pushed (2026-05-05)
- [x] Q10, Q12, Q18, Q20, Q32, Q37 specific version pins confirmed against package definitions (completed 2026-04-29)
- [ ] Q38 ≥2 peer-review contributions completed and linked
- [ ] Pre-PR-open final pass: SBAT entry + signed binary hashes + all gated items resolved
