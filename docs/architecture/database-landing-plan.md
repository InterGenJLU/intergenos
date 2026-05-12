# Database Landing Plan — v1.0

**Status:** TODO scaffold — v1.0-launch planning, stage-appropriate-depth
(architectural decisions + TODOs + cross-cutting observations; not
finished package code).
**Created:** 2026-05-12
**Owner:** InterGenJLU
**Source research:** ten parallel research subagent reports completed
2026-05-12 covering 14 database packages across 5 categories. Each
per-database report exists in the conversation log; this document
consolidates the cross-cutting decisions and the build-order plan.

---

## 0. The wow-factor reasoning

A from-source Linux distribution at v1.0 launch needs to demonstrate
that comprehensive coverage exists across the categories users expect.
"Casual observer runs `pkm search database` and sees one or two
packages" is project-existential failure. "Casual observer runs the
same search and sees relational + document + KV + cache + time-series
+ wide-column + embedded options, with both license-aligned defaults
and well-disclosed opt-ins for the SSPL/RSAL outliers" is the wow
surface that drives adoption.

For a from-source distribution, comprehensiveness across the standard
database categories is the bar at v1.0 — not a nice-to-have, not
deferrable, and not weighed against ongoing maintenance overhead.

All 14 packages target `packages/extra/` (one exception: sqlite is
already in `packages/core/` — proposed version bump in place). None
are installed in the desktop ISO by default. Every one ships as a
signed `.igos.tar.gz` on `repo.intergenos.org/x86_64/packages/` and is
installed by users via `pkm install <name>`.

---

## 1. Database matrix

| Package | Version | Category | License | Tier | New deps |
|---|---|---|---|---|---|
| sqlite (rebump) | 3.53.1 | Embedded SQL | Public Domain | core | — |
| postgresql | 18.3 | Relational SQL | PostgreSQL (BSD-style) | extra | — |
| mariadb | 11.8.6 | Relational SQL | GPL-2.0 + LGPL-2.1 | extra | — (optional krb5 to verify) |
| valkey | 8.1.3 | KV cache (in-mem) | BSD-3-Clause | extra | — |
| redis | 7.4.5 | KV cache (in-mem) | **RSALv2/SSPLv1** (non-OSI) | extra | — |
| memcached | 1.6.41 | KV cache (in-mem) | BSD-3-Clause | extra | — |
| leveldb | 1.23 | Embedded KV | BSD-3-Clause | extra | snappy |
| rocksdb | 11.1.1 | Embedded KV | GPL-2 OR Apache-2 | extra | snappy, gflags, jemalloc, liburing |
| mongodb | 8.0.4 | Document NoSQL | **SSPL-1.0** (non-OSI) | extra | scons, snappy |
| ferretdb | 2.1.0 | Document NoSQL (MongoDB wire) | Apache-2.0 | extra | postgresql-documentdb |
| etcd | 3.5.21 | Distributed KV | Apache-2.0 | extra | — (uses existing Go) |
| influxdb | 3.9.2 (Core) | Time-series | Apache-2.0 OR MIT | extra | protobuf |
| cassandra | 5.0.8 | Wide-column NoSQL | Apache-2.0 | extra | openjdk17, ant |
| couchdb | 3.5.1 | Document NoSQL | Apache-2.0 | extra | erlang/OTP 28.5, mozjs128 |

**Coverage:** every major FOSS database category is covered, plus the
two notable non-OSI server lines (MongoDB Community + Redis original)
shipped as license-disclosed opt-ins alongside their FOSS-clean
counterparts (FerretDB for MongoDB-wire-compat; Valkey for
Redis-wire-compat). A casual observer browsing the mirror sees the
full landscape.

---

## 2. New prerequisite packages — Wave 1

These must land before the database packages can build. Grouped by
size of the addition and by inter-dep.

### Wave 1a — new ecosystem-class toolchains

These are comparable in scope to having added Go or Rust to the tree.
Each unlocks a category of downstream packages we cannot otherwise
build from source.

| Package | Why | Approx size | Used by |
|---|---|---|---|
| `openjdk17` | Java 17 LTS (Cassandra 5.0 maxes at JDK 17; BLFS-tracked OpenJDK 21 is unsupported) | ~3.8 SBU; requires a bootstrap JDK following the BLFS pattern | cassandra; future Java packages |
| `erlang/OTP 28.5` | Erlang VM (CouchDB is Erlang) | ~85 MB source; autotools; new ecosystem | couchdb; future Erlang packages |
| `mozjs128` | SpiderMonkey 128 ESR — parallel package alongside our existing spidermonkey-140 (CouchDB 3.5.1 supports SpiderMonkey 1.8.5/60/68/78/91/102/115/**128**, not 140) | Same recipe pattern as the existing spidermonkey package, different version | couchdb |

### Wave 1b — small build tools / libraries (clean deps)

| Package | License | Why | Used by |
|---|---|---|---|
| `ant` | Apache-2.0 | Apache Ant 1.10.x build tool (Cassandra build system) | cassandra |
| `scons` | MIT | Python-based meta-build (MongoDB build system); pure Python, build-time-only | mongodb |
| `snappy` | BSD-3-Clause | Google's compression library; ~50 KB source | leveldb, rocksdb, mongodb |
| `gflags` | BSD-3-Clause | Google command-line flags library; required by RocksDB tools | rocksdb |
| `jemalloc` | BSD-2-Clause | High-perf allocator (RocksDB recommends; cross-cutting performance win) | rocksdb |
| `liburing` | MIT + LGPL-2.1 | Linux io_uring async I/O wrapper | rocksdb; cross-cutting use cases (see §5) |
| `protobuf` | BSD-3-Clause | Google Protocol Buffers compiler + libprotobuf | influxdb |
| `postgresql-documentdb` | Apache-2.0 | Microsoft-authored PostgreSQL extension implementing BSON storage; FerretDB v2 backend | ferretdb |

### Wave 1c — inter-dep ordering

- `ant` depends on `openjdk17` (it's a Java tool). openjdk17 must land first.
- `postgresql-documentdb` depends on the existing `postgresql` package — already in core.
- Everything else in Wave 1 is independent and can build in parallel.

---

## 3. Build order (topological)

```
Wave 1a (ecosystems, parallel):
    openjdk17 — — — — — ▶ ant
    erlang/OTP 28.5
    mozjs128

Wave 1b (small libs, parallel with Wave 1a):
    snappy, gflags, jemalloc, liburing, protobuf, scons,
    postgresql-documentdb (depends on existing postgresql)

Wave 2 (databases, parallel where deps allow):
    sqlite (version bump in core; no Wave 1 deps)
    postgresql (no Wave 1 deps; see §5 about liburing re-enable)
    mariadb (no Wave 1 deps)
    valkey (no Wave 1 deps)
    redis (no Wave 1 deps; license-disclosure)
    memcached (no Wave 1 deps)
    etcd (no Wave 1 deps; existing Go toolchain)
    leveldb (needs snappy)
    rocksdb (needs snappy, gflags, jemalloc, liburing)
    mongodb (needs scons, snappy; license-disclosure)
    ferretdb (needs postgresql-documentdb)
    influxdb (needs protobuf)
    cassandra (needs openjdk17, ant)
    couchdb (needs erlang/OTP, mozjs128)
```

The build orchestrator's topological sort handles all of this
automatically once each package's `dependencies.build:` is correct.
No manual ordering required at build time.

---

## 4. License handling — opt-in disclosure pattern

Three of the 14 databases ship under non-OSI licenses. They are
included because each has substantial wow-factor weight (users
expect to be able to choose them) but must be packaged with explicit
license disclosure so users opt in knowingly.

### Pattern (consistent across SSPL/RSAL packages):

1. `package.yml` `description:` field carries an unmissable license
   banner as the FIRST line of the description block, e.g.:
   `"WARNING: SSPL-1.0 (non-OSI, non-FOSS by Debian/Fedora/FSF
   definition). Read /usr/share/doc/<pkg>/LICENSE-CAVEAT before
   deploying as a service."`

2. `package.yml` has `non_osi: true` flag so audit tooling can
   surface non-OSI packages in build reports.

3. `pkm install <pkg>` displays the license banner on the terminal
   **before** download, mirroring how `pkm install chrome-helper`
   handles the Google Chrome EULA.

4. `do_install` ships `/usr/share/doc/<pkg>/LICENSE-CAVEAT` with a
   500-800 word plain-English explanation of the specific license's
   trigger conditions (SSPL §13 SaaS-disclosure trigger; RSAL's
   prohibition on offering the software as a service).

5. The FOSS-clean alternative is named explicitly in the banner.
   Examples:
   - MongoDB banner mentions FerretDB as the Apache-2.0 alternative
   - Redis banner mentions Valkey as the BSD-3-Clause alternative

### Per-package license disposition

| Package | License | Posture |
|---|---|---|
| mongodb | SSPL-1.0 | Opt-in with banner; recommend FerretDB |
| redis | RSALv2 OR SSPLv1 | Opt-in with banner; recommend Valkey |
| All others | OSI-approved FOSS | Standard `extra/` package |

This applies the per-archive-sig and repository-trust patterns
already established in the v1.0 doc set — license trust is an
extension of the same trust model.

---

## 5. Cross-cutting observations

### 5a. liburing re-enable for postgresql

The postgresql research report explicitly set `-Dliburing=disabled`
because liburing was not in our tree at the time. But the rocksdb
research adds `liburing` as a new prerequisite package. Once liburing
lands as part of Wave 1b, **postgresql's build.sh should be updated
to enable it** (`-Dliburing=enabled`). This is a small follow-up
edit that should land in the same commit as the liburing package
introduction, not deferred.

Same dynamic applies to any future Wave 1 dep that postgresql's
research flagged as "disabled per audit-finding rule": `libnuma`
remains disabled because no current Wave 1 candidate adds it.

### 5b. PORTABLE=1 — binary-distribution principle

The RocksDB research surfaces a Holy-Grail-grade concern that applies
beyond RocksDB: `PORTABLE=0` (the CMake default for many performance-
oriented C++ libraries) bakes `-march=native` into the binary based
on the **build host's** CPU features. Because InterGenOS distributes
pre-built binaries via the public mirror, this would silently leak
the build VM's microarchitectural assumptions to every user host.

**Principle:** every package built with a "native vs portable" build
flag must explicitly select the portable path. The RocksDB
`PORTABLE=1` flag is one instance; reviewers should look for the
same in any future C++ database or performance library landing.

### 5c. JIT and MemoryDenyWriteExecute — the FerretDB advantage

MongoDB embeds the mozjs JavaScript engine, which JIT-compiles
JavaScript at runtime and requires writable+executable memory pages.
This forces `MemoryDenyWriteExecute=false` in the systemd unit — a
real attenuation of our default hardening posture.

FerretDB is pure Go with no JIT and runs with `MemoryDenyWriteExecute=true`
under full systemd hardening. This is a concrete Holy-Grail
differentiator that favors FerretDB as the default MongoDB-wire-compatible
option, beyond the license alignment.

Document this in the FerretDB and MongoDB user-facing docs so users
making the choice see the security-posture tradeoff alongside the
license tradeoff.

### 5d. Build cost summary

Approximate build wall-times on the InterGenOS build VM (16 vCPU,
32 GB RAM):

| Tier | Examples | Range |
|---|---|---|
| Trivial | sqlite, snappy, gflags, leveldb, memcached, valkey, redis | seconds to a couple of minutes |
| Moderate | postgresql, mariadb, rocksdb, etcd, ferretdb, jemalloc, liburing, protobuf | 5–20 min each |
| Substantial | influxdb (Rust), couchdb, mongodb | 30 min to a few hours each (MongoDB the heaviest at ~3.5–4.5 hours due to SCons + linker peak) |
| Toolchain-class | openjdk17, erlang/OTP, mozjs128 | 30 min to 2 hours each |

Total wall-time for the entire Wave 1 + Wave 2 build is in the order
of half a build day if serialized; the build orchestrator's
parallelism brings it closer to a few hours of additional time
appended to the normal build cycle. Acceptable for v1.0.

### 5e. Common systemd hardening pattern

Every server database in this plan ships a systemd unit with the
following hardening directives applied. Build reviewers and runbook
authors can treat these as a baseline (with one documented exception
for MongoDB's JIT):

- `NoNewPrivileges=true`
- `ProtectSystem=strict`
- `ProtectHome=true`
- `PrivateTmp=true`
- `PrivateDevices=true`
- `ProtectKernelTunables=true`
- `ProtectKernelModules=true`
- `ProtectKernelLogs=true`
- `ProtectControlGroups=true`
- `ProtectHostname=true`
- `ProtectClock=true`
- `ProtectProc=invisible`
- `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`
- `RestrictNamespaces=true`
- `RestrictRealtime=true`
- `RestrictSUIDSGID=true`
- `LockPersonality=true`
- `MemoryDenyWriteExecute=true` (false only for MongoDB — JIT)
- `RemoveIPC=true`
- `SystemCallArchitectures=native`
- `SystemCallFilter=@system-service`
- `SystemCallFilter=~@privileged @resources @mount @swap @reboot`
- `CapabilityBoundingSet=`
- `AmbientCapabilities=`
- `ReadWritePaths=` (per-package allowlist)

Bind defaults: every server defaults to 127.0.0.1 only; operators
opt in to network exposure deliberately by editing the unit or the
service config. This is consistent with the Prime Directive's
"user must do it consciously" framing.

### 5f. AppArmor profiles

Each server database in this plan ships an AppArmor profile at
`/etc/apparmor.d/<binary-path>` constraining filesystem access to
the package's own state directories, plus the minimum required
network and IPC access. Profiles ship in **enforce** mode by default.
This is a substantial wow-factor element: most distributions either
ship no AppArmor profile or ship one in complain mode. InterGenOS's
default-enforce posture across the database fleet is unusual and
visible.

---

## 6. Per-database highlights

Brief notes for each package; full research lives in the conversation
log subagent reports.

### sqlite 3.53.1 (core, version bump)
Already in tree at 3.51.2. Proposed: bump + flag expansion (add
`--enable-rtree`, `--enable-geopoly`, `--enable-session`, plus
`SQLITE_ENABLE_MATH_FUNCTIONS`, `SQLITE_ENABLE_STMTVTAB`,
`SQLITE_ENABLE_PREUPDATE_HOOK`, `SQLITE_ENABLE_LOAD_EXTENSION`). The
3.53.0 WAL-reset corruption fix is the headline reason to bump
regardless. Zero new deps.

### postgresql 18.3
Six high-severity CVEs from 2026-Q1 closed by targeting 18.3 vs the
BLFS-tracked 18.2. All 19 runtime/build deps already in tree. Meson
build. After liburing lands (Wave 1b), enable `-Dliburing=enabled`.

### mariadb 11.8.6
CMake build. All build deps already in tree. Optional krb5 needs
in-tree presence verification (BLFS db indicates `core/`, but a tree
audit showed nothing — flag for verification before GSSAPI auth is
enabled). `SECURITY_HARDENED=ON` cmake flag is the umbrella that
disables anon user / test db / weak auth.

### valkey 8.1.3
Linux Foundation steward, BSD-3-Clause. The default in-memory KV
store for InterGenOS. Custom Makefile build. Three CVEs from 2025
closed by 8.1.3. Generated random `requirepass` on install (no
default "foobared"-class password).

### redis 7.4.5
Opt-in only. License banner mandatory. Port-collision check with
valkey at post-install (both default to 6379). Otherwise identical
build to valkey.

### memcached 1.6.41
BSD-3-Clause. Autotools. Zero known CVEs against the server in 24
months. Enables `--enable-tls` (we have openssl) and
`--enable-seccomp` (we have libseccomp). UDP listener stays
disabled by default (historical UDP-amplification DDoS vector).

### leveldb 1.23 (2021)
BSD-3-Clause. CMake. Quiet upstream; no CVEs in 12+ months. Needs
snappy (Wave 1b).

### rocksdb 11.1.1
GPL-2 OR Apache-2 dual. CMake. Needs snappy, gflags, jemalloc,
liburing (all Wave 1b). `PORTABLE=1` is mandatory per §5b.

### mongodb 8.0.4
SSPL-1.0 opt-in. License banner mandatory. SCons build (Python). Six
hours of build time. mozjs JIT forces `MemoryDenyWriteExecute=false`
(documented). Needs scons + snappy (Wave 1b).

### ferretdb 2.1.0
Apache-2.0. Pure Go. The InterGenOS-preferred MongoDB-wire-compat
package. Uses postgresql + postgresql-documentdb backend (FerretDB
v2 dropped the SQLite backend). Full `MemoryDenyWriteExecute=true`
hardening.

### etcd 3.5.21
Apache-2.0. Go-based, uses existing Go toolchain + go-vendor pattern
(same as hugo). Zero new deps. Etcd ships TLS-required + auth-on by
default in our config skeleton.

### influxdb 3.9.2 (Core)
Apache-2.0 OR MIT. Rust-based, uses existing Rust toolchain. Needs
protobuf (Wave 1b). Embedded Python plugin VM (security
consideration documented; AppArmor profile constrains plugin
exec).

### cassandra 5.0.8
Apache-2.0. Java-based; **needs OpenJDK 17 specifically** (not 21).
Two HIGH-severity 2025 CVEs closed by 5.0.3+; 5.0.8 is patched.
Default-secure config: auth on, UDFs off, bind 127.0.0.1, JMX
localhost-only.

### couchdb 3.5.1
Apache-2.0. Erlang-based + needs SpiderMonkey 128 for the view
server. The largest new-ecosystem lift (Erlang/OTP entire VM). No
admin-party mode in our shipped config; first-run helper enforces
admin password before service start.

---

## 7. Open questions for Owner review

1. **OpenJDK 17 vs 21 strategy.** Cassandra 5.0 caps at JDK 17.
   BLFS only ships 21. Options:
   - (a) Add OpenJDK 17 alongside OpenJDK 21 (parallel packages,
         `<name><major>` convention).
   - (b) Only ship OpenJDK 17 for v1.0 (defer 21 until a v1.x
         consumer surfaces).
   - (c) Skip Cassandra v1.0 and revisit after JDK strategy
         clarifies.
   **Recommendation:** (a) — parallel packages. Matches the existing
   spidermonkey pattern.

2. **MongoDB inclusion.** SSPL is non-OSI. Three viable postures:
   - (a) Ship as opt-in with banner (the plan above)
   - (b) Skip MongoDB entirely; ship only FerretDB as MongoDB-wire-compat
   - (c) Ship MongoDB without disclosure
   **Recommendation:** (a). Comprehensive coverage + clear disclosure
   is the wow-factor-aligned posture. (c) is unacceptable for trust.
   (b) underdelivers on user expectation.

3. **Redis inclusion.** Same logic as MongoDB. Recommendation: (a)
   ship opt-in with banner; Valkey is the default.

4. **PostgreSQL liburing flag.** Once liburing lands as part of
   Wave 1b for RocksDB, should we revise the postgresql package to
   enable `-Dliburing=enabled`? **Recommendation:** yes, same
   commit.

5. **InfluxDB 2.x vs 3.x.** The plan targets 3.9.2 (Rust, active).
   2.x is on no-new-features security-only maintenance. Confirm
   we ship 3.x only, not both.

6. **MariaDB krb5 verification.** BLFS db says krb5 is in `core/`;
   tree audit returned empty. Verify or add before enabling
   `PLUGIN_AUTH_GSSAPI=YES`.

---

## 8. Consolidated TODO list

### Phase 1 — prerequisite landings (Wave 1)

- [ ] Package and land `openjdk17` (separate task — BLFS-style
      from-source build with bootstrap JDK; comparable scope to
      adding Go/Rust)
- [ ] Package and land `erlang/OTP 28.5` (separate task — new
      ecosystem; comparable scope to adding Go/Rust; autotools build)
- [ ] Package and land `mozjs128` (parallel package alongside
      `spidermonkey-140`; copy + version-pin pattern)
- [ ] Package and land `ant` (small; depends on `openjdk17`)
- [ ] Package and land `scons` (small; pure Python)
- [ ] Package and land `snappy` (small; BSD-3 CMake build)
- [ ] Package and land `gflags` (small; BSD-3 CMake build)
- [ ] Package and land `jemalloc` (small; autotools)
- [ ] Package and land `liburing` (small; LGPL/MIT autotools build)
- [ ] Package and land `protobuf` (medium; Apache-2.0 CMake build)
- [ ] Package and land `postgresql-documentdb` (medium;
      Apache-2.0 PostgreSQL extension)

### Phase 2 — database landings (Wave 2)

- [ ] `sqlite` version-bump in `core/` (3.51.2 → 3.53.1) + flag
      expansion
- [ ] `postgresql 18.3` in `extra/`
- [ ] `mariadb 11.8.6` in `extra/`
- [ ] `valkey 8.1.3` in `extra/`
- [ ] `redis 7.4.5` in `extra/` (opt-in, license banner)
- [ ] `memcached 1.6.41` in `extra/`
- [ ] `leveldb 1.23` in `extra/`
- [ ] `rocksdb 11.1.1` in `extra/` (PORTABLE=1)
- [ ] `mongodb 8.0.4` in `extra/` (opt-in, license banner)
- [ ] `ferretdb 2.1.0` in `extra/`
- [ ] `etcd 3.5.21` in `extra/`
- [ ] `influxdb 3.9.2` in `extra/`
- [ ] `cassandra 5.0.8` in `extra/`
- [ ] `couchdb 3.5.1` in `extra/`

### Phase 3 — cross-cutting follow-ups

- [ ] Update `postgresql/build.sh` to enable `-Dliburing=enabled`
      once liburing lands (same commit as liburing introduction)
- [ ] Document the PORTABLE-vs-native build-flag principle in
      `docs/research/binary-distribution-build-flags.md` so the
      next contributor adding a C++ performance library knows to
      check
- [ ] Add CVE watchlist entries for each of the 14 databases to
      whatever security-tracking system InterGenOS adopts post-v1.0
- [ ] Verify mariadb krb5 prerequisite (in-tree or missing — open
      question 6)

### Phase 4 — post-landing verification

- [ ] Build all 14 databases against the next full Build #N cycle;
      verify each produces a working `.igos.tar.gz` archive
- [ ] First-publish runbook validates that each archive deploys
      cleanly onto a fresh InterGenOS install via `pkm install`
- [ ] AppArmor profile sanity-check: install each database in a
      test VM with `aa-status` and confirm enforce mode is active
      on the binary

---

## 9. What the mirror looks like after this lands

When a casual observer browses
`https://repo.intergenos.org/x86_64/packages/` after the v1.0
publish (or runs `pkm search database` on a fresh InterGenOS install),
the visible surface is:

```
Relational SQL:        postgresql-18.3   mariadb-11.8.6   sqlite-3.53.1
KV cache (in-memory):  valkey-8.1.3      redis-7.4.5      memcached-1.6.41
Embedded KV:           leveldb-1.23      rocksdb-11.1.1   sqlite-3.53.1
Document NoSQL:        ferretdb-2.1.0    mongodb-8.0.4    couchdb-3.5.1
Time-series:           influxdb-3.9.2
Distributed KV/coord:  etcd-3.5.21
Wide-column:           cassandra-5.0.8
```

That is the wow surface this plan exists to produce.
