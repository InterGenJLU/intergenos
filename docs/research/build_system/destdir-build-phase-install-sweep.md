# Build-phase DESTDIR-redirect class — full recipe sweep (findings)

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Status:** COMPLETE (read-only audit, findings only — no recipe changes).
**Date:** 2026-07-23. **Scope:** every `packages/*/*/build.sh` in the tree.

## The class

The staging `DESTDIR` exists for the **install** phase: an install run under it lands
in the per-package staging root, which the packager then seals into an archive. Any
installer that runs during **`configure`/`build`/`check`** while `DESTDIR` is set in the
environment is silently affected — an autotools/cmake `make install` prepends `DESTDIR`
to its prefix, so the files land under the staging root instead of where the build's own
later steps read them. The failure is **quiet** (rc=0, artifacts relocated) or **oblique**
(a follow-up copy or a build-system rule fails to find the just-"installed" file), never a
clear "wrong DESTDIR" error.

Four members of this class were found dynamically (by building), each fixed at the recipe
with a `DESTDIR`-clearing guard:

1. Python-wheel / cmake build backends invoked at build time (the on-file precedent).
2. A language build-runner's staged bootstrap-compiler install (`zig` stage3).
3. A compiler build system's bundled-library install (`ghc` / Hadrian in-tree `libffi`).
4. A bundled-fork cmake install inside a package's dependency makefiles (`julia` LLVM).

This sweep asks: statically, does any **other** recipe carry the same shape unguarded?

## The structural root — driver DESTDIR-scoping asymmetry

The two recipe drivers scope `DESTDIR` differently, and that asymmetry **is** the class:

- **Python recipe driver** (custom build style; tiers desktop/ai/extra/compute).
  `build_env` sets `DESTDIR=<staging>` **once per package** (`igos-build/builder.py`), and
  the same environment drives **every** phase — configure, build, check, and install. So a
  build-phase installer here is redirected unless the recipe clears `DESTDIR` itself.
  (Exception: `direct_install` packages, for which the driver pops `DESTDIR` entirely.)
- **Bash recipe driver** (tiers core/base). `DESTDIR` is exported **only inside the install
  runner** (`pkg_stage` in `scripts/pkg-functions.sh`), immediately before `do_install`, and
  is `unset` on every exit path from that runner. `configure`/`build`/`check` run before it
  and **never** see `DESTDIR`.

**Consequence:** the class is possible **only** for Python-driver tiers. Every core/base
build-phase install is safe by construction. Every per-recipe `env -u DESTDIR` / `unset
DESTDIR` guard in the tree exists to compensate for the Python driver's whole-run scope.

## Method

Phase-aware static parse of all `build.sh` recipes: each file is split into its top-level
function bodies; only `configure()`, `build()`, `check()` bodies are examined (`do_install`
/ `install` / `post_install` are `DESTDIR`'s legitimate home and are excluded). Within those
bodies, non-comment lines are matched against build-phase installer verbs — `make … install`,
`cmake --install` / `--target install`, `ninja`/`meson install`, `pip`/wheel builds,
`cargo`/`go install`, `zig build`, `cabal install`, `setup.py install`, coreutils
`install(1)` — plus any `DESTDIR` mention (guard or leak). A bare `./configure --prefix=…`
is **not** an install (the install lands in `do_install`) and is treated as context only.

1092 recipes scanned; 2270 in-scope function bodies; **29 candidate hit-lines across 20
packages** after excluding the configure-prefix noise.

## Findings

| Package | Tier | Phase:line | Invocation (excerpt) | Class | Reasoning |
|---|---|---|---|---|---|
| arrow-cpp | ai | configure:33, build:53 | `env -u DESTDIR cmake -S/-B …`, `env -u DESTDIR cmake --build` | GUARDED | configure + compile with `DESTDIR` cleared; install (if any) in `do_install`. |
| bitsandbytes | ai | build:35 | `env -u DESTDIR pip3 wheel …` | GUARDED | wheel build, `DESTDIR` cleared. |
| pyarrow | ai | build:28 | `env -u DESTDIR pip3 wheel "$PWD"` | GUARDED | wheel build, `DESTDIR` cleared. |
| pytorch | ai | build:73 | `env -u DESTDIR pip3 wheel "$PWD"` | GUARDED | wheel build, `DESTDIR` cleared. |
| sentencepiece | ai | build:33 | `env -u DESTDIR pip3 wheel "$PWD"` | GUARDED | wheel build, `DESTDIR` cleared. |
| triton | ai | build:75 | `env -u DESTDIR pip3 wheel "$PWD"` | GUARDED | wheel build, `DESTDIR` cleared. |
| rocblas | compute | configure:70-71 | `sed …/grep …` on `cmake/virtualenv.cmake` | BENIGN | recipe string-edit of a cmake file's pip line (adds `--no-index`); not an install invocation. |
| rocprofiler-sdk | compute | build:112 | `env -u DESTDIR cmake --build` | GUARDED | compile, `DESTDIR` cleared. |
| cryptsetup-static | core | build:111,128,203 | `make install` → `${STAGING_DIR}` | BENIGN | bash-tier: no `DESTDIR` in `build()`; installs to the recipe's private scratch prefix (`$PWD/../staging-static`). |
| fido2-tools-static | core | build:110 | `make install` → `${STAGING_DIR}` | BENIGN | bash-tier; private scratch prefix. |
| tpm2-tools-static | core | build:122,171 | `make install` → `${STAGING_DIR}` | BENIGN | bash-tier; private scratch prefix. |
| gptfdisk | core | configure:23-25 | `install … $(DESTDIR)/usr/sbin` | BENIGN | heredoc **text** appended to the upstream Makefile (a make `install:` target using make's `$(DESTDIR)`); the real `make install` runs in `do_install`. bash-tier. |
| fftw | desktop | build:57 | `make DESTDIR="$DESTDIR" install` | BENIGN | explicitly installs **to** the staging `DESTDIR` by design (multi-precision passes stage-install into the real staging root); uses, not redirects. |
| sassc | desktop | configure:18 | `env -u DESTDIR make install` | GUARDED | intentional live-fs install of the bundled `libsass`; `DESTDIR` cleared so it lands on `/`, not staging. |
| ghc | extra | configure:104 | `env -u DESTDIR make install` (seed) | GUARDED | bootstrap-seed install to a scratch prefix; `DESTDIR` cleared (documented in-recipe). |
| influxdb | extra | configure:114 | `install -m644 … vendor/wit/world.wit` | BENIGN | `install(1)` to a **relative** in-tree path; `DESTDIR`-irrelevant. |
| julia | extra | build:78 | `env -u DESTDIR make` | GUARDED | `make` recurses into `deps/*.mk` which cmake-installs bundled forks; `DESTDIR` cleared. |
| libreoffice | extra | build:113; configure:27 | `unset DESTDIR` (build); `install -dm755 external/tarballs` (configure) | GUARDED (build) / BENIGN (configure) | build unsets `DESTDIR` (its `make build` runs `install-gdb-printers`); configure line is a relative `mkdir`. |
| mariadb | extra | configure:215-216 | `install -d build/…`, `install -m644 fmt.zip build/…` | BENIGN | **relative** in-tree staging of a vendored source. |
| zig | extra | build:40 | `env -u DESTDIR make` | GUARDED | `make` wraps `zig build`'s stage3 install; `DESTDIR` cleared. |

## Totals

| Class | Hit-lines | Note |
|---|---:|---|
| GUARDED | 13 | build-phase installer with `DESTDIR` cleared (`env -u DESTDIR` / `unset DESTDIR`). |
| BENIGN | 16 | bash-tier (no build-phase `DESTDIR`), relative/scratch-path install, explicit-`$DESTDIR` by design, or a non-invocation string match. |
| **VULNERABLE** | **0** | no unguarded build-phase install that would honor an inherited staging `DESTDIR`. |

All four known members are present and confirmed **guarded**; no fifth static-visible member
was found.

## Completeness critique — what this finder cannot catch

Static line-grep matches **explicit** install verbs. It structurally **cannot** catch the
dominant real-world shape of this class:

1. **Bare build-runner with an internal install.** `make`, `zig build`, `ninja`,
   `cargo build`, `python -m build` whose invoked target performs an install internally
   carry **no** `install` token on the invocation line. Two of the four confirmed members
   (`zig` stage3, `julia` bundled-LLVM) are exactly this shape — this sweep "sees" them only
   because they now carry a `DESTDIR`-clearing guard that the `DESTDIR`-mention pattern
   flags. **A new, unguarded package of this shape is invisible to static analysis** and is
   found only by building it.
2. **Recursion into sub-makefiles / vendored forks.** The install can live in a build
   system's own recipes (`deps/*.mk`, a bundled fork's `CMakeLists`), which a grep of the
   top-level `build.sh` cannot follow.
3. **Ecosystems outside the seed.** The verb list was fit to this corpus. A future package
   using `npm`/`yarn`, `gem`, `opam`, `stack install`, `R CMD INSTALL`, `dotnet`, etc. at
   build time would not match until the seed is extended.
4. **Adjacent class — `install(1)` to an absolute system path.** `install -m … /usr/…` in a
   build phase escapes staging entirely (coreutils `install(1)` ignores `DESTDIR`). None
   were found targeting absolute system paths, but this is a different failure than the
   redirect class and warrants its own check if the corpus grows.

**Bottom line:** a per-recipe static sweep can confirm the *known* members are guarded and
can catch *explicit* build-phase `make install`, but it cannot make this class safe — the
vulnerable shape is frequently a bare build-runner whose install is invisible without
running the build.

## Recommendation (input to the builder-scoping design — no fix in this audit)

The durable fix is at the **builder**, not per-recipe: make the Python recipe driver match
the bash driver and scope `DESTDIR` to the **install phase only** (leave it unset during
configure/build/check, exactly as `pkg_stage` already does for core/base). That single
change makes the class **impossible** for Python-tier packages by construction, lets the 13
existing per-recipe guards degrade to harmless belt-and-suspenders, and removes the need for
a per-recipe sweep at all. Verification for that change: the guarded recipes still build
(guards become no-ops), and the four known members build with their guards removed. It is a
single environment-construction site in `igos-build/builder.py`.
