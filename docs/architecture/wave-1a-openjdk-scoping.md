# Wave 1a — OpenJDK Scoping

**Status:** Scoping doc — owner-decision input for the Wave 1a OpenJDK strategy
called out as open question §7 Q1 in [`docs/architecture/database-landing-plan.md`](database-landing-plan.md).
**Created:** 2026-05-12

This document scopes what landing OpenJDK into the InterGenOS package tree
actually entails: tier placement, version strategy (17 vs 21 vs both),
bootstrap pattern, source provenance, build complexity, downstream
dependency cascade, default hardening posture for Java daemons, and what
changes about the tree if we land it. It exists to give the owner the
data needed to make the §7 Q1 strategic call before the Wave 1a package
work begins.

The doc is **research and scoping only** — no package directories are
created and no scripts are modified in the commit landing this doc.

---

## 1. TL;DR and recommendation

**Tier:** confirm `extra/` per [`database-landing-plan.md`](database-landing-plan.md)
§2 "Tier placement (prior art)". OpenJDK has no `core/` or `desktop/`
consumer at v1.0 — its only in-tree downstream is Cassandra, which
lives in `extra/`. Every major from-source distribution that uses
tier splits (Arch, Alpine, Debian's optional priority, Gentoo,
Void) places JDKs in the broader/secondary tier, not the build-time
toolchain tier. No push-back on the tier call.

**Version strategy:** **Option (c) parallel packages — `openjdk17`
plus `openjdk21`** — matching landing plan §7 Q1's recommendation
and the existing `desktop/spidermonkey` (SpiderMonkey 140) plus
`extra/mozjs128` parallel-package pattern in §2 of the same plan.
Reasoning:

- **Cassandra 5.0 requires JDK 17 specifically** (its
  `build.xml` and runtime-version checks reject 21). Without
  `openjdk17`, the v1.0 wide-column-NoSQL slot stays empty.
- **JDK 21 is the current LTS** (released September 2023,
  Oracle/community LTS through 2031) and what an end user calling
  `pkm install openjdk` expects to receive. Shipping only 17 leaves
  the user on a 2021-era runtime; shipping only 21 leaves Cassandra
  unbuildable.
- **The cost surface is real but not architecture-breaking.**
  ~3.8 SBU per JDK (per landing plan §5d build cost table), so
  ~7.6 SBU for both vs ~3.8 SBU for one. The project's
  comprehensive-coverage-at-v1.0 posture treats maintenance-burden
  as an absorbed cost when the wow-factor weight is real (see
  [`docs/architecture/database-landing-plan.md`](database-landing-plan.md)
  §0 for the underlying reasoning). Build-time wall-clock is the
  only real delta, absorbed by the existing parallel-build
  orchestrator.
- **The pattern is already established in tree.** Parallel
  language-runtime versions co-exist cleanly when the version is
  baked into the package name (see `desktop/spidermonkey` and the
  planned `extra/mozjs128`). The same applies for `extra/openjdk17`
  and `extra/openjdk21`.

**Recommended bootstrap:** Eclipse Temurin pre-built binaries from the
Adoptium project's GitHub releases. Reproducibility-tracked,
permissively licensed, signed releases, GitHub-source-pinned by sha256.
Same provenance discipline as our existing Rust bootstrap
(pre-built rustc/cargo binaries pinned by sha256 in `packages/core/rust/package.yml`).

**Recommended source:** OpenJDK upstream from `github.com/openjdk/jdk17u`
(GA + updates branch) and `github.com/openjdk/jdk21u`. Pinned by
release-tag sha256, verified against the openjdk.org public-key
signature on the tag.

If the owner wants to deviate from the recommended (c) parallel
posture, the lowest-cost alternative is **(a) ship only `openjdk17`
for v1.0 + defer `openjdk21` to v1.1** — saves ~3.8 SBU and one
package's worth of tree weight at the cost of leaving end-user
Java workloads on a 2021-era runtime.

---

## 2. Version strategy options

Restating the three options from landing plan §7 Q1 with cost and
wow-factor analysis.

### Option (a) — Ship only `openjdk17` for v1.0, defer 21 to v1.1+

**Effect on v1.0:**
- Cassandra unblocked (its sole consumer is fed)
- Users running `pkm install openjdk` get JDK 17 — a 2021-era runtime
- No tree-position for current-LTS JDK 21 until v1.1

**Cost:**
- ~3.8 SBU build time
- 1 toolchain-class package added to `extra/`
- 1 bootstrap-JDK fetch path (Adoptium 17.0.16 or similar) pinned

**Wow-factor angle:**
- Cassandra availability is the wow surface (database lineup remains complete)
- Java-development positioning is muted — current-LTS expectations
  unmet

**When this is right:** if v1.0 scope discipline overrides ecosystem
positioning and the project is willing to surface JDK 21 in a v1.1
release within ~3-6 months of v1.0.

### Option (b) — Ship only `openjdk21`, skip Cassandra in v1.0

**Effect on v1.0:**
- Wide-column-NoSQL slot empty (database lineup down to 13 from 14)
- Java users see current LTS
- No Java consumer in tree at v1.0

**Cost:**
- ~3.8 SBU build time
- 1 toolchain-class package added to `extra/`
- 1 bootstrap-JDK fetch path
- The Cassandra package's existing research investment is shelved

**Wow-factor angle:**
- A 13-database lineup with no wide-column option underdelivers
  on the "every major FOSS database category" wow surface
- "Java distro that ships JDK 21" is the wow signal — but no in-tree
  Java consumer to demonstrate it on

**When this is right:** essentially never for v1.0. Loses Cassandra
without a balancing gain. Listed for completeness only.

### Option (c) — Ship parallel `openjdk17` + `openjdk21`  ←  RECOMMENDED

**Effect on v1.0:**
- Cassandra unblocked (uses `openjdk17`)
- Users running `pkm install openjdk21` get the current LTS
- Both runtimes co-exist (no per-system conflict — they live at
  `/usr/lib/jvm/openjdk17` and `/usr/lib/jvm/openjdk21` with no
  `/usr/bin/java` default symlink at install time)

**Cost:**
- ~7.6 SBU build time (2x of single-version case)
- 2 toolchain-class packages added to `extra/`
- 2 bootstrap-JDK fetch paths (or 1 shared if both use Adoptium 17
  as bootstrap — JDK 21 can be bootstrapped from JDK 17, which is
  the upstream-documented path)
- Tree gains 2 new packages instead of 1

**Wow-factor angle:**
- Comprehensive Java coverage (one LTS supporting Cassandra,
  one current LTS for new Java workloads)
- Matches the parallel-mozjs / parallel-spidermonkey pattern
  already in the tree
- A casual observer running `pkm search openjdk` sees a Java-friendly
  distro, not a single-version corner

**When this is right:** v1.0 scope is "comprehensive ecosystem
coverage" per the database-landing-plan philosophy, and
maintenance-burden argument is ruled out per the wow-factor filter.

### Why (c) is the recommendation

Three factors push (c) over (a):

1. The landing plan philosophy ("comprehensive coverage is the bar
   at v1.0") generalizes from databases to language runtimes once
   the precedent of multiple runtimes is set.
2. The parallel-version pattern already lives in tree
   (`desktop/spidermonkey` + planned `extra/mozjs128`) — adding two
   JDKs is the same idiom, not a new architectural pattern.
3. The cost differential (~3.8 SBU additional build time for one extra
   toolchain) is small relative to the v1.0 build cycle as a whole.

The dispatch's "push back if you see a problem" prompt: the only
genuine push-back I see is that 21 has no in-tree Java consumer at
v1.0. That makes 21 a *user-facing toolchain* rather than a
*build-time-required* dep, which is a slightly weaker position. If
the owner wants to apply a strict "must have an in-tree consumer"
rule, the answer flips to (a) and 21 lands when the first v1.x Java
consumer surfaces. I do not recommend that, but it is a defensible
posture.

---

## 3. Bootstrap path

OpenJDK self-hosts — building OpenJDK requires an already-installed
JDK to compile the Java sources. The bootstrap JDK is the chicken-and-egg
input.

### Recommended: Eclipse Temurin (Adoptium project)

**Source URL pattern:**
`https://github.com/adoptium/temurin17-binaries/releases/download/jdk-<version>/OpenJDK17U-jdk_x64_linux_hotspot_<version>.tar.gz`

**Provenance:**
- Reproducible-build-tracked (Adoptium publishes SBOMs + signed
  release attestations)
- Eclipse Foundation governance (well-audited, permissively licensed)
- GitHub-hosted release artifacts — same retrieval discipline as our
  Rust bootstrap
- Sha256 of each release is published in the GitHub release notes;
  we pin against the sha256 we observe at fetch time

**License posture:**
GPLv2-with-Classpath-Exception — same as the OpenJDK source itself.
Acceptable for a distro-bootstrap input (we redistribute, but the
GPLv2 + Classpath-Exception is functionally permissive for
applications that link against the runtime).

### Why not Oracle JDK binaries

Oracle's distribution license requires acceptance per-user and
prohibits unrestricted redistribution. Off the table.

### Why not "use the system JDK"

We do not have one. Until OpenJDK 17 lands, the build VM has no
JDK at all.

### Bootstrap chain through 17 to 21

A clean approach for option (c) parallel packages:
- `openjdk17` builds from upstream source using Adoptium Temurin 17
  binary as bootstrap
- `openjdk21` builds from upstream source using **our just-built
  openjdk17** as bootstrap (upstream supports this — JDK N-1 can
  bootstrap JDK N for adjacent LTS releases)

This compresses to a single external bootstrap-binary fetch
(Adoptium Temurin 17), with `openjdk21` deriving its bootstrap from
`openjdk17`'s build output. Cleaner provenance chain.

If for any reason JDK 17 bootstrapping JDK 21 turns out to be
difficult in practice (sometimes upstream's preferred bootstrap is
N rather than N-1), the fallback is a parallel Adoptium Temurin 21
bootstrap fetch — same provenance pattern, second URL.

### Security-only-alignment considerations

The bootstrap JDK is an external pre-built binary entering the build
chain. Two mitigations:

1. **Sha256 pinning** — the Adoptium release sha256 is locked in
   `package.yml`. Any drift fails the build.
2. **Build-from-source on first re-bootstrap** — once `openjdk17` is
   in tree, subsequent builds can bootstrap-from-source by chaining
   through prior `openjdk17` builds. This reduces our dependency on
   the external pre-built binary to a one-time provenance event.

This is the same posture our Rust toolchain takes
(`packages/core/rust/package.yml` pins a pre-built rustc 1.94.0 +
cargo 1.94.0 + rust-std 1.94.0 by sha256). The pattern is established.

---

## 4. Source provenance

### Upstream

- `openjdk17`: `github.com/openjdk/jdk17u` (the GA + updates branch
  for JDK 17 LTS). Current head as of 2026-05-12 is `jdk-17.0.16-ga`
  or later — pin to the tagged release at landing time.
- `openjdk21`: `github.com/openjdk/jdk21u` (the GA + updates branch).
  Current head as of 2026-05-12 is `jdk-21.0.4-ga` or later — pin
  similarly.

### Verification

- Each upstream release ships a GPG-signed git tag. Verify the tag
  signature against the openjdk.org-published signing key fingerprint
  at fetch time.
- Pin the resulting GitHub tarball sha256 in `package.yml`.

This is one verification layer more than our Rust toolchain currently
applies (Rust pins only the binary tarball sha256; OpenJDK adds a
GPG-signed-tag verification before the sha256 pin). The additional
verification is reasonable because the OpenJDK upstream signs tags
formally and we benefit from anchoring against the canonical key.

### Cargo-vendor-equivalent for Java

The Java ecosystem's analog of cargo-vendor or Go vendor is the
**Maven local repository** plus **Ant-resolved JARs**. For OpenJDK
itself, the source tree is self-contained — it does not pull
external Java JARs at build time, so the cargo-vendor pattern
specifically does not apply. (Cassandra and Ant, when they land,
do consume Maven JARs — that's a separate Wave-2-and-later
consideration, not an OpenJDK consideration.)

The OpenJDK source build does fetch a few non-Java external
tarballs (FreeType source if `--with-freetype=bundled`, but we use
`--with-freetype=system`). These should be vendored or marked as
required system deps; flag during package landing.

---

## 5. Build complexity

### Configure pattern

```
bash ./configure                                           \
    --with-boot-jdk=<path to bootstrap JDK>                \
    --prefix=/usr                                          \
    --with-freetype=system                                 \
    --with-alsa=system                                     \
    --with-libjpeg=system                                  \
    --with-libpng=system                                   \
    --with-zlib=system                                     \
    --with-giflib=system                                   \
    --with-stdc++lib=dynamic                               \
    --with-extra-cflags="<distro CFLAGS>"                  \
    --with-extra-ldflags="<distro LDFLAGS>"                \
    --disable-warnings-as-errors                           \
    --with-version-build=<release> --with-version-pre=     \
    --with-version-opt="intergenos-${version}-${release}"
```

Followed by:

```
make -j${IGOS_JOBS} images
```

The `make images` target produces the install staging directory at
`build/<config-string>/images/jdk`. The `do_install` then copies this
tree to `${DESTDIR}/usr/lib/jvm/openjdk<major>/` and links
`/usr/bin/java<major>`, `/usr/bin/javac<major>`, etc. into PATH.

Per the parallel-version pattern (matching mozjs128 / spidermonkey-140),
we do **not** create a default `/usr/bin/java` symlink at install
time. Users select which JDK is active via `update-alternatives` or
the runtime's `JAVA_HOME` env var. This avoids version-conflicts
between the two packages and matches what Debian and Arch do for
multi-version JDK installs.

### SBU and wall-time

Per landing plan §5d:
- Toolchain-class package range: 30 min to 2 hours
- OpenJDK specifically: ~3.8 SBU each per the §2 Wave 1a table

On the InterGenOS build VM (16 vCPU, 32 GB RAM), expect ~30-60 min
each. With option (c) parallel packages: ~1-2 hours added to the
v1.0 build cycle, fully parallelizable with Wave 1b small libs.

### Two-stage internal compilation

OpenJDK's build internally does two-stage compilation: it first uses
the bootstrap JDK to compile an interim JDK from source, then uses
the interim JDK to compile the final JDK. This is upstream's standard
self-bootstrap pattern, not something the package recipe controls
or needs to special-case.

### Cross-compile

We do not cross-compile. The build VM is x86_64 Linux; we emit x86_64
Linux binaries. OpenJDK supports cross-compile for other targets, but
none of those targets are in scope for v1.0.

---

## 6. Dependency cascade

Dependencies expected for the OpenJDK build, cross-referenced against
the current package tree:

| Dep | Used for | In tree? | Tier |
|---|---|---|---|
| `freetype` | Font rendering (AWT, Swing) | yes | `core/` (verify) |
| `libpng` | Image library (AWT) | yes | `core/` (verify) |
| `libjpeg-turbo` | JPEG image library (AWT) | yes | `desktop/` |
| `giflib` | GIF image library (AWT) | likely | verify |
| `zlib` | Compression library | yes | `core/` |
| `alsa-lib` | Audio (Java Sound) | yes | `core/` (verify) |
| `cups` | Printing (PDL / AWT printing) | likely | verify |
| `libX11` | Display server protocol | yes | `desktop/` |
| `libXrender`, `libXtst`, `libXt`, `libXrandr` | X11 extensions | likely | `desktop/` |
| `fontconfig` | Font discovery | likely | `desktop/` |

No new system-level dep is expected. The X11 family lives in
`desktop/`; if `extra/openjdk17` builds against them, this is the
first cross-tier dep arrow from `extra/` into `desktop/`. The
build orchestrator's topological sort handles it, but it is worth
flagging to confirm the tier boundary is intentional and not
accidentally inverted.

Mitigation if cross-tier-dep is undesirable: pass
`--enable-headless-only` to the OpenJDK configure (drops X11 +
AWT/Swing dependencies entirely). For server workloads (Cassandra,
future server-only Java consumers), headless-only is sufficient.
Tradeoff: end users running `pkm install openjdk21` and expecting
to run desktop Java apps (e.g., IntelliJ IDEA bundled JDK or
JavaFX) lose that capability.

**Open question for owner on this specific point:** ship full JDK
(with AWT/Swing) or headless-only? Recommended posture: full JDK,
because the wow-factor for "Java distro" includes desktop Java app
support. Cross-tier dep is acceptable. If headless-only is preferred,
the `extra/openjdk17-headless` variant could be added as a separate
parallel package once a desktop-vs-headless tier-isolation requirement
surfaces.

---

## 7. AppArmor and systemd hardening for Java daemons

Java workloads have two characteristics that interact with our
default hardening posture from landing plan §5e:

1. **JIT compilation** — the JVM compiles bytecode to native at
   runtime and requires writable+executable memory pages. This
   means `MemoryDenyWriteExecute=true` cannot be applied to Java
   daemons without disabling the JIT (which destroys runtime
   performance). The same exception applies to MongoDB per §5c.
2. **Large memory footprint** — JVMs typically reserve large
   heap commitments at startup. `MemoryHigh` and `MemoryMax`
   tuning matters for any Java daemon, especially Cassandra which
   ships with multi-gigabyte default heap. The systemd unit for
   Cassandra should leave memory tuning to the operator (no
   default cap) but document the recommended tuning.

### Systemd unit baseline (Java daemon variant)

Apply landing plan §5e baseline with these adjustments:
- `MemoryDenyWriteExecute=false` (JIT)
- `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` (typical Java daemon)
- `SystemCallArchitectures=native`
- `SystemCallFilter=@system-service` (Java workloads need broad
  syscall surface for the GC + JIT + I/O paths)
- `NoNewPrivileges=true` (no setuid escalation from JVM)
- `ProtectSystem=strict` + per-package `ReadWritePaths=` (the JVM's
  state dir, the daemon's data dir, the log dir)
- `ProtectKernelTunables=true`, `ProtectKernelModules=true`,
  `ProtectKernelLogs=true`, `ProtectControlGroups=true`,
  `ProtectHostname=true`, `ProtectClock=true`,
  `ProtectProc=invisible` (all standard)
- `CapabilityBoundingSet=` and `AmbientCapabilities=` (drop all;
  Java runtime does not need any privileged capability)
- `PrivateTmp=true`, `PrivateDevices=true`

### AppArmor profile (Java daemon variant)

The AppArmor profile constrains:
- Read/write to the JVM's own state dir (`/var/lib/<daemon>/`,
  `/var/log/<daemon>/`)
- Exec of the JRE binary (`/usr/lib/jvm/openjdk<major>/bin/java`)
- The bootstrap class loader needs read access to the entire
  `/usr/lib/jvm/openjdk<major>/` tree
- Network bind: per-daemon (Cassandra inter-node + JMX +
  CQL ports)
- No write outside the daemon's allowlist
- No exec of arbitrary binaries (the JVM should not spawn `/bin/sh`
  except in carefully-scoped maintenance paths — Cassandra's
  drain/repair scripts may need this; verify per-daemon)

Profile ships in **enforce** mode (per landing plan §5f).

### Reproducibility note

Java builds embed timestamps in JAR files unless explicitly told
not to. The `--with-version-pre=` and `--with-version-opt=` flags
control the version string; the SOURCE_DATE_EPOCH env var controls
timestamps in compiled .class and .jar artifacts (Java 9+ honors
it). Pinning both at build time is required for byte-identical
rebuilds. Cross-link to the reproducible-builds design doc once it
lands.

---

## 8. Tree-position effect

| Aspect | Single JDK (option a or b) | Parallel JDKs (option c) |
|---|---|---|
| `packages/extra/` count delta | +1 | +2 |
| `packages/core/` change | none | none |
| New ecosystem-class toolchain count | +1 | +2 |
| Build-time delta (per-cycle, parallel) | ~30-60 min | ~30-60 min (parallel) |
| Build-time delta (serial) | ~30-60 min | ~1-2 hours |
| ISO size delta | 0 (extra/ packages not in ISO) | 0 |
| Mirror archive count delta | +1 | +2 |
| Mirror archive size delta | ~200-300 MB | ~400-600 MB |

The mirror size delta is the only meaningful resource concern, and
even at the high end (~600 MB for parallel packages) it sits below
the threshold where mirror hosting cost is a v1.0 concern.

The `core/` vs `extra/` boundary is unchanged. The new packages
live alongside the other Wave 1a items in `extra/` (matching
`erlang/OTP 28.5` and `mozjs128` per landing plan §2).

---

## 9. Open questions for owner

1. **Strategy: (a) / (b) / (c).** Recommendation: (c) parallel
   packages. The only push-back consideration is that `openjdk21`
   has no in-tree Java consumer at v1.0, making it a user-facing
   toolchain rather than a build-time-required dep. If the
   project's tier-placement rule is strict ("must have an in-tree
   consumer to ship"), the answer flips to (a). Otherwise (c).

2. **Bootstrap binary source.** Recommendation: Eclipse Temurin
   from the Adoptium project (`github.com/adoptium/temurin17-binaries`).
   Alternative: OpenJDK official build infrastructure binaries
   (also signed, also permissively licensed) at openjdk.org.
   Recommendation rests on Adoptium's reproducibility-build
   tracking and our existing pattern of pinning pre-built bootstrap
   binaries from a GitHub-hosted release artifact.

3. **Full JDK vs headless-only.** Recommendation: full JDK (with
   AWT/Swing, X11 dep arrow from `extra/` into `desktop/`).
   Headless-only is a defensible alternative that breaks the
   cross-tier dep but loses desktop-Java-app support for end users.

4. **JDK 21 bootstrap path.** Recommendation: bootstrap JDK 21 from
   the just-built `openjdk17` rather than fetching a second
   Adoptium pre-built binary. Compresses to a single external
   bootstrap fetch. Fallback to parallel Adoptium 21 fetch is fine
   if upstream's preferred bootstrap turns out to be JDK 21 itself.

5. **Cross-link timing.** Once the reproducible-builds design doc
   lands at `docs/architecture/reproducible-builds-design.md`
   (in flight separately), this scoping doc and the landing-plan
   §5e/§5f references should be updated to cross-link the
   Java-specific reproducibility notes in §7 above.

6. **Wave 1a sequencing.** Recommendation: land `openjdk17` first
   (unblocks Cassandra + provides the JDK 21 bootstrap chain),
   then `ant` (which depends on `openjdk17`), then `openjdk21`.
   Erlang/OTP and mozjs128 are independent and can build in
   parallel with the JDK chain at any point.

---

## References

- [`docs/architecture/database-landing-plan.md`](database-landing-plan.md)
  — the Wave 1 / Wave 2 plan this scoping fills in
- [`docs/architecture/per-archive-sig-decision.md`](per-archive-sig-decision.md)
  — repository trust model decisions
- [`docs/architecture/public-hosting-plan.md`](public-hosting-plan.md)
  — what the mirror hosts at v1.0
- [`docs/operational/first-publish-runbook.md`](../operational/first-publish-runbook.md)
  — operational publish procedure
- [`packages/core/rust/package.yml`](../../packages/core/rust/package.yml)
  — pre-built bootstrap-binary pinning precedent
- [`packages/core/go/package.yml`](../../packages/core/go/package.yml)
  — self-bootstrapping language toolchain precedent
- BLFS Java chapter (canonical from-source JDK reference): publishes
  OpenJDK 21 only; we deviate by also landing 17 for Cassandra
- Eclipse Adoptium project: `https://adoptium.net/` — Temurin
  release artifacts, reproducibility documentation, SBOM artifacts
- OpenJDK upstream: `https://github.com/openjdk/jdk17u` and
  `https://github.com/openjdk/jdk21u` — GA + updates branches
- Cassandra 5.0 documentation: confirms JDK 17 maximum supported
  version (build.xml + runtime version-check)
