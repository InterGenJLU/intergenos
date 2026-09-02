# 03 — Automating release signing

**Audience:** maintainers who hold the signing token and need to sign a release-grade artifact set after a clean build.

## Scope (what this doc covers vs. what it doesn't)

This doc covers **release-grade artifact signing** — the artifacts that ship in the public ISO + index. It does NOT cover:

- **Shim signing** — InterGenOS ships Fedora's pre-signed shim (the Fedora-piggyback path is the v1.0 ship path). We do not sign our own shim for v1.0 ship; the parallel `rhboot/shim-review` PR submission produces our own MS-signed shim for later releases.
- **Installed-system per-kernel UKI signing** — Installed systems regenerate and sign UKIs at kernel install/upgrade time using the **user's local MOK key**, not the release PIV slot 9c (UKI parity Option A). That flow runs inside `packages/core/linux-kernel`'s post_install hook on the user's machine; the InterGenOS release PIV slot 9c key never leaves the offline signing workstation.
- **Module signing** — Runs inside the kernel build with an ephemeral per-build key that never touches the hardware token. See topic 02 for kernel-build context.

## Goal

Sign three classes of release artifact, on an offline signing workstation, with hardware-backed keys:

1. **pkm repo index** (`InterGenOS.db`) — GPG detached signature with the release subkey (release-grade PGP).
2. **Kernel UKI binaries** (`igos-live.efi`, `igos-install-gui.efi`, `igos-install-tui.efi`) — Authenticode signatures via `sbsign` against the vendor X.509 cert backed by the PIV applet, slot 9c. The three-UKI scope is canonical (live ISO, install-gui, and install-tui), matching the live ISO output of `scripts/build-iso.sh`. An earlier version of `sign-release.sh` silently skipped the install-gui and install-tui UKIs because of an incomplete glob; the post-sign count assertion described in step 4 below closes that regression class.
3. **GRUB EFI binary** (`grubx64.efi`) — Authenticode signature with the same PIV key.

## Signing entry points — two operator ceremonies plus the superset

A release build has **two** operator-only signing pauses, in pipeline order, each driven by its own pain-free wrapper: the operator runs exactly one command and signs, while all staging and verification around it is done beforehand by whoever is driving the build. A third script, `sign-release.sh`, performs every signing step in one automated pass for automated and continuous-integration runs and is **not** the human ceremony.

- **`scripts/sign-manifest.sh` — operator ceremony #1, at the `phase_manifest` pause (BEFORE squashfs).** Signs the build-emitted archive integrity manifest (`intergenos-archive-manifest.txt`) with the release OpenPGP signing subkey [S1] via `gpg --detach-sign --armor`. **PIN + on-card touch, and NO `sudo`** — GPG card signing runs as the operator; `sudo` would use root's empty keyring with no card stub and fail. The wrapper sanity-gates the manifest (BSD `SHA256 (…) = …` entries plus the `# Manifest-version: 1` header and `# End of manifest.` terminator), signs, verifies with `gpg --verify`, and exports the release public key — so one command yields the complete **trust triplet**: the manifest, its detached `.sig`, and `intergenos-release-key.asc`. `build-squashfs.sh` **Step 4.8** seals that triplet into the squashfs so the installer's `PHASE_VERIFY` validates archive integrity offline (see "The archive-manifest pause and the trust triplet" below). This pause comes first because the manifest must be signed before `mksquashfs` runs.
- **`scripts/sign-bootloader.sh` — operator ceremony #2, at the `phase_ukis_verity` pause (AFTER squashfs).** Signs the **bootloader set only**: `grubx64.efi` plus the three UKIs (`igos-live`, `igos-install-gui`, `igos-install-tui`), via `sbsign` against the hardware token's PIV slot 9c. This is the operator-driven path the pipeline pause invokes (`sudo bash scripts/sign-bootloader.sh`). Slot 9c is **PIN-only, no touch** (and `sudo` IS correct here). It does not sign the pkm index or the archive manifest. This pause comes second because the live UKI's sealed cmdline carries the squashfs dm-verity root hash, known only after squashfs.

  **The operator types the PIN exactly once**, even though four binaries are signed. PIV slot 9c is an always-authenticate slot — per-signature authentication is the PIV standard for the signature slot — so the signing engine asks for the PIN again before every binary. The wrapper captures the PIN once with a silent read, writes it to an owner-only file on `tmpfs` (never to disk), hands the engine a `pin-source=file:` reference rather than putting the PIN on a command line or in the environment, and runs each signing operation under `scripts/sign-pty-feeder.py`, which answers the engine's per-binary prompts from that one capture. The PIN file is shredded on every exit path. Per-operation authentication stays fully intact — the operator types once because the feeder answers the rest, not because the prompts were removed. If the feeder is absent the wrapper falls back to the original behaviour and simply prompts the operator for each binary; that is a usability difference, never a weakening. A `pin-value=` URI, which would place the PIN itself on the command line, is refused unconditionally.
- **`scripts/sign-release.sh` — the release signer (superset).** Covers all release-grade signing in one pass: bootloader binaries plus the build-emitted archive manifests (`--manifest <file>` for the full manifest the mirror ships; `--iso-manifest <file>`, or the `intergenos-archive-manifest-iso.txt` beside it, for the ISO manifest) and the pkm repo index. It accepts a staged unsigned artifacts directory (`--artifacts <dir>`) and emits signed artifacts plus detached signatures to a clean output directory. This is the automated alternative for continuous-integration and scripted runs — do **not** recommend it to the operator in place of the per-pause wrappers during a live ceremony.

The rest of this doc details **`sign-release.sh`** (the superset, including the pkm index and archive manifest). The two operator wrappers use the identical crypto on their respective subsets — `sign-manifest.sh` the same GPG [S1] detached-sign as the archive-manifest step below, `sign-bootloader.sh` the same `sbsign` / slot-9c crypto on the bootloader subset. See those scripts and topic 02's signing pauses.

## The archive-manifest pause and the trust triplet (ceremony #1)

`phase_manifest` of `scripts/build-intergenos.sh` emits **two** manifests in `build/` — BSD-style `SHA256 (file) = hash` sums — then **hard-exits** for ceremony #1:

- `intergenos-archive-manifest.txt` — the **full** manifest: every `.igos.tar.gz` the build chroot holds. This is the mirror's manifest; `publish-repo.sh` ships it.
- `intergenos-archive-manifest-iso.txt` — the **ISO** manifest: the full census minus the mirror-only archives `build-squashfs.sh` keeps off the media (the same exclusion list Step 2.6 derives). It carries a `# Manifest-scope: iso` header line. This is the manifest the ISO carries.

The two exist because the ISO ships a subset of the archives the build produces, and a signed manifest that lists archives the media does not hold is refused by the installer's integrity check (R001.2, 2026-08-27: the ISO carried the full manifest and the install aborted on 284 promised-but-absent archives). Both MUST be signed *before* `mksquashfs`, because `build-squashfs.sh` Step 4.8 seals the signed **trust triplet** INTO the squashfs so the installer's `PHASE_VERIFY` can validate it offline (design: `docs/research/security/install-integrity-verification.md`). The triplet Step 4.8 seals, under the canonical installer names in `/install/`, is:

- `intergenos-archive-manifest.txt` — the **ISO** manifest, staged under the canonical name (from `build/intergenos-archive-manifest-iso.txt`)
- `intergenos-archive-manifest.txt.sig` — its detached [S1] signature
- `intergenos-release-key.asc` — the exported release public key (so the target self-validates without network)

**Pre-ceremony preparation (done before the operator is called, never by the operator):** stage the *current* manifest into the signing dir, clearing any stale prior-ceremony copy first (`mkdir -p /tmp/c6r2-manifest && rm -f /tmp/c6r2-manifest/*.{txt,sig} && cp build/intergenos-archive-manifest.txt /tmp/c6r2-manifest/`, then confirm the sha); `gpgconf --kill scdaemon` (the OpenPGP card path goes stale → "Card error"); confirm the token is present.

**Operator's entire role — one command, NO `sudo`:**

```sh
cd /mnt/intergenos && bash scripts/sign-manifest.sh    # OpenPGP [S1], PIN + touch
```

The pause's own printed hint may say `sudo bash …`; that generic hint is **wrong for the GPG card path** — do not relay it. Both manifests are staged in `/tmp/c6r2-manifest/` beforehand; the wrapper validates each manifest's BSD format + v1 header/terminator (and the `# Manifest-scope: iso` line on the ISO one), signs each (the card asks for the PIN and a touch for each file: two PIN entries, two touches), verifies each, and exports `intergenos-release-key.asc`, leaving the whole signed set in `/tmp/c6r2-manifest/`.

**Post-ceremony delivery (again, not the operator's step):** deliver all FIVE artifacts (two manifests, two signatures, the key) into `build/` (the directory Step 4.8 reads), then resume `--start-at squashfs`. Step 4.8 seals the ISO manifest, and its staging gate fail-closes unless the triplet is present + non-empty, the signature verifies against the staged key, and coverage holds in **both directions**: every shipped archive appears in the manifest, and every manifest entry has a shipped archive.

> **Dev/test ISO (NOT a release):** resume `phase_squashfs` with `UNSIGNED_TEST=1`; Step 4.8 stages the explicit `IGOS_DEV_ALLOW_UNVERIFIED` marker and bypasses the integrity gate (Secure-Boot-off test artifact only). A real release REQUIRES the signed triplet.

## UKI staging freshness (CRITICAL — before any bootloader ceremony)

`sign-bootloader.sh` reads its input unsigned binaries from a staging directory (`/tmp/c6r2-bootloader/` by default), **not** directly from the build output. That dir can retain unsigned UKIs left over from a *prior* ceremony. Signing only **appends a PE signature** — it reproduces the input's embedded kernel cmdline verbatim — so a stale unsigned UKI signs into a stale **signed** UKI, and nothing downstream catches it. Each live/installer UKI seals the squashfs's dm-verity root hash into its cmdline (`igos.verity.roothash=<hash>`, set by `phase_ukis_verity`); if the signed UKI's hash does not match the squashfs actually shipped on the ISO, **dm-verity fails at boot** and the firmware/initramfs reports the *stale* root hash. (Observed 2026-06-04: June-2 UKIs were signed against a June-4 squashfs → the ISO booted to a dm-verity failure displaying the stale hash.)

**Re-staging the current build's UKIs and verifying their root hash before signing happens before the ceremony — it is never the operator's step.** The operator only runs the script and signs (PIN / touch). The full re-stage + root-hash-verify procedure, and the tracked durable fix that moves it into `sign-bootloader.sh` itself, are in **[`docs/signing-procedure.md` → "UKI staging freshness"](../signing-procedure.md)**.

## Binding project decisions

This ceremony composes with the following binding project decisions:

- **D-002** — Fedora-piggyback shim is the v1.0 ship path. We do not sign our own shim during this ceremony (see Scope).
- **D-005** — Installed-system per-kernel UKIs are signed by the user's local MOK at the user's machine, NOT here. This ceremony signs the live ISO + install-* UKIs that ship in the public ISO.
- **D-007** — SSH/root posture is enforced as a Class A ship-gate by `scripts/check-d007-compliance.sh` wired into `scripts/build-iso.sh phase_image`. That gate runs at ISO build time, separate from this release-signing ceremony. The signed artifacts produced here are consumed by the ISO build that runs the D-007 gate.

## Prerequisites

- An **offline signing workstation** — never the same physical machine as the build VM host. Air-gapped where practical; at minimum, browser closed + non-essential processes terminated for the duration of the ceremony (this is a ceremony-grade operation, not a casual scripted run).
- A hardware token with:
  - GPG release subkey loaded
  - PIV applet, slot 9c populated with the X.509 cert backing `grubx64.efi` + UKI Authenticode signatures
- Token PIN unlocked (`gpg --card-status` succeeds, `pkcs11-tool --list-objects` returns the PIV cert).
- `gpg`, `sbsigntool` (provides `sbsign`), `pkcs11-tool` (from `opensc`), `scd` (scdaemon) installed on the signing workstation.
- The vendor X.509 cert PEM-encoded at `/etc/intergenos/signing/vendor-cert.pem` (pre-positioned, NOT transported with each artifact bundle — protects against substitution).
- A staged unsigned artifacts directory containing some or all of:
  - `InterGenOS.db` (pkm repo index)
  - `intergenos-archive-manifest.txt` (build-emitted archive manifest)
  - `igos-live.efi`, `igos-install-gui.efi`, `igos-install-tui.efi` (UKI variants from `scripts/build-uki.sh`)
  - `grubx64.efi` (unsigned standalone from `scripts/build-grub-standalone.sh`)
- A scdaemon configuration that allows the PIV applet to coexist with the GPG card. See "scdaemon configuration" below; the canonical configuration is documented in `docs/signing-with-gpg.md`.

## scdaemon configuration

The GPG-card and PKCS#11-PIV split on the same token requires scdaemon to be configured so both applets can be driven through the same reader. The canonical configuration lives at `~/.gnupg/scdaemon.conf` and includes a `disable-ccid` line plus a `pcsc-shared` line, so OpenSC can drive the PIV applet via PC/SC while GPG drives the OpenPGP applet via the same reader. The `pcsc-shared` line is what allows the two to coexist on one reader. Without this configuration, the PIV `pkcs11-tool --login` call and the `gpg --detach-sign` call race for the same applet and one of them fails non-deterministically. See `docs/signing-with-gpg.md` for the verbatim file contents.

## Manual signing is not supported

Every signing pass goes through one of the three sanctioned scripts: `scripts/sign-manifest.sh` (operator manifest ceremony), `scripts/sign-bootloader.sh` (operator bootloader ceremony), or `scripts/sign-release.sh` (the superset: bootloader, pkm index, and archive manifest). Manual step-by-step `sbsign` / `gpg` invocations are not supported and not permitted. The scripts encode the full sequence (token presence check, vendor cert match, key-material validation, per-artifact signing, output-directory layout), and any deviation introduces ceremony drift. If something needs to change, change the script in a reviewed commit and re-run; never copy-paste a one-off invocation.

## Step-by-step procedure

### 1. Stage the artifacts on the signing workstation (preparation, not the operator's step)

The unsigned artifacts are transported from the build VM to the signing workstation by whatever low-trust mechanism the operational threat model allows (USB stick wiped before and after; scp over a known-trusted LAN segment; rsync over the build VPN). The cert at `/etc/intergenos/signing/vendor-cert.pem` is pre-positioned and is NOT part of the artifact transport. The operator's only actions in this ceremony are running the script (step 4) and signing (PIN / touch) — the operator never stages.

Conventional staging path:

```
/home/<user>/signing/staged/<release-tag>/
├── InterGenOS.db
├── intergenos-archive-manifest.txt
├── igos-live.efi
├── igos-install-gui.efi
├── igos-install-tui.efi
└── grubx64.efi
```

### 2. Confirm token + key material before signing

```sh
# OpenPGP side — release subkey present + reachable
gpg --card-status
# Look for `Signature key ...:` with the expected key ID

# PIV side — vendor cert present + reachable
pkcs11-tool --list-objects --type cert
# Look for the InterGenOS vendor cert by CKA_LABEL or CKA_ID

# Vendor cert matches what sign-release.sh expects
openssl x509 -in /etc/intergenos/signing/vendor-cert.pem -noout -subject -issuer -fingerprint
# Confirm the fingerprint matches the build's embedded shim vendor cert
```

If any of these fail, **stop**. Do not proceed with a partial ceremony. Resolve the configuration drift first.

### 3. Set the signing env vars

```sh
export INTERGENOS_GPG_KEY_ID=<release-subkey-fingerprint>
export INTERGENOS_PKCS11_URI='pkcs11:id=%02;type=private'
export INTERGENOS_VENDOR_CERT=/etc/intergenos/signing/vendor-cert.pem
```

The PKCS#11 URI selects the signing key by object **id**: `%02` is the PIV slot 9c
key (id `02`) that holds the vendor cert. Do **not** embed the PIN in the URI —
`sign-release.sh` rejects a `pin-value=`/`pin-source=` URI (B-049: a PIN in the URI
leaks into process listings and error output), and the OpenSSL pkcs11 engine prompts
for the PIV User PIN interactively during the ceremony.

### 4. Run sign-release.sh

```sh
scripts/sign-release.sh \
    --artifacts /home/<user>/signing/staged/<release-tag>/ \
    --output /home/<user>/signing/signed/<release-tag>/ \
    --strict
```

`--strict` requires every artifact class in the staged directory to be present; absent files fail the ceremony rather than silently skipping. Use `--strict` for releases, drop it for incremental signing of subset-rebuilds.

The script:

1. Confirms token presence + PKCS#11 enumeration succeed.
2. Confirms the `--vendor-cert` X.509 matches the PKCS#11 key. This guards against signing with the wrong key against the right cert, a class of error that is otherwise costly to diagnose.
3. Enumerates input UKIs by the canonical glob set (`*.uki.efi`, `igos-live.efi`, `igos-install-gui.efi`, `igos-install-tui.efi`, future `igos-install-*.efi` variants). Records the input count.
4. For each artifact:
   - **InterGenOS.db** → `gpg --detach-sign --armor --local-user $GPG_KEY_ID InterGenOS.db` → emits `InterGenOS.db.sig` (the detached ASCII-armored signature).
   - **intergenos-archive-manifest.txt** → `gpg --detach-sign --armor --local-user $GPG_KEY_ID` (with an additional `--local-user $GPG_MASTER_KEY_ID` cosignature when `INTERGENOS_GPG_MASTER_KEY_ID` / `--gpg-master-key-id` is set) → emits `intergenos-archive-manifest.txt.sig`. The script also exports the release public key to `intergenos-release-key.asc` (shipped to `/install/intergenos-release-key.asc`) so the signature is verifiable on the target.
   - **UKI binaries** → `sbsign --engine pkcs11 --key "$INTERGENOS_PKCS11_URI" --cert "$VENDOR_CERT" --output <out>.efi <in>.efi` → emits the signed UKI. UKI shape is preserved through the sign operation (signature lands in the existing PE32+ certificate-table; `.linux`/`.initrd`/`.cmdline`/etc. sections untouched).
   - **grubx64.efi** → same sbsign invocation → emits the signed GRUB binary.
5. **Post-sign count assertion** — verifies `signed_uki_count == input_uki_count`. Catches the regression class where a new UKI variant is added (e.g., a future `igos-recovery.efi`) but the signing glob isn't extended; the assertion fires and the ceremony aborts rather than shipping with unsigned UKIs that the build-iso phase would then refuse-or-warn on.
6. Validates each signed binary with `sbverify --cert "$VENDOR_CERT"` and aborts if any verify-step fails.
7. Prints a per-artifact stdout trace (input/output paths per signed artifact) — no persisted log file is written.

### 5. Verify the signed artifacts before transport

```sh
cd /home/<user>/signing/signed/<release-tag>/
for f in *.efi; do
    sbverify --cert /etc/intergenos/signing/vendor-cert.pem "$f" \
        && echo "OK: $f"
done

gpg --verify InterGenOS.db.sig InterGenOS.db && echo "OK: InterGenOS.db"
```

All artifacts must verify before they leave the signing workstation. If any fail, **do not transport** — investigate first.

### 6. Transport the signed bundle to the build VM

Same path as the unsigned-transport mechanism, in reverse. The signed bundle is what `scripts/build-iso.sh` consumes as its SHIM/GRUB/UKI env vars (topic 05).

## Validation

- `sign-release.sh` exits 0.
- `sbverify` succeeds against every `.efi` output.
- `gpg --verify` succeeds for the InterGenOS.db detached signature.
- The per-artifact stdout trace lists each signed artifact's input/output paths (no persisted log file is written).

## Common failures + troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `gpg --card-status` returns "No such device" | Token unplugged or pcscd not running | Plug token, `systemctl restart pcscd`, retry |
| `pkcs11-tool --list-objects` empty | scdaemon holding the reader and not yielding to OpenSC | adjust `~/.gnupg/scdaemon.conf` per the operational note; restart scdaemon |
| `sbsign` errors with "Could not load key" | PKCS#11 URI doesn't resolve to a private key on the inserted card | re-check `pkcs11-tool --list-objects --type privkey` and the URI's `object=` value |
| `sbverify` fails on a freshly-signed binary | Vendor cert in `--cert` doesn't match the PKCS#11 key used to sign | the `--vendor-cert` and PKCS#11 key MUST be the same key-pair; resync the cert file |
| sign-release.sh aborts with "vendor cert SHA mismatch" | The X.509 at `--vendor-cert` doesn't match the cert embedded in the build's shim | rebuild shim with the correct vendor cert OR re-position the correct vendor cert on the signing workstation |
| Detached `.sig` signature opens as a 0-byte file | GPG didn't actually sign (silent failure mode under some scdaemon races) | rerun, confirm the file is non-empty before transport |

## Cross-references

- Topic 02: How to run the builder — produces the unsigned UKIs + GRUB binary
- Topic 05: How to create an ISO — consumes the signed outputs
- `scripts/sign-manifest.sh` — the operator manifest ceremony (archive integrity manifest, OpenPGP [S1] detached signature, PIN + touch, no `sudo`); the operator-driven path at the `phase_manifest` signing pause, producing the trust triplet sealed by squashfs Step 4.8
- `scripts/sign-bootloader.sh` — the operator bootloader ceremony (GRUB plus the 3 UKIs, hardware-token PIV slot 9c, PIN-only no-touch); the operator-driven path at the `phase_ukis_verity` signing pause
- `scripts/sign-release.sh` — the release signer (bootloader, pkm index, and archive manifest); canonical reference for the procedure above
- `scripts/build-uki.sh` — produces the UKI envelope that gets signed
- `scripts/build-grub-standalone.sh` — produces the unsigned grubx64.efi
- `scripts/check-d007-compliance.sh` — Class A ship-gate at ISO build time per D-007; consumes the artifacts this ceremony produces
- `docs/signing-with-gpg.md` — canonical scdaemon configuration and GPG-card signing notes; read it before any signing pass
- `docs/signing-procedure.md` — full pre-flight checklist for the signing ceremony
