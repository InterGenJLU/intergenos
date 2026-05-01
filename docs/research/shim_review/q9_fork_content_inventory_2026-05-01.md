# Q9 — InterGenJLU/shim-review Fork Content Inventory

**Last updated:** 2026-05-01
**Status:** Internal inventory (NOT reviewer-facing)
**Purpose:** Specify exactly what content the InterGenJLU/shim-review fork needs at PR-open time, cross-referenced against rhboot/shim-review's canonical submission template.

**Sources surveyed during drafting:**
- rhboot/shim-review README.md (fetched 2026-05-01)
- `docs/research/shim_review/README_draft_skeleton.md` (current 39Q at master tip)
- `docs/v1.0-documentation-scope.md` §3.1 (graduation plan)
- `docs/grub2-cve-audit.md` (32 CVEs verified per Q13; per-batch table post-G1)
- `docs/ephemeral-module-signing.md` (Q19 design doc)
- `docs/signing-key.md` (Q6 fingerprints, post-ceremony fills)

**Companion document:** `docs/research/shim_review/post_b2_completion_roadmap_2026-05-01.md` — the broader sequenced runbook; this doc fills out Step 5.

**Purpose:** specify exactly what goes in the InterGenJLU/shim-review fork at PR-open time (2026-05-15 target). Compresses the post-B2-build window so that "graduate skeleton → fork content" is mechanical copy-with-template-adjustment rather than write-from-scratch.

**Scope:** content + file structure + naming convention. Out of scope: actually creating the fork (owner-only GitHub action), actually running B2 (covered in companion roadmap Step 3-4), actually doing C6 (covered in companion roadmap Steps 1-2).

**Authority:** rhboot/shim-review README at `https://github.com/rhboot/shim-review` (fetched 2026-05-01) is canonical for fork structure. Anything in this doc that conflicts with rhboot README → rhboot wins; correct this doc.

---

## §1 — Branch / tag naming convention

**Canonical format (per rhboot README):** `myorg-shim-arch-YYYYMMDD`

**InterGenOS instantiation:** `intergenos-shim-x64-20260515`

**Match check against current 39Q skeleton:** Q9 already specifies `https://github.com/InterGenJLU/shim-review/tree/intergenos-shim-x64-20260515` — ✓ matches. No change needed.

**Date selection (`20260515`):** matches our self-imposed PR-open target. If PR-open slips to e.g. 2026-05-22, the tag rolls accordingly. Tag is part of the commit metadata, not part of the URL we publish in advance.

---

## §2 — Required files in the fork (per rhboot canonical template)

The rhboot README specifies the following submission-package contents. Mapping each to InterGenOS source-of-truth:

| Required file | InterGenOS source | Status | Step in roadmap |
|---|---|---|---|
| `shim.efi` binary (canonically `shimx64.efi` for our arch) | Produced by `docker build` from `docker/shim-build/Dockerfile`; lands as build artifact | GATED on B2 + C6 (vendor cert needed) | Step 3 |
| Build logs | Docker buildkit output + DS's verify-b2-reproducibility.sh harness output | GATED on B2 + harness run | Steps 3-4 |
| Additional binaries / certificates / SHA256 hashes | Vendor cert public PEM (from C6); shim binary sha256; SBAT section dump; embedded GRUB2 module list | GATED on C6 + B2 | Steps 2-4 |
| `Dockerfile` (for reproducible builds) | `docker/shim-build/Dockerfile` on InterGenOS master | EXISTS — copy verbatim into fork | Step 5 |
| PGP key files (`.asc` format) — primary contact + secondary contact | Master key (Chris) generated 2026-04-30; Ethan's secondary key pending Phase 1 | Master ASC EXPORTABLE NOW; Ethan's pending | Step 5 + Step 6 |
| README.md (the 39 answers) | `docs/research/shim_review/README_draft_skeleton.md` graduates to `README.md` in fork | Available now (with GATED placeholders for B2-dependent items) | Step 5 + 8 |

---

## §3 — Canonical upstream template structure (from rhboot README, 2026-05-01)

The rhboot template organizes questions into 10 categories. Our internal 39Q skeleton is question-numbered 1-39 but the canonical template is category-organized. **Mapping is one-to-many — each canonical category subsumes 2-7 of our numbered questions.**

| Canonical category | Our 39Q skeleton questions |
|---|---|
| Organization & Legal Verification | Q1 (org), Q2 (legal), Q3 (product) |
| Product & Justification | Q4 (justification), Q5 (no-reuse) |
| Security Contacts | Q6 (primary), Q7 (secondary) |
| Build & Source Code Details | Q8 (16.1 tar?), Q9 (repo URL), Q10 (patches), Q11 (NX bit), Q12 (GRUB2 SB impl) |
| GRUB2 CVE Compliance | Q13 (covered in `docs/grub2-cve-audit.md`) |
| SBAT & Boot Chain | Q14 (SBAT gen 5), Q15 (old-shim hashes), Q21 (CA reuse) |
| Kernel Security | Q16 (lockdown commits), Q17 (lockdown enforcement), Q18 (local patches), Q19 (ephemeral keys) |
| Certificate & Key Management | Q20 (vendor_db), Q26 (key custody), Q27 (EV cert), Q28 (CA cert embedding) |
| Reproducibility & Documentation | Q22 (Dockerfile reproducibility), Q23 (build logs paths) |
| Additional Information | Q24 (chain changes), Q25 (binary sha256), Q29 (SBAT entry), Q30 (GRUB2 modules), Q31-Q37 (boot/launch chain), Q38 (peer-review contributions), Q39 (additional info) |

**Implication for graduation (Step 8 of the roadmap):** the README in the fork can either:
- **Option Alpha:** preserve our internal Q1-Q39 numbering verbatim (clearer audit trail; reviewer can map back to our internal docs)
- **Option Beta:** reorganize into the rhboot canonical category structure (more familiar to reviewers; matches their template directly)

**Recommendation: Option Alpha.** Reviewers will look at our README; the canonical category headings are the rhboot README's organization, not a rigid submission-format requirement. Keeping our numbering preserves the cross-reference web back to our research docs (grub2-cve-audit, ephemeral-module-signing, etc.). If a reviewer specifically asks for category-organized, we can re-reflow at that point — cheap to do post-feedback.

---

## §4 — Specific upstream-template requirements that map to our content

### §4.1 — Shim 16.1 source verification

**Canonical requirement:** Source binaries must originate from shim release 16.1, with matching tarball SHA256 + SHA512 checksums as published in the rhboot/shim-review README "Source Code Verification" section.

**Our state (Q8 + Q10):** Q10 cites `rhboot/shim` tag `16.1` commit `afc49558b34548644c1cd0ad1b6526a9470182ed`. Q8 is GATED on B2 build completion.

**Action for Q9 fork:** include shim 16.1 release tarball verification in the fork's README. Recommend Q8 fill structure (checksums pulled from upstream rhboot/shim-review README at fork-creation time, not restated here to avoid false-positive secret-scanner matches):

```markdown
__FILLED__: Yes. Built from rhboot/shim release tarball shim-16.1.tar.bz2 (upstream https://github.com/rhboot/shim/releases/tag/16.1).
- SHA256: <pull from rhboot/shim-review README "Source Code Verification" section> ✓
- SHA512: <pull from rhboot/shim-review README "Source Code Verification" section> ✓
- Git tag: 16.1 (commit afc49558b34548644c1cd0ad1b6526a9470182ed)
- Dockerfile pulls from this tarball (sha256-pinned base image, sha256-verified tarball download)
```

### §4.2 — GRUB2 CVE compliance list (per rhboot canonical CVE manifest)

**Canonical requirement:** the rhboot README explicitly lists CVE batches by date/category that submitters must confirm patched. Total reading: 49 CVEs across 6 batches (see `docs/grub2-cve-audit.md` "Historic CVE Clusters" section for the per-batch table — addressed via G1 fix in this same prep batch).

**Our state (Q13 post-G1):** `docs/grub2-cve-audit.md` covers 32 unique CVEs verified in v2.12..v2.14 range AND the historic-clusters section now enumerates all 28 pre-v2.12 CVEs across 5 batches as inherited from v2.12 baseline. Total verified coverage: 49 / 49 rhboot-listed CVEs ✓ + 11 extra in our v2.12..v2.14 audit.

**Action for Q9 fork:** Q13 in the fork README cites the now-exhaustive `docs/grub2-cve-audit.md` CVE-batch table. No additional fork-side action needed beyond the G1 grub2-cve-audit.md edit.

### §4.3 — Three specific kernel commits to confirm

**Canonical requirement (per rhboot README "Kernel Security" section):**
> "Confirmation of applied upstream commits:
> - `1957a85b0032a81e6482ca4aab883643b8dae06e` (efivar_ssdt_load restriction)
> - `75b0cea7bf307f362057cc778efe89af4c615354` (ACPI configfs lockdown)
> - `eadb2f47a3ced5c64b23b90fd2a3463f63726066` (kgdb lockdown)"

**Our state (Q16 post-G2):** the 39Q skeleton's Q16 now includes a "Specific upstream commits called out by rhboot/shim-review template" subsection with a 3-row table confirming all 3 commits exist in mainline torvalds/linux and are transitively included in InterGenOS Linux 6.18.10. Verified via direct GitHub commit URL fetch 2026-05-01T18:00Z. Reviewer-runnable verification recipe (clone upstream + `git log v6.18.10 -- <files>`) included.

**Action for Q9 fork:** Q16 fork answer cites the now-explicit confirmation. No additional fork-side action needed beyond the G2 README_draft_skeleton.md edit.

### §4.4 — 2011 + 2023 key dual-signing (October 2025 update)

**Canonical requirement (per rhboot README, dated 2025-10-20):** Shims submitted to Microsoft will receive signatures using BOTH 2011 and 2023 keys. Applicants should reference Microsoft's [2023 signing guidance](https://techcommunity.microsoft.com/blog/hardware-dev-center/signing-with-the-new-2023-microsoft-uefi-certificates-what-submitters-need-to-kn/4455787) and updated requirements.

**Our state (Q5 empirical note):** *"Our own shim-review submission, opened before the 2026-06-27 cert-transition deadline, will receive dual-signed (2011 + 2023 CA) binaries from Microsoft for maximum hardware compatibility — a strict improvement over the Fedora-piggyback posture."*

**Status:** ALREADY ALIGNED. Our Q5 anticipates the dual-signing. No gap.

### §4.5 — Contact verification via PGP-encrypted email

**Canonical requirement:** contact verification occurs via PGP-encrypted emails containing random words; proof of ownership is demonstrated by posting decrypted contents in the issue.

**Our state:** Q6 + Q7 have PGP fingerprints (Q6 filled, Q7 PENDING for Ethan Phase 1).

**Action for owner (post-PR-open):** when rhboot maintainers send the verification challenge email, decrypt with `gpg --decrypt` using the Nitrokey (S1 or S2 subkey) and post the decrypted random-word string in the GitHub issue thread. Standard process; no fork-content implication beyond having the keys ready.

### §4.6 — `peer-review contributions` for faster processing

**Canonical requirement:** "Contributing to reviews of other applications significantly accelerates the overall process. Applications labeled 'easy to review' are recommended for newcomers."

**Our state (Q38):** plan says "starting 2026-05-04, peer-review at least 2 open shim-review PRs." Per v1.0-doc-scope sequencing, 2026-05-04 is in our window (≤PR-open at 2026-05-15).

**Action for Q9 fork:** Q38 in the fork README cites the actual PR URLs once the reviews are completed (not at fork-creation time, but updated before PR-open).

---

## §5 — Recommended fork population sequence

**Pre-conditions (must hold before fork population):**
- C6 done (vendor cert public PEM exists) → see roadmap Steps 1-2
- B2 done (shim binary exists) → see roadmap Step 3
- DS reproducibility verified (6/6 PASS) → see roadmap Step 4
- Q7 Ethan PGP fingerprint filled → see roadmap Step 6 (parallel; can be left as `__PENDING__` per R6)

**Population sequence (post-fork-creation):**

1. **Owner forks `rhboot/shim-review` → `InterGenJLU/shim-review`** (GitHub action, ~10 min)

2. **Owner creates branch `intergenos-shim-x64-20260515`** (1 min)

3. **Copy `docker/shim-build/Dockerfile` from InterGenOS master into fork root** (1 min)

4. **Copy vendor cert public PEM into fork root as `vendor_cert.pem`** (1 min)

5. **Export PGP keys to ASC files** (5 min, owner runs):
   - `gpg --armor --export <chris-master-fingerprint> > christopher-cork.asc`
   - `gpg --armor --export <ethan-master-fingerprint> > ethan-bambock.asc` (or `__PENDING__` placeholder if Ethan Phase 1 not done)

6. **Copy `shimx64.efi` build artifact into fork root** (from B2 build output, 1 min)

7. **Copy build logs into `logs/` subdir** (Docker buildkit output + DS harness output, 5 min)

8. **Graduate `docs/research/shim_review/README_draft_skeleton.md` → fork's `README.md`** with all GATED items filled per the post-B2 outputs (per the roadmap Step 4 + GP's GATED-item paste-ready templates from 2026-05-01T17:23:55Z deliverable)

9. **Tag the commit:** `git tag intergenos-shim-x64-20260515 && git push --tags`

10. **File issue in `rhboot/shim-review` linking to the tag** per rhboot process

11. **Post-issue:** wait for "accepted" label or maintainer-feedback; iterate as needed

**Total time estimate (assuming pre-conditions hold):** ~60-90 min owner work + 30-45 min fleet support (SPOC review, etc.)

---

## §6 — File-structure inventory for the fork

```
InterGenJLU/shim-review/  (fork root, branch intergenos-shim-x64-20260515)
├── README.md                            # 39Q answers (graduated from skeleton)
├── Dockerfile                           # copy of docker/shim-build/Dockerfile
├── vendor_cert.pem                      # InterGenOS Secure Boot CA public PEM (from C6)
├── christopher-cork.asc                 # primary maintainer PGP master pubkey ASC
├── ethan-bambock.asc                    # secondary maintainer PGP master pubkey ASC (or PENDING)
├── shimx64.efi                          # the binary to be MS-signed (from B2 build)
├── shimx64.efi.sha256                   # sha256 of the binary (for cross-reference; matches Q25)
├── sbat.csv                             # SBAT section dump from shimx64.efi (from B2 verification)
├── grub2-modules.txt                    # built-in GRUB2 modules list (from B2 verification, fills Q30)
└── logs/
    ├── docker-build_<timestamp>.log     # Docker buildkit output
    ├── verify-reproducibility_<timestamp>.log  # DS harness output (host A vs host B comparison)
    └── readelf_shimx64_<timestamp>.log  # NX bit verification per Q11
```

**Total files:** 11 (README + 10 supporting). Modest payload; most files are <100KB.

---

## §7 — Gaps surfaced + status

| # | Gap | Status |
|---|---|---|
| G1 | Q13 doesn't explicitly map to rhboot's per-batch CVE list | RESOLVED via `docs/grub2-cve-audit.md` enhancement in this same prep batch |
| G2 | Q16 doesn't explicitly confirm the 3 rhboot-cited kernel commits | RESOLVED via `docs/research/shim_review/README_draft_skeleton.md` Q16 enhancement in this same prep batch |
| G3 | No `sbat.csv` artifact yet (gated on B2 verification) | Expected — Step 3 verification produces this |
| G4 | `vendor_cert.pem` doesn't exist yet (gated on C6) | Expected — Step 2 produces this |
| G5 | Ethan PGP key (`.asc`) doesn't exist yet (gated on Ethan Phase 1) | Expected — `__PENDING__` marker in fork README acceptable per R6 |

---

## See also

- `docs/research/shim_review/post_b2_completion_roadmap_2026-05-01.md` — companion sequenced runbook (8 steps from C6 NK#4 smoke-test through PR-open)
- `docs/research/shim_review/README_draft_skeleton.md` — 39Q skeleton (graduates to fork's README at Step 8 of the roadmap)
- `docs/grub2-cve-audit.md` — CVE compliance reference (per-batch table for rhboot reviewer cross-reference, post-G1)
- `docs/v1.0-documentation-scope.md` §3.1 — broader graduation context
