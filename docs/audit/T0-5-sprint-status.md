# T0-5 Sprint Status — for operator morning review

**Sprint start:** 2026-05-19 ~03:22Z (post-walkthrough operator scope grant)
**Status snapshot taken:** 2026-05-19 ~04:00Z (live document — updated each cycle)
**Master tip:** see `git -C /mnt/intergenos log --oneline -1`

---

## Operator-engagement state

**All 4 T0-5 owner-decision items are CLOSED.** Matrix annotations landed at `80ea0d20`:

| Matrix # | Audit row | Decision | Implementation state |
|---|---|---|---|
| 25 | O-011 release-channel model | Pattern B — single public stable + internal-only testing pipeline (Solus-shape); no per-package channel pinning | Pending implementation (lives within windows-host coordinator + build-system coordinator sub-cluster work; no separate scope) |
| 26 | L-022 DR scope | Hybrid local-only-no-B2 — signed-index → private GitHub repo; archive bytes → workstation-local | **LANDED** at `02:48Z`: `/mnt/intergenos-storage` bind-mount + `InterGenJLU/intergenos-mirror-backup` private repo |
| 27 | P-001 GPL §6 path | SOURCES.md dual-path commitment (already in tree at `52f8d17e`) — 3 plumbing items remained | **ALL 3 PLUMBING ITEMS LANDED** (LICENSE pointer + intergenos-legal package + publish-repo.sh source emission) |
| 28 | K-017 `pkm sync` resolution | Natural-language alias sweep — README:39 "Natural-language CLI" claim made load-bearing | **LANDED** at `f4b45135` + doc callout at `95928a52` |

**No operator decisions outstanding for T0-5.** Sprint is implementation-mode only.

---

## Per-row landing tally (live)

### build-system coordinator lane (this session)

| Row(s) | Commit | Description |
|---|---|---|
| Doc callout + drift fix | `95928a52` | pkm natural-language aliases callout in `docs/users/package-management.md` + fix the section that described `pkm update` as doing what `pkm upgrade` does |
| K-017 | `f4b45135` | pkm/cli.py alias sweep — 7 subcommands accept natural-language aliases |
| Matrix #25/26/27/28 | `80ea0d20` | Matrix annotations for the 4 closed operator decisions |
| P-001 item 1/3 | `d7dc38df` | LICENSE preamble pointing at SOURCES.md as the §6 commitment |
| L-027 | `3d6173c1` | pkm/release-keys.json — added S3 + S4 subkey fingerprints |
| H-002 / L-015 / O-004 | `d8656ab1` | pkm/repo.py — parse repos.conf as INI (ratified format); fail-closed on parse error |
| L-008 / H-001 / A-031 | `ed4cfa2a` | `intergenos-keyring` package — ships `/etc/pkm/trusted.gpg` |
| L-008 (defensive) | `1593c897` | intergenos-keyring fingerprint-assert post-import (absorbed from installed-system coordinator peer-review) |
| P-001 item 2/3 | `caebf8a0` | `intergenos-legal` package — ships LICENSE + SOURCES.md to `/usr/share/doc/intergenos/` |
| P-001 item 3/3 | `7e2fb1d8` | `build-source-archives.py` generator + `publish-repo.sh` sources/ upload wiring (fail-closed on missing source archives) |
| L-023 | `5dcddb21` | `mirror-verify.sh` — daily VPS-side integrity verifier |
| Morning-status doc | `231edbba` | This document (live, regenerated each cycle) |
| L-024 | `279d62d9` | publish-repo.sh transparency-log step — appends each signed-index publish to the InterGenJLU/intergenos-mirror-backup git repo with a structured commit message (audit-row-proposed minimum-viable shape; Rekor v2 queued as v1.1 enhancement) |

### windows-host coordinator lane (this session)

| Row(s) | Commit | Description |
|---|---|---|
| Tooling (githooks) | `dc325544` | Pre-push host-portable Python detection at all 3 gates |
| H-009 | `f2b3cf87` | pkm/database.py — missing `import sys` |
| H-005 | `9d85139e` | smoke check `pkm query` → `pkm info` (also fixed function name + labels) |
| H-011 / H-021 | `8b10ad46` | `PackageDB(root="/")` parameter for non-root install scenarios |
| H-013 | `735b1891` | Reject `--archive` with multiple packages (silent-drop fix) |
| H-006 / H-018 | `45953dd0` | pkm verify exit codes — non-zero on usage error and integrity problems |
| tracker.py hygiene | `aa41dcbf` | tracker.py:395 root= param + PackageDB ctor POSIX-paths comment (non-audit-row chore; closes the `"/" + path` syntactic class tree-wide) |

### installed-system coordinator lane (this session)

Peer-reviews delivered (APPROVE in all cases): `ed4cfa2a` keyring (with defensive-assert suggestion absorbed), `f2b3cf87` H-009, `9d85139e` H-005, `8b10ad46` H-011/H-021, `45953dd0` H-006/H-018, `aa41dcbf` tracker.py hygiene. Plus pre-approve on windows-host coordinator's H-006/H-018 exit-code scheme proposal. Cross-lane coordination on H-008 manifest-format question pending. No installer-side regressions surfaced.

---

## Sub-cluster status

| Sub-cluster | Scope | State |
|---|---|---|
| **SC1** Trust-chain preconditions | L-008/H-001/A-031 keyring + H-002/L-015/O-004 repos.conf + L-027 fingerprints | **All 3 rows landed** |
| **SC2** Mirror infrastructure | RESOLVED via 2026-05-18 mirror-sweep (`941d8b6c`); residual is L-001 first-publish actually running (operator action) | RESOLVED |
| **SC3** Trust-chain hardening | L-019 anti-rollback + L-020 schema-version + L-021 TOCTOU + H-022 tar-traversal + L-024 verify-side + L-025 trust-anchor pin | **windows-host coordinator tier 4 — pending; engaged after tier 1-3** |
| **SC4** CLI + DB integrity | ~12 H-rows + O-001 reinstall + H-007/H-008 helpers | **6 rows landed** (H-005, H-009, H-011/H-021, H-013, H-006/H-018) + tracker.py hygiene chore; ~6 remaining: H-004, H-007, H-008, H-022, H-023, H-024, O-001 |
| **SC5** Upgrade safety | ~17 O-rows (--yes/--dry-run + install-new-first + hook execution + kernel re-sign + autoremove + retry+backoff + failover + needrestart) | **windows-host coordinator tier 5 — pending; engaged after SC4** |
| **SC6** DR + transparency + GPL source | L-022 + L-023 + L-024 + P-001 (3 items) | **All 6 rows landed** (L-022 workstation+repo, L-023 mirror-verify.sh, L-024 transparency-log push, P-001 LICENSE pointer + intergenos-legal pkg + source-archive generator) |
| **SC7** User doc sweep | Was K-017 follow-up | RESOLVED (callout landed at `95928a52`; docs were already mostly aligned post-`f4b45135`) |

---

## Remaining work — realistic estimate

**Per the remediation plan, T0-5 was estimated 3-4 weeks of single-coordinator work.** Even at fleet-parallel speeds (5-10x throughput per [feedback_fleet_throughput_calibration]), the cluster won't close in one overnight session.

What is reasonably reachable overnight at fleet speed:
- SC4 remaining ~6 rows (windows-host coordinator + installed-system coordinator) — likely landable
- SC3 tier 4 start (windows-host coordinator) — partial likely once SC4 closes

What is NOT reasonable overnight:
- SC5 in full (~17 upgrade-safety rows; each is a real semantic design call, not a mechanical fix)
- L-001 first-publish actually executing (operator-owned action — ceremony + DNS + key)
- Final integration + peer-review pass (D-009 item 8) requires the implementation to converge first

**My honest projection at session-end:** at the sub-cluster level, T0-5 will be ~65-75% closed at morning — SC1, SC2, SC6, SC7 fully closed; SC4 mostly closed; SC3 partially started; SC5 not started. At the per-row level, that's closer to ~25-35% closure (since SC5 alone is ~17 rows and most of those are design-heavy). No "complete" claim per D-009 item 7. The remaining work is mostly SC5 upgrade-safety which is the highest design-cost-per-row segment of T0-5.

---

## What you have to do (the morning checklist for the operator)

Nothing time-critical. When you're ready:

1. **Review the matrix annotations at `80ea0d20`** — confirm the 4 decisions are captured accurately. If you want any of them encoded as a formal `D-NNN` directive in `docs/owner-directives.md`, that's a follow-on op.
2. **Confirm the L-024 transparency-log shape** — landed at `279d62d9` using the existing InterGenJLU/intergenos-mirror-backup git repo as the append-only log substrate (each publish pushes signed index + structured commit). This was the audit-row-proposed minimum-viable shape. Sigstore Rekor v2 is queued as a v1.1 enhancement (second attestation target alongside the git log). If you'd prefer a different L-024 architecture, the publish-repo.sh wiring is reversible. Operationally, you need to enable force-push protection on the master branch of `InterGenJLU/intergenos-mirror-backup` (GitHub branch protection setting) — that's the constraint that makes the git log actually append-only. Untouched, git lets force-pushes overwrite history.
3. **Schedule L-001 first-publish exercise** — the first-publish runbook has never been run end-to-end. Once SC4 + SC3 + SC6 implementations are stable, exercising the runbook is the next operator-owned milestone. Triggers source-mirror activation + first signed index.
4. **Deploy `scripts/mirror-verify.sh`** to VPS when convenient — script is in tree at `5dcddb21`; deployment is rsync + cron registration on the VPS side. Not urgent until first publish runs.

---

## Standing posture notes for build-system coordinator

- Cycle bus every 270s.
- Per-row broadcasts on every landing (calibration restored after the early-session batch).
- READ-before-POST hook is enforced; tail before every post.
- Public-content audit BLOCKS push if any internal memory/file references leak; WARN-only on vocabulary use.
- All commits push immediately under operator standing direction; rebase on origin before push.
- D-009 item 7 prohibits completion-claim language until peer-review pass closes the sprint. The word "complete" is reserved.
- D-009 item 5 (amended) prohibits any deferment framing without explicit operator direction.

---

*This document is regenerated each cycle by build-system coordinator; treat the most recent commit as authoritative.*
