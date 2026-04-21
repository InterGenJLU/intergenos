# InterGenOS Release Signing Key

**Last updated:** 2026-04-21
**Status:** Placeholder — key ceremony pending hardware arrival (Nitrokey 3 NFC, in transit from Nitrokey GmbH, ETA 2026-04-28 – 2026-05-02).

## Summary

InterGenOS release artifacts are signed with a PGP key that chains to an offline root. Verifying the key before trusting it on your machine protects you from man-in-the-middle tampering during package download.

This page is the **canonical fingerprint publication** for the release signing key. Cross-publication locations are listed below; every copy of the fingerprint on the internet should match the value here. If they don't, do not trust the mismatched source.

## Release Key

| Field | Value |
|---|---|
| Role UID | `InterGenOS Release Key` |
| Algorithm | *(to be published post-ceremony — RSA 4096 or Ed25519)* |
| Primary fingerprint | *`PENDING — ceremony scheduled for the week of 2026-04-28`* |
| Release-signing subkey fingerprint | *`PENDING`* |
| EFI-binary signing cert CN | `InterGenOS Secure Boot CA` |
| EFI-binary signing key fingerprint | *`PENDING`* |
| Hardware | Nitrokey 3 NFC (2 units — primary + backup) |
| Root custody | Offline, Tails-generated, paper + 2× LUKS USB (home safe + bank SDB) |
| Primary expiry | None on root; 2 years on subkeys |

## Verification

Once the ceremony is complete and fingerprints are populated above:

### Fetch from keys.openpgp.org

```
gpg --keyserver keys.openpgp.org --recv-keys <FINGERPRINT>
```

### Verify a release artifact

```
# Repository index
gpg --verify InterGenOS.db.sig InterGenOS.db

# Kernel / GRUB EFI binary (uses the EFI cert, not the PGP key)
sbverify --cert intergenos-vendor-cert.pem vmlinuz-<version>
sbverify --cert intergenos-vendor-cert.pem grubx64.efi
```

`gpg --verify` must print the expected fingerprint and "Good signature." `sbverify` must print "Signature verification OK."

### Cross-check the fingerprint

Before you trust the keyserver response, confirm the fingerprint appears identically in **at least three** of the locations below:

- This page (`docs/signing-key.md` in the InterGenOS repo, git-tracked)
- The pinned fingerprint announcement on [InterGenOS GitHub releases](https://github.com/InterGenJLU/intergenos/releases)
- `https://intergenstudios.com/signing-key` (TLS, maintainer-operated)
- The signed fingerprint announcement (offline-root signed — published alongside the subkey rollover)

If any two sources disagree on the fingerprint, assume your network path is compromised and do not trust the key.

## Key Hierarchy

Three distro keys, one not-our-key, covered here for context. Detailed design rationale in `docs/research/installer/signing_key_custody_2026-04-18.md`.

| Key | Purpose | Lifetime | Custody |
|---|---|---|---|
| Distro GPG root | Certifies the signing subkey, signs rollover announcements | 5 years | Offline, air-gapped (Tails generation + paper + LUKS USB x2) |
| Distro GPG signing subkey | Signs `pkm` repo indexes and release artifacts | 2 years | Hardware token (primary + backup), touch required |
| EFI-binary X.509 (PIV 9c) | Signs kernel vmlinuz and custom GRUB | 2 years | Same hardware token (PIV slot 9c), touch required |
| Machine Owner Key (MOK) | Signs DKMS / out-of-tree modules on the end-user machine | Per-install | **End user** — generated at first-boot by Forge installer; not distro-held |

## Publication

Once generated, the release key is published to:

1. **`keys.openpgp.org`** — role-UID only, no personal email in the UID (D1-6 hardening).
2. **This doc** — repo-tracked, git-signed.
3. **GitHub releases page** — pinned announcement referencing the fingerprint.
4. **intergenstudios.com** — TLS-served, maintainer-operated domain.
5. **Signed announcement** — fingerprint announcement signed by the offline root.

The role UID is `InterGenOS Release Key` with `release@intergenstudios.com`. The primary maintainer's personal email does not appear in the UID.

## Rollover

Subkeys rotate on a 2-year cadence. Rollover flow:

1. Generate a fresh signing subkey from the offline root.
2. Publish the new subkey signed by the old one + by the root.
3. Cross-publish the new fingerprint via every channel listed above.
4. Continue signing releases with the old subkey for a 30-day overlap window so `pkm update` clients pick up the new keyring before the old subkey is revoked.
5. Revoke the old subkey.

Emergency rollover (compromise): see the trust-anchor compromise policy in [SECURITY.md](../SECURITY.md). The 6-hour-to-revocation SLA applies; the orderly 30-day overlap does not.

## Contact

Security reports: `security@intergenstudios.com` — see [SECURITY.md](../SECURITY.md) for the full disclosure policy and PGP-encryption expectations.
