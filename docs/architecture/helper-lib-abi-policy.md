# intergenos-helper-lib ABI Stability Policy

**Status:** LIVE for v1.0. Authored 2026-05-19 alongside the T0-5
closure-checklist Decision C disposition.

**Audit row link:** H-007 (helper manifest spec) +
[`docs/architecture/helper-manifest-spec-v1.md`](helper-manifest-spec-v1.md).

---

## Policy summary

The `intergenos-helper-lib` shell API (function names, parameter
arities, manifest schema) is **frozen at v1.0**. From v1.0 onwards:

- **Patch and minor releases** within the v1 line MUST preserve the
  v1 API contract. No silent removal, no silent rename, no silent
  arity change. Adding new optional API functions to v1 is permitted
  (additive change). Adding new optional parameters at the end of an
  existing function signature is permitted IF the new parameter is
  defaulted in a way that preserves the existing call-site shape.
- **Breaking changes** ship as a new major version (`helper-lib-v2`),
  packaged as `intergenos-helper-lib` declaring its package version
  bump + a `Supersedes: intergenos-helper-lib < 2.0` entry per the
  pkm RFC §11 SUPERSEDES primitive.
- **Overlap window:** when v2 lands, v1 stays installable in parallel
  for at least one full release cycle so existing helpers continue
  to source their pinned major version. Helpers MAY pin the major
  version they require via the
  `IGOS_HELPER_LIB_API_VERSION` constant exposed by the library (see
  below).

## API version marker

The library exports an `IGOS_HELPER_LIB_API_VERSION` integer constant
at the top of `/usr/share/igos/helpers/helper-lib.sh`. Helpers MAY
assert against this constant immediately after sourcing the library:

```bash
source /usr/share/igos/helpers/helper-lib.sh

if [ "${IGOS_HELPER_LIB_API_VERSION:-0}" -lt 1 ]; then
    echo "ERROR: this helper requires intergenos-helper-lib API v1 or newer." >&2
    exit 1
fi
```

The constant is monotonically incremented across major API versions
(v1=1, v2=2, ...). Within a major version, the constant value does
NOT change even when new optional functions are added (additive
changes do not warrant a new API version number; they ship in the
v1 library and helpers that don't use them are unaffected).

## API surface (v1)

The v1 API consists of seven shell functions:

| Function | Required args | Purpose |
|---|---|---|
| `igos_helper_init <name>` | 1 | Initialize a per-invocation manifest staging area for package `<name>`. Must be called once before any `record_*` call. |
| `igos_helper_set_version <version>` | 1 | Record the upstream version string (free-form) for the `version_installed` manifest field. |
| `igos_helper_record_file <abs_path>` | 1 | Append a file path to the manifest's `files[]` array. Path must be absolute. |
| `igos_helper_record_symlink <link_path> <target>` | 2 | Append a symlink entry. `link_path` is what pkm tracks + unlinks on remove; `target` is informational. |
| `igos_helper_record_dep <pkg_name>` | 1 | Append a runtime dependency package name. pkm threads through `add_depends` so reverse-dep tracking works. |
| `igos_helper_record_post_install_action <description>` | 1 | Append a descriptive action string. v1.0 stores these as a transparency-log artifact; not replayed on remove (teardown lives in a v1.x hook surface). |
| `igos_helper_commit` | 0 | Assemble staging state into the JSON manifest at `/var/lib/igos/helpers/<name>.manifest`. Atomic mv from sibling `.tmp` path. |

Any function not in this table is **not** part of the v1 API surface
and MAY be removed or restructured without a v2 supersede.

## Crash recovery: partial-manifest sidecar

When `igos_helper_init` runs, it installs an `EXIT` trap that, on
abnormal process exit (helper crash, `set -e` abort, kill signal),
writes a `<name>.manifest.partial` JSON sidecar at the manifest dir
capturing whatever was staged. The sidecar carries the same schema
as the canonical manifest plus a `"partial": true` flag. pkm's
`_run_helper` detects the sidecar after a non-zero helper exit and
surfaces the orphan file list to the user in the install-failed
error message. `igos_helper_commit` clears `IGOS_HELPER_COMMITTED` +
removes the EXIT trap before doing its work, so the sidecar is only
written on crash. A subsequent successful `igos_helper_init` of the
same package cleans any prior `.manifest.partial` so a retried
install does not leave a stale orphan-state signal.

This recovery surface is part of the v1 contract: helpers MAY rely
on the trap being installed by `igos_helper_init`; tooling MAY rely
on the sidecar shape (same schema + `partial: true`) for crash
detection. Future major versions may change the sidecar location or
naming scheme via the same SUPERSEDES mechanism.

### `IGOS_HELPER_USER_CLEANUP` env-var contract

Because bash `trap ... EXIT` semantics REPLACE prior traps (no native
composition), helpers MUST NOT install their own EXIT trap when they
source `helper-lib.sh`. Doing so would collide with the sidecar trap
that `igos_helper_init` installs -- one trap wins, the other is
silently dropped depending on call ordering.

To register a cleanup command (typically `rm -rf "$TMPDIR"`),
helpers SHOULD assign the command string to the
`IGOS_HELPER_USER_CLEANUP` shell variable instead. The library's
internal EXIT handler runs `eval "$IGOS_HELPER_USER_CLEANUP"` on
every exit (both success and crash paths) before doing any
sidecar-related work. The variable is read at exit time, so helpers
may assign it any time before script termination.

```bash
TMPDIR=$(mktemp -d)
IGOS_HELPER_USER_CLEANUP="rm -rf $TMPDIR"
```

This contract is part of the v1 API surface: helpers MAY rely on
the library running their assigned cleanup on exit; the library
guarantees the assignment is honored on both success and crash
paths. Changes to the variable name or eval semantics go through
SUPERSEDES.

## Manifest schema version

The JSON manifest written by `igos_helper_commit` carries its own
`"version": 1` field. pkm's manifest reader at
`pkm/installer.py:HELPER_MANIFEST_DIR` validates this field; manifest
version bumps go through the same SUPERSEDES mechanism as the library
API itself. Manifest v1 schema is documented at
[`docs/architecture/helper-manifest-spec-v1.md`](helper-manifest-spec-v1.md).

## What constitutes a breaking change

A change is breaking if any of these are true:

1. Removal of a v1 API function.
2. Rename of a v1 API function.
3. Change to the required arity (positional) of an existing function.
4. Change to the order or meaning of existing positional arguments.
5. Change to the path of the manifest file or the staging tmpdir
   location (would silently break parallel-running helpers).
6. Change to the manifest JSON schema that removes or renames an
   existing field consumed by pkm's reader.

A change is NOT breaking (allowed in v1.x):

- Adding a new API function (additive).
- Adding a trailing optional positional parameter with a backwards-
  compatible default.
- Adding a new manifest field that pkm's reader treats as optional.
- Strengthening internal validation (rejecting invalid input that v1
  callers should not have been producing) -- documented as security
  hardening, not API breakage.

## SUPERSEDES wiring for the v1 → v2 transition

When the v2 library lands:

1. `packages/core/intergenos-helper-lib/package.yml` bumps `version`
   to `2.0` and adds a `supersedes:` entry referencing the v1
   package per the RFC §11 spec.
2. The v2 build deposits its content at
   `/usr/share/igos/helpers/helper-lib.sh` (replacing v1's file).
3. A parallel package `intergenos-helper-lib-v1` may be shipped
   during the overlap window providing the v1 library at
   `/usr/share/igos/helpers/helper-lib-v1.sh` so helpers that
   explicitly source the v1 path continue to work.
4. Helpers that source `helper-lib.sh` without a major-version pin
   pick up v2; helpers that source `helper-lib-v1.sh` continue to
   resolve against v1.

The overlap-window package model is the v1.x extension point; v1.0
ships only the unversioned path. Helpers shipped in v1.0 that want
forward-compatibility with v2 should assert against
`IGOS_HELPER_LIB_API_VERSION` immediately after sourcing.

## Where this policy is enforced

- **Maintainer-discipline** (v1.0): the v1 helper-lib.sh comments
  reference this policy doc; reviewers reject breaking changes that
  do not also ship a major-version bump + SUPERSEDES wiring.
- **Mechanical-discipline** (v1.x candidate): a pre-push gate that
  asserts the `IGOS_HELPER_LIB_API_VERSION` constant matches the
  declared major version + asserts each v1 API function is still
  present in the library. Not in v1.0 scope; tracked as a v1.x
  hardening item.

## References

- [`docs/architecture/helper-manifest-spec-v1.md`](helper-manifest-spec-v1.md) — manifest schema spec
- [`packages/core/intergenos-helper-lib/helper-lib.sh`](../../packages/core/intergenos-helper-lib/helper-lib.sh) — the library implementation
- [`pkm/installer.py`](../../pkm/installer.py) — the reader path (`HELPER_MANIFEST_DIR` + `_run_helper`)
- pkm RFC §11 — SUPERSEDES primitive specification
