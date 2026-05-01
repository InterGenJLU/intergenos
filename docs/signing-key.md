# InterGenOS Release Signing Key

**Last updated:** 2026-05-01
**Status:** Master keypair + signing subkeys [S1] [S2] LIVE on hardware tokens (ceremony 2026-04-30). EFI vendor cert (PIV slot 9c) deferred — Nitrokey 3 PIV toolchain ecosystem gap; follow-up air-gap session pending. Secondary contact's keypair (Ethan) pending his Phase 1 onboarding.

## Summary

InterGenOS release artifacts are signed with a PGP key that chains to an offline root. Verifying the key before trusting it on your machine protects you from man-in-the-middle tampering during package download.

This page is the **canonical fingerprint publication** for the release signing key. Cross-publication locations are listed below; every copy of the fingerprint on the internet should match the value here. If they don't, do not trust the mismatched source.

## Release Key

| Field | Value |
|---|---|
| Primary key UID | `InterGenOS Project Signing Key (primary) <intergenos-primary@intergenstudios.com>` |
| Secondary key UID | *`PENDING — Ethan's Phase 1 generation; planned UID will follow project-role-identity convention`* |
| Algorithm | RSA 4096 (master + subkeys), no expiry on master, 2-year expiry on subkeys |
| Primary fingerprint | `46DD 1029 F98F D453 1D44  99C3 A2AF 3A36 C5CE F2C3` |
| Secondary fingerprint | *`PENDING — Ethan's Phase 1 generation`* |
| Release-signing subkey [S1] | `6451 D186 F997 5781 A145  1DE6 7E65 89C3 954F 031F` (Nitrokey 3 NFC, serial `B9753481`; daily-driver token. Touch-policy enablement deferred to post-ceremony hardening pass — recoverable via `gpg --card-edit` without re-keytocarding.) |
| Release-signing subkey [S2] | `AB6C 6EA3 EDE8 4067 9044  EE5E 237E 35D9 6422 136B` (Nitrokey 3 NFC, serial `43D33126`; redundancy token in local SDB. Touch-policy: same status as [S1].) |
| EFI-binary signing cert CN | `InterGenOS Secure Boot CA` |
| EFI-binary signing key fingerprint | *`PENDING — PIV slot 9c keypair deferred per Nitrokey 3 PIV toolchain ecosystem gap; follow-up air-gap session in flight`* |
| Hardware | Nitrokey 3 NFC × 4 (Nitrokey #1 daily-driver, #2 local SDB, #3 secondary maintainer's, #4 spare) |
| Root custody | Master keypair generated air-gapped on Tails 7.7; LUKS-encrypted backup on dedicated USB drive; paperkey × 2 printed (one home safe + one offsite); revocation cert on backup |
| Primary expiry | None on master; 2 years on subkeys |

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
| Distro GPG signing subkey | Signs `pkm` repo indexes and release artifacts | 2 years | Hardware token (primary + backup); touch-policy enablement is a post-ceremony hardening item (recoverable in-place via `gpg --card-edit`) |
| EFI-binary X.509 (PIV 9c) | Signs kernel vmlinuz and custom GRUB | 2 years | Same hardware token (PIV slot 9c); touch-policy enablement planned during the C6 follow-up air-gap session |
| Machine Owner Key (MOK) | Signs DKMS / out-of-tree modules on the end-user machine | Per-install | **End user** — generated at first-boot by Forge installer; not distro-held |

## Publication

Once generated, the release key is published to:

1. **`keys.openpgp.org`** — role-UID only, no personal email in the UID (D1-6 hardening).
2. **This doc** — repo-tracked, git-signed.
3. **GitHub releases page** — pinned announcement referencing the fingerprint.
4. **intergenstudios.com** — TLS-served, maintainer-operated domain.
5. **Signed announcement** — fingerprint announcement signed by the offline root.

The primary key UID is `InterGenOS Project Signing Key (primary) <intergenos-primary@intergenstudios.com>` — a project-role identity, not personally-named, on the project's own domain. The secondary key (Ethan's Phase 1 keypair, pending generation) will follow the same project-role-identity convention. Matches the big-distro convention of separating project-signing identity from any individual maintainer's personal correspondence channels.

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
