# Deferred Build System Hardening — 2026-04-01

Tracked here so they don't become invisible technical debt.
These are real issues that don't need fixing *yet* because the current
workflow is single-builder with author-controlled templates. Revisit
before opening the build system to external contributors or automation.

## SHA256 case sensitivity
- **Location:** `builder.py:169`
- **Issue:** `sha256sum` output compared with `!=` — no `.lower()` normalization.
  If a hash is stored uppercase in a template and `sha256sum` returns lowercase
  (or vice versa), verification will false-positive fail.
- **When to fix:** Before accepting externally contributed templates.

## URL query parameter in filename extraction
- **Location:** `builder.py:144`
- **Issue:** `primary.url.split("/")[-1]` doesn't strip `?query=params`.
  Would break if a source URL ever includes query parameters.
- **When to fix:** If we ever use URLs with tokens or query strings (CDN, private repos).

## Shell injection via template values
- **Location:** `builder.py:113` (and anywhere `shell=True` is used)
- **Issue:** `configure_flags` and other template values are interpolated into
  shell command strings without escaping. A malicious template could inject
  arbitrary shell commands.
- **When to fix:** Before accepting community-contributed templates. Use `shlex.quote()`
  on all template-derived values before shell interpolation.

## Partial deployment recovery
- **Location:** `builder.py:331-349` (`pkg_deploy`)
- **Issue:** `cp -a --remove-destination` to `/` is not atomic. If it fails
  mid-copy, the system is left in an inconsistent state.
- **When to fix:** Before using `--tracked` mode for critical system packages
  in production. Consider a two-phase approach (stage + atomic swap) or at
  minimum a rollback mechanism using the archive.

## Dependency cycle reporting
- **Location:** `graph.py` cycle detection
- **Issue:** Only reports the first cycle found. Multiple cycles require
  multiple fix-and-retry iterations to discover.
- **When to fix:** When the dependency graph gets complex enough that multiple
  cycles are plausible (desktop tier, external packages).
