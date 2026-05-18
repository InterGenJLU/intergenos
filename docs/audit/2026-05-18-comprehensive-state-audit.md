# InterGenOS — Comprehensive State Audit

**Started:** 2026-05-18 (Monday night CDT)
**Trigger:** Cycle-5 ISO presumed ready for signing; subsequent stubs sweep revealed significant gaps (Holy-Grail-class microcode regression, installer-can't-actually-install bugs, multiple Rule 21 stubs across systemd units / shell / Python / packages). Owner directive: **hard halt on ISO builds until this tracker is exhaustively compiled and every item remediated.**
**Coordinator:** SPOC
**Participating coordinators:** SPOC, IGOSC, WC. Other coordinators sidelined per owner.

---

## Scope — 16 lanes

| Lane | Title | Owner |
|---|---|---|
| A | Build system + chroot completeness | SPOC |
| B | Bootloader + Secure Boot chain | SPOC |
| C | Forge installer end-to-end | SPOC |
| D | First-boot + post-install UX | IGOSC |
| E | Kernel + drivers + firmware | SPOC |
| F | Security posture (Holy Grail filter) | IGOSC |
| G | Network + systemd service hygiene | IGOSC |
| H | pkm end-to-end functionality | WC |
| I | InterGen AI integration | IGOSC |
| J | GUI / desktop integration | IGOSC |
| K | Documentation drift | WC |
| L | Repository / mirror state | WC |
| M | Test coverage gaps | SPOC |
| N | Disk / partitioning capabilities | WC |
| O | Update mechanism | WC |
| P | License / legal compliance | WC |

---

## Schema — every finding row

```
| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
```

- **ID**: `<LANE>-<NNN>` (e.g., `A-001`, `F-014`). Stable forever.
- **Severity**: `Holy-Grail` | `Critical` | `High` | `Medium` | `Low` | `Cosmetic`
- **Title**: one-line summary
- **Description**: what's broken/missing, why it matters
- **Code refs**: `path:line` (markdown links preferred)
- **Remediation plan**: one-sentence direction (not necessarily the full diff)
- **Status**: `open` → `in-progress` → `fixed-pending-verify` → `closed`
- **Verified**: commit sha + on-disk validation result, or empty

## Severity rubric

| Tier | Definition | Examples |
|---|---|---|
| **Holy-Grail** | Violates Holy Grail security alignment — affects the security posture of every install | Microcode never loading; signed-boot chain missing; SSH host key reuse; default-password account |
| **Critical** | Breaks installer / boot / core functionality on real hardware | parted missing; bootloader stage fails; kernel won't load |
| **High** | Feature non-functional or significantly degraded; user-visible failure | pkm GPG keyring missing; service won't start; intergen daemon fails to connect |
| **Medium** | Workaround exists; degraded UX; non-critical drift | UX hint dropped; redundant code paths; documentation drift |
| **Low** | Polish, hygiene, defensive | Vestigial comments; orphaned dead source; cosmetic |
| **Cosmetic** | No functional impact | Typos, formatting, naming consistency |

## Conventions

- **Each agent edits only their own section below.** No cross-section edits during scan. Avoids merge conflicts; synthesis is SPOC's job at end.
- **Append-only during scan.** Don't reclassify existing findings; flag for revisit instead.
- **Don't deduplicate across lanes during scan.** Same issue surfacing in two lanes = signal, not noise. Synthesis collapses duplicates.
- **Iterate.** First pass: surface findings. Second pass: cross-check against actual code + classify false positives. Third pass: what did the first two passes miss?
- **Use sub-agents aggressively.** Owner directive: as many parallel sub-agents per lane as feasible.
- **Commit + push your section every iteration.** Don't hoard findings locally. SPOC pushes synthesis commits.

## Status header (update as agents complete iterations)

| Agent | Lanes | Initial scan | Iteration 2 | Iteration 3 | Notes |
|---|---|---|---|---|---|
| SPOC | A, B, C, E, M | in-progress | | | |
| IGOSC | D, F, G, I, J | dispatched | | | |
| WC | H, K, L, N, O, P | dispatched | | | |

---

## SPOC findings

### Lane A — Build system + chroot completeness

_(SPOC sub-agent population)_

### Lane B — Bootloader + Secure Boot chain

_(SPOC sub-agent population)_

### Lane C — Forge installer end-to-end

_(SPOC sub-agent population; seeded with tonight's Python sweep findings)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| C-001 | Critical | parted binary not in chroot | `installer/backend/disks.py:131-153,277,282,311` calls parted; binary missing from chroot entirely; no package builds it | [disks.py:131](../../installer/backend/disks.py#L131) | Add `packages/base/parted/` package; standard autotools build; verify chroot post-build | open | |
| C-002 | Critical | partprobe missing (ships with parted) | Alongside-install path dies on `partprobe` invocation; same root cause as C-001 | [disks.py:318](../../installer/backend/disks.py#L318) | Resolved by C-001 once parted package lands and includes partprobe | open | |
| C-003 | Critical | shim-signed staging fails unconditionally | `installer/backend/bootloader.py:37-39` references `/usr/share/shim-signed/{shimx64,mmx64}.efi`; directory doesn't exist; install raises RuntimeError at line 133 | [bootloader.py:37](../../installer/backend/bootloader.py#L37) | Add pre-flight check in install.py that detects missing shim and surfaces clear error BEFORE partitioning; band-aid until MS sponsorship | open | |

### Lane E — Kernel + drivers + firmware

_(SPOC sub-agent population)_

### Lane M — Test coverage gaps

_(SPOC sub-agent population)_

---

## IGOSC findings

### Lane D — First-boot + post-install UX

_(IGOSC sub-agent population; seeded with tonight's findings)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| D-001 | High | First-boot animation built but never shipped | `assets/intergen-firstboot-drm/` has working DRM/KMS binary + .service + Makefile; no `packages/desktop/intergen-firstboot/` package; chroot has no binary; greeter's `After=intergen-firstboot.service` is a no-op | [assets/intergen-firstboot-drm/](../../assets/intergen-firstboot-drm/) | Create `packages/desktop/intergen-firstboot/`; ship binary + session wrapper; wire via custom Wayland session that runs pre-compositor on first login | open | |
| D-002 | Medium | Greeter binary is a stub | `installer/data/intergenos-first-boot-greeter.service` references `/usr/libexec/intergenos/first-boot-greeter`; binary doesn't exist anywhere | [intergenos-first-boot-greeter.service:37](../../installer/data/intergenos-first-boot-greeter.service#L37) | DELETE the .service entirely (owner directive); installer handles user creation, no role for tty1 greeter | open | |

### Lane F — Security posture

_(IGOSC sub-agent population; seeded with tonight's finding)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| F-001 | Holy-Grail | Microcode never loading on shipped ISOs | `scripts/create-image.sh:292` tests `/usr/bin/iucode_tool` but binary is at `/usr/sbin/iucode_tool`; entire Intel microcode early-load block silently skipped; shipped images running with unpatched CPU vulns (Spectre/Meltdown/Zenbleed-class) | [create-image.sh:292](../../scripts/create-image.sh#L292) | One-line path fix; verify post-build that `/boot/intel-ucode.img` exists in shipped ISO; add post-build assertion | open | |

### Lane G — Network + systemd service hygiene

_(IGOSC sub-agent population; seeded)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| G-001 | High | 7 server packages have ReadWritePaths=/run/<name> without tmpfiles.d | etcd/haproxy/influxdb/memcached/valkey/postgresql/apache-httpd all declare RW paths under /run with no leading `-`, no RuntimeDirectory=, no tmpfiles.d entry; systemd refuses unit start if path missing at namespace setup | [packages/extra/{etcd,haproxy,influxdb,memcached,valkey,postgresql,apache-httpd}/](../../packages/extra/) | Add `tmpfiles.d/<name>.conf` to each package creating `/run/<name>` at boot; canonical systemd pattern | open | |
| G-002 | High | mariadb /run/mysqld vs /run/mariadb path mismatch | Service declares `ReadWritePaths=/run/mysqld`; package's own tmpfiles.d creates `/run/mariadb`; unit will fail at start | [mariadb.service:41](../../packages/extra/mariadb/mariadb.service#L41) | Pick one path; align both sides | open | |
| G-003 | Low | init.sh masks nonexistent apache.service | `init.sh:220-223` masks `apache.service`; chroot has `httpd.service` instead; no-op but Rule 21 noise | [init.sh:220](../../installer/init/init.sh#L220) | Drop the apache.service line from the mask list | open | |

### Lane I — InterGen AI integration

_(IGOSC sub-agent population; seeded)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| I-001 | Low | intergen/data/intergen.service is orphan dead code | Package build.sh generates its own inline replacement; source file misleading | [intergen/data/intergen.service](../../intergen/data/intergen.service) | Delete the orphan; build.sh inline is authoritative | open | |
| I-002 | Low | intergen/data/com.intergenos.InterGen.service same orphan class | Same as I-001 | [intergen/data/com.intergenos.InterGen.service](../../intergen/data/com.intergenos.InterGen.service) | Delete the orphan | open | |
| I-003 | Medium | ai/intergen verify_paths undercount | Declares 2 paths; build.sh installs ~10 (CLI wrapper, config, systemd user unit, dbus service, state dirs) | [packages/ai/intergen/package.yml](../../packages/ai/intergen/package.yml) | Extend verify_paths to cover load-bearing entry points | open | |

### Lane J — GUI / desktop integration

_(IGOSC sub-agent population; seeded)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| J-001 | Medium | install-theming.sh writes divergent intergen-welcome path | Writes script to `/usr/share/intergen-welcome/` with stale-Exec autostart; canonical package installs to `/usr/libexec/intergen-welcome/`; bypasses package's once-per-user gate | [install-theming.sh:397](../../scripts/install-theming.sh#L397) | Remove divergent write block from install-theming.sh; defer entirely to package | open | |

---

## WC findings

### Lane H — pkm end-to-end functionality

_(WC sub-agent population; seeded)_

| ID | Severity | Title | Description | Code refs | Remediation plan | Status | Verified |
|---|---|---|---|---|---|---|---|
| H-001 | High | pkm GPG keyring missing | `pkm/repo.py:74` GPG_KEYRING=/etc/pkm/trusted.gpg; doesn't exist in chroot; pkm update fails | [pkm/repo.py:74](../../pkm/repo.py#L74) | Generated as side effect of signing ceremony; verify exists post-ceremony and during install | open | |

### Lane K — Documentation drift

_(WC sub-agent population)_

### Lane L — Repository / mirror state

_(WC sub-agent population)_

### Lane N — Disk / partitioning capabilities

_(WC sub-agent population)_

### Lane O — Update mechanism

_(WC sub-agent population)_

### Lane P — License / legal compliance

_(WC sub-agent population)_

---

## Iteration log

| Date/time | Agent | Action |
|---|---|---|
| 2026-05-18 ~00:50 CDT | SPOC | Tracker created. Seeded with tonight's 4-agent stubs sweep findings. Lane assignments dispatched. |
