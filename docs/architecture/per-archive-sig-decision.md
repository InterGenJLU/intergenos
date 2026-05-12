# Per-Archive Signature Decision — v1.0 Architecture Call

**Author:** InterGenOS build fleet (DS lane)
**Date:** 2026-05-12
**Status:** Proposal, awaiting owner greenlight

## TL;DR Decision

**Stick with signed-index-only for v1.0. Per-archive signatures are deferred to v1.1+ as a project-backlog item.**

Both options provide integrity. Signed-index-only is simpler, already implemented, and sufficient for our current threat surface given the atomic-promote publish pipeline. Per-archive sigs add defense-in-depth but double signing operations, complicate the runbook, and require pkm-side changes — none of which are load-bearing for the v1.0 launch window (Thursday 2026-05-14 MOK trigger).

## Options Considered

### Option A — Signed-index-only (current implementation, RECOMMENDED)

**How it works:**
- `scripts/generate-repodb.py` calls `pkm.repo.generate_index()`, which walks `.igos.tar.gz` archives, computes sha256 per archive, and writes `InterGenOS.db` (gzipped JSON)
- `pkm.repo.sign_index()` produces `InterGenOS.db.sig` (GPG detached signature)
- `pkm sync` verifies the index signature against `pkm/release-keys.json`, then trusts per-archive sha256 from the verified index
- `pkm install` verifies each archive's sha256 against the index entry before extraction

**One signature per repo publish.**

**Pros:**
- Already implemented and tested (22/22 repo-publish tests PASS, GPG sign+verify roundtrip verified)
- Single GPG operation per publish cycle (minimizes Nitrokey touch-interactions)
- Atomic-promote pipeline (directory-swap at publish-repo.sh) eliminates partial-index-read windows
- pkm client performs per-archive sha256 verification before install (defense-in-depth at install-time)
- Runbook is simpler — one signing step, one verification step

**Cons:**
- Index corruption (tampered or protocol-level error) affects all packages simultaneously
- Third-party rebroadcast without our index cannot independently verify archives
- No per-archive offline verification (auditor checking a single archive without network access to our index relies on the index having been verified previously)

### Option B — Dual-layer (per-archive sig + signed index)

**How it would work:**
- Same as A, plus: each `.igos.tar.gz` gets a sibling `.igos.tar.gz.sig` (GPG detached signature of the archive bytes)
- Mirror layout: `packages/<pkg>-<ver>.igos.tar.gz` + `packages/<pkg>-<ver>.igos.tar.gz.sig` alongside the `InterGenOS.db` + `.db.sig` at `/x86_64/` root
- `pkm sync` could optionally verify per-archive sigs as additional defense
- `pkm install` would verify per-archive sig before extraction

**Per-archive sig architecture:**
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
- Defense-in-depth on archive integrity (tampered archive can be caught independently of index)
- Third-party rebroadcast compatible (any mirror can host archives + per-archive sigs)
- Per-archive offline verification (auditor can GPG-verify a single archive without trusting the index)

**Cons:**
- 700+ GPG detach-sign operations per publish cycle (one per package). On Nitrokey hardware (NK1, USB 2.0), estimated 2-3 seconds per signing operation → 30-40 minutes of hardware-token interaction for a full repo publish
- pkm-side changes needed (`pkm/repo.py` verification path must grow a new per-archive sig path)
- Runbook complexity increase (two signing layers, two verification passes)
- All scripts change: `emit-package-archives.py` must sign each archive after creation; `generate-repodb.py` may optionally embed per-archive sig status; `publish-repo.sh` staging layout grows
- Key rotation: more signatures to re-emit when signing subkey rotates

## Threat Model Analysis

| Threat | Signed-index-only (A) | Dual-layer (B) |
|---|---|---|
| Index tampered in transit | Index sig verification catches it | Same as A; per-archive sigs redundant here (archives not touched) |
| Individual archive tampered at mirror | Sha256 mismatch on pkm install catches it | Per-archive sig catches it earlier (at sync time vs install time) |
| Index corrupted, archives intact | All packages fail verification (sha256 link broken) | Per-archive sigs on archives are still valid; packages can be verified independently |
| Third-party rebroadcast (e.g., LAN mirror, USB stick) | Trust chain flows through index; rebroadcaster must also host our signed index | Rebroadcaster can serve archives + per-archive sigs without the index |
| Signing key compromise | Revoke subkey, re-sign index only | Revoke subkey, re-sign index + 700+ per-archive sigs |
| Mirror server compromise (full disk write) | Atomically promoted directory is all-or-nothing; attacker replacing one archive creates sha256 mismatch caught at install | Per-archive sigs catch tampering at the archive level, before index validation |

## Holy Grail Filter

> "Security is not first — it is ONLY. No trade-offs for convenience."

At first read, this tilts toward Option B (defense-in-depth). But the filter must be applied correctly: it forbids *trading away* security for convenience, NOT *choosing between two security mechanisms* that both deliver integrity.

Option A delivers integrity via a single chain: GPG → index → sha256 → archive. Every step is verified at install time. Option B adds a parallel chain: GPG → per-archive sig → archive. Both chains terminate at `gpg --verify`. Both are backed by the same Nitrokey hardware root-of-trust. Adding a second path does not make the first path *stronger* — it makes verification *faster* (sync-time vs install-time) and *more granular* (single archive verification without index dependency).

The Holy Grail filter's application here is: **both options deliver the same security outcome (archive integrity verified against the release key).** The choice is about operational characteristics (signing time, runbook complexity, pkm code-surface growth) — not about *whether* archives are verified.

**Holy Grail conclusion:** Option A is sufficient for v1.0 because the chain of trust (GPG key → signed index → sha256 per archive) is complete and every link is verified by pkm at install time. Adding Option B's per-archive path is an optimization, not a security gap-closure.

## Recommended Path

**Option A — signed-index-only for v1.0.**

Implementation deltas: **none** (already shipped on master at commit chain `27a45773` → `c4c8ee02`, 22/22 tests PASS).

**Option B is deferred to v1.1+ project-backlog** with the following candidate trigger conditions:
- First real-world index-corruption incident (proving the "corrupted index, intact archives" threat is non-hypothetical)
- Third-party mirroring becomes a v1.1 feature (per-archive sigs support rebroadcast without index trust)
- Nitrokey signing speed improves or automated signing-key management reduces the hardware-interaction time concern
- Community request for per-archive offline verification

### Runbook Revision

The existing `first-publish-runbook.md` (WC lane) flags the per-archive sig question in Appendix A.4. With this decision:
- Appendix A.4 resolves to: "v1.0 is signed-index-only; per-archive sigs are a v1.1 project-backlog item"
- The mirror layout description drops the per-archive `.sig` files from `/x86_64/packages/`
- The signing step in the numbered procedure remains a single `InterGenOS.db` + `InterGenOS.db.sig` operation via `generate-repodb.py`

## Gate Tests (for implementation if pursued later)

Should Option B be greenlit for v1.1+, the acceptance tests include:

1. `pytest tests/repo-publish/test_per_archive_sig.py` — per-archive sign step + verify roundtrip
2. Tampered-archive negative test (sig fails on bit-flipped archive)
3. Tampered-archive + valid-sig negative test (pkm refuses install)
4. Performance benchmark: 700+ detach-sign operations wall-clock with NK1
5. Mirror layout verification: `ls /x86_64/packages/*.sig | wc -l` matches archive count

## References

- `docs/repository-trust.md` §1 — trust model description
- `docs/signing-key.md` — canonical release key fingerprints
- `pkm/repo.py` — index generation, signing, verification paths
- `scripts/generate-repodb.py` — CLI for index generation + signing
- `scripts/emit-package-archives.py` — per-package archive emission
- `scripts/publish-repo.sh` — atomic-promote publish orchestrator
- `pkm/release-keys.json` — canonical key configuration
- `first-publish-runbook.md` Appendix A.4 — flag origin of this decision
