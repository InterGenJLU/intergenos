# InterGenOS Repository Trust Model

This document explains how InterGenOS ensures that the software you install—whether via the pkm package manager or through ISO downloads—is authentic and untampered. 

The update path itself is a primary attack surface. Our "Security-Only Alignment" doctrine dictates that we prioritize strict verification over installation convenience.

## 1. The Trust Chain

When you sync packages or download an InterGenOS repository archive, your system verifies the trust chain automatically:

1. **SHA-256 Verification:** pkm fetches the canonical repository index (InterGenOS.db) from https://repo.intergenos.org/x86_64/. This index contains the expected SHA-256 hashes of all valid packages. Every downloaded .igos.tar.gz package archive is hashed locally. If the hash does not match the index perfectly, the installation is hard-rejected.
2. **PGP Signature Verification:** The repository index itself is cryptographically signed (InterGenOS.db.sig) by the InterGenOS release signing key. pkm verifies this signature using the local keyring.
3. **Cross-Publication Fingerprint Checking:** The public key used to verify the signature must match the canonical master fingerprint published across multiple out-of-band channels.

If an attacker intercepts your network traffic or compromises a mirror, they cannot forge the PGP signature of the repository index, meaning they cannot force pkm to accept a malicious package hash.

## 2. The Release Signing Key

The InterGenOS release signing key is anchored by an offline, air-gapped master key. Daily signing operations use subkeys securely stored on hardware tokens (Nitrokey 3 NFC) that require physical touch for operations.

**Master Fingerprint:** 5597 A3E0 587B 2530 06D0  DD7B 8C50 8261 8208 3050

The canonical [docs/signing-key.md](signing-key.md) publication is the authoritative source for the full fingerprint set (master + four signing subkeys on hardware tokens + the encryption subkey) plus the custody policy, key hierarchy, and verification commands. Always cross-check that page against at least one out-of-band source before trusting the key.

## 3. What pkm Does at Install Time

When you run pkm install <package>, the following trust enforcement occurs automatically:

1. pkm syncs the repository index (InterGenOS.db) and its signature (InterGenOS.db.sig) from the mirror.
2. It verifies the signature against the intergenos-keyring installed on your system.
3. It resolves the requested package and dependencies.
4. It downloads the .igos.tar.gz archives and compares their SHA-256 hashes against the signed index.

### Archive Trust Flags

For manual installations of local .igos.tar.gz archives (pkm install --archive <file>), pkm provides the --archive-trust flag to control the strictness of the verification:

*   --archive-trust=strict (Default): Hard-rejects the installation unless the local archive's SHA-256 hash perfectly matches the hash recorded in the cryptographically signed InterGenOS.db index. This prevents installing tampered or out-of-date archives.
*   --archive-trust=repo-only: Rejects the installation unless the archive hash matches the repository index, similar to strict.
*   --archive-trust=loose: **WARNING.** Overrides repository verification and installs the archive without checking the signed index. You must verify the SHA-256 hash independently before trusting an archive with this flag.

### Source Reproducibility

InterGenOS builds from source and uses a robust vendor-tarball pipeline to ensure dependency reproducibility, particularly for ecosystem-heavy tools like Rust/Cargo packages. The source URLs and canonical hashes (including vendor archives) are published in each package's manifest, ensuring the supply chain is auditable and repeatable.

## 4. Key Rotation

The subkeys used to sign packages operate on a 2-year cadence. When a key is rotated, the new keys will be distributed via a intergenos-keyring package update during a 30-day overlap window. The rollover announcements are signed by the offline master key, and the procedure is detailed in the [Signing Key Ceremony Procedure](ceremony/signing-key-ceremony-procedure.md). The operator-side multi-key trust window (parallel `docs/signing-key.asc` + `docs/signing-key-next.asc` keyring bundles, additive `pkm/release-keys.json` entries) is documented in [docs/signing-key.md §Multi-Key Trust Window](signing-key.md#multi-key-trust-window-operator-procedure).

Rotation evidence appears in the §5 transparency log: every signed-by-the-master cross-attestation of an incoming subkey, and every revocation announcement at the end of the overlap window, lands as a transparency-log entry alongside the regular release-publish entries. Co-maintainers cross-checking via the §5 procedure can therefore detect rotation events that were not authorized by the master (i.e. an attacker-published "new subkey" that the legitimate master never signed) — the missing transparency-log entry is the tell.

## 5. Transparency Log Cross-Check (L-024)

Every signed repository index publish appends an entry to an append-only git-backed transparency log. The log entry carries the published index's SHA-256, archive size, signer subkey fingerprint, and a link to the previous log entry — providing public-record evidence that pkm clients can use to detect mirror-swap or signer-compromise scenarios that the GPG signature chain alone cannot surface.

### v1.0 Substrate

The transparency log is published to a co-maintainer-accessible git repository. Each publish operation runs the existing `scripts/publish-repo.sh` which appends a commit containing:

*   `index_sha256` — SHA-256 of the published `InterGenOS.db`
*   `index_size` — byte count of the index
*   `signer_fingerprint` — the signing subkey fingerprint used for this publish
*   `previous_entry_hash` — SHA-256 of the prior log commit's payload (chain integrity)
*   `log_version` — log format version (v1 today)

### How to Cross-Check (Co-Maintainers)

A co-maintainer with access to the transparency log repository can verify that the index they just synced has a corresponding log entry:

```
# 1. Compute the SHA-256 of your locally-synced index:
sha256sum /var/cache/pkm/db/intergenos-current.db

# 2. Clone the transparency log repository (path is co-maintainer-internal):
git clone <transparency-log-remote> ~/.intergenos-transparency-log

# 3. Grep the commit log for the matching SHA-256:
cd ~/.intergenos-transparency-log
git log --grep="<sha256-from-step-1>" --all
```

A matching entry confirms the index you fetched was the one canonically published. No matching entry means either (a) your local sync raced ahead of the log push (rare, retry in a few minutes) or (b) the index you have is NOT the canonically-published one — investigate.

### v1.0 End-User Limitation

The v1.0 transparency log substrate is a co-maintainer-accessible git repository, not yet a public-clone-able log. End users cannot directly perform the cross-check above without co-maintainer credentials. This is the audit row L-024 "minimum-viable" substrate (per the row's `git repo with co-maintainer pushes works; rekor is the proper tool` framing).

**End-user-facing transparency cross-check is queued for v1.1**, with two design surfaces under consideration:

1.  **Public mirror of the transparency log** — a read-only HTTP-accessible mirror of the log commits, suitable for end-user clone + verify.
2.  **Rekor / Sigstore integration** — sign each publish into the Sigstore public-good transparency log via cosign with a Fulcio identity for the `InterGenOS-Release-Bot`. Rekor v2 (GA October 2025) is the gold-standard substrate with witness-managed append-only guarantees.

Until either v1.1 substrate ships, end users rely on the existing GPG signature chain + master-fingerprint cross-check (Section 2 above) for trust enforcement. The transparency log adds defense-in-depth for co-maintainers monitoring for the signer-compromise / mirror-swap class of attacks that the signature chain alone cannot detect.

### Configuration

No client-side configuration is required for v1.0. Publish-side configuration (for mirror operators):

*   `PUBLISH_TRANSPARENCY_REMOTE` — git remote URL for the log repository
*   `PUBLISH_TRANSPARENCY_LOCAL` — local clone path (default `$HOME/.intergenos-transparency-log`)
*   `--skip-transparency` — emergency override flag on `scripts/publish-repo.sh` (fail-closed by default)

See [scripts/publish-repo.sh](../scripts/publish-repo.sh) for the publish-side implementation, landed alongside the L-022 DR backup substrate (single git repo, dual coverage — L-022 frames the WHY, L-024 frames the HOW).

## 6. Failure Modes and Recovery

If pkm encounters a verification failure, it is designed to **fail closed**.

*   **Signature Verification Failed:** pkm will refuse to trust the repository index. This indicates that the index was tampered with, corrupted in transit, or your local intergenos-keyring is out of date. Do not force an update.
*   **SHA-256 Mismatch:** pkm will reject the individual package archive. This means the file downloaded does not match the signed index. 

**When to STOP:**
If you encounter a persistent signature failure or hash mismatch that is not resolved by checking your network connection, assume the upstream source is compromised. Halt installations, do not bypass verification checks using --archive-trust=loose, and check the InterGenOS GitHub or community channels for active security advisories. Contact maintainers via the process outlined in SECURITY.md if you suspect a breach.

## 7. For Redistributors and Mirror Operators

If you are operating a mirror for InterGenOS packages, you must preserve the exact directory structure, including:
*   The raw .igos.tar.gz package archives.
*   The InterGenOS.db repository index.
*   The InterGenOS.db.sig signature file.

Do not re-sign the index. Users rely on the signature matching the canonical InterGenOS master fingerprint. Mirror operators should encourage users to cross-check the master fingerprint against docs/signing-key.md to ensure they are not victims of a localized MITM attack. Further instructions for first-time mirror publication can be found in the [First Publish Runbook](operational/first-publish-runbook.md).
