# InterGenOS Release Signing Key

**Last updated:** 2026-05-05
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
| EFI cert fingerprint (SHA-256, DER) | `7B:8F:21:50:B5:D0:0C:7B:28:DD:51:8F:AD:D7:0B:C0:E8:37:AE:43:DF:7B:5E:23:D6:18:5E:9C:75:30:C8:76` |
| EFI cert PEM file SHA-256 (transport integrity) | `8ce749e7e77169205e4761d82b48a4333f48cdec2ee0f711b8cff560fe150514` |
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

## Contact

Security reports: `security@intergenstudios.com` — see [SECURITY.md](../SECURITY.md) for the full disclosure policy and PGP-encryption expectations.
