# 10 — Recommendations and what's missing

**Audience:** maintainers reading the 1-9 runbook set and asking "what else?"

This is the synthesis layer. After authoring topics 1-9, the following gaps surfaced — partly through inconsistencies between dispatched scope and tree reality, partly through the case-study work in topic 9. Each item is a recommendation, not a fait accompli; they need maintainer review before any of them lands as work.

## R1 — Author `scripts/build-vm-seed.sh`

**Gap:** `vm/cloud-init/README.md` references `scripts/build-vm-seed.sh` as the canonical way to substitute the three template placeholders (`<username>`, `REPLACE_WITH_PASSWORD_HASH`, `REPLACE_WITH_SSH_PUBLIC_KEY`) in `vm/cloud-init/user-data` before packing the seed.iso. **The script does not exist.** Every operator setting up a build VM right now has to do the substitution manually with a private working copy.

**Recommendation:** author the script. Shape:

```sh
scripts/build-vm-seed.sh \
    --username christopher \
    --pubkey ~/.ssh/id_ed25519.pub \
    --output /tmp/seed.iso
# Prompts for password interactively (or accepts via $VM_SEED_PASSWORD env);
# never accepts password on argv (would land in shell history).
```

Substitute into a tempfile, generate seed.iso via `cloud-localds`, delete the substituted intermediate before exiting. Don't allow the substituted output to land in the repo (pre-commit gate against placeholder-replacement on `vm/cloud-init/user-data` already exists per the README).

**Priority:** moderate. Topic 01 currently documents the manual workaround; the script is friction-reducer, not a blocker.

## R2 — Public-facing operational-notes mirror

**Gap:** the runbook set in this directory cites several incidents and operational disciplines that live in the project's internal memory namespace (the `feedback_*.md` files). The pre-push public-content audit gate (correctly) blocks references to that namespace from public-repo docs — those files are internal. But the runbook reader sometimes wants the canonical text of the operational note being referenced, and there's currently no public-facing place to find it.

**Recommendation:** author a public mirror at `docs/operational-notes/` containing the subset of internal operational discipline that's safe to publish. Initial set (one file per topic; each ~30-80 lines):

- `docs/operational-notes/apt-timer-isolation.md` — why apt timers are masked on the build VM, what fails if they aren't, how to re-mask after an accidental unmask
- `docs/operational-notes/mksquashfs-mount-point-preservation.md` — the wildcard-exclusion form (`-e '<path>/*'` not `-e <path>`) and the regression it prevents
- `docs/operational-notes/silent-skip-class.md` — the class of regression where a package recipe exists but doesn't actually produce files; how the verify_paths audit catches it
- `docs/operational-notes/scdaemon-conf-coexistence.md` — verbatim scdaemon.conf shape for GPG+PKCS11 on the same hardware token (used by topic 03)
- `docs/operational-notes/no-manual-ceremony.md` — why every signing pass goes through `scripts/sign-release.sh` rather than ad-hoc invocations
- `docs/operational-notes/reproducibility-link-graph.md` — why sha256-of-artifact alone is insufficient for hermetic-build claims; readelf -d for DT_NEEDED + RPATH/RUNPATH

Once these exist, the runbook docs can cite them by relative path inside the repo (`docs/operational-notes/...`) — passes the public-content audit, gives the reader the canonical text. The internal `feedback_*.md` set continues to be the source-of-truth working set for active discipline; the public mirror is the curated subset that's stable enough to publish.

**Priority:** high. The runbook set's value compounds when readers can self-serve on the cited disciplines.

## R3 — `scripts/check-aspirational-stubs.py`

**Gap:** Rule 21 in `docs/build-development-rulebook.md` mentions `scripts/check-aspirational-stubs.py` as "scheduled" — a script that would grep init.sh / *.service / *.desktop / tmpfiles.d / sysusers.d / polkit rules / dbus configs for path references and cross-check each against the packages-yml-derived install manifests. **It doesn't exist.** Rule 21 enforcement is currently manual + reactive (someone notices a stub during audit) rather than continuously gated.

**Recommendation:** author the script. Two reasonable shapes:

- **Pre-push hook gate** — like the verify_paths gate (gate 8). Catches stubs before they land on master.
- **Periodic full-tree audit** — like `check-builder-coverage.py`. Catches stubs that landed before the gate existed (the existing backlog).

Both have value. Start with the periodic full-tree audit (it's lower-stakes — false positives don't block pushes) and graduate to a pre-push gate once the rule set has been tuned against the real codebase.

**Priority:** high. Manual stub-hunts (today's work) tally up the labor cost of continuous-detection absence.

## R4 — Public-facing incident history / postmortems

**Gap:** topic 9 references five case studies by incident name (TLS-trust verification bug, objcopy --dump-section mutates, chroot-rsync coverage gap, greedy-glob silent-skip, forge tarball stale-snapshot) and points readers at internal memory for the verbatim text + dated commit references. A new contributor reading topic 9 can see the calibration but can't trace the citations.

**Recommendation:** author `docs/incident-history.md` (or `docs/postmortems/`) containing public versions of each case study — same facts, scrubbed of internal vocabulary. Format per-incident: title, date, symptom, root cause, remediation, lesson. Optional: link to the canonical fix commit by SHA so a reader can see the actual diff that closed the issue.

This complements R2 (operational-notes mirror) — the operational notes are the *rules*, the incident history is the *evidence base* for the rules.

**Priority:** moderate. Lower than R2/R3 because the cost of absence is "harder for new contributors to internalize the lessons" rather than "active regression-prevention gap."

## R5 — Top-level `CLAUDE.md` or equivalent project orientation doc

**Gap:** dispatched-AI work has historically referenced a top-level `CLAUDE.md` as if it exists. It doesn't (per the F1' meta-finding in the recent stub-hunt audit). The canonical project-orientation file lives in each agent's gitignored `.claude/` namespace — fine for human operators who know to look there, opaque for new agents or external reviewers.

**Recommendation:** decide and document the policy. Two reasonable shapes:

- **No top-level CLAUDE.md, and update dispatches to stop referencing it.** Make `README.md` + this `docs/operations/` set the canonical orientation surface.
- **Author a public top-level CLAUDE.md** that summarizes project conventions, points at `docs/operations/`, and explicitly says "operator-private discipline lives in each operator's .claude/ namespace and is not committed."

Either resolves the dispatched-ref-as-if-exists footgun.

**Priority:** low. Cosmetic / discoverability rather than correctness.

## R6 — Unified audit-script invocation surface

**Gap:** the project currently has several Python audit scripts that read package.yml, walk the chroot, or grep public content — `scripts/check-public-content.py`, `scripts/check-builder-coverage.py`, `scripts/pre-squashfs-audit.py`, plus the proposed `scripts/check-aspirational-stubs.py` (R3). Each is invoked separately. There's no `scripts/audit-all.sh` that runs the whole set and produces a unified report.

**Recommendation:** author a wrapper. Shape:

```sh
scripts/audit-all.sh                # run everything, exit non-zero if any halt
scripts/audit-all.sh --check builder-coverage stubs   # subset
scripts/audit-all.sh --report audit-report-<ts>.md    # emit consolidated report
```

Makes the "before squashfs" audit gate a single invocation; makes the "ahead of a campaign" sanity check a single invocation; gives reviewers a single artifact to reference instead of grepping six logs.

**Priority:** moderate. Quality-of-life rather than correctness — the existing scripts already work; this is convenience + report-consolidation.

## R7 — Cross-reference index in this directory

**Gap:** the topic-1-through-10 docs in this directory each have a "Cross-references" section that links to siblings. There's no master index that lists every doc + a one-line synopsis + the cross-link graph.

**Recommendation:** the `README.md` in this directory (topic-11, authored alongside the 10 topics) serves as the index. Verify it lists all 10 topics + the operational-notes from R2 + the incident-history from R4 in a single navigable surface.

**Priority:** low — it's literally the README being authored as part of this work.

## R8 — Document the chroot-vs-image install distinction explicitly

**Gap:** a subtle source of confusion during the recent stub-hunt audit: scripts like `create-image.sh` install some artifacts into the **disk-image mount-point** (`$MOUNT_POINT`), not into the **build chroot** (`/mnt/igos`). So a fresh check of `/mnt/igos/usr/libexec/intergenos/first-boot-greeter` returns "missing" even though the greeter ships correctly in the installed system — it lands at image-creation time, not at chroot-build time. The same applies to `/usr/share/intergen-welcome/intergen-welcome.py` and others.

The live ISO's squashfs (built from the chroot) therefore doesn't contain these image-time installs. For most of them that's correct (they're post-install greeters). For a few it could be unintentional drift.

**Recommendation:** author `docs/architecture/chroot-vs-image-installs.md` documenting:

- Which artifacts install into the chroot (and therefore the squashfs / live ISO)
- Which install into the disk image only (and therefore the installed system only, not the live ISO)
- For each "image-only" artifact, the explicit rationale (greeter shouldn't fire on live ISO; welcome.py is post-install onboarding; etc.)
- A guard rail audit that catches "this should be in chroot but is image-only" drift

**Priority:** moderate. Currently this surfaces as confusion during audits; documentation reduces the confusion but doesn't actively prevent the drift class. A guard-rail audit (R3 generalization) would do the latter.

## Summary — priority ordering

| # | Recommendation | Priority |
|---|---|---|
| R2 | Public-facing operational-notes mirror | high |
| R3 | `scripts/check-aspirational-stubs.py` (Rule 21 enforcement) | high |
| R1 | `scripts/build-vm-seed.sh` | moderate |
| R4 | Public-facing incident history / postmortems | moderate |
| R6 | Unified audit-script invocation surface | moderate |
| R8 | Chroot-vs-image install distinction doc | moderate |
| R5 | Top-level CLAUDE.md policy decision | low |
| R7 | Cross-reference index (already the README) | low (covered) |

None of these are blockers for the immediate kickoff. They are the next-meaningful-batch of operational-infrastructure work that maintains the rate at which 1-9 stays accurate and the rate at which new contributors can self-serve.
