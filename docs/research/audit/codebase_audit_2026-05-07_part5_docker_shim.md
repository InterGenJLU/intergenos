# Codebase Audit — §5 Docker / Shim

**Auditor:** chris-ubuntu_hplt-codium-deepseek  
**Date:** 2026-05-07 10:40–10:55 CDT  
**Master:** c4d5126  
**Paths audited:** `docker/`, `docs/shim-review-submission.md`  
**Files:** `docker/shim-build/Dockerfile` (160 lines), `docker/shim-build/README.md` (111 lines), vendor cert + SBAT assets, `docs/shim-review-submission.md` (638 lines)  
**Findings:** 0 CRITICAL, 0 HIGH, 0 MEDIUM, 5 LOW

---

## Summary

The Docker/shim section is in good shape. The Dockerfile is a well-engineered reproducible build with all three reproducibility leaks closed (L1: `make -j1`, L2: snapshot.debian.org package pinning, L3: commit SHA assertion on shim source). Triple-host reproducibility confirmed by DS-workstation at SHA256 `22ba569ab8543d456e4bf0289b9c63b7c28046ed3d98a0549cc38491322f8e97`. The shim-review submission document (638 lines) is comprehensive, covering all 29 rhboot/shim-review questions with inline citations to repo sources. 2 `__TBD__:` markers remain (Ethan's PGP fingerprint, cross-signing completion) — both gated on external onboarding steps, not code gaps. 1 `__GATED__:` marker remains for post-merge binary extraction.

---

## Closure Table

| ID | Severity | Section | File:Line | Finding | Proposed Fix |
|----|----------|---------|-----------|---------|--------------|
| D1 | LOW | Docker | `docker/shim-build/README.md:109` | README states "Multi-host verification not yet executed" — stale documentation. DS-workstation confirmed triple-host reproducibility at SHA256 `22ba569...` on 2026-05-07 11:02:28Z (channel message #81). The README should be updated to reflect the verified status. | Update README line 109 to: "Multi-host verification: CONFIRMED across 3 hosts / 3 hardware configurations / 2 Ubuntu releases / 3 Docker versions — all produce identical sha256sum 22ba569ab8543d456e4bf0289b9c63b7c28046ed3d98a0549cc38491322f8e97." |
| D2 | LOW | Docker | `docker/shim-build/README.md:103-106` | PKCS#11 URI in sign-shim.sh documented as a "default guess" for PIV slot 9c. The URI `id=%02` is the OpenSC default for slot 9c but should be validated against the actual token. This is correctly documented as a known limitation — no code change needed, but a verification step should be added to the README checklist. | Add verification command to README: `pkcs11-tool --list-objects --type privkey --id %02` to confirm the URI matches the token. |
| D3 | LOW | Docker | `docker/shim-build/Dockerfile:88` | `git clone --depth 1` fetches only the latest commit. If `rhboot/shim` force-pushes a new commit to the v16.1 tag between build and review, the commit SHA assertion on line 91 catches it and fails the build. This is correct behavior (L3 fix), but the error message doesn't explain the remediation: "delete the Docker build cache and retry." | Add to error message on line 91: `echo "Run 'docker build --no-cache' to force a fresh clone."` |
| D4 | LOW | Docker | `docker/shim-build/Dockerfile:66` | `snapshot.debian.org` URL with `[trusted=yes check-valid-until=no]` — the `check-valid-until=no` flag disables GPG signature validity checking for the snapshot repo because the snapshot archive key has an expired Valid-Until date. This is acceptable for a build-time ephemeral container but should be documented as a deliberate trade-off, not an oversight. The base image digest pin provides defense-in-depth. | Add comment above line 66: "check-valid-until=no is required because snapshot.debian.org archive signing keys may have expired Valid-Until dates. Defense-in-depth: base image is digest-pinned on line 25, so even a compromised snapshot URL cannot inject content into the layer below." |
| D5 | LOW | Shim | `docs/shim-review-submission.md:1` | 2 `__TBD__:` markers remain: Ethan's PGP fingerprint (gated on onboarding Phase 1), and cross-signing completion (gated on community keysigning event). 1 `__GATED__:` marker remains for post-merge binary extraction. All three are documented external dependencies, not code gaps. PR-open target 2026-05-15. | No code fix needed. Document resolution status: TBD items resolve when Ethan completes onboarding (unblocks fingerprint) + community keysigning event scheduled (unblocks cross-sign). |

---

## Detailed Analysis

### A. Dockerfile (`docker/shim-build/Dockerfile`, 160 lines)

Well-engineered reproducible build with comprehensive reproducibility anchors:

**Reproducibility anchors (8 total):**
1. Base image digest-pinned: `debian:bookworm-slim@sha256:5a2a80d1...` — not `:bookworm-slim`
2. `SOURCE_DATE_EPOCH=1746489600` fixed timestamp
3. `LANG=C.UTF-8`, `TZ=UTC`, `LC_ALL=C.UTF-8` — no locale-dependent behavior
4. Shim source commit-pinned: clone at tag, then assert HEAD matches `afc49558...` (L3 fix)
5. Serial build: `make -j1` eliminates thread-race ordering (L1 fix)
6. snapshot.debian.org timestamp: `20260501T000000Z` — pins all apt packages (L2 fix)
7. Deterministic tar: `--sort=name --owner=0 --group=0 --numeric-owner --mtime=@$SOURCE_DATE_EPOCH`
8. Vendor cert + SBAT committed in repo — deterministic inputs

**Build flow:**
1. Install build dependencies from snapshot.debian.org
2. Clone shim at tag v16.1, assert commit SHA
3. Copy vendor cert (DER + PEM) + SBAT CSV into build context
4. Build with `make -j1 VENDOR_CERT_FILE=... DEFAULT_LOADER=\\\\grubx64.efi`
5. Collect outputs into deterministic tarball
6. Compute SHA256 of output tarball

**Known limitations (correctly documented):**
- PKCS#11 URI default guess (D2)
- No `--no-cache` guidance in error message (D3)
- `check-valid-until=no` rationale not documented in Dockerfile (D4)

### B. Vendor Assets

- `vendor-cert/intergenos-secure-boot-ca.der` — valid X.509 certificate (Version 3)
- `vendor-cert/intergenos-secure-boot-ca.pem` — valid PEM certificate
- `sbat/sbat.intergenos.csv` — correct format: `shim.intergenos,1,InterGenOS,shim,16.1,https://github.com/InterGenJLU/intergenos`

### C. shim-review-submission.md (638 lines)

Comprehensive submission covering all 29 rhboot/shim-review questions:
- Q1-Q6: Organization identity, legal data, product justification
- Q7-Q14: Build environment, compiler, SBAT, NX compatibility
- Q15-Q22: Kernel config, lockdown, module signing, CA bundle
- Q23-Q29: Updates, revocation, reproducibility

Inline citations point to specific commit SHAs in the intergenos repository for auditability. Per-line kernel config citations reference exact fragment file line numbers.

**Outstanding items:**
- 2 `__TBD__:` marks for external dependency resolution (Ethan PGP, cross-signing)
- 1 `__GATED__:` mark for post-merge binary extraction
- PR-open target: 2026-05-15 (8 days from audit)

### D. Docker Availability

Docker is installed on this host (ubuntu.hplt): `/usr/bin/docker`. Handoff.md notes "Docker: not yet installed" — this is now stale. Docker 29.4.3 confirmed by the workstation DeepSeek agent's earlier channel message.

---

## Audit Techniques Applied

| Technique | Result |
|-----------|--------|
| Logic-flow tracing | Dockerfile build flow: install deps → clone source → copy certs → build → archive. All error paths handled: commit assertion (line 91-93), apt install failure (RUN exit code). |
| Error-handling scan | Dockerfile error paths: L3 commit assertion exits non-zero on mismatch. apt-get install exits non-zero if package not found. No try/except needed in Dockerfile — RUN commands propagate exit codes. |
| Hardcoded-path scan | `/build/`, `/out/` — Dockerfile-local paths. `/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so` in sign-shim.sh — correct for Tails/Debian x86_64. |
| Test gap analysis | No automated Dockerfile test. Manual verification: build + sha256sum check. Multi-host reproducibility now confirmed (stale README, D1). |
| Shell robustness | Dockerfile RUN commands use `&&` chaining — correct. No shell scripts inside Dockerfile. |
| Missing dep declaration | Not applicable — Dockerfile declares all dependencies inline. |
| git-hygiene | Vendor cert is a public certificate (no private key). SBAT CSV contains no secrets. |
