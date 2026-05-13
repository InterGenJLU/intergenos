# Web-Server Landing Plan — v1.0

**Status:** TODO scaffold — v1.0-launch planning, stage-appropriate-depth
(architectural decisions + TODOs + cross-cutting observations; not
finished package code).
**Created:** 2026-05-12
**Owner:** InterGenJLU
**Source research:** SPOC pre-dispatch authoring; per-server research
to be conducted by the dispatched fleet agent and consolidated back
into this document at landing time.

---

## 0. The wow-factor reasoning

A from-source Linux distribution at v1.0 launch needs to demonstrate
that comprehensive coverage exists across the categories users expect.
"Casual observer runs `pkm search web-server` and sees zero packages"
is project-existential failure. The current tree has **zero** web
servers — `apache-httpd`, `nginx`, `caddy`, `lighttpd`, `haproxy`
are all absent.

"Casual observer runs the same search and sees the heritage default
(Apache), the modern frontend default (Nginx), an ACME-aware modern
single-binary (Caddy), a lightweight option (lighttpd), and a
production-grade load balancer / reverse proxy (HAProxy)" is the wow
surface that drives adoption among the audience most likely to evaluate
a from-source distro: sysadmins, infrastructure engineers, and self-
hosting hobbyists. Every one of them expects to be able to `pkm install
nginx` on day one.

For a from-source distribution serving an audience that includes
infra-curious users, comprehensiveness across the standard web-server
categories is the bar at v1.0 — not a nice-to-have, not deferrable,
and not weighed against ongoing maintenance overhead.

All 5 packages target `packages/extra/`. None are installed in the
desktop ISO by default. Every one ships as a signed `.igos.tar.gz` on
`repo.intergenos.org/x86_64/packages/` and is installed by users via
`pkm install <name>`.

---

## 1. Web-server matrix

| Package | Version (provisional) | Category | License | Tier | New deps |
|---|---|---|---|---|---|
| apache-httpd | 2.4.x (latest stable) | Heritage / module-rich HTTP | Apache-2.0 | extra | apr, apr-util |
| nginx | 1.28.x (mainline) | Modern frontend HTTP / reverse proxy | BSD-2-Clause | extra | — |
| caddy | 2.10.x | ACME-aware single-binary HTTP/H2/H3 | Apache-2.0 | extra | — (Go toolchain in core) |
| lighttpd | 1.4.x (latest) | Lightweight HTTP / embedded angle | BSD-3-Clause | extra | — |
| haproxy | 3.0.x LTS | Load balancer / reverse proxy / TLS terminator | GPL-2.0-or-later | extra | — |

**Coverage:** every major use case is covered. Apache for module-
heritage workloads (mod_wsgi, mod_php, mod_dav), Nginx for the modern
default-frontend pattern, Caddy for the auto-HTTPS / single-binary
modern workflow, lighttpd for the lightweight / embedded niche, and
HAProxy for the L4/L7 load-balancing and TLS-terminating tier.

All 5 packages are **FOSS-clean under OSI-approved licenses**. No
opt-in license-disclosure pattern is needed here (contrast with the
database wave's mongodb/redis SSPL/RSAL opt-in handling).

---

## 2. New prerequisite packages — Wave W1a

Only Apache HTTPD introduces new prereqs. Nginx, Caddy, lighttpd, and
HAProxy build against deps already in tree (zlib, pcre2, openssl,
go-toolchain — all `core/`).

### Tier placement

Both new prereqs target `packages/extra/`, matching the target tier of
their sole consumer (apache-httpd). Same rationale as the database
plan's §2 tier-placement-prior-art: language toolchains and
package-specific runtimes follow their consumers, not a hidden "base"
tier. There is no `core/` or `desktop/` consumer for APR.

| Package | License | Why | Used by |
|---|---|---|---|
| `apr` (Apache Portable Runtime) | Apache-2.0 | OS-abstraction layer required by httpd 2.4.x; not vendored | apache-httpd |
| `apr-util` | Apache-2.0 | LDAP / DBM / xml utility library on top of APR; mandatory for httpd | apache-httpd |

### Inter-dep ordering

- `apr-util` depends on `apr`. `apr` must land first.
- Otherwise no Wave W1a inter-dep.

---

## 3. Build order (topological)

```
Wave W1a (Apache APR stack, serial):
    apr ───▶ apr-util

Wave W1b (web servers, parallel where deps allow):
    apache-httpd (needs apr + apr-util)
    nginx (no Wave W1a deps)
    caddy (no Wave W1a deps; uses core/go)
    lighttpd (no Wave W1a deps)
    haproxy (no Wave W1a deps)
```

The build orchestrator's topological sort handles all of this
automatically once each package's `dependencies.build:` is correct.
No manual ordering required at build time.

---

## 4. License handling

All 5 web servers + 2 prereqs ship under OSI-approved FOSS licenses
(Apache-2.0, BSD-2-Clause, BSD-3-Clause, GPL-2.0-or-later). No opt-in
disclosure pattern is needed — these are standard `extra/` packages
with the existing per-archive-sig and repository-trust pattern
(consistent with the rest of the tree outside the database wave's
mongodb/redis special-cases).

Should any embedded module ship under a non-OSI license (e.g., a
caddy plugin pulled in by default), that single module follows the
opt-in disclosure pattern documented in `database-landing-plan.md`
§4 — not the whole package.

---

## 5. Cross-cutting observations

### 5a. Default-bind 127.0.0.1, not 0.0.0.0

Every web server defaults to bind `127.0.0.1` only — operators opt in
to network exposure deliberately by editing the unit or the service
config. This matches the database wave's default-secure posture and
the Prime Directive's "user must do it consciously" framing.

This is a substantial deviation from upstream defaults for most of
these packages (Apache, Nginx, lighttpd all default to `*:80` upstream)
and is the kind of difference a casual observer notices when they
install the package, start the service, and find it does NOT
immediately get scanned by the internet on their VPS. Default-secure
is the wow surface for the threat-modeling-aware subset of users.

### 5b. TLS-mandatory in shipped sample configs

Each web server's shipped sample config (e.g.,
`/etc/<server>/sample-tls.conf`) demonstrates TLS termination using
`openssl` (already in `core/`). The default site config in
`/etc/<server>/conf.d/00-default.conf` enables TLS-only on `:443`
with self-signed certs auto-generated at first-start (similar to how
many distros handle MariaDB's default cert). This nudges users toward
TLS-by-default while not forcing a Let's Encrypt dependency at install
time.

A future certbot landing (adjacent wave; see §7 Q3) would close this
loop with ACME-issued certs by default. For W1, the self-signed
default + sample ACME config is the right balance — works
out-of-box, documents the next step.

### 5c. Common systemd hardening pattern

Every server in this plan ships a systemd unit with the same
hardening directive set documented in `database-landing-plan.md` §5e.
**No JIT exception applies** to any of these web servers — none of
Apache, Nginx, Caddy, lighttpd, HAProxy do JIT compilation in the
hot path, so `MemoryDenyWriteExecute=true` is universal.

For HAProxy specifically, the directive set extends with
`AmbientCapabilities=CAP_NET_BIND_SERVICE` to allow port-80/443 bind
without root, since HAProxy intentionally drops privileges post-bind
in its standard runtime model.

### 5d. AppArmor profiles

Each web server ships an AppArmor profile in **enforce** mode at
`/etc/apparmor.d/<binary-path>` constraining filesystem access to
the server's own state/log directories + the configured document
roots. Same wow-factor delta as the database wave — most distributions
either ship no AppArmor profile or ship one in complain mode.

### 5e. Build cost summary

Approximate build wall-times on the InterGenOS build VM (16 vCPU,
32 GB RAM):

| Tier | Examples | Range |
|---|---|---|
| Trivial | nginx, lighttpd, haproxy, caddy (Go single-binary), apr, apr-util | 1-5 min each |
| Moderate | apache-httpd (autotools + module compilation) | 5-15 min |

Total wall-time for the entire Wave W1 build is well under an hour,
even serialized. The build orchestrator's parallelism brings it down
to ~15 min appended to the normal build cycle.

### 5f. Port-collision policy

Apache and Nginx both default to port 80/443 in stock upstream
configs. Per §5a, we ship them with 127.0.0.1 bind, so the casual-
install case doesn't collide. For operators installing multiple web
servers on the same host:

- Document in each server's user-facing doc that the **service unit
  is disabled by default** (operator runs `systemctl enable <server>`
  explicitly).
- Document the listen-port reassignment recipe in
  `/etc/<server>/conf.d/00-default.conf`.

This is consistent with the database wave's valkey/redis port-6379
collision handling (post-install check + recipe).

### 5g. Caddy auto-HTTPS posture

Caddy's distinguishing wow feature is upstream-default automatic ACME
TLS issuance against Let's Encrypt. We ship Caddy with auto-HTTPS
**enabled but pointed at the staging Let's Encrypt endpoint** in
the default config — operators flip to production by removing one
config line. Reasoning: shipping with production-ACME enabled by
default would generate real Let's Encrypt traffic from every
package-install dry-run, which is unfriendly to ACME infrastructure
and reveals install-time fingerprints to a third party. Staging
endpoint exercises the full code path without those concerns.

### 5h. nginx + apache module-loading posture

Both nginx and Apache support dynamic modules (DSO). We ship the
core/default module set compiled-in for the common case, and document
the recompile-or-DSO-pull workflow for less-common modules:

- nginx: ship with `--with-http_ssl_module --with-http_v2_module
  --with-http_v3_module --with-http_realip_module
  --with-http_gzip_static_module --with-stream` enabled at build
  time. Document the `--add-dynamic-module` recipe for OOT modules.
- apache: ship with mod_ssl + mod_http2 + mod_proxy + mod_rewrite
  enabled. Document the `apxs` recipe for OOT modules. **Do NOT**
  ship mod_php in the apache-httpd package — language-interpreter
  modules belong in a dedicated `apache-mod-<lang>` follow-on
  package (see §7 Q2 for v1.x scoping).

---

## 6. Per-server highlights

Brief notes for each package; full research deliverable from the
dispatched agent should fill in upstream-CVE history + exact final
version pin.

### apache-httpd 2.4.x (heritage HTTP)

Apache HTTPD remains the most-installed web server in many shops and
the only one with the module ecosystem to handle mod_wsgi / mod_perl
/ mod_dav / mod_security workloads natively. Autotools build. Needs
apr + apr-util (Wave W1a). MPM (Multi-Processing Module) choice:
ship with `mpm_event` as default — modern, threaded, lower memory
than mpm_worker for the same concurrency. `--enable-mods-shared=all`
for DSO support.

Default-secure: `ServerSignature Off`, `ServerTokens Prod`, no default
mod_status / mod_info enabled, mod_ssl enabled with SECURE-by-default
cipher suites. Document.

### nginx 1.28.x mainline (modern frontend)

The modern frontend default. BSD-2-Clause. Mainline (1.28) preferred
over stable (1.26) — Nginx project recommends mainline for production;
stable is for distros that want fewer-but-bigger version jumps. Custom
configure. Zero new deps (zlib/pcre2/openssl all in `core/`).

Default-secure: `server_tokens off;`, no default
`autoindex on;`, no default `location /nginx_status`. mod_stream
enabled for L4 proxy use cases.

### caddy 2.10.x (ACME-aware single binary)

The modern auto-HTTPS workflow. Apache-2.0 (Caddy core; embedded
modules carry their own licenses, mostly Apache-2.0 or MIT). Built
with Go (existing `core/go` toolchain). Single static binary post-
build — install layout is just `/usr/bin/caddy` + a sample config.

Default config bundled in our package points at Let's Encrypt's
staging endpoint per §5g.

### lighttpd 1.4.x (lightweight)

Single-process / event-driven. BSD-3-Clause. Autotools. Embedded /
low-resource niche — wow-factor for users running a NAS or single-board
computer who need a real web server with full feature set under low
memory. mod_openssl enabled for TLS.

### haproxy 3.0.x LTS (load balancer)

GPL-2.0-or-later. Custom Makefile build (`make TARGET=linux-glibc
USE_OPENSSL=1 USE_LUA=1 USE_PCRE2=1`). The 3.0 LTS branch is the
right pin — long support tail (multi-year), proven QUIC support.
`AmbientCapabilities=CAP_NET_BIND_SERVICE` per §5c.

Default-secure: master-worker process model, drops privileges post-
bind, stats socket disabled by default.

---

## 7. Open questions for Owner review

1. **Adjacent wave — application servers + WSGI/PHP runtimes.** Once
   web servers land, the natural follow-on is mod_wsgi (Python),
   php-fpm, gunicorn, uwsgi. Wave W2 candidates? **Recommendation:**
   surface as Wave W2 candidates after Build #9 / v1.0 milestone is
   stable. Not blocking for v1.0.

2. **Apache mod_php inclusion.** Bundling mod_php in the apache-httpd
   package would balloon its dep surface (libphp + entire PHP runtime).
   Recommendation: do NOT bundle; ship as `apache-mod-php` if PHP
   adoption is desired (v1.x). The apache-httpd package itself stays
   PHP-free; users who want PHP install both.

3. **Certbot / ACME-client landing.** Pairs naturally with web servers
   but is a separate package. **Recommendation:** land certbot
   alongside Wave W1 web servers (~7th dispatch). It is the
   user-facing wow companion to "we have web servers" — without
   certbot, users still have to figure out TLS-cert acquisition.

4. **Caching-proxy lane (Varnish, Squid, etc.).** Not in Wave W1.
   Recommendation: defer to v1.x. Coverage at v1.0 is adequate without.

5. **Reverse-proxy posture between HAProxy and Nginx.** Both can do
   reverse-proxy + TLS terminator. We ship both; user-facing doc
   should explain the tradeoff (HAProxy for L4/L7 LB at scale; Nginx
   for HTTP-aware reverse proxy with module ecosystem). **Recommendation:**
   covered in the per-server `/usr/share/doc/<pkg>/README.md` files;
   no architectural decision needed beyond shipping both.

---

## 8. Consolidated TODO list

### Phase 1 — prerequisite landings (Wave W1a)

- [ ] Package and land `apr` (small; Apache-2.0 autotools; bridges to httpd)
- [ ] Package and land `apr-util` (small; Apache-2.0 autotools; depends on apr)

### Phase 2 — web-server landings (Wave W1b, parallel where deps allow)

- [ ] Package and land `apache-httpd` (depends on apr + apr-util; mpm_event default; full default-secure)
- [ ] Package and land `nginx` (mainline; ssl/v2/v3/realip/gzip_static/stream modules; default-secure)
- [ ] Package and land `caddy` (Go single-binary; auto-HTTPS staging-endpoint default)
- [ ] Package and land `lighttpd` (autotools; mod_openssl; embedded angle)
- [ ] Package and land `haproxy` (custom Makefile; OPENSSL + LUA + PCRE2; LTS pin)

### Phase 3 — TLS companion (parallel with Phase 2 ideally)

- [ ] Package and land `certbot` (Python; Let's Encrypt client; documented in user-facing doc as the production-ACME path companion to caddy's staging-default)

### Phase 4 — per-server user-facing docs

- [ ] `docs/users/web-servers.md` — overview, when-to-pick-which, sample config patterns

### Phase 5 — `pkm search web-server` polish

- [ ] Verify pkm search indexer picks up "web-server" / "http-server" / "reverse-proxy" / "load-balancer" keywords from each package.yml's description field — the wow surface lives in this command.

---

## 9. Dispatch routing (provisional)

| Package | Routing | Rationale |
|---|---|---|
| apache-httpd | IGOSC | Heritage Apache work has reference-grade audit depth; httpd's MPM/DSO complexity benefits from gold-grade discovery |
| nginx | WC | Modern frontend; standard custom-configure flow; WC has package-landing rhythm |
| caddy | DS-v1 | Go single-binary; cargo not needed but Go toolchain familiarity helps; simplest of the five |
| lighttpd | DS-v2 | Lightweight; pairs with docs-lane work DS-v2 has been doing; first-package-landing reasonable scope |
| haproxy | Rotation (IGOSC or WC next idle) | Custom Makefile pattern; benefits from peer-review on the build-flag choice |
| apr + apr-util | IGOSC (alongside apache-httpd) | Co-deps; same dispatch makes sense |
| certbot | DS-v1 or DS-v2 (rotation) | Python; established pattern |

---

## 10. Re-orient notes

This document carries the same scaffolding-grade discipline as
`database-landing-plan.md`. The dispatched fleet agent for each
package is expected to:

1. Verify the upstream version pin against current releases at landing time.
2. Verify each declared dep against the in-tree package name (preflight gate enforces; see `feedback_in_tree_pkg_names_case_preserving`).
3. Produce a full per-package research deliverable noting upstream
   CVE history, deviations from the default-secure pattern, any
   missing-from-tree deps the audit surfaces.
4. Land the package + systemd unit + AppArmor profile + sample config
   in one commit per the `database-landing-plan.md` precedent.

After the wave is complete, this document gets a §11 retrospective
folded in from the per-package research deliverables, the same way
`database-landing-plan.md` did the version-pin closure for
postgresql/mariadb at landing.
