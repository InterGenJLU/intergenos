# Helper Manifest Spec v1

**Status:** LIVE — Phase A authored alongside this document. Phase B
(migration of the remaining extra-tier helpers + flip from
WARN-continue to required-manifest) is queued as the next WC-lane
work block.

**Audit row:** H-007. Install-helper path never registers files or
dependencies. Helpers (chrome, vscode, edge, brave, discord, spotify,
claude-code) deposit files into `/opt/`, `/usr/bin/`, `/usr/share/`
etc., and without manifest-driven tracking pkm has zero record of
those files. Consequences before this spec: `pkm files chrome`
returns nothing, `pkm verify chrome` reports `total: 0 OK` even when
the deposited files have been deleted, `pkm remove chrome` only
removes the DB row + leaves orphaned binaries on disk, and
`pkm provides /usr/bin/google-chrome` returns no owner.

## Scope

Helper-installed packages now write a JSON manifest at
`/var/lib/igos/helpers/<name>.manifest` recording the files,
symlinks, and runtime dependencies the helper deposited. pkm reads
the manifest on helper success (`pkm/installer.py:_run_helper`) and
threads the file list through `PackageDB.add_files` and the
dependency list through `PackageDB.add_depends` so the
`pkm files / verify / provides / remove` paths work for
helper-installed packages exactly as they do for archive-installed
packages.

This v1 spec is the stable API contract. Breaking changes go via
SUPERSEDES per the RFC §11 supersede primitive (`helper-lib-v2`
supersedes `helper-lib-v1`; the v1 library remains available during
the supersede overlap window so existing helpers keep working).
Silent removal of an API function is not a permitted change shape.

## Manifest Schema (v1)

JSON document at `/var/lib/igos/helpers/<name>.manifest`:

```json
{
  "version": 1,
  "name": "chrome",
  "version_installed": "138.0.7204.49",
  "files": [
    "/opt/google/chrome/google-chrome",
    "/opt/google/chrome/chrome",
    "/usr/share/applications/google-chrome.desktop"
  ],
  "symlinks": [
    {
      "path": "/usr/bin/google-chrome",
      "target": "/opt/google/chrome/google-chrome"
    }
  ],
  "depends": ["glibc"],
  "post_install_actions_log": [
    "gtk-update-icon-cache /usr/share/icons/hicolor"
  ],
  "build_date": "2026-05-19T15:55:00Z"
}
```

### Field Reference

| Field | Type | Required | Notes |
|---|---|---|---|
| `version` | integer | yes | Schema version. v1 documents must set to `1`; pkm refuses other values. |
| `name` | string | yes | Package name. Must match the helper file `/usr/bin/igos-install-<name>`. |
| `version_installed` | string | recommended | Upstream package's version (e.g. `"138.0.7204.49"`). pkm uses this in `add_installed(version=...)`. Empty string is accepted and treated as `"latest"`. |
| `files` | array of strings | yes (may be empty) | Absolute paths of every regular file the helper deposited. pkm strips the leading slash and threads through `PackageDB.add_files`. |
| `symlinks` | array of objects | yes (may be empty) | Each entry has `path` (the symlink itself, absolute) and `target` (informational, what the symlink points at). pkm tracks `path` in the files table; `target` is **not** auto-deleted on remove unless it independently appears in `files[]`. |
| `depends` | array of strings | yes (may be empty) | Runtime dependency package names. pkm threads each through `PackageDB.add_depends` with `dep_type="runtime"`. |
| `post_install_actions_log` | array of strings | yes (may be empty) | Descriptive text. pkm logs to operation history for transparency; **not replayed on remove** in v1. Teardown for helper-installed side effects (icon caches, mime databases, etc.) lives in a future audit row's surface — separate pre-remove hook. |
| `build_date` | string | recommended | ISO-8601 UTC timestamp the manifest was committed. |

## Path Allowlist

Every absolute path in `files[]` and `symlinks[].path` MUST start
with one of:

- `/usr/`
- `/opt/`
- `/etc/`
- `/var/lib/`

Paths starting with `/`, `/boot/`, `/sbin/`, `/lib/`, `/root/`,
`/home/`, or any other prefix are refused at manifest-read time.
pkm refuses the manifest wire-up + warns the operator on
allowlist violation but does **not** remove the already-deposited
files (the helper script ran as root and may have legitimate
reasons — e.g. a per-user wrapper at `/home/<user>/...` — that
warrant operator triage). The package install record still lands
(via the legacy WARN-continue path) so `pkm list` shows the
package; only the file footprint is missing.

## DoS Cap

Combined `files[] + symlinks[]` count must not exceed **10000**
entries. Manifests exceeding the cap are refused at read time
(same WARN-continue handling as allowlist violation: helper
deposits persist, install record lands, file tracking is
unavailable). The cap defends against a runaway helper script
accidentally recording millions of paths via something like a
`find /` foot-gun.

## Helper API (helper-lib.sh)

Sourceable bash library installed by `intergenos-helper-lib`
(tier=core) at `/usr/share/igos/helpers/helper-lib.sh`.
Helpers source the library at their first line and use the
functions below to record their install footprint.

| Function | Purpose |
|---|---|
| `igos_helper_init <name>` | Initialize manifest staging for package `<name>`. Must be called once before any `record_*`. Exports `IGOS_HELPER_STAGING` + `IGOS_HELPER_NAME`. |
| `igos_helper_set_version <version-string>` | Record the upstream package version (free-form). |
| `igos_helper_record_file <abs-path>` | Append a file path to the manifest's `files[]`. Must be absolute. |
| `igos_helper_record_symlink <abs-link-path> <target>` | Append a symlink entry. Link path must be absolute; target is informational. |
| `igos_helper_record_dep <package-name>` | Append a runtime dependency package name to `depends[]`. |
| `igos_helper_record_post_install_action <text>` | Append a descriptive action string to `post_install_actions_log[]`. Logged not replayed. |
| `igos_helper_commit` | Assemble the JSON manifest + atomic-mv to the final path. Cleans up staging. Must be called at the end of a successful helper run. |

The library uses `python3` for JSON assembly so the schema field
shape stays in lock-step with pkm's reader (both Python-side). A
helper script that aborts after `igos_helper_init` but before
`igos_helper_commit` leaves NO manifest at the final path (the
staging tmpdir + `.manifest.tmp` are abandoned but the atomic-mv
never fires); pkm's `_run_helper` then takes the WARN-continue
path and the package lands without file tracking.

### Atomicity + Known Limitations (v1)

- **Atomic-on-commit, not atomic-during-record.** If the helper
  fails halfway through (e.g. between `record_file` 200 and
  `record_file` 201), the deposited files exist on disk but the
  manifest stays in its `.tmp` state and pkm sees nothing. The
  package install record is NOT created, but the on-disk files
  ARE present — orphan-files class. Acceptable v1 posture; a
  transactional-wrap surface would be a v1.1 audit-row enhancement.
- **ABI stability.** The function shapes above are a stable API
  contract once Phase B ships 7 migrated helpers. Breaking
  changes go via SUPERSEDES (RFC §11), not silent removal. Refs:
  `feedback_audit_multi_wiring_lands_single_commit`.

## pkm-side Integration

`pkm/installer.py:_run_helper` reads + validates the manifest after
the helper subprocess returns 0:

1. Read `/var/lib/igos/helpers/<name>.manifest`.
2. Validate schema (version, required fields, list-typed arrays),
   path allowlist (every `files[]` + `symlinks[].path` entry must
   start with an allowlist prefix), and DoS cap (combined entries
   ≤ 10000).
3. On any validation failure, take the WARN-continue path: print
   a warning to stderr + register the package via
   `add_installed(install_method="helper")` + `log_operation`
   without file tracking. The helper-deposited files remain on
   disk; operator triages.
4. On validation success, run a single `BEGIN/COMMIT` transaction:
   - `add_installed(name, version=manifest.version_installed,
     install_method="helper", commit=False)` → returns `pkg_id`
   - `add_files(pkg_id, [strip-leading-slash for each in files +
     symlinks.paths], commit=False)`
   - `add_depends(pkg_id, [(d, "runtime") for d in depends],
     commit=False)`
   - `log_operation("install", name, new_version=version,
     method="helper")`
   - `conn.commit()` (rollback on any exception)
5. Print each `post_install_actions_log[]` entry to stdout
   (transparency artifact; not replayed on remove).

### Remove-side Behavior

`pkm remove <helper-installed-name>` works naturally via the
existing `pkm/remover.py` path:

- `db.get_files(name)` returns the rows
  `add_files` wrote at install time, including the symlink-path
  entries.
- The remover loop at `pkm/remover.py:62-90` calls `os.remove(abs_path)`
  for each entry. POSIX `unlink()` semantics unlink the symlink
  itself rather than following it, so symlink entries are
  correctly cleaned up + the target files (which appear
  independently in `files[]`) are also deleted.
- Empty directories left after file removal are cleaned by the
  existing `dir_paths` loop at `pkm/remover.py:93-99`.

`pkm files <name>`, `pkm verify <name>`, and
`pkm provides <abs-path>` all read from the same files table, so
all three work as expected for helper-installed packages once
the manifest wire-up lands.

## Phase A vs Phase B

| | Phase A (this commit cluster) | Phase B (next commit cluster) |
|---|---|---|
| Missing manifest | WARN-continue (legacy helpers still work without footprint tracking) | Hard failure (`return False`) — all bundled helpers must have migrated |
| chrome-helper | Migrated (canary) | Already migrated in Phase A |
| Other 6 helpers | Legacy | Migrated to the lib API |
| Phase A peer-review focus | Helper-lib infrastructure correctness; pkm-side read+wire correctness; canary chrome migration | Per-helper mechanical migrations |

Phase B's required-manifest flip + the 6 remaining helper
migrations land as a single commit per audit-multi-wiring
discipline.

## Out of Scope (v1.0)

The following surfaces are explicitly out of v1 scope and queued
as future audit rows or registered v1.1 deferrals:

- **Pre-remove hook for helpers** — teardown for icon caches,
  mime database refresh, font cache, etc. v1.0
  `post_install_actions_log[]` is descriptive transparency only.
- **Action replay on remove** — see above.
- **Manifest-driven update path** — when a helper-installed
  package is upgraded (e.g. chrome 138 → 139), the helper re-runs
  and atomic-mv overwrites the manifest with the new file list.
  Old files no longer in the new manifest may remain on disk if
  the upgrade didn't `cp -a` over them. Mitigation: helpers
  should re-extract into the same install root so the old files
  are overwritten in place. Permanent fix: a manifest-diff-driven
  cleanup pass on upgrade — v1.1 candidate.
- **Manifest-driven verify-content (sha256 per helper file)** —
  v1.0 `add_files` falls back to filesystem-walk sha256 when no
  hash is provided, which works for helper-installed files. A
  helper-time sha256 column in the manifest would let pkm
  compare against the helper-recorded hash rather than the
  live-filesystem hash. v1.1 audit-row candidate.

## References

- [`packages/extra/intergenos-helper-lib/`](../../packages/extra/intergenos-helper-lib/) — the package shipping `helper-lib.sh`
- [`packages/extra/chrome-helper/`](../../packages/extra/chrome-helper/) — Phase A canary migration
- [`pkm/installer.py`](../../pkm/installer.py) — `_run_helper` (helper invocation + manifest read), `_read_helper_manifest` (schema + allowlist + DoS validation)
- [`pkm/remover.py`](../../pkm/remover.py) — `db.get_files(name)` iteration cleanly handles helper-installed files via the existing remove path
- Audit row H-007 in [`docs/audit/2026-05-18-comprehensive-state-audit.md`](../audit/2026-05-18-comprehensive-state-audit.md)
