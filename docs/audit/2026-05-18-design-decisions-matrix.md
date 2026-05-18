# InterGenOS — Design Decisions Matrix

**Started:** 2026-05-18 (Monday morning CDT)
**Trigger:** Owner observation 2026-05-18 ~06:50 CDT — this morning's remediation plan surfaced items as "open" that were in fact ratified weeks/months ago. Three concrete examples: T0-2 (MOK-until-MS-shim was the plan from day 0; PR scheduled 2026-05-22), T0-3 (dracut vs PARTUUID decided after a full day of discussion previously), T0-4 (SSH host-key baking was explicitly directed AGAINST by owner — represents a drift FROM a stated directive).
**Coordinator:** SPOC
**Participating coordinators:** SPOC, IGOSC, WC. All three scan their host-local memory + their angle on the shared repo + VPS resources, then push findings into their section. SPOC synthesizes the cross-host conflict list.
**Companion docs:** [audit](2026-05-18-comprehensive-state-audit.md) (~662 raw findings), [remediation plan](2026-05-18-remediation-plan.md) (17 clusters, 38 owner decisions). Once this matrix is populated, the remediation plan's owner-decision queue gets cross-checked against ratified decisions captured here.

---

## Scope

Each coordinator scans their HOST-LOCAL state plus their angle on shared resources:

### Build-system coordinator
- Local memory tree (operator's local agent-memory directory)
- In-repo docs: `/mnt/intergenos/docs/` (architecture, research, governance, operations, signing-procedure, etc.) + README + CLAUDE.md
- Operator's living tracker + archives (under operator home)
- VPS canonical rules: `intergen://rules/canonical/*` (12+ files)
- VPS reference: `intergen://reference/*` (12 files now incl. operations runbook)
- VPS working/ dir contents (if any)
- Commit history relevant to design decisions
- SPECIFIC PRIORITY HUNT: SSH host-key directive provenance + regression introduction commit

### Installed-system coordinator
- Host-local memory tree
- Host-local carryovers + handoffs + reorient files
- Same repo (mounted)
- Same VPS resources

### Windows-host coordinator
- Host-local memory tree
- Host-local carryovers + handoffs + reorient files
- Same repo (via private overlay network)
- Same VPS resources

---

## Categorical taxonomy

Each scanned decision gets categorized so the matrix sorts cleanly:

| Category | Examples |
|---|---|
| **BOOT** | Shim path, signing chain, UKI/grub model, MOK strategy, initramfs choice (dracut), measured-boot scope |
| **PARTITION** | Disk strategy (LUKS/LVM/BTRFS/ZFS), swap default, encryption-at-rest posture, alongside-install scope |
| **SECURITY** | SSH posture, root account, default firewall, kernel hardening, AppArmor scope, lockdown, password policy |
| **PACKAGE-MGR** | pkm trust model, signed mirror, GPG keyring, upgrade safety, channels, anti-rollback |
| **INSTALLER** | Forge architecture, GUI/TUI parity, package groups, hook framework, locale generation |
| **AI/INTERGEN** | Service model, safety classifier, D-Bus auth, model selection, semantic layer, voice |
| **DESKTOP/UX** | First-boot animation, intergen-welcome, theming, GNOME defaults, GDM session policy |
| **LEGAL** | License posture, GPL source-availability, SPDX, DCO, trademark, BIS, redistribution |
| **BUILD** | Reproducibility, orchestrator scope, test infrastructure, signing ceremony automation |
| **DOCS/MIRROR** | Public hosting, repo hosting, doc layout, runbook canon |
| **PROCESS** | Fleet coordination, MCP namespaces, peer-review, succession, owner-direction discipline |

---

## Decision status states

- **RATIFIED** — owner explicitly approved (with date + provenance pointer)
- **PROPOSED** — captured in design doc, not yet ratified
- **SUPERSEDED** — earlier decision replaced by later one (link both)
- **DEFERRED** — explicitly v1.x or post-v1.0
- **VIOLATED** — code/runtime state diverges from ratified decision (THE category the SSH host-key class lives in)
- **UNKNOWN** — surfaced in audit but no decision found in memory/tracker/docs

---

## Schema per row

```
| Category | Topic | Decision | Status | Date | Ratified by | Source | Conflicts |
```

- **Category** — from taxonomy above
- **Topic** — short hook (e.g., "SSH host keys", "shim path", "first-boot animation")
- **Decision** — the actual position (plain English)
- **Status** — from status states above
- **Date** — when ratified / proposed / superseded
- **Ratified by** — owner / coordinator consensus / individual coordinator
- **Source** — file path or memory ID with line ref if possible
- **Conflicts** — IDs of other rows that contradict this one

---

## Build-system-coordinator findings

_(empty — sub-agents populating now)_

---

## Installed-system-coordinator findings

_(empty — dispatched, awaiting)_

---

## Windows-host-coordinator findings

_(empty — dispatched, awaiting)_

---

## Conflicts to surface to owner

_(populated during synthesis pass — items where ratified decisions disagree, OR where shipped code violates a ratified decision)_

### Class A — Drift FROM stated directives (highest priority — trust calibration)
_(empty)_

### Class B — Conflicting decisions across time (need owner re-ratification)
_(empty)_

### Class C — Decisions captured in memory but never reached the repo
_(empty)_

### Class D — Decisions in the repo but no provenance in memory
_(empty)_

---

## Reconciliation against remediation plan

_(Final pass — once matrix is populated, walk every item in `2026-05-18-remediation-plan.md` and mark each as: ALREADY RATIFIED (vaporize from open queue), CONFLICTS WITH RATIFIED (needs owner re-decision), or GENUINELY OPEN (proceed as plan item).)_

---

## Iteration log

| Date/time | Agent | Action |
|---|---|---|
| 2026-05-18 ~07:00 CDT | SPOC | Matrix scaffolded. Dispatching SPOC sub-agents + IGOSC + WC. |
