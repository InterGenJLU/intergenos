# InterGenOS Signing Procedure

**Last updated:** 2026-05-07
**Applies to:** the signing workstation (primary maintainer) during a release-signing window.

This is the operational runbook for signing an InterGenOS release. It covers the distro GPG repo-index signature, the EFI-binary signatures for the kernel and GRUB, and the install-time archive integrity manifest signature. Kernel module signing is ephemeral per-build and is handled inside the kernel build itself — it does not appear in this procedure.

For the decisions and rationale behind this architecture, see `docs/research/installer/signing_key_custody_2026-04-18.md`.

## Trust Chain Attestation

Each signature this procedure produces attests to one layer of the InterGenOS trust chain:

- **`InterGenOS.db.sig`** (distro GPG subkey [S1] on Nitrokey #1) — signs the pkm repository index. The index records per-file SHA-256 for every file in every package, so signing the index is a transitive attestation of every file in the distribution. Recipients verifying the index signature can subsequently run `pkm verify --strict <package>` to re-check any installed file against its signed hash. (Index format extended for per-file content-hash at commit `c9534f7`.)
- **`vmlinuz-<version>-intergenos.sig`** (PIV slot 9c X.509 via `sbsign`) — signs the kernel EFI binary. shim verifies this signature against the embedded vendor cert when Secure Boot is active.
- **`grubx64.efi.sig`** (PIV slot 9c X.509 via `sbsign`) — signs the GRUB EFI binary. Same shim verification path.
- **`intergenos-archive-manifest.txt.sig`** (distro GPG subkey [S1] on Nitrokey #1; optionally cosigned by master key for tagged releases) — signs the build-emitted archive integrity manifest. The manifest contains BSD-style sha256 sums for every `.igos.tar.gz` archive shipped on the install media. At install time, Forge's `PHASE_VERIFY` validates this signature before trusting any per-archive sha256 — manifest tampering at any point between signing and install fails non-overridably. See `docs/research/security/install-integrity-verification.md` for the full design (status: APPROVED 2026-05-07).

The kernel-module signing key (ephemeral, per-build) and end-user MOK enrollments are orthogonal to this procedure — they live inside the kernel build and per-install respectively, not at release-signing time.

## The two build-pipeline signing pauses (operator ceremonies)

A from-scratch ISO build through `scripts/build-intergenos.sh` stops at **two** operator-only signing pauses, in pipeline order. Each is driven by its own pain-free wrapper: the operator runs exactly one command and signs; the Coordinator does all staging + verification around it. This two-pause structure is **new since the GB001-era ISOs** (the archive-manifest pause was added with Option-1 install-integrity in 2026) — an operator who signed earlier ISOs will not expect the first pause.

| # | Pause (orchestrator phase) | Operator command | Key / crypto | `sudo`? | Produces |
|---|---|---|---|:--:|---|
| 1 | end of **`phase_manifest`** (BEFORE squashfs) | `bash scripts/sign-manifest.sh` | OpenPGP **[S1]** subkey — `gpg --detach-sign --armor` (PIN **+ touch**) | **NO** | the install-integrity trust **triplet** |
| 2 | end of **`phase_ukis_verity`** (AFTER squashfs) | `sudo bash scripts/sign-bootloader.sh` | **PIV slot 9c** X.509 — `sbsign` (PIN-only) | **yes** | signed GRUB + 3 UKIs |

The superset `scripts/sign-release.sh` (documented in the rest of this runbook) performs every signing step in one pass — it is the CI / coordinator-driven path and the standalone path for re-signing the pkm index or a shipped kernel image. It is **not** the human ceremony: during a live pipeline pause, recommend the per-pause wrapper, never `sign-release.sh`, to the operator.

### Ceremony #1 — archive integrity manifest (`sign-manifest.sh`)

`phase_manifest` emits `build/intergenos-archive-manifest.txt` (BSD-style `SHA256 (file) = hash` sums for every shipped `.igos.tar.gz`, wrapped in a `# Manifest-version: 1` header and `# End of manifest.` terminator) and **hard-exits**. The manifest MUST be signed *before* `mksquashfs`, because `build-squashfs.sh` **Step 4.8** seals the signed **trust triplet** INTO the squashfs so the installer's `PHASE_VERIFY` validates archive integrity offline.

The triplet is three files (the wrapper emits all three from one command):

- `intergenos-archive-manifest.txt` — the manifest
- `intergenos-archive-manifest.txt.sig` — the detached [S1] ASCII-armored signature
- `intergenos-release-key.asc` — the exported release public key (so the target self-validates without network)

**Coordinator pre-ceremony (never the operator):**

1. Stage the *current* manifest into the signing dir, clearing any stale prior-ceremony copy first (same stale-input hazard as the bootloader stage):
   ```
   mkdir -p /tmp/c6r2-manifest && rm -f /tmp/c6r2-manifest/*.{txt,sig}
   cp /mnt/intergenos/build/intergenos-archive-manifest.txt /tmp/c6r2-manifest/
   ```
   then confirm its sha256.
2. `gpgconf --kill scdaemon` (the OpenPGP card path goes stale → "Card error"); confirm the token is present (`gpg --card-status`).

**Operator's entire role — ONE command, NO `sudo`:**

```
cd /mnt/intergenos && bash scripts/sign-manifest.sh
```

GPG card-signing runs **as the operator**; `sudo` uses root's empty keyring with no card stub and fails. (The pause's own printed hint may say `sudo bash …` — that generic hint is **wrong** for the GPG path; do not relay it.) The wrapper sanity-gates the manifest's BSD format + v1 header + terminator (refusing to sign a malformed manifest that would later break `PHASE_VERIFY`'s parser), signs via `gpg --detach-sign --armor` against [S1], verifies with `gpg --verify`, and exports `intergenos-release-key.asc` — leaving the whole triplet in `/tmp/c6r2-manifest/`. Signing requires the OpenPGP **User PIN + an on-card touch** (UIF policy — watch the LED).

**Coordinator post-ceremony:** deliver all THREE artifacts into `/mnt/intergenos/build/` (the directory Step 4.8 reads; the mount is shared host↔build-VM, so the `.sig` + `release-key.asc` just need copying in beside the manifest), then resume `sudo bash scripts/build-intergenos.sh --user <user> --debug-verbose --start-at squashfs`. Step 4.8's staging gate fail-closes unless the triplet is present + non-empty, the signature verifies against the staged key, and every staged archive appears in the manifest.

> **⚠️ `build/` (and `build/bootloader/`) is root-owned AND may hold a PRIOR ceremony's stale triplet / `.efi.signed` set.** A plain `cp` from your user shell fails *permission-denied*, and even with privilege a stale prior copy can shadow the fresh one (same stale-input hazard as the bootloader stage). So the delivery is NOT a bare "just copy them in": **(1) clear the stale prior-ceremony artifacts first, then (2) copy the fresh set in, using privilege.** On the shared mount the build VM's NOPASSWD sudo is the clean privilege path: `scp` the fresh triplet to the VM's `/tmp`, then on the VM `sudo rm -f /mnt/intergenos/build/{intergenos-archive-manifest.txt,intergenos-archive-manifest.txt.sig,intergenos-release-key.asc}` (the stale set) → `sudo cp /tmp/<each> /mnt/intergenos/build/`. (Same shape for `build/bootloader/*.efi.signed` in ceremony #2.) Verify the delivered shas before resuming. (Documented 2026-06-19 after the bare-copy step failed on root-owned `build/` + stale files during GBC004.1.)

> **Dev/test ISO (NOT a release):** resume `phase_squashfs` with `UNSIGNED_TEST=1`; Step 4.8 stages the explicit `IGOS_DEV_ALLOW_UNVERIFIED` marker and bypasses the integrity gate (Secure-Boot-off test artifact only). A real release REQUIRES the signed triplet — never ship a release with the dev marker.

### Ceremony #2 — GRUB + the 3 UKIs (`sign-bootloader.sh`)

The second pause (`phase_ukis_verity`, after squashfs) signs GRUB + the three UKIs via PIV slot 9c. Its CRITICAL pre-flight — re-staging the current build's UKIs and verifying each UKI's sealed `igos.verity.roothash` equals the current squashfs `ROOT_HASH` before signing — is the Coordinator's step and is documented in full under "UKI staging freshness" below.

## When This Procedure Runs

- Every tagged release of InterGenOS.
- Any time the `pkm` repository index is regenerated and needs to be re-signed.
- Any kernel update that produces a new `vmlinuz-*` we intend to ship.
- Any GRUB update that produces a new `grubx64.efi`.

## Prerequisites

Before starting a signing window:

1. **Hardware token physically present in the signing workstation.** Nitrokey 3 NFC plugged into a USB port you can reach for touch confirmation. One session = one token = one signer.
2. **PIN unlocked.** `gpg --card-status` should list the card and its serial. `pkcs11-tool --list-slots` should list the PIV interface.
3. **Artifacts staged (by the Coordinator).** The Coordinator assembles the build orchestrator's unsigned output into a single staging directory and verifies its freshness (see "UKI staging freshness" below) before the operator is brought in to sign. The operator never stages, copies, or moves any artifact. Staged files:
   - `InterGenOS.db` — the pkm repository index
   - `vmlinuz-<version>-intergenos` — one or more kernel images
   - `grubx64.efi` — the custom GRUB build
   - `intergenos-archive-manifest.txt` — the unsigned archive integrity manifest emitted by `phase_manifest` of `scripts/build-intergenos.sh`
   - `vendor-cert.pem` — the EFI vendor cert that pairs with the PIV-slot-9c private key
4. **Output directory prepared.** A clean destination directory where signed artifacts + detached sigs are written.
5. **Environment configured.** Either via flags to `sign-release.sh` or via env vars:
   - `INTERGENOS_GPG_KEY_ID` — fingerprint of the distro GPG release subkey
   - `INTERGENOS_PKCS11_URI` — PKCS#11 URI for the sbsign private key. Canonical value: `pkcs11:id=%02;type=private` (`%02` = PIV slot 9c). Do **not** embed the PIN (no `pin-value=`/`pin-source=`) — `sign-release.sh` rejects a PIN-bearing URI (B-049) and the OpenSSL pkcs11 engine prompts interactively.
   - `INTERGENOS_GPG_MASTER_KEY_ID` (optional; **set for tagged releases**) — fingerprint of the offline master key. When set, the manifest is cosigned by master + [S1] for release-grade trust. When unset, the manifest is signed by [S1] only (sufficient for routine builds, but `check-manifest-signature.sh` will not assert release-grade).

## Pre-Sign Discipline (Signing Ceremony)

Every signing-release invocation is treated as a ceremony. Before running `sign-release.sh`:

- [ ] Close web browsers, chat clients, and non-essential background tools.
- [ ] Disable screen sharing, remote-assist, and recording software.
- [ ] Confirm no unexpected USB devices are attached.
- [ ] Confirm the workstation's login session has not been idle or locked-and-reopened since the last reboot (minimises stale privileged session risk).
- [ ] Note the start time in a local signing-session log (text file is fine).
- [ ] Verify token presence before any artifact is touched: `gpg --card-status`.

Touch-to-sign protects against a compromised host silently signing on your behalf. The pre-sign checklist is defence-in-depth — minimise concurrent attack surface during the touch-required window.

## UKI staging freshness — verify the verity root hash before signing (CRITICAL)

The UKI/bootloader signing ceremony (`scripts/sign-bootloader.sh`) reads its
**input** unsigned binaries from a staging directory (`/tmp/c6r2-bootloader/` by
default), **not** directly from the build output. That staging dir can retain
unsigned UKIs left over from a *prior* ceremony. Signing reproduces the input's
embedded kernel cmdline verbatim — it only appends a PE signature — so a stale
unsigned UKI signs into a stale **signed** UKI, and nothing downstream catches it.

Each live/installer UKI seals the squashfs's dm-verity root hash into its cmdline
(`igos.verity.roothash=<hash>`, set by `phase_ukis_verity`). If the signed UKI's
hash does not match the squashfs actually shipped on the ISO, **dm-verity fails at
boot** and the firmware/initramfs reports the *stale* root hash.

**This is the Coordinator's step, never the operator's.** Before every ceremony the
Coordinator moves the OLD artifacts out and the NEW ones in:

1. **Re-stage.** Move whatever is in the signing dir aside, then copy the *current
   build's* unsigned UKIs + GRUB from the build output into it:
   ```
   mkdir -p /tmp/c6r2-bootloader/stale-backup-<date>
   mv -f /tmp/c6r2-bootloader/*.efi*  /tmp/c6r2-bootloader/stale-backup-<date>/   # OLD out
   cp -f /mnt/intergenos/build/bootloader/{grubx64,igos-live,igos-install-gui,igos-install-tui}.efi \
         /tmp/c6r2-bootloader/                                                    # NEW in
   ```
2. **Verify the root hash matches the current squashfs** (the gate that catches a
   stale stage):
   ```
   for f in igos-live igos-install-gui igos-install-tui; do
     objcopy -O binary --only-section=.cmdline /tmp/c6r2-bootloader/$f.efi /dev/stdout \
       | tr -d '\0' | grep -o 'roothash=[0-9a-f]*'
   done
   grep ROOT_HASH /mnt/intergenos/build/filesystem.squashfs.verity-params
   ```
   All three UKI roothashes **must** equal the squashfs `ROOT_HASH`. If any differ,
   STOP — the staging dir holds a stale UKI; re-stage before signing.
3. **After signing, before rebuilding the ISO,** re-verify the *signed* UKIs carry
   the same matching hash (confirm signing consumed the staged inputs, not a cache).

**Observed failure (2026-06-04):** the signing dir held June-2 unsigned UKIs
(`roothash=88f774b0…`); they were signed and shipped against a June-4 squashfs
(`ROOT_HASH=06f9d432…`). The ISO booted to a dm-verity failure displaying the stale
`88f774b0` hash. Root cause: the fresh UKIs were never copied into the signing dir.
**Durable fix (tracked):** `sign-bootloader.sh` should self-stage from
`build/bootloader/` and assert each UKI's `igos.verity.roothash` equals the current
squashfs `ROOT_HASH`, aborting on mismatch — so this Coordinator step cannot be
skipped and the operator's job stays exactly: run the script, sign, report done.

## Running the Signature Steps

**The operator's entire role is here: run one wrapper per pause, enter the PIN
when prompted, then report completion to the Coordinator. All staging and
freshness-verification is already done (by the Coordinator) before this point —
the operator stages nothing.**

**Use the per-pause wrappers, not the superset.** A release build halts twice,
and each pause has its own wrapper, matching the table at the top of this
document:

```
# Pause 1 — end of phase_manifest, BEFORE squashfs. NO sudo.
bash scripts/sign-manifest.sh

# Pause 2 — end of phase_ukis_verity, AFTER squashfs.
sudo bash scripts/sign-bootloader.sh
```

Pause 1 signs with the OpenPGP subkey and requires the **User PIN plus an
on-card touch** (UIF policy — watch the LED). Pause 2 signs GRUB and the three
Unified Kernel Images against PIV slot 9c. That slot is `ALWAYS_AUTHENTICATE`,
so the engine demands authentication per signature; `scripts/sign-pty-feeder.py`
answers those per-binary prompts from a single capture, so **the operator types
the PIN once** for the whole pause. The PIN is never placed in a command line,
an environment variable, or a log. Per-operation authentication is intact — the
prompts are answered, not removed. If the feeder is absent the flow degrades to
the older behaviour of prompting per binary, which is safe, just tedious.

### The automated superset

`scripts/sign-release.sh` performs the same signing operations in one
non-interactive pass. **It is the CI/automation path and is not the human
ceremony** — do not hand it to the operator in place of the two wrappers above.
For reference, it performs, in order:

1. **Token presence check.** Fails fast with exit code 1 if no OpenPGP card is visible.
2. **Key-material configuration check.** Fails fast with exit code 2 if `--gpg-key-id` or `--pkcs11-uri` (or their env-var equivalents) are not set.
3. **pkm repo index (`InterGenOS.db`).** Distro GPG subkey. `gpg --detach-sign --armor` produces `InterGenOS.db.sig`. **One touch.**
4. **Kernel `vmlinuz-*` images.** PIV-slot-9c EFI X.509 key via `sbsign --engine pkcs11`. **One authentication per image** (the slot is `ALWAYS_AUTHENTICATE`).
5. **GRUB `grubx64.efi`.** Same PIV-slot-9c EFI X.509 key. **One authentication.**
6. **Archive integrity manifest (`intergenos-archive-manifest.txt`).** Distro GPG subkey ([S1]) + optional master cosignature when `INTERGENOS_GPG_MASTER_KEY_ID` is set. Produces three outputs in the signed directory: the canonical `intergenos-archive-manifest.txt`, the detached `intergenos-archive-manifest.txt.sig`, and `intergenos-release-key.asc` (public key export so the install-time verifier can self-validate without external network). **One touch for [S1]; one additional touch for master cosign when present.** Pre-emits a sanity gate: refuses to sign manifests missing the v1 header, the BSD SHA256 entries, or the terminator line — a malformed manifest signed at this stage would be cryptographically valid but break install-time `PHASE_VERIFY`'s parser.

If any expected artifact is missing, the script skips that step with a log line. Pass `--strict` to fail instead of skipping.

## Verification

After signing, verify locally before publishing:

```
# pkm repo index
gpg --verify /path/to/signed/InterGenOS.db.sig /path/to/signed/InterGenOS.db

# Kernel + GRUB
sbverify --cert /path/to/unsigned/vendor-cert.pem /path/to/signed/vmlinuz-*
sbverify --cert /path/to/unsigned/vendor-cert.pem /path/to/signed/grubx64.efi

# Archive integrity manifest — full Q14-style precheck
bash scripts/check-manifest-signature.sh \
     /path/to/signed/intergenos-archive-manifest.txt \
     /path/to/signed/intergenos-archive-manifest.txt.sig \
     /path/to/signed/intergenos-release-key.asc
```

Every `sbverify` should report "Signature verification OK." Every `gpg --verify` should print the expected signing subkey fingerprint with "Good signature." The `check-manifest-signature.sh` step asserts (1) manifest BSD format integrity, (2) signature verifies under the embedded release-key (the same key material the user will see embedded in the ISO at `/install/intergenos-release-key.asc`), and (3) master cosignature presence when `INTERGENOS_GPG_MASTER_KEY_ID` was set during signing. **Run this before passing the signed manifest into `build-iso.sh` for ISO embedding** — a malformed-but-validly-signed manifest at this gate is the last chance to catch it before the ISO is sealed.

## Post-Sign

1. **Hand signed artifacts back to the build orchestrator.** Either write them to the shared virtiofs mount that the igos-build VM can read, or scp them to the build-output directory the orchestrator expects.
2. **Log the session.** Append to the signing-session log: end time, what was signed, any touch-count anomalies, any warnings observed.
3. **Remove the token from the workstation.** Back to secure storage.
4. **Close the workstation session.** Lock or log out before returning to normal work.

## Recovery from Aborted Session

If the signing window is interrupted (token removed mid-run, process killed, power loss):

- **Nothing is ever signed partially.** `sign-release.sh` fails-fast on token unavailability and each sign step either completes or is absent entirely. Partial signatures are not a possible state.
- **Re-run the script on the same artifacts.** The sign operations are idempotent when run against a clean output directory. If the previous run produced a partial output dir, delete it and re-run.
- **Do not re-run against the output dir as its own artifacts dir.** The script is not designed to re-sign already-signed artifacts.

## Compromise Response

If the token is lost, stolen, or believed compromised:

- Stop using the primary subkey immediately.
- Follow the trust-anchor compromise policy in `SECURITY.md` (immediate acknowledgment, 6-hour revocation + new keyring package, simultaneous public disclosure).
- Publish the revocation certificate (pre-generated at root-key ceremony, stored offline) to `keys.openpgp.org`.
- Issue a new subkey signed by the offline root.
- Push a keyring update via `pkm` so users rotate before the dbx update path takes effect.

## Appendix B — Nitrokey 3 NFC First-Touch Checklist

This appendix covers the steps that run the **first time** a fresh-from-box Nitrokey 3 NFC is plugged into the signing workstation — before the key-generation ceremony itself. It takes a Nitrokey from factory-out-of-box state to ceremony-ready state. Run it once per device, on each of the Nitrokeys planned for production use.

The signing-ceremony procedure documented in `docs/research/installer/signing_key_custody_2026-04-18.md` assumes the cards are already in this ceremony-ready state; this appendix fills the gap.

### Scope notation

The release plan uses four physical Nitrokeys:

- **#1** — primary daily-driver (lives with the maintainer)
- **#2** — home-safe backup
- **#3** — bank safety-deposit-box backup
- **#4** — spare / test card

This checklist runs on each of the four. The test-cert dry-run in step 7 runs **only on #4** before any real ceremony key material exists on any device, so a flow that fails leaves no key material at risk.

### Steps

1. **Visual + packaging inspection.** Tamper-evident packaging intact. Serial visible on the unit through the window or on the back of the device matches the carrier's manifest. No signs of prior opening, label-shift, or shrinkwrap reseal. If anything looks off, do not use the device — surface to the security contact and request a replacement through the original purchase channel.

2. **Plug + enumerate.** Insert the Nitrokey into a USB-A port directly on the workstation (avoid hubs for the first-touch). Confirm the device enumerates cleanly:

    ```
    lsusb | grep -i nitrokey       # Vendor 20a0, product 42b1 expected for Nitrokey 3 NFC
    dmesg | tail -20               # Clean USB enumeration; no error lines
    ```

    If `lsusb` does not show the device or `dmesg` shows enumeration errors, replug into a different port and retry. Persistent failure is a defect; do not use the device.

3. **Factory-PIN verification.** Confirm the device responds to factory-default PINs on both applets it ships with:

    ```
    # OpenPGP applet — factory user PIN 123456, admin PIN 12345678
    gpg --card-status

    # PIV applet — factory user PIN 123456, PUK 12345678
    pkcs11-tool --module /usr/lib/opensc-pkcs11.so --list-slots
    pkcs11-tool --module /usr/lib/opensc-pkcs11.so --login --pin 123456 --list-objects
    ```

    Both applets must respond. If either fails to respond to its factory PIN, the device may be pre-personalised or defective — do not use it.

4. **Set new PINs.** Replace all factory PINs with new values picked by the maintainer. Record each PIN on paper at the time it is set; do not store electronically. Run on each applet:

    ```
    # OpenPGP applet — change user PIN, then admin PIN
    gpg --card-edit
    > admin
    > passwd
    # menu: 1 (user PIN), 3 (admin PIN), 0 (quit)

    # PIV applet — change user PIN
    pkcs11-tool --module /usr/lib/opensc-pkcs11.so \
        --login --pin 123456 --change-pin --new-pin <new-user-pin>

    # PIV applet — change PUK (use opensc utility variant for the PIN unblock key)
    yubico-piv-tool --action change-puk --pin <new-user-pin> \
        --current-puk 12345678 --new-puk <new-puk>
    ```

    PIN selection guidance: 6-8 digit PINs. Avoid birthdays, sequences, or repeats. Different values per applet.

5. **Touch-policy verification.** Confirm the device requires physical touch on the operations that matter:

    ```
    # OpenPGP signing slot [S] — Nitrokey 3 default = touch-required for sign
    gpg --card-edit
    > admin
    > uif S on               # Confirms or sets touch-required for signing operations

    # PIV slot 9c — touch-policy "always" or "cached" for signing
    yubico-piv-tool --action read-object --slot 9c --hex
    # If the slot reports touch_policy = NEVER, set it to CACHED or ALWAYS:
    yubico-piv-tool --action change-touch-policy --slot 9c --touch-policy=cached
    ```

    Touch-required protects against a compromised host silently signing without the maintainer present. This is non-negotiable for ceremony devices.

6. **Card-identity recording.** Each of the four physical Nitrokeys is treated as distinct hardware. Record both the OpenPGP Application ID and the PIV token serial for each device:

    ```
    gpg --card-status                              # Application ID line
    pkcs11-tool --module /usr/lib/opensc-pkcs11.so --list-slots
    ```

    Label the device on the back with a Sharpie matching its assigned slot (1, 2, 3, or 4 per the scope notation above). Record the Application ID + token serial in the maintainer's offline log alongside the slot number. This makes any later "which card am I holding" question a one-glance answer.

7. **Test-cert dry-run on Nitrokey #4 only.** Validate the full PIV slot-9c PKCS#11 write-and-verify flow on the test card before any real ceremony key material exists. The PIV PIN-Always policy can defeat sessions that do not re-issue VERIFY immediately before a write; better to surface that on the test card:

    ```
    # On Nitrokey #4 only — generate a throwaway test keypair in slot 9c,
    # write a self-signed test cert, verify the read-back round-trip.
    # Per D-016, scratch artifacts live under ~/tmp/<workflow>/, not /tmp.
    mkdir -p ~/tmp/piv-9c-dry-run
    yubico-piv-tool --action generate --slot 9c --algorithm RSA2048 \
        --output ~/tmp/piv-9c-dry-run/test-9c-pubkey.pem
    yubico-piv-tool --action verify-pin --pin <new-user-pin> \
        --action selfsign-certificate --slot 9c \
        --subject "/CN=intergen-test-9c-throwaway" \
        --input ~/tmp/piv-9c-dry-run/test-9c-pubkey.pem --output ~/tmp/piv-9c-dry-run/test-9c-cert.pem
    yubico-piv-tool --action read-certificate --slot 9c

    # Confirm the read-back cert matches what was just written
    diff ~/tmp/piv-9c-dry-run/test-9c-cert.pem <(yubico-piv-tool --action read-certificate --slot 9c)

    # Factory-reset slot 9c to clear the test material before ceremony
    yubico-piv-tool --action reset --slot 9c
    rm -f ~/tmp/piv-9c-dry-run/test-9c-*.pem
    ```

    If any step fails, surface to the security contact and resolve the underlying issue (typically PIN-Always policy interaction with the chosen tooling) before running the real ceremony. If all steps pass, Nitrokey #4 is now back to a clean post-test state and can rejoin the spare-pool.

8. **Pre-ceremony resting state.** Each Nitrokey is now ready to enter the signing ceremony:

    - [ ] Device is back in its packaging or a labeled pouch, slot-number visible.
    - [ ] New PINs are written on paper, stored separately from the device.
    - [ ] Application ID + token serial recorded in the maintainer's offline log against the slot number.
    - [ ] Touch-policy verified for both OpenPGP `[S]` and PIV slot 9c.
    - [ ] Test-cert dry-run completed on Nitrokey #4 (this step runs once globally, not per-device).

When all four Nitrokeys reach this state, the ceremony procedure in `signing_key_custody_2026-04-18.md` can run.

### References

- Nitrokey 3 PIV documentation: <https://docs.nitrokey.com/nitrokeys/features/piv/certificate_management>
- Nitrokey 3 OpenPGP documentation: <https://docs.nitrokey.com/nitrokeys/features/openpgp>
- `yubico-piv-tool` is the de-facto cross-vendor CLI for PIV slot operations on PKCS#11-compatible devices including Nitrokey 3 NFC. Equivalent operations are available via `nitropy nk3 piv` for the Nitrokey-native tooling path.

## See Also

- [SECURITY.md](../SECURITY.md) — disclosure policy, trust-anchor compromise response, security contacts.
- [docs/signing-key.md](signing-key.md) — fingerprint publication + verification instructions.
- [docs/research/installer/signing_key_custody_2026-04-18.md](research/installer/signing_key_custody_2026-04-18.md) — full design rationale, decision history, alternatives considered.
