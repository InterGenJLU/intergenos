# Pure-Python source-from-GitHub pattern

**Canonical recipe for sourcing pure-Python packages from GitHub directly, with zero PyPI exposure.**
**Authored: 2026-05-28. Owner: InterGenJLU. Status: ACTIVE.**

This document is the recipe template for any pure-Python package that is currently sourced from PyPI and must instead be sourced from GitHub directly. It composes with the general build-hygiene rules and [`packages/core/maturin/`](../../packages/core/maturin/) (the worked precedent for the Rust+Python hybrid case). The pattern was synthesized after the 2026-05-12 PyPI Mini Shai-Hulud attack window and codified as the canonical replacement strategy when the PyPI prohibition was extended indefinitely on 2026-05-27.

---

## §0. Reading order

If you are about to write or peer-review a per-package PyPI-replacement recipe:

1. This document (start to finish).
2. The general build-hygiene rules, especially the pre-built artifact pattern and class-hazard scrutiny.
3. [`packages/core/maturin/build.sh`](../../packages/core/maturin/build.sh) and [`packages/core/maturin/package.yml`](../../packages/core/maturin/package.yml), the worked precedent. Re-read the `do_install` function's minimal `.dist-info` minting block; that is the load-bearing snippet for any package that consumers resolve via PEP 517 `Requires`.
4. [`docs/VISION.md`](../VISION.md) — project philosophy: the two questions every design choice answers (does it keep the user in control, and does it hold up against capable adversaries).

---

## §1. The policy

**PyPI sourcing is prohibited indefinitely for any package added or re-sourced on or after 2026-05-27.**

The policy was extended from the original 2026-05-12 attack-window posture on 2026-05-27. The re-evaluation criteria are: (a) PEP 740 attestation coverage exceeds 80% of the upper-quartile most-downloaded packages, (b) registries implement maintainer-account anomaly detection, and (c) at least 6 months pass with zero major supply-chain wave. None are currently met.

**Empirical basis (as of 2026-05-27):** Wave 4 `durabletask` (Microsoft Azure Durable Task SDK, ~417K monthly downloads) hit PyPI on 2026-05-19. Same day, `@antv` published 639 malicious npm versions across 323 packages in ~1 hour — the largest single-hour surge in the campaign. Mini Shai-Hulud source code went public the evening of 2026-05-12 before takedown — the full toolchain (CI cache-poisoning + OIDC token extractor + credential stealer with propagation) is in the wild and copycats are already observable per Unit 42. The TanStack compromise empirically defeated SLSA Build Level 3 + Sigstore attestations by hijacking the legitimate build pipeline itself. Industry consensus (Tenable, Unit 42, Trail of Bits, Phoenix Security, CSA, Wiz) is that hash-pinning + `--isolated` + min-release-age is now **insufficient** post-Mini-Shai-Hulud.

Both [`docs/VISION.md`](../VISION.md) decision questions rule against PyPI sourcing in this environment:

- **Does it hold up against capable adversaries?** No. The registry's signature-chain trust root is broken under current conditions, and we will not inherit that chain until the registry posture changes structurally.
- **Does it keep the user in control?** No. PyPI artifacts are opaque to the source-of-truth `package.yml` enumeration that the rest of the project relies on. The user cannot trace a shipped dependency back to a maintainer-signed GitHub tag through a PyPI hop.

---

## §2. The 8-step recipe

Each step is mechanical. Skipping a step or short-circuiting a verification is a recipe defect, not a time saver.

### §2.1 Source identification

For each package, identify exactly one canonical GitHub repository and exactly one maintainer (or maintainer team) that owns the signing/release key. Record:

- Canonical repo URL (`https://github.com/<org>/<repo>`).
- Maintainer username(s) + their public key fingerprint(s) — the keys that sign tags or releases.
- License (must be compatible with InterGenOS's GPL-3.0-or-later distribution).
- Whether the project ships an `ext_modules` block in `setup.py` or a `[build-system]` requirement that pulls a C compiler. If yes, see §2.7.

If the package is maintained on a forge other than GitHub (GitLab, Codeberg, sr.ht), the pattern still applies; substitute the forge's tag/release URL shape. If the package is maintained ONLY on PyPI with no source forge, escalate it (per §3).

### §2.2 Version selection

The selected version MUST satisfy ALL of:

1. **Tagged release** on the canonical repo (NOT a commit-SHA on a branch, NOT a draft pre-release).
2. **At least 90 days old** at the time of selection (measured from the tag's commit date, not the PyPI upload date).
3. **GPG-signed tag** if the maintainer signs tags. If they do not, use commit-SHA-pinning + the multi-source-hash cross-verification of §2.4.

The 90-day floor is the post-Mini-Shai-Hulud version of the old 30-day rule. Account-compromise timing is uncertain; 90 days gives the community time to surface a compromised release through downstream consumer noise.

### §2.3 Source retrieval

For a tagged release at the chosen tag:

```yaml
source:
- url: https://github.com/<org>/<repo>/archive/<tag>/<repo>-<tag>.tar.gz
  sha256: <pinned sha256>
```

GitHub's `/archive/<ref>/<filename>` endpoint produces a deterministic tarball as long as the ref resolves to a single commit. The filename slug at the end is cosmetic but MUST be present for InterGenOS's `phase_verify_sources` to write the file under a stable name.

For commit-SHA pinning (when the maintainer does not tag-sign):

```yaml
source:
- url: https://github.com/<org>/<repo>/archive/<full-40-char-sha>.tar.gz
  sha256: <pinned sha256>
```

NEVER use `master.tar.gz` or any moving ref. The pin must point at a single immutable commit object.

### §2.4 Multi-source-hash cross-verification

The pinned sha256 in §2.3 MUST be cross-verified against AT LEAST TWO independent observations of the same artifact before commit:

1. **Local fetch + sha256sum** — `curl -fL <url> | sha256sum`. Captures GitHub's archive-time hash.
2. **Author signature** — if maintainer signs tags, `git tag -v <tag>` after importing the maintainer's pubkey from `keyserver.ubuntu.com` (or a documented alternative keyserver). Records the maintainer's claimed source-tree state.
3. **Third-party mirror** (when one exists) — Debian's `dpkg-source` archive, Arch's `community/` source-tarball mirror, Fedora's source dist-git. Adds a non-attacker-controlled witness to the artifact's content.
4. **Reproducible-build cross-verification** — *aspirational, applicable only where upstream supports reproducible builds.* If the upstream project produces byte-identical artifacts from a given source tree, an independent re-build hashing to the published value is a genuinely adversary-independent witness. Most pure-Python projects don't support this today; record its absence in the commit message when not available. Named here so the doc has a target for the trust posture §1 is rejecting in SLSA + Sigstore form.

**Minimum witness counts:** (1) + (2) is enough only when no third-party mirror exists. Whenever a third-party mirror IS available, (1) + (2) + (3) must be used — (1) and (2) share the maintainer's trust-root (a compromised maintainer can sign a poisoned tag AND the GitHub archive of the poisoned source will hash to whatever GitHub computes from it), so (3) is the only adversarially-independent witness shape in the common case. For non-tag-signed packages, (1) + (3) is the minimum, with the absence of (2) called out in the package's `build.sh` header comments. (4), when available, can substitute for either (2) or (3).

Capture the verification trace in the commit message body for the package add/rewire commit. This is the audit trail an external reviewer reads when validating the source pin.

### §2.5 setup.py / pyproject.toml audit-before-stage

Before any tarball lands in `build/sources/`, audit:

1. **`pyproject.toml` runtime deps** — every entry in `[tool.poetry.dependencies]` / `[project] dependencies` / `setup.py install_requires`. Each becomes an input to §2.6 transitive inventory.
2. **`pyproject.toml` build-system requirements** — `[build-system] requires` MUST resolve to packages we already ship. If `setuptools` / `wheel` / `hatchling` are the only entries, fine — those are core build tools in our chroot. If a package introduces a NEW build-system requirement we do not yet ship, that requirement gets its own per-package recipe via this same 8-step procedure BEFORE the package consuming it can be built.
3. **`setup.py` ext_modules** — if present, the package has compiled extensions. Go to §2.7 for the `BUILD_EXTENSION=no` determination. The maturin package handles its Rust extension this way via `--no-default-features`; the websockets package handles its optional C extension via `BUILD_EXTENSION=no` per the locked decision below.
4. **Build hooks** — `[tool.poetry.scripts]` entries that run during install, `setup.cfg cmdclass = {...}` overrides, `setup.py` top-level imports of suspicious modules (`requests`, `urllib`, `socket`, `subprocess.Popen` outside obvious build invocations). Anything that fetches over the network during build is a recipe defect. Read it. Don't run it blindly.
5. **`.pre-commit-config.yaml`** + GHA workflows — these are not load-bearing for our build but they are observable artifacts of the project's release discipline. A project with no CI + no signed tags is one to escalate per §3, not work around.

Capture the audit results inline in the package's `build.sh` header comment block — the same place maturin's build.sh narrates its Cargo feature-strip rationale.

### §2.6 Transitive-deps inventory + reconciliation

This step exists because direct-import grep methodology systematically undercounts the install-time-resolved dependency set. A concrete example: the `rich` package declares `pygments` as a hard runtime dependency in its `pyproject.toml`, but no InterGen code directly imports pygments, so a direct-import-only walk misses the install-time-pulled set.

For every package admitted to the recipe set, enumerate:

1. **Direct runtime deps** — per the §2.5 audit's `[project] dependencies` block.
2. **Transitive runtime deps** — for each direct dep, recurse the same audit until the dep tree terminates at stdlib + already-shipped packages.
3. **Reconciliation** — every node in the transitive tree gets exactly one of:
   - (a) **Shipped** — itself runs through this same 8-step recipe and is admitted under `packages/extra/<surface>/` or `packages/core/<dep>/`.
   - (b) **Verified-unused** — empirically demonstrated that no InterGen code imports the surface that touches this dep. Captured as a per-package CI guard per §4 (so a subsequent code change cannot silently introduce the dep at runtime).
   - (c) **Excluded with documented reason** — for example, an optional-extra that only matters in a context InterGenOS does not ship. Documented inline in the consumer's `build.sh` header.

The default disposition is (a) shipped. (b) and (c) are exceptions that must be justified per-instance.

### §2.7 Build environment

The canonical `pip wheel` invocation for any pure-Python package built from a GitHub-sourced tarball:

```bash
export BUILD_EXTENSION=no                                # for any package that ships optional C extensions
export SOURCE_DATE_EPOCH=$(git -C <source-dir> log -1 --format=%ct)   # deterministic wheel timestamps
pip3 wheel \
    --wheel-dir dist \
    --isolated \
    --no-build-isolation \
    --no-deps \
    --no-index \
    --no-cache-dir \
    --verbose \
    .
```

Each flag is load-bearing:

- **`--isolated`** — ignores user-site, ignores environment-variable overrides for index URLs.
- **`--no-build-isolation`** — `pip` will not silently spawn a sub-environment that fetches from PyPI to satisfy `[build-system] requires`. Those requirements must already be present in the chroot.
- **`--no-deps`** — do not resolve runtime deps. Each one is handled by its own recipe in §2.6.
- **`--no-index`** — even if a `pip.conf` lives somewhere with an `index-url` value, this flag short-circuits any HTTP fetch.
- **`--no-cache-dir`** — the wheel is rebuilt from the source tarball, not pulled from a stale cache.
- **`--verbose`** — emit each build step to stdout for forensic capture. Recipe `build.sh` scripts should redirect this to a per-package log captured by `phase_verify_sources` (or equivalent) for post-build inspection.

And the env vars:

- **`BUILD_EXTENSION=no`** — covered in detail below; forces pure-Python fallback when upstream supports it.
- **`SOURCE_DATE_EPOCH`** — without this, wheel timestamps embed the build-host wall-clock; the wheel's sha256 varies across build hosts even with identical source. Pinning to the source commit's timestamp gives byte-identical wheels across reproducible-builds.org-style invocations. Native support in setuptools / pip wheel / flit / hatchling.

For `BUILD_EXTENSION=no`: a package that ships an optional C extension via `setup.py ext_modules` typically follows a pattern like `websockets`'s:

```python
if os.environ.get("BUILD_EXTENSION") == "no":
    ext_modules = []
else:
    ext_modules = [setuptools.Extension(..., optional=...)]
```

Default behavior (env var unset) is "build if compiler available." That means the wheel content depends on the build host's state — non-deterministic. Setting `BUILD_EXTENSION=no` in the recipe forces the pure-Python fallback path, eliminates the C trust-root we would otherwise need to audit, and gives a wheel that is byte-identical regardless of whether the build host has gcc installed.

**Project decision (2026-05-28):** every package that ships optional C extensions gets `BUILD_EXTENSION=no` (or the equivalent env-var/opt-out the upstream supports). The marginal runtime performance cost is negligible at our load profile; the C trust-root reduction is real. See §4 for the pre-commit gate that enforces this.

### §2.8 Per-package instantiation

The instantiation lives under `packages/<tier>/<pkg>/` with these files:

- **`package.yml`** — metadata. Fields:
  - `name`, `version`, `release`, `description`, `license`, `homepage`
  - `tier` — `extra` for user-facing app deps (the InterGen web UI surface lands here); `core` for build-time deps required by other packages
  - `build_style: custom`
  - `install_func: do_install`
  - `source:` block per §2.3
  - `dependencies.build:` — runtime deps already shipped (these are NOT resolved at install time; the build host needs them to construct the wheel)
  - `dependencies.runtime:` — packages this one needs at runtime, all of which themselves go through this recipe
  - `verify_paths:` — paths that must exist post-install for the install to be considered successful (`scripts/preflight-silent-loss.py` enforces)
- **`build.sh`** — Bash recipe. Header comment block:
  - SPDX line + copyright
  - One-paragraph WHY summary (what the package is, why we ship it, what the upstream maintainer's posture is)
  - GPG-tag verification trace if applicable (key fingerprint, signing identity, when imported)
  - C-extension opt-out rationale if applicable
  - PyPI source-bypass rationale linking back to §1
  - Build-hook audit results from §2.5
- **`configure()` / `build()` / `do_install()` / `check()`** — see maturin's build.sh for the canonical shape.

The `do_install` function MUST mint a minimal PEP 517 `.dist-info` directory if any downstream package resolves this one via `pip install <name>>=<min>`. Without `.dist-info`, `pip`'s version resolver will silently consider the package not-installed and try to fetch from PyPI. The maturin precedent (the METADATA + WHEEL + entry_points.txt + RECORD heredocs near the end of its build.sh) is the canonical template — adapt the field values to the package's actual metadata.

---

## §3. Failure modes + escalation

A package may genuinely resist this recipe. The patterns:

1. **No source forge.** The package is published ONLY on PyPI. Escalate to the project maintainers with the package name, the consuming InterGen surface, and a brief plain-English statement of the gap. Do NOT work around it. Do NOT fetch from PyPI "temporarily."
2. **Source forge but no signed tags and no third-party witness.** §2.4 (1) is still possible (sha pin via local fetch) but the verification is single-witness. Surface the gap explicitly; the project maintainers decide whether to accept the single-witness pin, find an alternative package, or drop the feature.
3. **`[build-system] requires` introduces a wholly new build-system dep we do not ship.** Two sub-paths: (a) the build-system dep itself goes through this same 8-step recipe BEFORE the consuming package; (b) we use a different upstream package that does not need the new build system. Surface both options enumerated.
4. **The package's `setup.py` runs network code during install.** This is a recipe-blocker, not a recipe-bug. The package is unsafe to build at any sourcing path until that code is either patched out (via a local quilt-style patch we maintain) or the maintainer fixes it upstream. Surface to the project maintainers.
5. **The package legitimately requires a C extension that has no pure-Python fallback.** Then it is not a pure-Python package and this recipe does not apply. Escalate; the package needs a different recipe pattern (closer to `packages/core/maturin/`'s Rust pipeline, or a vendored static-archive / pre-built-artifact approach).
6. **Repo lifecycle change: the package was on GitHub but moved or stopped.** Sub-shapes: (a) repo deleted or returns 404; (b) repo archived read-only with no successor named; (c) maintainer transferred ownership to a new account; (d) abandoned — last release more than 2 years old and maintainer non-responsive to security issues. Each of these is materially different from §3.1 (no-forge-from-the-start). Surface the package's last-known-good state, the lifecycle signal observed, and a recommendation: alternative-source (a fork by a different maintainer who took up active maintenance), alternative-package (replace the dependency), or drop-feature.
7. **Maintainer key rotation without a revocation certificate.** The maintainer publishes a new signing key but never publishes a revocation cert for the old one. Old tags still verify under the old key and new tags verify under the new key; without a transition note signed by both keys, there is no clear signal which key is authoritative going forward. Surface to the project maintainers. Treat the new key as untrusted until either (a) the maintainer publishes a key-transition note OR (b) a third-party witness (distro packaging, sigstore attestation history, mailing-list announcement archived independently) confirms the rotation was the maintainer's own.

Standing rule: if a package genuinely cannot be re-sourced through this recipe (no GitHub release, unsigned, no alternative), surface it to the project maintainers rather than working around it. Reaching for a workaround means the recipe has silently shifted to a different threat model than the one this document was written for.

---

## §4. Pre-commit gates

Two gates are mandatory for any commit that adds or modifies a recipe under this pattern:

### Gate 1 — `BUILD_EXTENSION=no` enforcement

`scripts/check-build-extension-opt-out.sh` scans `packages/*/<pkg>/build.sh` for any invocation of `pip wheel` / `python setup.py bdist_wheel` / `python -m build` where the matching source tarball at `build/sources/<pkg>-*/setup.py` declares an `ext_modules` block. If a build.sh meets both conditions but does not export an explicit `BUILD_EXTENSION=no` (or the upstream-equivalent opt-out variable) before the build invocation, the gate fails. Recipes whose source tarball isn't unpacked at gate-run time are skipped (the gate re-fires later at build-time, when `phase_verify_sources` has populated `build/sources/`).

The gate is principle-driven: any recipe that *could* compile a C extension on a compiler-present build host must explicitly state "no compiled output" in the recipe.

### Gate 2 — Forbidden-import guards for verified-unused transitive deps

For each transitive dep dispositioned as §2.6(b) verified-unused (none in the initial 4-package wave, but the pattern needs to exist for subsequent use), the recipe MUST install a CI guard that flunks any new import of the surface that would re-introduce the dep at runtime. `scripts/check-forbidden-imports.sh` ships alongside this doc with empty-input early-exit; per-recipe entries get added as `§2.6(b)` dispositions activate.

A single grep pattern is insufficient because Python admits multiple import grammars for the same module. A forbidden-surface entry must cover ALL of:

- `from X import ...` — canonical form
- `import X` and `import X as Y` — direct binding form
- `__import__("X")` — dynamic-import builtin
- `importlib.import_module("X")` — reflective-import via the importlib facade

For example, a forbidden-`rich.syntax` entry would enumerate four grep patterns (one per form). For richer coverage, an ast-walk-based detector is the principled upgrade — surfaces every import-discovery path including conditional imports inside function bodies. Recommend ast-walk when the verified-unused set grows past two entries.

The initial 4-package wave (websockets, prompt-toolkit, rich, pygments) does NOT need any active forbidden-import entry because the locked decision ships pygments as a full package (the §2.6(a) shipped disposition). The script's empty-input state is the canonical "no active guard" baseline.

---

## §5. Commit cadence and review

Per-package re-sourcing work follows a consistent commit discipline:

- One commit per package, carrying its `package.yml`, `build.sh`, and `sources/` entry together.
- Every commit is reviewed before it is pushed, and the final merge to `master` is an explicit gate.
- Commits may be class-batched: if two packages share a shape (for example, both ship optional C extensions, or both share the same upstream maintainer's signing key), commit them together. Two shared shapes in one commit is the threshold; three would be over-batching.

Keeping the change set small and reviewable per commit is what makes the source-pin verification trace (§2.4) auditable after the fact.

---

## §6. Worked example — the maturin precedent

[`packages/core/maturin/`](../../packages/core/maturin/) is the first instantiation of this pattern in the codebase, predating this doc. The recipe was authored 2026-05-12 during the Mini Shai-Hulud window. Re-reading it against this doc's §2:

- **§2.1 Source identification:** GitHub `PyO3/maturin`, maintainer @messense, signing key `BB41A8A2C716CCA9` (RSA-4096 from 2015-06-22).
- **§2.2 Version selection:** the pinned tag (see `packages/core/maturin/package.yml`) satisfied the then-current 30-day floor when the recipe was authored on 2026-05-12. It is grandfathered under the 90-day floor instituted 2026-05-28; the recipe stands as-is rather than churning the precedent to satisfy a rule it predates.
- **§2.3 Source retrieval:** `https://github.com/PyO3/maturin/archive/v${version}/maturin-${version}.tar.gz` — exactly the canonical shape.
- **§2.4 Multi-source-hash cross-verification:** sha256 + GPG-verified tag (`git tag -v <tag>` after key import). Two witnesses.
- **§2.5 Audit:** the build.sh header documents the `--no-default-features` Cargo feature-strip rationale (drops `ureq` + `rustls` + `native-tls` HTTP/TLS deps that the PEP 517 entry point does not use).
- **§2.6 Transitive-deps inventory:** Cargo's `--offline` + a vendored crate tarball (the `build_artifacts:` entry) handles transitive resolution at the Rust layer. The Python layer has no runtime deps.
- **§2.7 Build environment:** `cargo build --release --no-default-features --frozen --offline`. The `--frozen` + `--offline` pair is the Cargo analog of `pip wheel --no-index --no-cache-dir`.
- **§2.8 Per-package instantiation:** `package.yml` + `build.sh` + the `build_artifacts:` block declaring the vendor tarball as a generated dep. `do_install` mints minimal `.dist-info` so `cryptography`'s PEP 517 backend resolves the maturin version it needs.

Maturin is a Rust+Python hybrid, so §2.7's `BUILD_EXTENSION=no` does not apply directly — the C analog is the Cargo feature-flag strip. The shape is otherwise identical to what a pure-Python package recipe should look like.

---

## §7. Worked decisions for the initial 4-package set

The PyPI prohibition's first applied wave was the InterGen web UI and console re-sourcing, scoped and reviewed on 2026-05-28. The decisions:

- **Approach:** drive toward zero PyPI at all layers — replace `aiohttp` with `websockets`, split the console dependencies (`prompt-toolkit`, `rich`, `pygments`) into their own packages, and re-source each retained package from GitHub directly as it is re-wired. That is the target end-state. **It is only partially realized:** the `websockets` package was created and is GitHub-sourced, but `aiohttp` has not yet been removed (see the HTTP-server-replacement note below), and most of the split-out console dependencies are still PyPI-sourced. The per-package status below records what is done versus outstanding.
- **GitHub-sourced (re-sourcing complete):**
  - `websockets` — `python-websockets/websockets` (`packages/extra/websockets/`) — WebSocket transport for the InterGen web server and console client. Its `package.yml` `source:` URL is a GitHub archive tag.
- **PyPI carry-overs (still awaiting re-sourcing under this same 8-step procedure):** each of these packages exists and builds, but its `package.yml` `source:` URL still points at PyPI (`files.pythonhosted.org`), not a GitHub tag. They are tracked carry-overs, not yet GitHub-sourced:
  - `prompt-toolkit` (`packages/core/prompt-toolkit/`) — terminal REPL primitives for the InterGen console shell. Transitively pulls `wcwidth`.
  - `rich` (`packages/core/rich/`) — terminal Panel/Table/Text/Console rendering for the InterGen console shell. Transitively pulls `markdown-it-py` and `mdurl`.
  - `wcwidth`, `markdown-it-py`, `mdurl` (`packages/core/`) — transitive dependencies of the above, each with its own recipe.
  - `pygments` (`packages/core/pygments/`) — a required runtime dependency of `rich` per Textualize's `pyproject.toml`, shipped as a full package per the §2.6(a) shipped disposition.
- **C-extension opt-outs:** `BUILD_EXTENSION=no` for `websockets` per §2.7. The other three packages do not ship C extensions per their `pyproject.toml` audits.
- **HTTP-server replacement (decided direction; not yet implemented):** the decided plan is to replace `aiohttp.web.Application` and its middleware with the Python standard-library `http.server` for the REST endpoints and static-file serving, adding no new pure-Python HTTP server library. **As of this writing the migration is outstanding:** `intergen/web_server.py` and `intergen/console/client.py` still use `aiohttp` (`aiohttp.web` for the server, `aiohttp.ClientSession`/WebSocket for the console client), and `aiohttp` remains a declared dependency of the `intergen` package. When implemented, end-user behavior stays equivalent at the cost of more verbose handler code, the CSP/auth/CSRF hardening re-implements inline in the standard-library handlers, and the token-out-of-URL handshake-then-auth pattern moves to the `websockets` protocol layer.
- **Alternatives considered and rejected:** `h11` plus a sansio-style request/response adapter is the closest pure-Python alternative to standard-library `http.server` that is also GitHub-sourceable (`python-hyper/h11`, pure-Python, well-maintained). It would produce cleaner handler code, but it adds a GitHub-sourced package and its transitive surface to inspect and maintain. The trade-off: the standard library gives zero new dependencies at the cost of more verbose handlers; `h11` gives cleaner handlers at the cost of one more package recipe. The chosen posture is the standard library, for the surface-area-minimization win. Documented here so the alternatives-considered chain stays auditable.

Transitive dependencies (`wcwidth`, `markdown-it-py`, `mdurl`, in `packages/core/`) get their own per-package recipes via this same 8-step procedure. Of this wave, only `websockets` is GitHub-sourced today; `prompt-toolkit`, `rich`, and the transitive dependencies (`wcwidth`, `markdown-it-py`, `mdurl`), plus `pygments`, are still PyPI carry-overs whose `package.yml` `source:` URLs point at `files.pythonhosted.org`. Each carries its own per-package recipe and is tracked for re-sourcing from GitHub under this same 8-step procedure.

---

## §8. Reference + cross-pointers

- [`packages/core/maturin/`](../../packages/core/maturin/) — worked precedent
- [`docs/VISION.md`](../VISION.md) — project philosophy and the two questions every design choice answers

External references (for the §1 empirical basis):

- https://www.tenable.com/blog/mini-shai-hulud-frequently-asked-questions
- https://phoenix.security/teampcp-github-breach-durabletask-pypi-supply-chain-wave-four-2026/
- https://unit42.paloaltonetworks.com/monitoring-npm-supply-chain-attacks/
