# 39Q Post-B2 Completion Roadmap

**Last updated:** 2026-05-01
**Status:** Internal roadmap (NOT reviewer-facing)
**Purpose:** Sequence the work between "B2 docker build is runnable" and "39Q skeleton ready for shim-review PR-open at 2026-05-15."

**Sources surveyed during drafting:**
- `docs/research/shim_review/README_draft_skeleton.md` (current 39Q state at master tip)
- `docs/v1.0-documentation-scope.md` §6.2 (sequencing context)
- B2 reproducibility harness research (DeepSeek deliverable 2026-05-01)
- GATED-item paste-ready templates (Gemini-Pro deliverable 2026-05-01)
- C6 PIV-toolchain research (DeepSeek deliverable 2026-05-01 — V2 PASS, untested-against-hardware)

**Purpose:** sequence the work between "B2 build is runnable" and "39Q skeleton ready for shim-review PR-open at 2026-05-15." Internal roadmap — owner runs it, fleet supports.

**Not reviewer-facing.** This doc never goes in the shim-review submission. The shim-review submission graduates from `docs/research/shim_review/README_draft_skeleton.md` to `docs/shim-review-submission.md` per v1.0-doc-scope §6.2 step 5 ("Day-of-launch: graduate the README skeleton as a mechanical move").

**Hard external deadline:** 2026-06-27 (Microsoft 2011 UEFI CA expiration → 2023-CA-only after that date). Self-imposed deadline: 2026-05-15 PR-open target. Buffer: 6 weeks if we slip the self-imposed by 2 weeks.

---

## §1 — Dependency chain (visualization)

```
C6 (PIV slot 9c keygen) ──→ vendor cert public part exists
   │
   ├──→ B2 (docker build embeds vendor cert pub) ──→ unsigned shim binary
   │       │
   │       ├──→ DS verify-b2-reproducibility.sh ──→ Q22 reproducibility verdict
   │       │       │
   │       │       └──→ Q23 build logs + Q25 sha256 + Q14 SBAT + Q29 SBAT entry + Q30 GRUB2 modules
   │       │
   │       └──→ Q8 "created from 16.1 release tar?" trivially YES
   │
   ├──→ Owner sbsign GRUB2 + vmlinuz with C6 private key ──→ signed boot artifacts
   │       │
   │       └──→ (independently fills the trust-chain claim in Q19; not directly a GATED Q but reinforces Q19's "vmlinuz signed by vendor cert" assertion)
   │
   └──→ Q26 + Q28 update from "DEFERRED" to "live with serial NK#1 PIV slot 9c"

Q7 PGP fingerprint ──→ Ethan Phase 1 ceremony (independent of B2/C6)
Q9 InterGenJLU/shim-review fork creation ──→ owner manual GitHub action (independent)
```

**Critical-path:** C6 → B2 → Q22-Q25-Q14-Q23-Q29-Q30 fills (in that causal order). Everything else is parallel.

**Current blocker:** C6 (PIV slot 9c keypair generation) deferred per ceremony-day toolchain gap. DS's research (V2 PASS) recommends `piv-tool` from opensc as the candidate primary. Owner Nitrokey #4 smoke-test is the gating step before C6 re-attempt.

---

## §2 — Sequenced runbook (owner-facing)

### Step 1: NK#4 smoke-test the piv-tool toolchain (gating)

**Time estimate:** 30-60 min (one home session, non-air-gap)
**Lead:** owner; SPOC supports
**Inputs:** Nitrokey #4 (factory-fresh spare), DS's research doc, opensc + piv-tool packages

Owner runs DS's recommended command sequence on NK#4 in a non-air-gap setting to validate the toolchain works on real Nitrokey 3 hardware:

1. PIN/PUK/Management-Key change from factory (NK#4 hasn't had this)
2. `piv-tool -G 9C:06` (RSA-4096 keypair generation in slot 9c)
3. `openssl x509 -pubkey -noout` (extract pubkey from response)
4. Selfsign vendor cert (CN `InterGenOS Secure Boot CA`, 2-year validity)
5. Read-back-from-card diff to verify cert landed correctly

**Verification:** card stores keypair + selfsigned cert; openssl successfully verifies the cert against the on-card pubkey.

**Failure-mode handling:** if `piv-tool` doesn't work as DS researched, fall back to `nitropy nk3 piv` (DS's #2 candidate) or `OpenSSL+libp11` (DS's #3 candidate). Each fallback adds ~30 min to this step.

### Step 2: C6 re-attempt in air-gap (executes the validated toolchain)

**Time estimate:** 60-90 min (Tails air-gap session, owner runs)
**Lead:** owner; SPOC + IGOSC support via runbook
**Inputs:** validated piv-tool sequence from Step 1, Nitrokey #1 (already has [S1] subkey), Drive #3 USB

Owner repeats Step 1's sequence in Tails 7.7 air-gap session against Nitrokey #1 (the daily-driver token):

1. Same PIV provision sequence as Step 1 but on NK#1
2. Selfsigned vendor cert exported to Drive #3 (USB) for transport to online side
3. Cert public-PEM lands at `/path/on/Drive3/intergenos-secure-boot-ca-pub.pem`

**Verification:** `openssl x509 -in intergenos-secure-boot-ca-pub.pem -text` shows CN `InterGenOS Secure Boot CA`, RSA-4096, 2-year validity from 2026-05-NN.

**Updates to repo:**
- `docs/signing-key.md` adds vendor cert fingerprint + Nitrokey #1 PIV slot 9c serial reference
- 39Q Q26 + Q28 change from `DEFERRED` to `live` with the cert details

### Step 3: B2 docker build runs

**Time estimate:** 15-30 min wall-clock for the build itself
**Lead:** SPOC or owner runs `docker build`; DS supports
**Inputs:** vendor cert PEM from Step 2 (cert public-part embeds in the shim binary), `docker/shim-build/Dockerfile` at master tip

**Pre-flight:**
- DS's batch-5 forge-installer fixes have landed (✓ at master `15ce475`); independent of B2 but should be merged before B2 run for clean master state
- Vendor cert PEM placed at the path referenced by `docker/shim-build/Dockerfile` `VENDOR_CERT_FILE`

**Run:**
```bash
cd /mnt/intergenos
docker build -t intergenos-shim-build -f docker/shim-build/Dockerfile .
docker run --rm -v $(pwd)/build-output:/output intergenos-shim-build cp /workspace/shimx64.efi /output/
```

**Verification (immediate):**
- `sha256sum build-output/shimx64.efi` produces the binary hash → fills Q25
- `objcopy --dump-section .sbat=/dev/stdout build-output/shimx64.efi` produces SBAT data → fills Q14 + Q29
- `objdump -p build-output/shimx64.efi | grep VENDOR_CERT` confirms vendor cert embedded
- `readelf -lW build-output/shimx64.efi` shows GNU_STACK as RW (not RWE) → confirms Q11 NX bit assertion

**Failure-mode handling:** if `docker build` fails on dependency download, check that source mirror is reachable (per package.yml). If sha256-pinned base image has been pulled, retry. If `VENDOR_CERT_FILE` path is wrong, fix and rebuild.

### Step 4: Run DS verify-b2-reproducibility.sh harness

**Time estimate:** Step 3 runs again on a different host + Step 4's verify ~10 min
**Lead:** DS runs on a second host; SPOC compares
**Inputs:** two independent `shimx64.efi` artifacts from Step 3 (one per host), DS's `verify-b2-reproducibility.sh` (currently at home-drive on Ubuntu; would graduate to `scripts/verify-b2-reproducibility.sh` if landed in repo)

**Run:**
```bash
./scripts/verify-b2-reproducibility.sh /path/to/host-A/shimx64.efi /path/to/host-B/shimx64.efi
```

Expected output: 6/6 PASS (per the audit lane's harness design). The 6 checks per the audit deliverable: tarball sha256, shim binary, vendor cert, commit SHA, SBAT, PE metadata.

**Verification (Q22 fill):** if 6/6 PASS, Q22 answer becomes "Yes — bit-for-bit reproducible across N independent hosts; harness output sha256 matches across hosts." If <6/6 PASS, identify which check fails and apply DS's reproducibility-leak fixes (L1 thread-race + L2 apt versions are the highest-impact per the audit lane's earlier deliverable).

**Updates to repo:**
- `docs/research/shim_review/README_draft_skeleton.md` Q14, Q22, Q23, Q25, Q29, Q30 fill from the documentation lane's paste-ready templates (per 17:23:55Z deliverable §1) using actual harness outputs
- `logs/build_<timestamp>.log` (in the InterGenJLU/shim-review fork, gated on Step 5)

### Step 5: Q9 InterGenJLU/shim-review fork creation

**Time estimate:** 15-30 min owner manual GitHub action
**Lead:** owner; WC + SPOC support with structure inventory
**Inputs:** owner GitHub access to InterGenJLU org

Owner forks `rhboot/shim-review` to `InterGenJLU/shim-review` and creates branch `intergenos-shim-x64-20260515`.

Detailed file-structure inventory in the companion doc `q9_fork_content_inventory_2026-05-01.md` (canonical rhboot template structure + 11-file payload). Highlights:
- `README.md` — the 39Q answers (graduated from `docs/research/shim_review/README_draft_skeleton.md`)
- `Dockerfile` — copy of `docker/shim-build/Dockerfile`
- `vendor_cert.pem` — the InterGenOS vendor cert public part (from C6)
- `shimx64.efi` — the produced binary (from B2)
- PGP `.asc` files for both maintainers
- `logs/` subdir with build + reproducibility-harness output

**Updates to repo:**
- 39Q Q9 changes from `__TBD__` to `https://github.com/InterGenJLU/shim-review/tree/intergenos-shim-x64-20260515` (live link)
- 39Q Q23 (build logs) gets the path inside the fork

### Step 6: Q7 Ethan PGP fingerprint fill (independent of B2/C6 chain)

**Time estimate:** Phase 1 ceremony for Ethan: 60-90 min (owner-supported)
**Lead:** Ethan generates own master + subkey pair following an Ethan-specific runbook
**Inputs:** Ethan's hardware token (TBD — owner-decision on whether Ethan gets his own NK#3 NFC or different model)

Out-of-scope for this roadmap; flagged for owner-Ethan coordination.

**Updates to repo:** Q7 PGP fingerprint fills + cross-sign with Chris's master key.

### Step 7: Q38 peer-review contributions

**Time estimate:** 30-60 min per review × 2 reviews = 1-2 hours
**Lead:** WC or DS (both have audit-lane experience); per Q38's own design, "starting 2026-05-04"
**Inputs:** open shim-review PRs from non-major-distro submitters

Per Q38's already-written plan: WC or DS picks ≥2 open shim-review PRs, posts substantive review comments. Records URLs in Q38's body.

### Step 8: Pre-PR-open final pass + graduation

**Time estimate:** 60-90 min per v1.0-doc-scope §6.2 step 5
**Lead:** SPOC; owner reviews
**Inputs:** all GATED items resolved per Steps 3-5; Q6 already filled (post-ceremony); Q7 filled per Step 6; Q9 filled per Step 5; Q38 filled per Step 7

Final mechanical move:
1. `git mv docs/research/shim_review/README_draft_skeleton.md docs/shim-review-submission.md`
2. Internal review of complete document end-to-end
3. Owner final-review
4. Open the shim-review PR in `InterGenJLU/shim-review` against `rhboot/shim-review` upstream
5. Submit per shim-review process

**RFC v1 incorporation note:** RFC v1 (supersedes primitive + per-file content-hash manifest) implementation landed at master commit `c9534f7` on 2026-05-01, ahead of the 2026-05-15 PR-open target. Step 8's mechanical move therefore incorporates Q22 + Q25 content-hash strengthening language per the now-attestable trust chain (per-file SHA-256 in pkm repository index, transitively GPG-signed by [S1]). Cross-reference: `docs/ceremony/signing-key-ceremony-procedure.md` Part 1.5 (Trust chain context) for the full chain description; the Q22/Q25 strengthening itself is in `docs/research/shim_review/README_draft_skeleton.md`.

---

## §3 — Per-question completion mapping

| Q | Status (master tip 2531520) | Step | Notes |
|---|---|---|---|
| Q1-Q5 | FILLED | n/a | Intro + identity sections; Q5 references Q19 (already filled with the trust-chain bullet) |
| Q6 | FILLED | n/a | Master + S1 + S2 fingerprints landed via post-ceremony fill commits |
| Q7 | TBD (PGP fingerprint) | Step 6 | Email-format decision RESOLVED to shared `security@` address; PGP fingerprint pending Ethan Phase 1 |
| Q8 | GATED on B2 | Step 3 | Trivial fill: "Yes, built from rhboot/shim tag 16.1 (`afc49558...`) per Dockerfile" |
| Q9 | TBD (fork creation) | Step 5 | Owner GitHub action; URL slot ready |
| Q10-Q13 | FILLED | n/a | Patches/NX/GRUB2 version/CVE audit all done |
| Q14 | GATED on B2 | Step 3 verification | SBAT generation extraction via `objcopy --dump-section .sbat` |
| Q15 | N/A | n/a | First submission, no prior shim hashes |
| Q16-Q19 | FILLED | n/a | Kernel lockdown / patches / ephemeral key / Q19 trust-chain done |
| Q20-Q21 | FILLED | n/a | vendor_db not used; first submission |
| Q22 | GATED on B2 + harness | Step 4 | DS harness 6/6 PASS verdict |
| Q23 | GATED on B2 + fork | Steps 4+5 | Build logs path inside InterGenJLU/shim-review fork |
| Q24 | N/A | n/a | First submission |
| Q25 | GATED on B2 | Step 3 verification | `sha256sum shimx64.efi` |
| Q26 | DEFERRED on C6 | Steps 1+2 | Update from "DEFERRED" to "live with NK#1 PIV slot 9c" |
| Q27 | FILLED | n/a | No EV cert |
| Q28 | DEFERRED on C6 | Steps 1+2 | Same as Q26 |
| Q29 | GATED on B2 | Step 3 verification | SBAT entry text + generation number |
| Q30 | GATED on B2 | Step 3 verification | GRUB2 module list via `grub2-script-check + objdump` |
| Q31 | N/A | n/a | x86_64-only |
| Q32-Q37 | FILLED | n/a | Bootloader version + kernel config all done |
| Q38 | FILLED-WITH-PLACEHOLDER | Step 7 | URL slots fill as 2 reviews complete |
| Q39 | FILLED | n/a | Cross-sign approach + project-context note done |

**Summary:** 23 questions FILLED, 6 N/A, 7 GATED on B2, 2 GATED on C6, 1 owner action (Q9 fork), 1 cross-agent action (Q7 Ethan), 1 cross-agent ongoing (Q38 reviews).

---

## §4 — Risk register

| # | Risk | Mitigation |
|---|---|---|
| R1 | NK#4 smoke-test fails — DS's piv-tool sequence doesn't work on real Nitrokey 3 hardware | Fall back to nitropy nk3 piv (DS's #2) or OpenSSL+libp11 (DS's #3). Adds 30-90 min per fallback attempt. |
| R2 | C6 re-attempt itself fails in air-gap even after Step 1 validates the tooling | The piv-toolchain research is decision-quality but UNTESTED-against-hardware per V2 PASS. Step 1 mitigates but doesn't eliminate. If C6 fails twice, escalate to alternative approaches: TPM-backed key (different threat model), Yubikey 5 instead of Nitrokey 3 (different vendor toolchain). Cloud-hosted KMS is out of scope per the project's hardware-rooted-key custody policy. |
| R3 | B2 docker build fails on dependency download (network instability, sha256-pin drift) | Source mirror is owner-controlled; pre-cache deps. Fallback to upstream Fedora kojipkgs per package.yml. Build is short (~15-30 min) so retries are cheap. |
| R4 | DS reproducibility harness reports <6/6 PASS | DS already identified L1 + L2 as primary fixes. Apply those, re-run. If new leaks surface, DS re-iterates. |
| R5 | Q9 fork creation hits GitHub-side issue (org permissions, repo-name collision) | Owner has InterGenJLU org admin. Repo name follows shim-review convention. Pre-validate org repo creation works on a test repo first. |
| R6 | Q7 Ethan Phase 1 ceremony slips beyond 2026-05-15 PR-open | Q7 can ship with `__PENDING__` placeholder + commitment to fill within 2 weeks of PR-open. shim-review reviewers tolerate "second contact pending" with a credible commitment timeline. |
| R7 | Q38 peer-reviews don't produce 2 substantive comments by 2026-05-15 | Reviews can land between PR-open and review-completion; doesn't block PR-open itself. The 2026-05-04 start in Q38's plan provides 11 days buffer. |
| R8 | DS's batch-5 fixes (already landed at 15ce475) cascade into Forge-installer instability that affects bare-metal install validation IGOSC will need for the truth-doc | Independent of B2 + C6 chain; SPOC + DS handle. Doesn't block 39Q completion. |
| R9 | Microsoft 2011 UEFI CA hard deadline (2026-06-27) approaches without our submission landing | 6-week buffer from 2026-05-15 self-imposed deadline. If we miss self-imposed by 4 weeks, surface to owner for explicit re-evaluation of timeline. |

---

## See also

- `docs/research/shim_review/README_draft_skeleton.md` — 39Q skeleton (graduates to `docs/shim-review-submission.md` per Step 8)
- `docs/research/shim_review/q9_fork_content_inventory_2026-05-01.md` — fork file-structure inventory (companion doc, fills out Step 5)
- `docs/grub2-cve-audit.md` — CVE compliance reference (per-batch table for rhboot reviewer cross-reference)
- `docs/v1.0-documentation-scope.md` §6.2 — broader v1.0 sequencing context
