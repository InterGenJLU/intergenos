# Public Hosting Plan — intergenos.org + repo.intergenos.org

**Status:** TODO scaffold — v1.0-launch planning at stage-appropriate-depth
(decisions + TODOs, not finished prose).
**Created:** 2026-05-12
**Owner:** InterGenJLU

---

## Scope

Two public-facing domains under the project's authority:

- **intergenos.org** — the project's web presence. Today serves a default
  Apache `Index of /` only.
- **repo.intergenos.org** — the binary package mirror that `pkm sync`
  pulls against. Infrastructure provisioned 2026-05-11 (DNS, TLS via
  Let's Encrypt, SSH push path, docroot); awaiting first signed publish.

This document records the v1.0-launch decisions about what each domain
hosts, what is deferred, and the TODOs to take each decision from
"decided" to "live". It is an architecture record, not a content-marketing
or launch-comms plan.

## Decision filter

Every entry below was filtered through, in priority order:

1. **The security-only alignment.** Content must not proliferate the
   project's trust surface. Each new hosted feature is each new
   compromise vector.
2. **The Prime Directive.** Content must put users in control of their
   machine. Anything that obscures how the system works is not welcome.
3. **Stage-appropriate-depth.** v1.0 ships the minimum viable hosting.
   v1.x layers on as concrete user needs surface.

A "YES" below means the item meets all three filters. A "DEFER" means
the item passes the filters but waits for evidence that it is needed.
A "NO" means the item is rejected at the architectural level — it
either violates a filter or has a better off-our-infrastructure home.

---

## intergenos.org

**Current state (2026-05-12):** DNS at `162.255.162.237` (same VPS as
the mirror). HTTP 200 returns the default Apache `Index of /` with
`cgi-bin/` and a 2016 `robots.txt`. Effectively blank.

**v1.0 launch target:** a single static landing page with the minimum
viable content for the first-public-release moment.

### Decided for v1.0

| Item | Decision | Rationale |
|---|---|---|
| Landing page (project intro, screenshots, Prime Directive blurb) | YES | First impression for visitors; needed for v1.0 announcement |
| ISO download link (direct + signature + verification instructions) | YES | The release primitive — users must be able to get the artifact |
| Pointer to GitHub repo (source, docs, issues, releases) | YES | Source of truth lives on GitHub; intergenos.org just routes |
| Security contact + PGP key fingerprint cross-link | YES | Coordinated disclosure entry point; covers visitors who don't navigate to GitHub |
| Static HTML + CSS only (no JS, no CMS, no dynamic content) | YES | Simpler = more auditable; aligns with `SECURITY.md` posture |

### Deferred to v1.x

| Item | Decision | Rationale |
|---|---|---|
| Project status page (mirror health, last build state, CI signal) | DEFER | Nice to have for operators; v1.0 audience is end users; adds dynamic surface |
| Community page (mailing lists, IRC, Discord, etc.) | DEFER | The project is single-maintainer at v1.0; community page after community |
| News / blog / release-announcement archive | DEFER | Premature pre-v1.0; one launch announcement is sufficient |
| Documentation mirror of `docs/` | DEFER | GitHub renders markdown; duplicating it adds drift risk |

### Architecturally rejected

| Item | Decision | Rationale |
|---|---|---|
| User accounts, forums, or hosted community discussion | NO | Proliferates trust surface (auth, session storage, moderation). Community-hosted alternatives (Matrix, Discord, mailing list) are better when needed. |
| Dynamic content management system (WordPress, Ghost, etc.) | NO | Each CMS plugin is an attack vector. Static HTML is auditable; CMS is not. |
| Bug tracker hosted by the project | NO | GitHub Issues is the source of truth; running our own duplicates effort and adds surface |

---

## repo.intergenos.org

**Current state (2026-05-12):** DNS at `162.255.162.237`. `/x86_64/`
docroot returns `Index of /x86_64` with an empty `packages/` subdir.
No `InterGenOS.db` yet. Awaiting Build #9 completion + the post-build
signed-publish chain.

**v1.0 launch target:** the package mirror that `pkm sync` pulls
against. API-only — no human-facing pages. Layout per
[`docs/repository-trust.md`](../repository-trust.md) and the operational
runbook at [`docs/operational/first-publish-runbook.md`](../operational/first-publish-runbook.md).

### Decided for v1.0

| Item | Decision | Rationale |
|---|---|---|
| `InterGenOS.db` + `InterGenOS.db.sig` at `/x86_64/` root | YES | Matches the repository-trust.md trust model; `pkm` verifies the index sig and trusts the index's archive sha256s |
| `*.igos.tar.gz` archives under `/x86_64/packages/` | YES | The actual binary artifacts |
| Atomic publish via `/x86_64.new/` → `rename → /x86_64/` | YES | Avoids partial-read races during publish; covered by the publish-repo orchestrator atomic-promote chain |
| Per-archive `*.sig` alongside each `*.igos.tar.gz` | NO (v1.0); deferred to v1.1+ project-backlog | Signed-index-only is sufficient for v1.0: the chain of trust (GPG key → signed index → sha256 per archive) is complete and every link is verified by pkm at install time. Decision recorded in `docs/architecture/per-archive-sig-decision.md`; independent peer-review confirmed the implementation claim (22/22 tests pass) and the operational impact (zero runbook procedure changes) on 2026-05-12. |
| Default Apache directory index for `/x86_64/packages/` | YES | Operators can curl the layout; users go through pkm |

### Deferred to v1.x

| Item | Decision | Rationale |
|---|---|---|
| `/sources/` upstream-tarball mirror | DEFER | Source mirroring targets `origin.intergenstudios.com`; keep the binary mirror focused. Users can rebuild from source via the build system regardless. |
| Per-package SBOMs (SPDX or CycloneDX) at `/x86_64/sboms/` | DEFER | The signed index already covers provenance; SBOMs are a transparency win but not v1.0-required |
| In-toto / sigstore build attestations at `/x86_64/attestations/` | DEFER | The signing-key chain is the v1.0 provenance story; attestations layer on |
| Additional architectures (`/aarch64/`, `/riscv64/`) | OUT OF SCOPE v1.0 | x86_64 only at first release; future arch ports get their own subdir |

### Architecturally rejected

| Item | Decision | Rationale |
|---|---|---|
| Human-facing catalog / search / browse UI | NO | Adds dynamic surface; users discover packages via `pkm search` |
| User-uploaded packages (AUR-style submissions) | NO | Inverts the trust model — every published archive must be signed by the project key, not by random submitters |
| Mirror server pulling from a third party | NO | We are the publisher, not a mirror of upstream; the mirror serves what the project signs |

---

## Cross-cutting

### Trust boundary

- **intergenos.org** = informational + ISO download.
- **repo.intergenos.org** = API surface for `pkm`.

Both sit under the same DNS authority (cPanel-managed at the project
VPS). Compromise of `intergenos.org` would affect new-user trust framing
but cannot install malicious packages on a running InterGenOS system
(`pkm` verifies against the signing key bundled with the image, not
against anything served from `intergenos.org`). Compromise of
`repo.intergenos.org` cannot install malicious packages without also
compromising the signing chain — `pkm` will refuse a non-signed or
sig-mismatched index.

This separation is intentional: it limits the consequences of any
single-domain compromise.

### Relationship to intergenstudios.com

The project today uses `intergenstudios.com` for:

- `/.well-known/security.txt` (RFC 9116)
- Signing-key publication page (referenced from `docs/signing-key.md`)
- The `security@intergenstudios.com` contact in `SECURITY.md`

v1.0 launch question: should `intergenstudios.com` content move under
`intergenos.org`, stay where it is, or run in parallel? Decision
deferred — Owner review needed; see Open Questions below.

### Open questions for Owner review

1. **`intergenstudios.com` vs `intergenos.org`** — keep parallel,
   redirect one to the other, or migrate content? Current state runs
   parallel and works fine; migration would consolidate but disrupts
   existing `security.txt` and signing-key URLs that are referenced
   in `SECURITY.md` and `docs/signing-key.md`.
2. **ISO download path** — host the v1.0 ISO at `intergenos.org/download`,
   route to a dedicated `releases.intergenos.org`, or use GitHub
   Releases as the canonical download? GitHub Releases gives the v1.0
   tag a canonical artifact; cloning to our own host gives us
   resilience against GitHub outage. Current bias: GitHub Releases as
   the canonical release primitive, with `intergenos.org/download`
   linking through. Confirm before authoring the landing page.
3. **Per-archive `.sig` final decision** — open dispatch in flight
   (see `docs/architecture/per-archive-sig-decision.md`). Outcome will
   determine whether `/x86_64/packages/*.igos.tar.gz.sig` files exist
   alongside each archive at first publish.

### TODO list (consolidated)

For **intergenos.org**:

- [ ] Draft static landing HTML skeleton; final copy from Owner
- [ ] Apply CSS aligned with `docs/VISUAL_LANGUAGE.md` (palette,
      typography, asset references)
- [ ] Wire ISO download link target (depends on Open Question #2)
- [ ] Cross-link `SECURITY.md`, `docs/signing-key.md`, GitHub repo
- [ ] Replace default Apache `Index of /` with the landing page
- [ ] Verify Let's Encrypt cert renewal on the existing chain

For **repo.intergenos.org**:

- [ ] E1.B.5 — wire `emit-package-archives.py` into `build-intergenos.sh`
      so Build #N produces signed `.igos.tar.gz` artifacts at
      `/var/lib/igos/archives/`
- [ ] Resolve Open Question #3 (per-archive-sig design)
- [ ] Run `scripts/dry-run-first-publish.sh` against real Build #9
      archives end-to-end before any live publish
- [ ] Execute the procedure in `docs/operational/first-publish-runbook.md`
- [ ] E1.B.7 — fresh-InterGenOS `pkm sync` against the live mirror; end
      to end install of one package; verify sig

### Out of scope of this document

- Marketing copy and launch announcement content (separate item)
- Community-channel setup (Matrix / Discord / mailing list — deferred
  per the v1.x section above)
- Bug-bounty program decisions (`SECURITY.md` records the current
  posture: no bug bounty at this time)
