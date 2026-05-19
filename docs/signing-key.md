# InterGenOS Release Signing Key

**Last updated:** 2026-05-13
**Status:** LIVE. Master pubkey published to `keys.openpgp.org` (verified by email). All four hardware tokens carry signing subkeys, UIF touch-policy on. PIV vendor cert (NK#1 slot 9c) generated and rotated. Drive #3 master backup secured.

## Summary

InterGenOS release artifacts are signed with a PGP key whose master is generated and held offline. Verifying the key fingerprint against this page **and** at least one other independent source before trusting it on your machine protects you from MITM tampering during package download.

This page is the **canonical fingerprint publication** for the release signing key. Cross-publication locations are listed below; every copy of the fingerprint on the internet should match the value here. If they don't, do not trust the mismatched source.

## Release Key

| Field | Value |
|---|---|
| Primary key UID | `InterGenOS Project Signing Key (primary) <intergenos-primary@intergenstudios.com>` |
| Algorithm | RSA-4096 master + 1 enc sub + 4 sign subs (one per Nitrokey) |
| Master fingerprint | `5597 A3E0 587B 2530 06D0  DD7B 8C50 8261 8208 3050` |
| Encryption subkey fingerprint | `62C7 E2C3 0908 823D AF5E  4EBF 917B 649E 00F2 868C` |
| Signing subkey [NK#1] | `D7AA 641D 81AC D690 C5AD  865E 7276 E14D D888 6BFE` (Nitrokey 3 NFC, OpenPGP serial `B9753481`; project daily-driver, also holds the EFI vendor cert in PIV slot 9c) |
| Signing subkey [NK#2] | `81DD 223F 9BA9 B3F2 AFBF  FC5A FA24 B042 975F 775E` (Nitrokey 3 NFC, OpenPGP serial `43D33126`; local backup token) |
| Signing subkey [NK#3] | `B34D 3D3F B5EA DFC4 80ED  BDB0 D3C5 DF2C C73B 67ED` (Nitrokey 3 NFC, OpenPGP serial `730D5185`; secondary maintainer's daily-driver) |
| Signing subkey [NK#4] | `99B3 E755 5064 180D C9CE  3284 32AE E441 15DE AAED` (Nitrokey 3 NFC, OpenPGP serial `CC1D07E3`; secondary maintainer's backup) |
| EFI-binary signing cert | CN `InterGenOS Secure Boot CA`, on Nitrokey #1 PIV slot 9c |
| EFI cert fingerprint (SHA-256, DER) | `61:8E:74:48:52:B5:8E:5F:01:C9:B0:59:7F:16:04:D4:C8:73:48:38:69:CE:8F:4E:F2:89:9C:36:AA:D9:5B:38` |
| EFI cert PEM file SHA-256 (transport integrity) | `cd34977e6efa37a572a9835c111a7d563809edbe838b1764be35100279d2c172` |
| Master expiry | None (revocation cert held in LUKS backup) |
| Subkey expiry | 2 years from issue (2028-05-04) |
| UIF (touch-to-sign) | Enabled on signing slot of all four NKs |
| Hardware | Nitrokey 3 NFC × 4 |
| Root custody | Air-gapped Tails 7.7 generation; LUKS-encrypted master-secret backup on Drive #3 (offline, in physical safe); base16 paperkey backup; revocation cert in same LUKS volume |

## Verification

### Fetch from `keys.openpgp.org`

```
gpg --keyserver keys.openpgp.org --recv-keys 5597A3E0587B253006D0DD7B8C50826182083050
gpg --fingerprint 5597A3E0587B253006D0DD7B8C50826182083050
```

The cards' on-card URL is `https://keys.openpgp.org/vks/v1/by-fingerprint/5597A3E0587B253006D0DD7B8C50826182083050` — `gpg --card-status` will auto-fetch when online.

### Fetch from `keyserver.ubuntu.com`

```
gpg --keyserver hkps://keyserver.ubuntu.com --recv-keys 5597A3E0587B253006D0DD7B8C50826182083050
gpg --fingerprint 5597A3E0587B253006D0DD7B8C50826182083050
```

This is the SKS-style keyserver Debian/Ubuntu workflows hit by default; useful when `keys.openpgp.org` is blocked or unreachable.

### Fetch from this repo

The pubkey is committed alongside this page as [`signing-key.asc`](signing-key.asc):

```
gpg --import signing-key.asc
gpg --fingerprint 5597A3E0587B253006D0DD7B8C50826182083050
```

### Verify a release artifact

```
# Repository index or release tarball
gpg --verify InterGenOS.db.sig InterGenOS.db

# Kernel / GRUB EFI binary (uses the EFI cert, not the PGP key)
sbverify --cert intergenos-vendor-cert.pem vmlinuz-<version>
sbverify --cert intergenos-vendor-cert.pem grubx64.efi
```

`gpg --verify` must print the expected fingerprint and "Good signature." `sbverify` must print "Signature verification OK."

### Cross-check the fingerprint

Before you trust a keyserver response, confirm the fingerprint appears identically in **at least three** of the locations below:

- This page (`docs/signing-key.md` in the InterGenOS repo, git-tracked)
- The committed pubkey at `docs/signing-key.asc` (git-tracked, same repo)
- The keyserver response from `keys.openpgp.org`
- The keyserver response from `keyserver.ubuntu.com`
- The pinned fingerprint announcement on the [InterGenOS GitHub releases page](https://github.com/InterGenJLU/intergenos/releases)
- (Future) `https://intergenstudios.com/signing-key` (TLS, maintainer-operated)
- (Future) The signed fingerprint announcement (offline-root signed — published alongside subkey rollover)

If any two sources disagree on the fingerprint, assume your network path is compromised and do not trust the key.

## Key Hierarchy

| Key | Purpose | Lifetime | Custody |
|---|---|---|---|
| GPG master (RSA-4096) | Certifies the signing subkeys, signs rollover announcements | No expiry | Offline, air-gapped — generated on Tails 7.7; LUKS USB backup; base16 paperkey; revocation cert |
| GPG signing subkeys × 4 (RSA-4096) | Sign `pkm` repo indexes and release artifacts | 2 years (2026-05-05 → 2028-05-04) | Hardware tokens (Nitrokey 3 NFC); touch-to-sign UIF enabled |
| GPG encryption subkey (RSA-4096) | Receive encrypted bug reports / responsible-disclosure submissions | None (rotates with master) | Disk-only on the offline keyring (not card-bound) |
| EFI-binary X.509 (PIV slot 9c on NK#1) | Signs kernel vmlinuz and custom GRUB | 2 years | Hardware token (NK#1 PIV applet); rotated AES-256 management key |
| Machine Owner Key (MOK) | Signs DKMS / out-of-tree modules on the end-user machine | Per-install | **End user** — generated at first-boot by Forge installer; not distro-held |

Detailed design rationale in `docs/research/installer/signing_key_custody_2026-04-18.md`.

## Publication

The release key is published at:

1. **`keys.openpgp.org`** — published 2026-05-05, email-verified. Searchable by fingerprint; searchable by email after the verification click. The role UID `intergenos-primary@intergenstudios.com` is a project-role identity, not personal.
2. **`keyserver.ubuntu.com`** — published 2026-05-05 (SKS-style; not email-verified, accepts the key as-is). Default target for `apt-key adv --keyserver` and many Debian/Ubuntu signing-key workflows.
3. **This repo** — `docs/signing-key.md` (this page) and `docs/signing-key.asc` (the armored pubkey), git-tracked.
4. **(Future) GitHub releases page** — pinned announcement referencing the fingerprint.
5. **(Future) `intergenstudios.com`** — TLS-served, maintainer-operated.
6. **(Future) Signed-by-master fingerprint announcement** — published alongside subkey rollover.

## Rollover

Subkeys rotate on a 2-year cadence; next rotation 2028-05-04.

1. Generate a fresh signing subkey from the offline master on a fresh Tails 7.7 air-gapped session.
2. `keytocard` the new sub to the same physical NK that held the previous one.
3. Publish the new subkey signed by the old one + by the master.
4. Cross-publish the new fingerprint via every channel above.
5. Continue signing releases with the old subkey for a 30-day overlap window so `pkm update` clients pick up the new keyring before the old subkey is revoked.
6. Revoke the old subkey.

Emergency rollover (compromise): see the trust-anchor compromise policy in [SECURITY.md](../SECURITY.md). 6-hour-to-revocation SLA applies; the orderly 30-day overlap does not.

### Multi-Key Trust Window (Operator Procedure)

The 30-day overlap window above requires the end-user-side keyring + pkm verifier to trust both the outgoing and incoming subkeys simultaneously. Two coordinated artifacts make this work:

| Artifact | What changes during overlap | What changes after overlap |
|---|---|---|
| `docs/signing-key-next.asc` | Operator commits this file containing the incoming subkey's ASCII-armored pubkey (signed by master per step 3 above). `packages/core/intergenos-keyring/build.sh` imports it into `/etc/pkm/trusted.gpg` alongside `docs/signing-key.asc` when present. | Operator removes this file and re-exports `docs/signing-key.asc` with only the new subkey active. |
| `pkm/release-keys.json` `keys` dict | Operator adds a new entry (e.g. `S5`) with the incoming subkey's 40-char fingerprint, role label, and aliases. `pkm/repo.py:_load_pinned_fingerprints` natively unions all entries — no parser change required. | Operator removes the retired subkey's entry (the one whose key was revoked at step 6). |

Step-by-step (apply between steps 4 and 5 of the §Rollover procedure above, then again after step 6):

**Begin overlap window** (after the new subkey has been published and cross-attested):

1. ASCII-armor-export the incoming subkey pubkey to `docs/signing-key-next.asc` (signed by master so transparency-log verifiers can chain the rotation evidence — see [repository-trust.md §4 Key Rotation](repository-trust.md#4-key-rotation) and §5 transparency log).
2. Add a new entry under `keys` in `pkm/release-keys.json`, naming it `S5` (or the next available slot) with the incoming subkey's fingerprint, role label (e.g. "transition signing (Nitrokey NK5 serial XXXXXXXX)"), and aliases.
3. Commit both files in a single coordinated commit so reviewers see the artifact + the pin update together.
4. Bump `packages/core/intergenos-keyring/package.yml` version (e.g. `0.1.0` → `0.2.0`) so end users pick up the dual-key keyring via `pkm upgrade intergenos-keyring`.
5. Rebuild + publish `intergenos-keyring` through the normal release pipeline.

**End overlap window** (after the outgoing subkey has been revoked at step 6):

1. Delete `docs/signing-key-next.asc`.
2. Re-export `docs/signing-key.asc` with only the new subkey active (the old subkey is revoked at the master keyring level, so its presence in the bundle no longer implies trust — but cleaning it from the bundle removes the failed-trust-decision noise from `gpg --verify` output).
3. Remove the retired subkey's entry from `pkm/release-keys.json`.
4. Bump `intergenos-keyring` version again (e.g. `0.2.0` → `0.3.0`).
5. Rebuild + publish.

End-user effect: across the rotation window, `pkm update` clients fetch the dual-key `intergenos-keyring` package once, then quietly accept signatures from either the outgoing or the incoming subkey until the overlap ends. No client-side configuration change required. No `pkm` CLI surface for trust-anchor management — rotation flows through git commits + standard package upgrades, keeping the trust state inspectable and audit-friendly per the Prime Directive.

## Contact

Security reports: `security@intergenstudios.com` — see [SECURITY.md](../SECURITY.md) for the full disclosure policy and PGP-encryption expectations.
