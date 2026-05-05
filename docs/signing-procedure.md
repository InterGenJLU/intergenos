# InterGenOS Signing Procedure

**Last updated:** 2026-04-21
**Applies to:** the signing workstation (primary maintainer) during a release-signing window.

This is the operational runbook for signing an InterGenOS release. It covers the distro GPG repo-index signature and the EFI-binary signatures for the kernel and GRUB. Kernel module signing is ephemeral per-build and is handled inside the kernel build itself — it does not appear in this procedure.

For the decisions and rationale behind this architecture, see `docs/research/installer/signing_key_custody_2026-04-18.md`.

## Trust Chain Attestation

Each signature this procedure produces attests to one layer of the InterGenOS trust chain:

- **`InterGenOS.db.sig`** (distro GPG subkey [S1] on Nitrokey #1) — signs the pkm repository index. The index records per-file SHA-256 for every file in every package, so signing the index is a transitive attestation of every file in the distribution. Recipients verifying the index signature can subsequently run `pkm verify --strict <package>` to re-check any installed file against its signed hash. (Index format extended for per-file content-hash at commit `c9534f7`.)
- **`vmlinuz-<version>-intergenos.sig`** (PIV slot 9c X.509 via `sbsign`) — signs the kernel EFI binary. shim verifies this signature against the embedded vendor cert when Secure Boot is active.
- **`grubx64.efi.sig`** (PIV slot 9c X.509 via `sbsign`) — signs the GRUB EFI binary. Same shim verification path.

The kernel-module signing key (ephemeral, per-build) and end-user MOK enrollments are orthogonal to this procedure — they live inside the kernel build and per-install respectively, not at release-signing time.

## When This Procedure Runs

- Every tagged release of InterGenOS.
- Any time the `pkm` repository index is regenerated and needs to be re-signed.
- Any kernel update that produces a new `vmlinuz-*` we intend to ship.
- Any GRUB update that produces a new `grubx64.efi`.

## Prerequisites

Before starting a signing window:

1. **Hardware token physically present in the signing workstation.** Nitrokey 3 NFC plugged into a USB port you can reach for touch confirmation. One session = one token = one signer.
2. **PIN unlocked.** `gpg --card-status` should list the card and its serial. `pkcs11-tool --list-slots` should list the PIV interface.
3. **Artifacts staged.** Unsigned artifacts available in a single directory, produced by the build orchestrator. Expected files:
   - `InterGenOS.db` — the pkm repository index
   - `vmlinuz-<version>-intergenos` — one or more kernel images
   - `grubx64.efi` — the custom GRUB build
   - `vendor-cert.pem` — the EFI vendor cert that pairs with the PIV-slot-9c private key
4. **Output directory prepared.** A clean destination directory where signed artifacts + detached sigs are written.
5. **Environment configured.** Either via flags to `sign-release.sh` or via env vars:
   - `INTERGENOS_GPG_KEY_ID` — fingerprint of the distro GPG release subkey
   - `INTERGENOS_PKCS11_URI` — PKCS#11 URI for the sbsign private key

## Pre-Sign Discipline (Signing Ceremony)

Every signing-release invocation is treated as a ceremony. Before running `sign-release.sh`:

- [ ] Close web browsers, chat clients, and non-essential background tools.
- [ ] Disable screen sharing, remote-assist, and recording software.
- [ ] Confirm no unexpected USB devices are attached.
- [ ] Confirm the workstation's login session has not been idle or locked-and-reopened since the last reboot (minimises stale privileged session risk).
- [ ] Note the start time in a local signing-session log (text file is fine).
- [ ] Verify token presence before any artifact is touched: `gpg --card-status`.

Touch-to-sign protects against a compromised host silently signing on your behalf. The pre-sign checklist is defence-in-depth — minimise concurrent attack surface during the touch-required window.

## Running the Signature Steps

All three steps run via a single script:

```
scripts/sign-release.sh \
    --artifacts /path/to/unsigned \
    --output    /path/to/signed
```

The script performs, in order:

1. **Token presence check.** Fails fast with exit code 1 if no OpenPGP card is visible.
2. **Key-material configuration check.** Fails fast with exit code 2 if `--gpg-key-id` or `--pkcs11-uri` (or their env-var equivalents) are not set.
3. **pkm repo index (`InterGenOS.db`).** Distro GPG subkey. `gpg --detach-sign --armor` produces `InterGenOS.db.sig`. **One touch.**
4. **Kernel `vmlinuz-*` images.** PIV-slot-9c EFI X.509 key via `sbsign --engine pkcs11`. **One touch per kernel image.**
5. **GRUB `grubx64.efi`.** Same PIV-slot-9c EFI X.509 key. **One touch.**

If any expected artifact is missing, the script skips that step with a log line. Pass `--strict` to fail instead of skipping.

## Verification

After signing, verify locally before publishing:

```
# pkm repo index
gpg --verify /path/to/signed/InterGenOS.db.sig /path/to/signed/InterGenOS.db

# Kernel + GRUB
sbverify --cert /path/to/unsigned/vendor-cert.pem /path/to/signed/vmlinuz-*
sbverify --cert /path/to/unsigned/vendor-cert.pem /path/to/signed/grubx64.efi
```

Every `sbverify` should report "Signature verification OK." Every `gpg --verify` should print the expected signing subkey fingerprint with "Good signature."

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
    # write a self-signed test cert, verify the read-back round-trip
    yubico-piv-tool --action generate --slot 9c --algorithm RSA2048 \
        --output /tmp/test-9c-pubkey.pem
    yubico-piv-tool --action verify-pin --pin <new-user-pin> \
        --action selfsign-certificate --slot 9c \
        --subject "/CN=intergen-test-9c-throwaway" \
        --input /tmp/test-9c-pubkey.pem --output /tmp/test-9c-cert.pem
    yubico-piv-tool --action read-certificate --slot 9c

    # Confirm the read-back cert matches what was just written
    diff /tmp/test-9c-cert.pem <(yubico-piv-tool --action read-certificate --slot 9c)

    # Factory-reset slot 9c to clear the test material before ceremony
    yubico-piv-tool --action reset --slot 9c
    rm -f /tmp/test-9c-*.pem
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
