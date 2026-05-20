# DR Mirror Backup Runbook — `intergenos-mirror-backup` Private Git Repo

**Last updated:** 2026-05-20
**Status:** v0.2 — design + runbook, post-implementation alignment. The DR backup mechanism is IMPLEMENTED via the transparency-log block at [`scripts/publish-repo.sh`](../../scripts/publish-repo.sh) lines 281-371 (added 2026-05-18T23:06Z for L-024 + extended at commit `3835933d` 2026-05-20 for L-022). The `pkm/release-keys.json` secondary-mirror schema extension (Section 4) remains PENDING-IMPLEMENTATION as T0-7-D continuation work per remediation-plan owner-decision-queue item 26.

This runbook covers the disaster-recovery (DR) backup architecture for the InterGenOS signed repository index and its trust-chain manifests. The primary mirror at `https://repo.intergenos.org/x86_64/` is single-tenant on a cPanel VPS and represents a single point of failure for the signed-index trust chain: disk loss, OpenVZ container snapshot rollback, or malicious `rm -rf` from a compromised cPanel session would take the live `current/` directory, the `_staging-*/` working slots, and the `_previous/` rollback target simultaneously. The DR backup at `github.com:InterGenJLU/intergenos-mirror-backup` (private repo) is a tamper-evident, off-VPS, signature-preserving second-origin store that closes that gap.

For the primary publication procedure see [`docs/operational/first-publish-runbook.md`](first-publish-runbook.md). For the user-facing trust model see [`docs/repository-trust.md`](../repository-trust.md). For audit-row origin see [`docs/audit/2026-05-18-comprehensive-state-audit.md`](../audit/2026-05-18-comprehensive-state-audit.md) row L-022.

## Status Banner

- **Audit row L-022 closure status.** This doc is the design + runbook artifact for closing audit row L-022 (Critical severity, surfaced 2026-05-18 iter-3 at `b0879120`). The DR backup mechanism is IMPLEMENTED via the transparency-log block at [`scripts/publish-repo.sh`](../../scripts/publish-repo.sh) lines 281-371 (originally added 2026-05-18T23:06Z for L-024 partial closure + extended with manifest fields at commit `3835933d` 2026-05-20 for L-022 closure). The `pkm/release-keys.json` secondary-mirror schema extension (Section 4 below) remains PENDING-IMPLEMENTATION per remediation-plan owner-decision-queue item 26 as T0-7-D continuation work.
- **Holy Grail security-only alignment.** The DR backup is a security-posture instrument per Holy Grail rules 4 (every package decision is a security decision), 9 (update infrastructure must be trustworthy — signed, verified, reproducible where achievable), and 10 (when in doubt, deny). A trust chain that cannot survive single-VPS failure is not a trustworthy update infrastructure.
- **Signed-index size envelope.** The signed index (`InterGenOS.db` + `InterGenOS.db.sig`) plus the release-window manifest (`intergenos-archive-manifest.txt` + `.sig`) plus the release public-key export (`intergenos-release-key.asc`) totals approximately 1-10 MB gzipped per snapshot. This fits comfortably in a Git repo as a tamper-evident, history-preserving backup.
- **DR repo currently empty.** Per the 2026-05-20 supply-chain awareness alert, `github.com:InterGenJLU/intergenos-mirror-backup` was created at 2026-05-19T02:46:56Z but currently holds 0 KB (no initial seed commit yet). The initial seed commit is part of the implementation surface tracked under L-022 closure work.

## What Gets Pushed (and What Does NOT)

### Pushed to `intergenos-mirror-backup` on every successful publish

Each `publish-repo.sh` run that has not set the `--skip-transparency` flag captures a consistent snapshot of the trust-chain primary artifacts via the transparency-log block (see [Sync Architecture](#sync-architecture-implemented) below):

- `InterGenOS.db` — the GPG-signed repository index (parsed by `pkm/repo.py`)
- `InterGenOS.db.sig` — detached GPG signature on the index (S1 subkey per `pkm/release-keys.json`)
- `intergenos-archive-manifest.txt` — install-time integrity manifest emitted by the build orchestrator (conditional; produced only during signed-release publishes via `sign-release.sh`, not on incremental staging)
- `intergenos-archive-manifest.txt.sig` — detached GPG signature on the integrity manifest (S1 subkey; conditional alongside the manifest)

Per-snapshot metadata is captured in the structured git commit message rather than as a separate file: SHA-256 + byte-size for each pushed artifact + `signed-by-fingerprint` of the signing GPG key + `prev-entry` hash (the previous transparency-log commit SHA, forming a Merkle-style chain) + `log-version=2`. The git commit DAG itself is the tamper-evidence layer; the underlying artifact GPG signatures remain verifiable independently.

### NOT pushed (intentional exclusions)

- **Per-package archives** (`packages/*.igos.tar.gz`) — these are too large (~hundreds of MB cumulative) for a Git repo, and they are deterministically reproducible from a clean rebuild of the build chroot against the corresponding commit SHA in the `intergenos` repo. The signed index commits SHA-256 of every archive; if the index survives, archives can be rebuilt and verified byte-exact against the surviving index.
- **Per-channel `_previous/` snapshots** — by design; the backup is for current-channel index recovery, not for snapshot-history preservation. The git commit history of `intergenos-mirror-backup` IS the snapshot history.
- **TLS certificates, Apache config, cPanel state** — these are VPS-infrastructure recovery concerns, distinct from trust-chain recovery; their backup belongs in operator-side VPS-image backup, not in the trust-chain DR repo.

## Recovery Procedure (Index-Loss Event)

**Trigger:** primary mirror `https://repo.intergenos.org/x86_64/` returns 404 / 410 / 5xx on `InterGenOS.db` for more than 15 minutes AND the VPS is confirmed compromised, disk-lost, or otherwise unrecoverable in-place (rather than transient network failure or a brief Apache restart).

### Step 1 — Clone the DR repo to a recovery workstation

```
mkdir -p ~/intergenos-recovery && cd ~/intergenos-recovery
git clone git@github.com:InterGenJLU/intergenos-mirror-backup.git
cd intergenos-mirror-backup
git log --oneline -5
```

The most recent commit's timestamp should match the expected last-publish window. If the most recent commit is significantly older than the expected last publish, the DR push pipeline silently failed on a recent publish — investigate before relying on the recovered artifacts.

### Step 2 — Verify the trust chain against canonical keyring

If the recovery workstation does not yet have the canonical signing keys in its GPG keyring (e.g. a fresh workstation that has never imported the InterGenOS keyring), fetch them from the published canonical sources first — `docs/signing-key.md` documents the canonical fingerprint publication + cross-publication channels (e.g. `keys.openpgp.org`).

```
# Fetch the canonical signing keys per docs/signing-key.md cross-publication channels
gpg --recv-keys 5597A3E0...8050  # master FP (replace with full fingerprint per docs/signing-key.md)
gpg --recv-keys D7AA641D81ACD690C5AD865E7276E14DD8886BFE  # S1 subkey per pkm/release-keys.json

# OR, if the InterGenOS repo working tree is locally available + the keyring is already populated:
gpg --import <(gpg --export 5597A3E0...8050 D7AA641D81ACD690C5AD865E7276E14DD8886BFE)
```

Then verify that S1 signed the index that the DR repo holds:

```
gpg --verify InterGenOS.db.sig InterGenOS.db
# Expected: "Good signature" from S1 fingerprint
#   D7AA641D81ACD690C5AD865E7276E14DD8886BFE
# Trust path: S1 cross-signed by master FP 5597A3E0...8050

# Verify the integrity-manifest signature (if the snapshot includes the manifest)
gpg --verify intergenos-archive-manifest.txt.sig intergenos-archive-manifest.txt
# Expected: "Good signature" from S1 fingerprint (same as above)
```

If either `gpg --verify` returns "BAD signature" or a different fingerprint than the canonical S1, **halt**. The DR repo has been tampered with at the GitHub layer (force-push that overrode branch protection, a compromised maintainer pushing malicious content, or an upstream GitHub-side incident). Resolve through the trust-anchor compromise procedure in `SECURITY.md` before any redeploy.

### Step 3 — Cross-check against canonical machine-readable config

```
# Compare DR-repo-recovered fingerprint against pkm/release-keys.json in the main intergenos repo
python3 -c "import json; d=json.load(open('/path/to/intergenos/pkm/release-keys.json')); print(d['keys']['S1']['fingerprint'])"
# Expected output: D7AA641D81ACD690C5AD865E7276E14DD8886BFE
# This must match the Step 2 verification target byte-for-byte
```

If the DR-repo fingerprint and the canonical `release-keys.json` fingerprint do not match byte-for-byte, **halt**. Either the DR repo is tampered, the canonical config has drifted from the in-use signing key, or a subkey rotation completed without `release-keys.json` update — investigate before redeploy.

### Step 4 — Negative test before redeploy

```
# Tamper one byte of InterGenOS.db and confirm verify fails
cp InterGenOS.db InterGenOS.db.tampered
echo "X" >> InterGenOS.db.tampered
gpg --verify InterGenOS.db.sig InterGenOS.db.tampered
# Expected: "BAD signature"
# If this returns "Good signature", the trust chain is broken at the GPG layer;
# halt and investigate the keyring state before any redeploy
rm InterGenOS.db.tampered
```

### Step 5 — Redeploy via existing `first-publish-runbook.md` Steps 5 and 6

The recovered artifacts in `~/intergenos-recovery/intergenos-mirror-backup/` constitute the mirror-layout staging directory minus the `packages/` subdirectory. Restore `packages/` from one of:

- A rebuilt-from-source archive set (deterministic against the InterGenOS repo source tree at the matching git commit; the recoverer identifies the source commit via cross-reference of the recovered manifest's per-package sha256 entries against InterGenOS commit history, or via operator-recorded build log if available)
- A prior off-host `packages/` archive backup, if one exists in another backup mechanism
- The compromised VPS itself, if disk recovery is partial (forensic-only path; full SHA-256 re-verify against the recovered index is mandatory before serving)

Then proceed to `first-publish-runbook.md` Step 5 (rsync the assembled mirror-layout staging directory to the new VPS `x86_64.new/` path) and Step 6 (atomic promote). If the VPS is a new host (DNS pointed at fresh `origin.intergenstudios.com`), complete `first-publish-runbook.md` Prerequisites Section 4 (SSH path provisioning) first.

## Sync Architecture (Implemented)

### Transparency-log block in `scripts/publish-repo.sh`

The DR backup is implemented as the transparency-log block (Step 5/5) in [`scripts/publish-repo.sh`](../../scripts/publish-repo.sh) lines 281-371. The block was originally introduced at lines 287-333 (added 2026-05-18T23:06Z) as a partial closure for audit row L-024 (transparency log / cosigner) and extended at lines 307-343 (commit `3835933d` 2026-05-20) to cover the archive manifest + signature, closing audit row L-022.

The block fires as the last step of every `publish-repo.sh` invocation that has not set the `--skip-transparency` (or `SKIP_TRANSPARENCY=true`) flag. The mechanism's flow:

1. If the local transparency-log working clone does not yet exist at `$HOME/.intergenos-transparency-log` (or the location set via the `PUBLISH_TRANSPARENCY_LOCAL` env var), clone the remote `intergenos-mirror-backup` repo (`PUBLISH_TRANSPARENCY_REMOTE` env var; defaults to `git@github.com:InterGenJLU/intergenos-mirror-backup.git`) with `--depth 100`.
2. Otherwise `git pull --ff-only origin master`; fail-closed if the remote has diverged.
3. Copy `InterGenOS.db` + `InterGenOS.db.sig` (and conditionally `intergenos-archive-manifest.txt` + `.sig` if the manifest is present in the staging directory) to `$LOG_DIR/x86_64/current/`.
4. Compute SHA-256 + byte-size for each copied artifact + capture the previous transparency-log commit SHA as `prev-entry` (forming a Merkle-style chain across the log history).
5. Build a structured commit message containing the sha256s + sizes + `signed-by-fingerprint` of the signing GPG key + `prev-entry` hash + `log-version=2`.
6. `git add` the staged artifacts + `git commit` + `git push origin master`.
7. Fail-closed on clone / pull / commit / push errors (`publish-repo.sh` exits 1; manual resolution required before next publish).
8. Skip-with-WARN if `git diff --cached --quiet` reports no changes (a snapshot whose artifacts already exist in the log byte-exact surfaces as a possibly-double-publish flag for investigation).

The append-only property relies on force-push protection on the `main` branch of `intergenos-mirror-backup` (configured at the GitHub branch-protection layer). The git commit DAG itself is the tamper-evidence; the underlying artifact GPG signatures remain verifiable independently of the log structure.

### Operator-visible behaviour

- Each successful publish leaves a new commit in `intergenos-mirror-backup` with the publish-time index sha256s, sizes, signer fingerprint, and prev-entry pointer. The git-author + git-committer is the operator account that runs `publish-repo.sh`. The transparency-log commit is itself NOT GPG-signed (the chain of branch-protected commits IS the tamper-evidence layer); the underlying `InterGenOS.db.sig` + `intergenos-archive-manifest.txt.sig` GPG signatures remain verifiable on the artifacts themselves.
- The `SKIP_TRANSPARENCY=true` flag (or `--skip-transparency` CLI flag) bypasses the block for emergency scenarios where the transparency-log repo is unreachable. `publish-repo.sh` emits a SKIP banner instructing the operator to re-publish without the flag before considering the snapshot fully attested.

### Earlier design proposal (historical context)

This doc landed at `9f9dd3a6` originally describing a proposed separate `scripts/sync-mirror-backup.sh` script invoked from a new post-publish hook in `publish-repo.sh`. The proposal was superseded by the choice to extend the existing transparency-log block at `publish-repo.sh:287-333` (which already implemented the same pattern for the L-024 surface) with the manifest fields needed for L-022 closure. The proposed-but-not-adopted standalone-script approach is preserved here as design-space history; the transparency-log block IS the implementation.

## Section 4 — Secondary-Mirror Client Fallback Config (Design Illustration; PENDING-IMPLEMENTATION)

The L-022 remediation includes a secondary-mirror entry in `pkm/release-keys.json` that `pkm` clients can fall back to if the primary mirror is unreachable. The intended schema extension:

```json
{
  "comment": "...existing comment preserved...",
  "keys": {
    "S1": { "fingerprint": "...", "role": "...", "aliases": ["..."] },
    "S2": { "fingerprint": "...", "role": "...", "aliases": ["..."] },
    "S3": { "fingerprint": "...", "role": "...", "aliases": ["..."] },
    "S4": { "fingerprint": "...", "role": "...", "aliases": ["..."] }
  },
  "primary_mirror": {
    "url": "https://repo.intergenos.org/x86_64/",
    "comment": "Primary publication target; first-publish-runbook.md is canonical procedure"
  },
  "secondary_mirrors": [
    {
      "url": "https://raw.githubusercontent.com/InterGenJLU/intergenos-mirror-backup/main/",
      "kind": "github-raw",
      "fallback_for": ["InterGenOS.db", "InterGenOS.db.sig", "intergenos-archive-manifest.txt", "intergenos-archive-manifest.txt.sig"],
      "comment": "DR backup; signed index + manifests only; per-package archives still require primary mirror or rebuild from source",
      "auth_required": "github-token-for-private-repo-access; needs operator decision on private-vs-public DR repo posture per remediation-plan owner-decision-queue item 26"
    }
  ]
}
```

**Implementation notes (PENDING-IMPLEMENTATION; needs separate operator GO on scope and sequence):**

- `pkm/repo.py` would consume `primary_mirror.url` as the default fetch base, with `secondary_mirrors[]` iterated on primary-unreachable for the URL classes listed in each entry's `fallback_for` field.
- The `auth_required` field surfaces the private-repo-access tradeoff: keeping `intergenos-mirror-backup` private limits attacker visibility into DR posture, but requires PAT distribution to the client base for fallback to work. The alternative — a public DR repo with signed-only contents — is acceptable because the signature IS the trust anchor; visibility of the bytes does not weaken the trust chain. Operator decision needed on private vs public DR repo posture (see remediation-plan owner-decision-queue item 26).
- Per-archive fallback is NOT included in current secondary-mirror scope; only signed-index + manifest fallback. Per-package archive recovery requires rebuild-from-source (the signed index commits each archive's SHA-256, so rebuild-then-verify is deterministic).

## Cross-references

- [`docs/operational/first-publish-runbook.md`](first-publish-runbook.md) — primary publication procedure (Steps 5 + 6 are reused in Recovery Step 5)
- [`docs/repository-trust.md`](../repository-trust.md) — user-facing trust model
- [`docs/signing-key.md`](../signing-key.md) — canonical signing-key fingerprint publication
- [`docs/signing-procedure.md`](../signing-procedure.md) — per-signing-window ceremony mechanics
- [`docs/audit/2026-05-18-comprehensive-state-audit.md`](../audit/2026-05-18-comprehensive-state-audit.md) row L-022 — origin Critical-severity audit finding
- [`docs/audit/2026-05-18-remediation-plan.md`](../audit/2026-05-18-remediation-plan.md) — T0-5 cluster context + owner-decision-queue item 26 (L-022 DR scope)
- [`pkm/release-keys.json`](../../pkm/release-keys.json) — canonical machine-readable signing-key config (target of Section 4 secondary-mirrors schema extension)
- [`scripts/publish-repo.sh`](../../scripts/publish-repo.sh) — primary publish orchestrator; the transparency-log block at lines 281-371 IS the DR backup mechanism described in this runbook (added 2026-05-18T23:06Z for L-024 + extended at `3835933d` 2026-05-20 for L-022 manifest fields)
- [`SECURITY.md`](../../SECURITY.md) — trust-anchor compromise response policy

## Appendix A — Why Private Git as the Backup Substrate?

**Tamper-evidence.** Git's commit DAG is content-addressed; a forced rewrite of history requires force-push, which the canonical `main` branch protection rule rejects. Any reader verifying recent commits against their local cache can detect history rewrites.

**Signature preservation.** GPG signatures are byte-stable. A git commit that adds `InterGenOS.db.sig` preserves the exact bytes; `gpg --verify` on the recovered file produces the same outcome as verification on the primary mirror.

**Off-VPS by construction.** GitHub's infrastructure is independent of the cPanel VPS. A VPS-side failure cannot delete the DR repo. Conversely a GitHub-side failure does not affect the primary mirror — the two surfaces fail independently.

**Size fit.** Signed index + manifests at ~1-10 MB per snapshot fits comfortably in a git repo. After 1000 publish cycles (~3 years at weekly release cadence), the repo is approximately 1-10 GB — still within GitHub's repository size soft limits.

**Cross-region resilience.** GitHub serves from a global CDN; recovery workstations can clone from any region with internet access. A regional-VPS-failure scenario is fully recoverable from any other region with no manual mirror configuration.

## Appendix B — Acknowledged Boundary Conditions

The following items are intentionally outside this design doc's scope. Each anchors to an explicit follow-on tracking item rather than to a temporal framing:

- **Per-package archive recovery.** This doc covers signed-index DR only. Per-package archive DR is tracked under remediation-plan owner-decision-queue item 26 (L-022 DR scope).
- **DR depends on canonical signing-key custody surviving the failure.** If the S1 hardware token is lost simultaneously with VPS loss, rekey via master + S2 (off-site backup) per `docs/signing-procedure.md`. The DR repo is for index recovery, not key recovery.
- **Restore time-to-publish window.** Manual procedure; expect 30-60 minutes from VPS-loss confirmation to redeployed mirror serving traffic on a healthy VPS. Automation of the recovery procedure is tracked under remediation-plan owner-decision-queue item 26.
- **Single-DR-repo single-point-of-failure.** The `intergenos-mirror-backup` repo itself is a single point of failure. Multi-region DR (e.g. parallel push to a self-hosted Gitea on a second VPS, or to a second GitHub org) is tracked under remediation-plan owner-decision-queue item 26.
- **GitHub TOS compliance.** Storing signed-only artifacts (no per-package binaries) keeps the DR repo well within GitHub's free-tier private-repo limits and TOS scope. The size envelope analysis in Appendix A holds.
