# InterGenOS Signing Procedure

**Last updated:** 2026-04-21
**Applies to:** the signing workstation (primary maintainer) during a release-signing window.

This is the operational runbook for signing an InterGenOS release. It covers the distro GPG repo-index signature and the EFI-binary signatures for the kernel and GRUB. Kernel module signing is ephemeral per-build and is handled inside the kernel build itself — it does not appear in this procedure.

For the decisions and rationale behind this architecture, see `docs/research/installer/signing_key_custody_2026-04-18.md`.

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

## See Also

- [SECURITY.md](../SECURITY.md) — disclosure policy, trust-anchor compromise response, security contacts.
- [docs/signing-key.md](signing-key.md) — fingerprint publication + verification instructions.
- [docs/research/installer/signing_key_custody_2026-04-18.md](research/installer/signing_key_custody_2026-04-18.md) — full design rationale, decision history, alternatives considered.
