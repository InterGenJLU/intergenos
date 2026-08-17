# The `tests:` allow-list policy (package.yml)

**Spec for the optional `tests:` block in `package.yml` — the single governed
mechanism for anything other than "run the suite, halt on failure."**
Adopted 2026-05-08 after the Build #5 audit; this document was authored
2026-07-03 (GE-01) when the yml lane gained enforcement — both consumers
referenced this path before the file existed.

**Consumers (keep in lockstep):**
- **Bash tiers** (`core`/`base` + every custom `build.sh` `check()`):
  `pkg_run_tests` in `scripts/pkg-functions.sh`.
- **Pure-yml Python lane** (`desktop`/`extra`/`compute`/`ai` without `build.sh`):
  `igos-build/parser.py` (`_parse_tests`, fail-closed) +
  `igos-build/builder.py` (check-phase policy) + the make-driven styles
  (`autotools.py`/`make.py` honor `jobs`).

## The block

```yaml
tests:
  enabled: true                 # default; false = skip the check phase
  failure_policy: strict        # default | known_failures
  jobs: 1                       # optional; bound make parallelism in check
  reason: "..."                 # REQUIRED for enabled=false, known_failures,
                                #   and jobs — an unreasoned waiver refuses
                                #   the template (yml lane) / errors (bash)
```

## Semantics

- **No block → strict.** The suite runs; any failure is fatal on the yml
  lane. (The bash tier drivers treat check() as informational-NON-FATAL by
  their own documented contract — the helper reports, the caller decides.)
- **`failure_policy: known_failures`** — the suite RUNS and a failure is
  converted to a LOUD logged waiver (never a silent pass), with the reason
  printed. For failures that are **environmental** (root CAP_DAC_OVERRIDE,
  no TPM/FIDO2 hardware, no loop devices, locale/FHS) — the Rule-10 ladder's
  first step. Canonical: `flac` / `lib32-flac` (root bypasses the read-only
  mode-bit test; upstream's own message asks "are you running as root?").
- **`jobs: N`** — run the check phase's make with `-jN` (a command-line `-j`
  overrides env `MAKEFLAGS`). For suites that are **not parallel-safe** —
  the honest alternative to waiving a harness race with known_failures,
  which would mask the suite's real signal. Ground it in the book:
  canonical is `lib32-libvorbis` (BLFS 13.0's own command is
  `make -j1 check`). Policy stays strict — the serialized suite must pass.
- **`enabled: false`** — skip the phase entirely, loudly, with the reason.
  LAST resort (Rule 10): only when the test infrastructure itself is
  hazardous (leaves loop mounts, needs live container ops).

## The Rule-10 ladder (private build-development-rulebook.md)

1. Environmental failure → `known_failures` + reason (suite still runs).
2. Harness not parallel-safe → `jobs: 1` + reason (suite still enforced).
3. Hazardous infrastructure → `enabled: false` + reason.
4. NEVER: disable tests because they are flaky or to make a build green.
