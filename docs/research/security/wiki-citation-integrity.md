# Verified wiki citations + the offline wiki package

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Status:** DESIGN + initial implementation (feature branch).
**Landing:** sequenced after review and the signed-release step below.

## 1. What this makes true

The wiki front page claims the local assistant "links you to the exact canonical
page it drew from … verifies a page's integrity before citing it and refuses to
cite on a mismatch." Today only half of that is real: every curated how-to entry
(`intergen/howto.py`) already carries a `doc_source` — the wiki page its answer is
pinned to at authoring time — but nothing consumes it. There are **no** user-facing
links and **no** per-cite integrity check (verified against dev `6207f976`; the
front-page wording was reworded to the honest present in the wiki wave, with the
link + integrity halves marked *planned*). The capability was decided IN.

This arc ships the capability in three coupled pieces:

- **(a) Surface** — a curated answer now ends with a `Source:` line linking the
  **local installed wiki page** (primary, fully offline — the Prime Directive) and
  the **canonical `wiki.intergenos.org` URL** (secondary).
- **(b) Per-cite integrity (L2)** — a page is linked ONLY after its shipped bytes
  verify against an **operator-signed per-page sha256 manifest**. Fail-closed:
  tampered/unsigned pages are never cited.
- **(c) The `intergenos-wiki` package (L1)** — the rendered mdBook `book/` HTML
  ships in the ISO (`iso_include:true`) as a first-party generated
  tarball, so every install carries the docs — and their signed manifest — day one.

## 2. Threat model — "undone by our own documentation"

InterGen is trusted by the user. When it cites a doc page as *the canonical
source*, it lends that page its own authority. So the doc tree becomes an
**attack surface**: an adversary who alters an installed wiki page — a page that
teaches an install command, a verification step, a recovery procedure — and gets
InterGen to relay it as authoritative has laundered malicious content through the
assistant's trust. The wiki is world-readable and (post-install, via an overlay
write or a swapped pkm package) potentially writable outside the read-only
squashfs. Concretely the class is: *content substituted into what InterGen trusts
and faithfully repeats*.

dm-verity protects the read-only squashfs at rest, but it is **not** the whole
answer: it does not cover an overlay-shadowed copy, a pkm-upgraded wiki package
landing on the writable layer, or a tarball tampered before it was signed. The
gate that kills the class regardless of where the page physically lives is a
**per-page hash pinned to the operator's key**, checked at the moment of citation.
That is (b).

## 3. The pin point (grounded + stated)

The per-page sha256 is computed **at release time over the exact rendered `book/`
HTML that ships in the tarball**, and the runtime check hashes the **shipped
read-only copy** at cite time and compares. The pin therefore binds *the page
bytes the operator signed* ⟷ *the page bytes present when InterGen is about to
cite*. This is deliberately the **shipped-copy** end, not a build-time-only
assertion: the value of the check is exactly that it re-proves the page at the
point of use, so an alteration introduced *after* signing (the overlay / upgrade /
supply-chain-before-verity cases above) is caught. It is defense-in-depth **over**
dm-verity and rooted in the operator key, independent of the filesystem's own
trust. Images/fonts/css are not cited and are not in the manifest; only the HTML
pages InterGen can name are pinned.

## 4. Trust chain — one implementation, fail-closed at every step

(b) reuses the never-list precedent verbatim rather than re-implementing crypto
(`intergen/destructive_policy.py`; T0-4-D / pair-atomic discipline):

- The signed manifest (`pages-manifest.json` + detached `.asc`) is loaded
  **pair-atomically** and verified with `gpgv --keyring /etc/pkm/trusted.gpg`
  against the **pinned operator primary-key fingerprint** (`OPERATOR_FINGERPRINT`,
  the VALIDSIG last field), read-once (no verify-then-parse TOCTOU). This arc adds
  one thin public wrapper, `load_verified_manifest_status`, exposing the existing
  generic loader with its `LOADED / ABSENT / UNTRUSTED` outcome — so the gpgv + pin
  + read-once logic has exactly **one** implementation (no fourth copy of the
  VALIDSIG parsing).
- **UNTRUSTED** (manifest present but signature/key/JSON bad) → all citation
  disabled, logged LOUD (tamper). **ABSENT** (no manifest — a from-source box with
  no wiki package) → citation quietly off (benign). This is the PI-D distinction.
- Per page: hash the shipped file, compare to the manifest's pinned value. No pin
  (unsigned page), read error, or **mismatch** → refuse to cite that page (mismatch
  logged loud). Never cite an unverified page.

`intergen/wiki_citations.py` holds the gate + the `doc_source` normalizer;
`Router._try_explain` (`intergen/router.py`) appends the verified citation to the
curated answer. The path is **model-independent**: `_try_explain` serves
`entry.answer` verbatim on the 2B floor (no model, no native dispatch), so
citations work on every hardware tier. Construction is guarded like the corpus —
a citation-subsystem failure degrades to no-citations, never a router startup risk.

### `doc_source` normalization

`doc_source` is free-form and may join references with `;` (e.g.
`"wiki packages/package-manager.md; docs/users/package-management.md"`). The
normalizer takes the **first `wiki `-marked** reference as the canonical page,
ignores private `docs/` references (not shipped, never cited), and maps mdBook's
output: `<path>.md → <path>.html`, `README.md → index.html`,
`<dir>/README.md → <dir>/index.html`. A source that names no wiki page yields no
citation.

## 5. The package + the signed-release event

`packages/desktop/intergenos-wiki/` — `tier: desktop` (auto-enumerated by the
Python tier driver, `iso_include` defaults true), `source: generated: true` (no
sha-pin — regenerate-and-stage). A **standalone** `scripts/build-intergenos-wiki-tarball.sh`
(wired into `build-intergenos.sh`; kept separate from the shared source-tarball
generator so it never enters the 8 theming packages' `source_tree` input surface)
tars the rendered `book/` + the committed
`pages-manifest.json` + its `.asc` into the package tarball; `build.sh` installs
all three read-only under `/usr/share/doc/intergenos/wiki/`. The archive joins the
**ceremony-#1 archive-integrity manifest automatically** (`phase_manifest` hashes
every built archive — no per-package wiring, no new ceremony).

**Two integrity layers, not one:** the *archive* rides ceremony #1 (the tarball's
sha256, signed at the pre-squashfs pause). The *per-page* manifest is a separate,
package-internal artifact signed with the operator key — that is what enables the
runtime cite-time check, which the archive hash cannot do.

**Content is release-staged, not git-vendored.** The rendered `book/` is ~75 MB
(63 MB of screenshots) and lives in the separate wiki repo. Vendoring it into the
OS repo is neither lean nor necessary. Instead:

1. The wiki repo's `mdbook build` output is staged (default
   `$REPO_ROOT/build/wiki-book`, `IGOS_WIKI_BOOK_DIR` overrides).
2. `scripts/build-wiki-page-manifest.py book/ pages-manifest.json` regenerates the
   per-page hash map (deterministic — sorted, byte-stable), committed under the
   package dir. `source_tree` lists it, so `content_hash` tracks the content
   identity via the manifest without hashing the 75 MB.
3. The operator signs it → `pages-manifest.json.asc` (committed). **This is the
   "signed-release event": each wiki-content refresh re-generates + re-signs the
   manifest** (the docs join the trust boundary; an accepted cost).
4. The generator **fails closed** if the committed manifest does not match the
   staged `book/` (the signature would not cover the shipped pages), and SKIPs
   (does not fabricate content) when the staged `book/` or the signed manifest is
   absent.

### Landing prerequisites (stated dependency, not a hold)

Because the package builds in the desktop tier, before it lands on the burn's
`dev` the signed-release step must be done: (i) stage the current `book/`;
(ii) regenerate `pages-manifest.json` from it (`scripts/build-wiki-page-manifest.py`);
(iii) the operator signs `pages-manifest.json.asc`; (iv) commit both under the
package dir, add them to `source_tree`, and add a **HEX-SECRET path-exemption** for
`pages-manifest.json` in the public-content audit (the file is by design a list of
public sha256 hashes; the exemption is authorized under the project's build rules —
which is exactly why it is NOT taken in this author branch). Until (iii)/(iv), the
generator SKIPs and the package is not built — so a premature merge cannot silently
ship unsigned docs. The change lands post-halt via the normal review-and-merge path
with these in place. This author branch
therefore ships the mechanism (generator, integrity gate, recipe, tests) but not
the churny hash manifest itself; `source_tree` fingerprints the generator tool
until the manifest joins it at release.

## 6. Tests

`intergen/tests/test_wiki_citations.py` (13, injected `gpg_verify` — no real key):
- **Normalizer**: primary wiki-page extraction, README→index, section README,
  private-`docs/`-ignored, non-wiki → none.
- **Integrity GREEN**: a verified page is cited with the local + canonical links.
- **Integrity RED (the security assertion)**: a page mutated after the manifest
  loaded is **refused** (no citation).
- **UNTRUSTED / wrong-key manifest** → all citation disabled; **ABSENT** → quiet
  off; **unsigned page not in manifest** → refused; non-wiki source → not cited.
- **Generator**: hashes only HTML, deterministic run-to-run.

`destructive_policy` (32) and howto/router (13) suites stay green; the new package
passes `check-builder-coverage.py` (0 orphans, 0 tier mismatches).

## 7. File map

| Concern | File |
|---|---|
| Integrity gate + normalizer + citation format | `intergen/wiki_citations.py` |
| Reused signed-manifest loader (+ new status wrapper) | `intergen/destructive_policy.py` |
| Citation surfacing (curated answer) | `intergen/router.py` `_try_explain` / `_cite_source` |
| Web anchor for the allow-listed citation links | `intergen/web/app.js` `renderMarkdownSafe` |
| Per-page manifest generator | `scripts/build-wiki-page-manifest.py` |
| Package | `packages/desktop/intergenos-wiki/{package.yml,build.sh,pages-manifest.json}` |
| Tarball generator (standalone) | `scripts/build-intergenos-wiki-tarball.sh` (wired into `build-intergenos.sh`) |
| Tests | `intergen/tests/test_wiki_citations.py` |
