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
| Morning-status doc (initial) | `231edbba` | This document (live, regenerated each cycle) |
| L-024 (publish-side) | `279d62d9` | publish-repo.sh transparency-log step — appends each signed-index publish to InterGenJLU/intergenos-mirror-backup git repo with structured commit message |
| Morning-status doc (refresh 1) | `90e6929e` | reflects L-024 landing + WC H-006/H-018 + tracker hygiene |

### windows-host coordinator lane (this session)

| Row(s) | Commit | Description |
|---|---|---|
| Tooling (githooks) | `dc325544` | Pre-push host-portable Python detection at all 3 gates |
| H-009 | `f2b3cf87` | pkm/database.py — missing `import sys` |
| H-005 | `9d85139e` | smoke check `pkm query` → `pkm info` (also fixed function name + labels) |
| H-011 / H-021 | `8b10ad46` | `PackageDB(root="/")` parameter for non-root install scenarios |
| H-013 | `735b1891` | Reject `--archive` with multiple packages (silent-drop fix) |
| H-006 / H-018 | `45953dd0` | pkm verify exit codes — non-zero on usage error and integrity problems |
| tracker.py hygiene | `aa41dcbf` | tracker.py:395 root= param + PackageDB ctor POSIX-paths comment (non-audit-row chore) |
| H-024 | `48e33d4d` | helper-installer env hygiene — strip to allowlist (PATH/HOME/USER/...); closes LD_PRELOAD / *_PROXY / PYTHONPATH attack vectors |
| H-023 | `81609e30` | fcntl.flock serialization on /var/lock/pkm.lock for mutating subcommands |
| O-001 / H-010 | `69db547d` | `pkm reinstall` subcommand (closes O-001 + H-010 error-message drift naturally) |
| H-008 | `a2caacec` | canonical .PKGINFO emission (lowercase Arch-style key=value) — populates DB tier/description/license/build_date |
| H-008 sibling fix | `13b58c0a` | follow-on for IGOSC-caught build_date dict-key rename — `pkginfo.get("build_date")` (was `"builddate"`) |
| H-004 | `dbdb9533` | wire `add_depends` from manifest at install time — closes the silent-`pkm remove glibc` foot-gun |
| L-020 | `0e265392` | index schema-envelope validation — version + min_pkm_version + signature_format gates |
| L-020 sibling fix | `f01afd8e` | follow-on for SPOC-caught `__version__` import missing — `from . import __version__` at pkm/repo.py:68 |
| L-025 | `0c71c27f` | pin release-key fingerprints (S1-S4 from release-keys.json); reject non-pinned signatures via gpg --status-fd parse |

### installed-system coordinator lane (this session)

Peer-reviews delivered (APPROVE in all cases): `ed4cfa2a` keyring, `f2b3cf87` H-009, `9d85139e` H-005, `8b10ad46` H-011/H-021, `45953dd0` H-006/H-018, `aa41dcbf` tracker.py hygiene, `48e33d4d` H-024, `81609e30` H-023, `69db547d` O-001/H-010, `a2caacec` H-008 (PARTIAL APPROVE — caught build_date BLOCKING bug, fixed in `13b58c0a`), `13b58c0a` H-008 follow-on, `dbdb9533` H-004. Plus inline observations on `0c71c27f` L-025 + `0e265392` L-020 (SPOC primary). Two BLOCKING runtime-semantic bugs caught at peer-review pre-close (H-008 build_date + L-020 __version__) — cross-coordinator review pattern working. Memory addendum saved on caller-vs-implementation contract class.

---

## Sub-cluster status

| Sub-cluster | Scope | State |
|---|---|---|
| **SC1** Trust-chain preconditions | L-008/H-001/A-031 keyring + H-002/L-015/O-004 repos.conf + L-027 fingerprints | **All 3 rows landed** |
| **SC2** Mirror infrastructure | RESOLVED via 2026-05-18 mirror-sweep (`941d8b6c`); residual is L-001 first-publish actually running (operator action) | RESOLVED |
| **SC3** Trust-chain hardening | L-019 anti-rollback + L-020 schema-version + L-021 TOCTOU + H-022 tar-traversal + L-024 verify-side + L-025 trust-anchor pin | **2 rows landed** (L-020 + L-025); 4 remaining: L-019 anti-rollback (state-persistence-class; WC engaging next) + L-021 TOCTOU + L-024 verify-side + H-022 tar-traversal |
| **SC4** CLI + DB integrity | ~12 H-rows + O-001 reinstall + H-007/H-008 helpers | **12 rows landed** (H-004 + H-005 + H-006/H-018 + H-008+sibling + H-009 + H-010 + H-011/H-021 + H-013 + H-023 + H-024 + O-001) + tracker hygiene chore; ~2 remaining: H-007 helper manifest spec + H-022 PEP 706 |
| **SC5** Upgrade safety | ~17 O-rows (--yes/--dry-run + install-new-first + hook execution + kernel re-sign + autoremove + retry+backoff + failover + needrestart) | **0 rows landed** — WC tier 5; engaged after SC4 + SC3 close |
| **SC6** DR + transparency + GPL source | L-022 + L-023 + L-024 + P-001 (3 items) | **All 6 rows landed** (L-022 workstation+repo, L-023 mirror-verify.sh, L-024 transparency-log push, P-001 LICENSE pointer + intergenos-legal pkg + source-archive generator) |
| **SC7** User doc sweep | Was K-017 follow-up | RESOLVED (callout landed at `95928a52`; docs were already mostly aligned post-`f4b45135`) |

---

## Remaining work — realistic estimate

**Per the remediation plan, T0-5 was estimated 3-4 weeks of single-coordinator work.** Even at fleet-parallel speeds (5-10x throughput per [feedback_fleet_throughput_calibration]), the cluster won't close in one overnight session.

What's reasonably reachable in the remaining overnight window:
- SC4 final 2 rows (H-007 helper manifest + H-022 PEP 706 tarfile filter) — windows-host coordinator's queue
- SC3 next 4 rows (L-019 anti-rollback + L-021 TOCTOU + L-024 verify-side + H-022) — windows-host coordinator tier 4

What is NOT reasonable overnight:
- SC5 in full (~17 upgrade-safety rows; each is a real semantic design call). Maybe 2-4 of the simpler ones land.
- L-001 first-publish actually executing (operator-owned action — ceremony + DNS + key)
- Final integration + peer-review pass (D-009 item 8) requires the implementation to converge first

**Honest projection at session-end (revised, mid-arc):** sub-cluster level — SC1, SC2, SC6, SC7 fully closed; SC4 ~85% closed (12 of 14); SC3 ~33% closed (2 of 6); SC5 0% / partial; that's ~70% sub-cluster coverage. At per-row level — ~18-22 rows landed out of ~50 audit rows in the sprint scope (excluding pre-resolved K-017 + matrix annotations), roughly 35-45% closure. SC5 remains the bulk of unclosed work + the hardest design surface.

**Important fleet-discipline observation:** 2 BLOCKING bugs caught at peer-review pre-close-claim — H-008 build_date dict-key rename (caught by installed-system coordinator) + L-020 `__version__` import missing (caught by build-system coordinator). Both ran py_compile + import-resolution clean while failing at runtime. D-009 item 7 (no completion claim until peer-reviewed) + item 8 (peer-review when possible) doing concrete work. Memory addendum saved on caller-vs-implementation contract class so the pattern doesn't repeat.

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
