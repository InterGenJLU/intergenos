# preflight-undeclared-deps (Scan A.2) — design + operator runbook

**Status:** v1 implementation, gated into `phase_validate`
**Authored:** 2026-05-12
**Companion:** [preflight_scanners_v1.md](preflight_scanners_v1.md) covers Scan A + Scan B

## Why this gate exists

Two halts in 24 hours from the same class of bug motivated Scan A.2:

| Date | Package | Halt class | Pattern in upstream |
|---|---|---|---|
| 2026-05-11 | linux-pam 1.7.2 | meson find_program required | `find_program('xmllint', required: feature_docs)` (with feature_docs enabled by docs subdir's deps) |
| 2026-05-12 | rpm 4.18.2 | PKG_CHECK_MODULES required | `PKG_CHECK_MODULES([LUA], [lua >= 5.2], [], [AC_MSG_ERROR(...)])` |

Both halts had the same shape: upstream's build system hard-requires a
dependency at configure time, but our package.yml's `dependencies.build`
didn't declare it. Scan A (build-order) couldn't catch them because Scan
A only validates DECLARED deps' ordering — it has no view into what the
upstream's configure script actually checks for.

Scan A.2 closes that gap by parsing the upstream source tarball's build-
system files (configure.ac, meson.build, CMakeLists.txt) and detecting
the dep-discovery calls, then cross-referencing them against the declared
deps.

## What gets detected

Five upstream-build-system patterns:

| Pattern | Build system | Required when | HARD/SOFT |
|---|---|---|---|
| `PKG_CHECK_MODULES([NAME], [pkg-spec])` | autotools/pkg-config | 2-arg form OR 4-arg with empty/AC_MSG_ERROR not-found | HARD if required |
| `AC_CHECK_LIB(libname, fn, ...)` | autotools raw lib | 4-arg with AC_MSG_ERROR not-found | HARD if required (v1: classified but reported INFO, not gate-blocking — see Limitations) |
| `AC_CHECK_HEADERS([h.h], ...)` | autotools raw header | 3-arg with AC_MSG_ERROR not-found | HARD if required (v1: reported INFO) |
| `dependency('name', ...)` | meson | `required:` absent OR literal `true` | HARD if required |
| `find_program('name', ...)` | meson | `required:` absent OR literal `true` | HARD if required |
| `find_package(NAME REQUIRED ...)` | cmake | `REQUIRED` keyword present | HARD if required |

Soft variants exist for all patterns — operator-supplied fallback path
detected via the not-found-action arg's content (autotools) or non-literal
`required:` values like `required: feature_foo` (meson, treated as
conditional → SOFT in v1).

## v1 architecture

```
scripts/preflight-undeclared-deps.py
   |
   +-> discover_repo_root(arg/env/autodetect)
   +-> for each package.yml in tree:
   |     +-> parse_pkg_yml() — stdlib YAML (handles indented + flush-left list styles)
   |     +-> find_source_tarball() — match URL filename, generic patterns
   |     +-> ensure_extracted() — selective: only configure.ac, meson.build,
   |     |     CMakeLists.txt, meson_options.txt, meson.options
   |     |     Cache: build/scan-cache/<pkg>/, keyed by tarball sha256
   |     +-> parse_pkg_check_modules() etc — m4 bracket-aware arg splitter
   |     +-> resolve_pkg_config_name() — alias lookup + heuristic transforms
   |     +-> emit findings
   |
   +-> emit summary (terse default, --verbose includes INFO + SOFT)
   +-> exit 0 if no HARD findings, 1 otherwise
```

The selective-extraction trick is critical: a full-tarball extraction
of all 721 source tarballs would consume 50-100GB of disk + 90+ min of
runtime. Selective extraction pulls only the 6 file basenames we parse
(configure.ac, meson.build, CMakeLists.txt, meson_options.txt,
meson.options, configure.in) — typical per-package cache footprint is
<100KB and extract time is <2s/package.

## Configuration

| Knob | Effect |
|---|---|
| `--root <path>` / `INTERGENOS_ROOT` | Override repo autodetection |
| `--sources <path>` / `INTERGENOS_SOURCES_DIR` | Override sources/ dir (default `<repo>/build/sources`) |
| `--cache <path>` / `INTERGENOS_SCAN_CACHE` | Override scan cache dir (default `<repo>/build/scan-cache`) |
| `--report` | Emit JSON + TSV artifacts to `<repo>/build/` |
| `--verbose` | Show all findings (incl. INFO + SOFT) |
| `--rebuild-cache` | Force re-extract all source tarballs |
| `--only pkg [pkg ...]` | Restrict scan to listed packages (useful for triage) |
| `--progress` | Print progress every 25 packages to stderr |

## Aliases (scripts/pkg-aliases.json)

Maps upstream-build-system dep names to our package.yml names. Four
tables:

| Table | Maps from | Used for |
|---|---|---|
| `pkg-config` | pkg-config names like `glib-2.0` | PKG_CHECK_MODULES, meson `dependency()`, cmake `find_package` |
| `header` | header paths like `lua.h`, `openssl/ssl.h` | AC_CHECK_HEADERS |
| `library` | library base names like `z`, `crypto` | AC_CHECK_LIB |
| `program` | executable names like `xmllint` | meson `find_program()` |

Bootstrap conventions in current table:

- `glib-2.0`, `gobject-2.0`, `gio-2.0` etc. → `glib2` (we ship one glib2 package)
- `gtk+-3.0` → `gtk3` (per package-naming convention for coexisting versions)
- `libssl`, `libcrypto`, `openssl/*.h`, `ssl` lib, `crypto` lib → `openssl`
- `xmllint` program → `libxml2` (libxml2 ships the binary)
- `xsltproc` program → `libxslt`
- glibc-provided libs (`z`, `pthread`, `rt`, `m`, `c`, `dl`, etc.) → `glibc` (in ch8; consumers don't need to declare)

Heuristic transforms applied before alias lookup:

1. Strip `-N.M` version suffix (`glib-2.0` → `glib`)
2. Replace `+` with `` (`gtk+` → `gtk`)
3. Strip leading `lib` for AC_CHECK_LIB (`libcurl` → `curl`)

When the scanner finds a name it can't resolve, it emits an
`UNRESOLVED-PKG-NAME` INFO finding. Operator adds an entry to
`pkg-aliases.json` and re-runs.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | No HARD findings — build kickoff can proceed |
| 1 | HARD findings present — build kickoff should halt |
| 2 | Environment problem (repo dir not found, etc.) |

HARD-class findings (gate-blocking in v1):

- `UNDECLARED-PKG-CONFIG-REQUIRED`
- `UNDECLARED-MESON-DEP-REQUIRED`
- `UNDECLARED-MESON-PROGRAM`
- `UNDECLARED-CMAKE-DEP-REQUIRED`

INFO-class findings (visible, non-blocking in v1):

- `UNDECLARED-LIB-REQUIRED` / `UNDECLARED-LIB-SOFT`
- `UNDECLARED-HEADER-REQUIRED` / `UNDECLARED-HEADER-SOFT`
- `UNDECLARED-PKG-CONFIG-SOFT`
- `UNDECLARED-MESON-DEP-SOFT`
- `UNRESOLVED-PKG-NAME`
- `SOURCE-NOT-FOUND` / `SOURCE-EXTRACT-FAILED`

## Limitations (known + intentional in v1)

### 1. AC_CHECK_LIB/HEADERS classified but not gate-blocking

These default to "set HAVE_LIBxxx; downstream code uses `#ifdef
HAVE_LIBxxx` to gate features." The scanner correctly detects when 4th
arg explicitly errors (AC_MSG_ERROR), but in practice rpm-class projects
often wrap these in `AS_IF([test "$with_X" = yes], [AC_CHECK_LIB([X], ...,
[AC_MSG_ERROR(...)])])` — the check only fires if the operator passed
`--with-X=yes`. Without parsing the surrounding `AS_IF` context, the
scanner can't tell which checks are flag-gated. Empirically the false-
positive rate is high enough that gate-blocking on AC_CHECK_LIB findings
would block clean kickoffs. v2 plan: parse `AS_IF` + cross-reference
with operator-supplied `--with-X`/`--without-X` flags in build.sh.

### 2. Meson feature-conditional `required:` treated as SOFT

When `dependency('foo', required: feature_foo)` appears, the scanner
classifies SOFT because the feature's enabled-by-default state requires
parsing meson_options.txt + tracking which features our build flags
enable. Misses cases where a feature is enabled by default and the
dependency is actually required at configure time. v2 plan: parse
meson_options.txt for feature defaults + correlate with our build.sh
meson args.

### 3. AS_IF / autoconf m4-conditional context not tracked

Same fundamental limitation as (1) — we treat each macro call in
isolation. v2 plan: full m4 conditional tracking.

### 4. Header-name + library-name aliases are sparse by design

Alias tables seed common cases (openssl, glibc-provided libs, lua, zlib).
Operator-driven expansion via UNRESOLVED-PKG-NAME findings is the
mechanism for closing the gap iteratively.

### 5. Cache invalidation by tarball sha256

The scan-cache stores a `.sha256` stamp per package. Re-runs with
matching sha256 skip re-extraction. `--rebuild-cache` forces wipe + re-
extract. Currently no automatic detection of "alias-table changed →
re-resolve" — alias edits require re-run from cache (fast) but the
extracted files don't change.

## Operator runbook

### When a HARD finding fires

1. Confirm the dep is real: read the source line cited in the finding
   (`<file>:<line>` in the report). Look at the surrounding context. Is
   it a configure-time hard-require or an AS_IF-conditional that our
   build.sh disables?
2. If real: add the resolved dep to the consumer's `package.yml`
   `dependencies.build` list.
3. If our build flags disable it (false positive): add to per-package
   suppression (TBD — for v1, add `# scanner: <dep> is conditional on
   --with-X which we pass --without-X` comment in build.sh; v2 will add
   formal allowlist file).
4. Re-run scan to confirm clean.

### When a SOFT or UNRESOLVED finding fires

1. SOFT: signal that the upstream supports the dep but our build doesn't
   declare it. Often fine (feature gracefully degrades). Worth a check —
   if we WANT the feature, declare the dep.
2. UNRESOLVED: scanner couldn't map the upstream name to our packages.
   Either (a) the dep IS in our tree under a different name → add an
   alias to `scripts/pkg-aliases.json`, or (b) the dep isn't in our tree
   → confirm it's truly absent and the upstream check should fail
   gracefully via SOFT mechanism, or (c) we want to add the dep to tree
   as a new package.

### When SOURCE-NOT-FOUND fires

Means `find_source_tarball()` couldn't locate the package's source[0]
tarball in `build/sources/`. Causes:

- Tarball not yet downloaded (run `scripts/download-sources.py
  --tier <tier> --only <pkg>`)
- Local filename doesn't match URL-derived filename (add `filename:`
  field to source[0] in package.yml, or improve scanner's filename
  resolution)
- Substitution variables (${version_major_minor}, custom) not handled

## Tests

`tests/preflight/test_preflight_undeclared_deps.py` — 46 unit tests
covering:

- All 5 dep-discovery pattern parsers (PKG_CHECK_MODULES, AC_CHECK_LIB,
  AC_CHECK_HEADERS, meson dependency/find_program, cmake find_package)
- HARD/SOFT classification logic for each
- m4 bracket-aware argument splitter (handles `[a, b]` not splitting at
  the inner comma)
- Parens-balanced macro call finder (skips substring matches in other
  macro names)
- Alias resolution (direct + heuristic transforms)
- package.yml parser handling both indented and flush-left list styles
  (Mako regression case)
- HARD_TYPES gate-blocking constant (exit-code policy)

## Future work

| Item | Why |
|---|---|
| v2: parse `AS_IF` context + correlate with build.sh flags | Closes the AC_CHECK_LIB false-positive gap; lets us escalate autotools-lib/header findings back to HARD |
| v2: parse meson_options.txt + correlate with meson args | Closes the feature-conditional gap |
| v2: per-package allowlist file | Formal mechanism for known-OK findings |
| v2: tarball-filename resolver via `download-sources.py` integration | Closes SOURCE-NOT-FOUND for packages whose downloader renames the artifact |
| v3: outright Python AST eval of meson.build | More accurate parsing than regex; handles dynamic name construction |
| v3: rust Cargo.toml + Go go.mod parsing | Extends coverage to non-autotools/meson/cmake build systems |

## Provenance

- 2026-05-11: Build #8 → #9 remediation arc surfaces linux-pam meson
  xmllint halt class (closed by ratifying docbook deps).
- 2026-05-12 06:04Z: Build #9 halts at rpm 4.18.2 PKG_CHECK_MODULES
  lua hard-require. Owner ratifies "scan-the-class" rather than
  one-off fix.
- 2026-05-12 11:00Z: owner approves v1 (all 5 patterns; no v1/v2 split)
  + parser fix + alias expansion + finding triage as a single block of
  work before Build #9 resume #3.
- 2026-05-12 11:30Z: parser flush-left list-item bug found + fixed
  (closes 87% SOURCE-NOT-FOUND coverage gap).
- 2026-05-12: this doc + scanner + tests landed in master.
