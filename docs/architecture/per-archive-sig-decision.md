# Per-Archive Signature Decision — v1.0 Architecture Call

**Date:** 2026-05-12
**Status:** Decided for v1.0 (signed-index-only)

## Summary

**InterGenOS v1.0 ships signed-index-only repository signing. Per-archive signatures are a documented v1.1+ candidate, tracked against the trigger conditions in [Recommended Path](#recommended-path).**

Both options provide integrity. Signed-index-only is simpler, already implemented, and sufficient for the current threat surface given the atomic-promote publish pipeline. Per-archive signatures add defense-in-depth, but they double the signing operations, complicate the publish procedure, and require changes to pkm. None of these is required for the v1.0 release.

## Options Considered

### Option A — Signed-index-only (current implementation, RECOMMENDED)

**How it works:**
- `scripts/generate-repodb.py` calls `pkm.repo.generate_index()`, which walks `.igos.tar.gz` archives, computes sha256 per archive, and writes `InterGenOS.db` (gzipped JSON)
- `pkm.repo.sign_index()` produces `InterGenOS.db.sig` (GPG detached signature)
- `pkm sync` verifies the index signature against `pkm/release-keys.json`, then trusts per-archive sha256 from the verified index
- `pkm install` verifies each archive's sha256 against the index entry before extraction

**One signature per repository publish.**

**Pros:**
- Already implemented, with the repo-publish test suite passing (GPG sign+verify roundtrip verified)
- Single GPG operation per publish cycle, which minimizes hardware-token interactions
- The atomic-promote pipeline (directory swap in `publish-repo.sh`) eliminates partial-index-read windows
- The pkm client performs per-archive sha256 verification before install (defense-in-depth at install time)
- The publish procedure is simpler: one signing step, one verification step

**Cons:**
- Index corruption (tampering or a protocol-level error) affects all packages simultaneously
- A third-party rebroadcaster without the InterGenOS index cannot independently verify archives
- No per-archive offline verification: an auditor checking a single archive without network access to the index relies on the index having been verified previously

### Option B — Dual-layer (per-archive signature + signed index)

**How it would work:**
- Same as A, plus: each `.igos.tar.gz` gets a sibling `.igos.tar.gz.sig` (GPG detached signature of the archive bytes)
- Mirror layout: `packages/<pkg>-<ver>.igos.tar.gz` and `packages/<pkg>-<ver>.igos.tar.gz.sig`, alongside `InterGenOS.db` and `InterGenOS.db.sig` at the `/x86_64/` root
- `pkm sync` could optionally verify per-archive signatures as additional defense
- `pkm install` would verify the per-archive signature before extraction

**Per-archive signature layout:**
```
x86_64/
├── InterGenOS.db          ← signed index (covers all)
├── InterGenOS.db.sig      ← index signature
├── packages/
│   ├── firefox-138.0-1.igos.tar.gz
│   ├── firefox-138.0-1.igos.tar.gz.sig    ← per-archive
│   ├── gimp-2.10.38-1.igos.tar.gz
│   └── gimp-2.10.38-1.igos.tar.gz.sig     ← per-archive
```

**Pros:**
- Defense-in-depth on archive integrity (a tampered archive can be caught independently of the index)
- Compatible with third-party rebroadcast (any mirror can host archives plus per-archive signatures)
- Per-archive offline verification (an auditor can GPG-verify a single archive without trusting the index)

**Cons:**
- One GPG detach-sign operation per package, scaling with the full package set (860 as of 2026-06-21; derive the live count from `packages/`). On the current hardware token over USB 2.0, an estimated 2-3 seconds per signing operation puts a full repository publish in the range of tens of minutes of hardware-token interaction
- pkm changes are needed: the `pkm/repo.py` verification path must grow a per-archive signature path
- The publish procedure becomes more complex (two signing layers, two verification passes)
- Several scripts change: `emit-package-archives.py` must sign each archive after creation, `generate-repodb.py` may optionally embed per-archive signature status, and the `publish-repo.sh` staging layout grows
- Key rotation requires re-emitting many more signatures when the signing subkey rotates

## Threat Model Analysis

| Threat | Signed-index-only (A) | Dual-layer (B) |
|---|---|---|
| Index tampered in transit | Index signature verification catches it | Same as A; per-archive signatures are redundant here (archives are not touched) |
| Individual archive tampered at a mirror | sha256 mismatch on pkm install catches it | Per-archive signature catches it earlier (at sync time rather than install time) |
| Index corrupted, archives intact | All packages fail verification (the sha256 link is broken) | Per-archive signatures on the archives remain valid; packages can be verified independently |
| Third-party rebroadcast (for example, a LAN mirror or USB stick) | The trust chain flows through the index; the rebroadcaster must also host the signed index | The rebroadcaster can serve archives plus per-archive signatures without the index |
| Signing key compromise | Revoke the subkey, re-sign the index only | Revoke the subkey, re-sign the index plus a per-archive signature for every package |
| Mirror server compromise (full disk write) | The atomically promoted directory is all-or-nothing; an attacker replacing one archive creates a sha256 mismatch caught at install | Per-archive signatures catch tampering at the archive level, before index validation |

## Security Posture Check

> "Security is not first. It is only."

At first read, the security-only posture appears to favor Option B (defense-in-depth). Applied correctly, however, it forbids *trading away* security for convenience; it does not require *choosing between two mechanisms* that both deliver integrity.

Option A delivers integrity through a single chain: GPG → index → sha256 → archive. Every step is verified at install time. Option B adds a parallel chain: GPG → per-archive signature → archive. Both chains terminate at `gpg --verify`, and both are backed by the same hardware root of trust. Adding a second path does not make the first path *stronger*. It makes verification *faster* (sync time rather than install time) and *more granular* (single-archive verification without an index dependency).

The conclusion is straightforward: **both options deliver the same security outcome — archive integrity verified against the release key.** The choice is about operational characteristics (signing time, procedure complexity, growth in the pkm code surface), not about *whether* archives are verified.

**Conclusion:** Option A is sufficient for v1.0 because the chain of trust (GPG key → signed index → sha256 per archive) is complete, and pkm verifies every link at install time. Option B's per-archive path is an optimization, not a closure of a security gap.

## Recommended Path

**Option A — signed-index-only for v1.0.**

Implementation work required: **none.** Option A is already shipped on `master` (commit range `27a45773` … `c4c8ee02`), with the repo-publish test suite passing.

**Option B is a documented v1.1+ candidate**, to be reconsidered when any of these trigger conditions arise:
- A first real-world index-corruption incident, proving the "corrupted index, intact archives" threat is non-hypothetical
- Third-party mirroring becomes a supported feature (per-archive signatures support rebroadcast without index trust)
- Signing-token throughput improves, or automated signing-key management reduces the hardware-interaction time concern
- A community request for per-archive offline verification

### Publish Procedure Alignment

With this decision, the first-publish procedure ([`docs/operations/first-publish-runbook.md`](../operations/first-publish-runbook.md)) reflects signed-index-only:
- The publish question for per-archive signatures resolves to "v1.0 is signed-index-only; per-archive signatures are a documented v1.1+ candidate"
- The mirror layout omits per-archive `.sig` files under `/x86_64/packages/`
- The signing step remains a single `InterGenOS.db` plus `InterGenOS.db.sig` operation via `generate-repodb.py`

## Acceptance Tests for Option B

Should Option B be adopted, the acceptance tests include:

1. `pytest tests/repo-publish/test_per_archive_sig.py` — per-archive sign step and verify roundtrip
2. Tampered-archive negative test (signature fails on a bit-flipped archive)
3. Tampered-archive with valid-signature negative test (pkm refuses to install)
4. Performance benchmark: wall-clock time for one detach-sign operation per package across the full set, on the production signing token
5. Mirror layout verification: `ls /x86_64/packages/*.sig | wc -l` matches the archive count

## References

- [`docs/repository-trust.md`](../repository-trust.md) §1 — trust model description
- [`docs/signing-key.md`](../signing-key.md) — canonical release key fingerprints
- `pkm/repo.py` — index generation, signing, and verification paths
- `scripts/generate-repodb.py` — CLI for index generation and signing
- `scripts/emit-package-archives.py` — per-package archive emission
- `scripts/publish-repo.sh` — atomic-promote publish orchestrator
- `pkm/release-keys.json` — canonical key configuration
- [`docs/operations/first-publish-runbook.md`](../operations/first-publish-runbook.md) — the publish procedure this decision feeds into
