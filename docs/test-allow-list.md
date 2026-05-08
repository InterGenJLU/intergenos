# Test Allow-List Specification

**Status:** v1 — adopted 2026-05-08 after Build #5 audit found 55 packages
silently swallowing test failures via `ninja test || true` patterns.

## The problem

Project rule: **tests are truth**. If a test fails, the code is suspect.

Build #5 audit found:
- gtk4 reported `[CHECK] OK (128.3s)` while 5,438 of 5,528 tests failed
- 55 of 107 packages with check() functions used `ninja test || true`
- Bash tier orchestrators (chroot-build-core-extra.sh, chroot-build-base.sh)
  ran `check >> $pkg_log 2>&1` with no exit-code check
- Result: signing-quality regressions could hide indefinitely

## Design goals

1. **Strict by default.** No package may silently swallow failures.
2. **Curated exemptions** for known-environment-only failures (no display in
   chroot, no network, hardware-required tests). BLFS-style — package-by-package.
3. **Owner reviewable.** Each exemption is annotated with a reason, visible
   in the package.yml that already controls everything else about the package.
4. **Catch regressions.** When a package with known-failures suddenly fails
   *more* tests than expected, halt — something new broke.

## Schema

A new top-level `tests:` key in each `package.yml`:

```yaml
tests:
  enabled: true                         # default true. false = skip tests entirely
  failure_policy: strict                # default. Halt on any non-zero exit from test suite.
  # OR
  failure_policy: known_failures        # opt-in. Allow non-zero exit if reason given.
  reason: "no display in chroot — gtk4 5,438/5,528 fail; verified all are display-init"
```

### Field reference

| field | type | default | meaning |
|---|---|---|---|
| `enabled` | bool | `true` | If `false`, the test phase is skipped entirely. Use for packages where tests are nonsensical (e.g., bootstrap toolchain). Must include `reason`. |
| `failure_policy` | string | `strict` | `strict` = any test failure halts. `known_failures` = log warning, continue. |
| `reason` | string | (required if `enabled: false` or `failure_policy: known_failures`) | Human explanation. Owner reviews on every package change. |

### Default behavior (no `tests:` key)

A package without a `tests:` block is treated as `enabled: true, failure_policy: strict`.
This means **simply omitting the block is the safer choice** — the build halts
on a test failure, which is what we want for new packages.

## Helper API

`pkg_run_tests` lives in `pkg-functions.sh` and is the only sanctioned way to
run a test suite from a `check()` function:

```bash
check() {
    set -e
    cd build
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ninja test
}
```

`pkg_run_tests` reads the `tests:` block from the given package.yml, runs the
remaining args as the test command, captures the exit code, and decides
whether to return success or failure based on policy.

`|| true` is forbidden after a test command. Lint check enforced by
`scripts/pre-clear-check.sh`.

## Bash tier orchestrators

`chroot-build-core-extra.sh` and `chroot-build-base.sh` are updated to:
- Capture `check()` exit code
- If non-zero, halt the tier build with the standard FAILED-package error block

This matches the existing behavior for configure/build/install phases.

## Python builder

`igos-build/builder.py` already enforces strict (any phase command exit != 0
halts the build). When a build.sh defines its own `check()`, the custom-style
adapter runs the function directly — so the policy in `pkg_run_tests` controls
the exit code that the Python builder sees. No changes needed in builder.py.

For meson/cmake/autotools auto-styles, the package has no build.sh and runs
the framework's default test command (`ninja -C build test`, `make check`,
etc.). Those default to strict. To allow known failures in an auto-style
package, the package.yml gets a `tests:` block AND the framework's default
check phase is replaced with one that calls `pkg_run_tests`. Implementation:
the style emits `pkg_run_tests $PACKAGE_YML <default-cmd>` instead of the
bare command.

## Migration plan (Batch 4)

55 packages currently use `ninja test || true` (or equivalent). Each gets:

1. A YAML block in package.yml with `failure_policy: known_failures` + reason.
2. The build.sh `check()` rewritten to call `pkg_run_tests`.
3. Owner reviews the reason field at PR time.

Acceptable reasons (with examples):
- `"no display in chroot — gtk4/gtk3/wxWidgets/etc."`
- `"no network in chroot — curl/wget test suites"`
- `"requires hardware — libgphoto2 camera detection"`
- `"BLFS known-failing tests in this version: <list>"`

Unacceptable:
- `"tests are flaky"` — fix or skip individually
- `"saves time"` — never
- (no reason at all) — pre-clear lint rejects
