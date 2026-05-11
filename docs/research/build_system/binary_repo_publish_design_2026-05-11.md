# Binary Repo Publish — Design Note (2026-05-11)

**Branch:** `e1b5-pipeline-emission`
**TRACKER refs:** E1.B.5 + E1.B.6 + E1.B.7
**Status:** scope-discovery — both halves of work I expected to need are already implemented in master. Remaining gap is much smaller than TRACKER currently claims.

---

## TL;DR

E1.B.5 + E1.B.6 are **already shipping code on master** as of `0c9c579`. The remaining v1.0-launch-block work is **a single ~50-100 LOC publish-orchestrator script** + post-build hook + DNS/repo URL configured in `/etc/pkm/repos.conf`. TRACKER section E1 needs revision to reflect the actual scope.

## Reality check against current master

### E1.B.5 — Per-package `.igos.tar.gz` emission

**TRACKER claim:** ❌ TODO — pkm-as-builder packaging hook; build pipeline emits qcow2, not per-package archives.

**Reality:** ✅ ALREADY IMPLEMENTED.

Evidence:
- `scripts/pkg-functions.sh:360-394` defines `pkg_archive(name, version)` which `tar -C "$dest" -czf "$archive" .` creates `${IGOS_PKG_ARCHIVES}/${name}-${version}.igos.tar.gz` from the staging dir.
- `igos-build/tracker.py:143-160` reimplements the same in Python for the build-tracker integration path.
- Default archive destination is `/var/lib/igos/archives/`.
- Per-file SHA256 hashes are computed at install time per [`pkm/installer.py:7`](file:///mnt/intergenos/pkm/installer.py#L7) docstring.

What this means: every package built by the orchestrator already emits a `.igos.tar.gz` archive. The build pipeline is binary-repo-ready by side effect.

### E1.B.6 — Index generator + signer

**TRACKER claim:** ❌ TODO — tool doesn't exist; need to walk docroot, compute SHA-256, emit InterGenOS.db, PGP-sign.

**Reality:** ✅ ALREADY IMPLEMENTED as library functions.

Evidence (full source in [`pkm/repo.py`](file:///mnt/intergenos/pkm/repo.py)):

- [`generate_index(package_dir, arch, output)`](file:///mnt/intergenos/pkm/repo.py#L414) — scans `package_dir` for `*.igos.tar.gz`, calls `_read_package_meta()` per archive, computes SHA-256 + size + filename, writes a gzipped JSON index at `package_dir/InterGenOS.db`. Output schema: `{version, generated, arch, package_count, packages: {name: {sha256, size, filename, ...}}}` — matches the `pkm/repo.py:RepoIndex` parser at line 89.
- [`sign_index(index_path, gpg_key_id)`](file:///mnt/intergenos/pkm/repo.py#L467) — runs `gpg --detach-sign --armor --output <path>.sig --local-user <key_id> <path>`. Produces `InterGenOS.db.sig` next to the index.

What this means: index generation + signing is library-ready. No author-from-scratch work needed. A CLI/automation wrapper just calls these two functions in sequence.

### E1.B.7 — First publish + pkm sync end-to-end

**TRACKER claim:** ❌ TODO — blocks on E1.B.5 + E1.B.6.

**Reality:** ❌ TRUE TODO, but scope reduces to: write the orchestrator script + plumb the repo URL into target images.

## What actually remains

### Component E1.B.NEW: `scripts/publish-repo.sh` (or `.py`)

Single-purpose orchestrator. Pseudocode:

```python
# Inputs: archive_dir (default /var/lib/igos/archives/), remote (default repo.intergenos.org/x86_64), gpg_key_id
# 1. Walk archive_dir for .igos.tar.gz
# 2. Call pkm.repo.generate_index(archive_dir, arch="x86_64", output=archive_dir/"InterGenOS.db")
# 3. Call pkm.repo.sign_index(<that path>, gpg_key_id=<release key id>)
# 4. rsync archive_dir/*.igos.tar.gz + InterGenOS.db + InterGenOS.db.sig
#    → intergenos@origin.intergenstudios.com:/home/intergenos/repo/x86_64/
#    (SSH key auth already configured per TRACKER E1.B.3)
# 5. Atomic publish: rsync to staging/, then mv on remote
```

Estimated work: ~50-100 LOC, ~30 min for first draft + tests.

### Component E1.B.HOOK: post-build orchestrator hook

Wire `scripts/publish-repo.sh` into `build-intergenos.sh` phase orchestration as an optional post-`image` step. Probably gated behind `--publish` flag so non-release builds don't auto-publish.

### Component E1.B.CONFIG: target-image repo URL

Build target images need `/etc/pkm/repos.conf` configured to point at `https://repo.intergenos.org/x86_64`. Verify per `pkm/repo.py:75` default already matches this URL. If yes, no work — installed systems pick it up automatically.

### Component E1.B.PKM-SYNC: end-to-end test

After first publish, install pkm on a fresh InterGenOS qcow2, run `pkm sync` + `pkm install <test-pkg>`, verify SHA + sig chain. This is validation, not new code.

## Why TRACKER E1.B.5 + E1.B.6 were over-scoped

Two factors:
1. I wrote TRACKER section E1 from the user-side trust-chain doc (`docs/repository-trust.md`) which describes the EXPECTED behavior, not the IMPLEMENTED state. The trust-chain doc reads as if it's documenting unrealized future state, but pkm has been building toward this trust model for months — much of the plumbing is in master.
2. I didn't grep the codebase before writing the TRACKER entry. `git grep igos.tar.gz` returns 15+ hits across pkg-functions.sh, igos-build/tracker.py, pkm/repo.py, pkm/installer.py — all of which are the implementation surface. Would have caught the actual state in ~30 seconds.

This is the verify-inherited-claims antipattern — don't propagate scope claims without first-hand verification of the code.

## Proposed TRACKER E1 revision (for post-Build #8 merge)

Replace E1.B.5 + E1.B.6 + E1.B.7 with:

- **E1.B.5 — Per-package `.igos.tar.gz` emission** — [CLOSED — already implemented in pkg-functions.sh:360 + igos-build/tracker.py:143]
- **E1.B.6 — Index generator + signer** — [CLOSED — already implemented as pkm.repo.generate_index() + sign_index(); library-ready]
- **E1.B.7 — Publish orchestrator script** — TODO — `scripts/publish-repo.sh` wraps library functions + rsync to repo.intergenos.org/x86_64
- **E1.B.8 — Post-build publish hook** — TODO — wire into build-intergenos.sh as optional `--publish` step
- **E1.B.9 — Target-image repo config** — TODO (verify) — ensure target images have `/etc/pkm/repos.conf` pointing at `https://repo.intergenos.org/x86_64`
- **E1.B.10 — Pkm sync end-to-end validation** — TODO — install fresh target, `pkm sync`, install test package, verify chain

Net scope reduction: from 3 large items (~days each) to 4 small items (~hours each). The Thursday trigger window (booting-ISO + MOK + first-light) is suddenly much more achievable because we don't need to author E1.B.5/E1.B.6 from scratch.

## Implementation coordination

- The index-generator/signer maintainer lane gets dramatically smaller — the lib functions exist. Collapses to "write the publish orchestrator script + wire post-build hook."
- The audit-pass scope changes: instead of auditing brand-new emission + index-gen code, audit the existing `pkm/repo.py:generate_index/sign_index` for correctness against `pkm/repo.py:RepoIndex` parser + verify the trust-chain assumptions in `docs/repository-trust.md` against actual implementation.
- Other lanes unchanged.

## Open questions for owner

1. **Release-key GPG identity for sign_index():** which subkey signs the index? S1 (NK#1) is "primary signing"; S2 (NK#2) is backup. Convention would suggest S1 for daily index-publish, but that requires NK#1 to be physically present. Long-term: a release-build subkey separate from S1/S2 may be warranted. Short-term: use S1 for v1.0 launch.
2. **Per-arch separation:** code assumes `arch="x86_64"`. v1.0 ships x86_64 only. ARM64 etc. = v1.0+N concern.
3. **Index format choice:** current implementation is gzipped JSON. Other distros use SQLite (Arch) or compressed text-tables (Debian). JSON is fine for our scale (~720 packages); revisit if scale changes.

---

**Recommendation:** keep the publish-orchestrator-script + post-build-hook + repo-config-verify work in the secondary-lane during Build #8. Audit-pass second-opinion on the design before merge. Branch this work onto `e1b5-pipeline-emission`. Merge to master post-Build #8 success.
