# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Verified wiki citations for curated how-to answers.

Every curated :class:`~intergen.howto.HowtoEntry` carries a ``doc_source`` — the
wiki page its answer is pinned to (authoring-time anti-drift). This module turns
that string into a user-facing CITATION: a link to the LOCAL installed wiki page
(primary) plus the canonical ``wiki.intergenos.org`` URL (secondary), so a user
can always open the exact source InterGen drew from.

SECURITY — refuse to cite tampered docs (the reason this is not just string
formatting). The wiki HTML ships in the ``intergenos-wiki`` package and is world-
readable; an attacker who slipstreams modified content into that installed tree
would have InterGen relay it as the authoritative source — "undone by our own
documentation." So a page is cited ONLY after its bytes verify against a per-page
sha256 manifest the operator SIGNED at release. Trust chain, fail-closed at every
step, mirroring the never-list precedent (:mod:`intergen.destructive_policy`):

  1. The signed manifest (``pages-manifest.json`` + detached ``.asc``) is loaded
     PAIR-ATOMICALLY and verified with ``gpgv`` against the pinned operator key
     (:func:`intergen.destructive_policy.load_verified_manifest_status`). A
     present-but-unverifiable manifest (tamper/corruption) DISABLES all citation
     and logs LOUD; a simply-absent manifest (dev/from-source box with no wiki
     package) is quiet.
  2. Before a specific page is cited, its shipped bytes are sha256'd and compared
     to the manifest's pinned hash. No pin, a read error, or a MISMATCH → refuse
     to cite that page (and log loud on mismatch). Never cite an unverified page.

PIN POINT (grounded + stated): the manifest hash is computed at RELEASE time over
the exact rendered ``book/`` HTML that ships in the tarball, and the runtime check
is against the SHIPPED read-only copy. The pin binds *the page bytes the operator
signed* to *the page bytes present at cite time* — defense-in-depth over dm-verity
(it also covers an overlay-shadowed or pkm-upgraded copy) and rooted in the
operator key, independent of the filesystem's own trust.

Model-independent: this runs on the curated-answer path (:mod:`intergen.router`
``_try_explain``), which serves ``entry.answer`` verbatim on the 2B floor — no
model, no native dispatch. Citations work on every tier.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from intergen.destructive_policy import (
    OPERATOR_FINGERPRINT,
    GpgVerify,
    PolicyLoad,
    load_verified_manifest_status,
)

logger = logging.getLogger(__name__)

# Resolution order (mirrors howto.py / model_manager): explicit env override ->
# the shipped system dir. The intergenos-wiki package installs the rendered book/
# HTML + the signed page manifest here (read-only, on dm-verity). There is no
# in-repo dev copy (the wiki is a separate repo), so a from-source box with no
# wiki package installed simply has citations OFF (a quiet ABSENT manifest).
_ENV_DIR = "INTERGEN_WIKI_DIR"
_SYSTEM_DIR = Path("/usr/share/doc/intergenos/wiki")
_MANIFEST_NAME = "pages-manifest.json"
_SIG_SUFFIX = ".asc"

# The canonical public mirror of the same content — the SECONDARY citation link.
WIKI_URL_BASE = "https://wiki.intergenos.org/"

_CHUNK = 1 << 20  # 1 MiB streaming read


def _default_doc_root() -> Path:
    env = os.environ.get(_ENV_DIR)
    if env and Path(env).is_dir():
        return Path(env)
    return _SYSTEM_DIR


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _title_from_slug(slug: str) -> str:
    """A readable citation label from a page slug (last path segment).

    ``package-manager`` -> ``Package Manager``; ``faq`` -> ``Faq``. Deterministic
    and dependency-free — the exact <title> is not read (that would mean trusting
    the very bytes we are gating)."""
    words = slug.replace("-", " ").replace("_", " ").split()
    return " ".join(w[:1].upper() + w[1:] for w in words) if words else slug


def wiki_page_from_doc_source(doc_source: str) -> "tuple[str, str] | None":
    """Extract the PRIMARY wiki page from a curated answer's ``doc_source``.

    ``doc_source`` is free-form and may join several references with ``;`` (e.g.
    ``"wiki packages/package-manager.md; docs/users/package-management.md"``). A
    wiki reference is marked by a leading ``wiki `` token; the private ``docs/``
    references are not shipped and are never cited. Returns
    ``(rel_html_path, title)`` for the first wiki reference, or ``None`` when the
    source names no wiki page.

    Mapping mirrors mdBook's output: ``<path>.md`` -> ``<path>.html``,
    ``README.md`` -> ``index.html``, ``<dir>/README.md`` -> ``<dir>/index.html``.
    """
    if not doc_source:
        return None
    for seg in doc_source.split(";"):
        seg = seg.strip()
        if seg[:5].lower() == "wiki ":
            md = seg[5:].strip().lstrip("/")
            if md:
                return _md_to_page(md)
    return None


def _title_for_page(rel_html: str) -> str:
    """A citation label from a rendered page path (the retrieval counterpart to
    :func:`_md_to_page`, which starts from the ``.md`` source). ``index.html`` ->
    the wiki root; ``<dir>/index.html`` -> the section; else the page slug."""
    stem = rel_html[:-5] if rel_html.endswith(".html") else rel_html
    if stem == "index":
        return "InterGenOS Wiki"
    if stem.endswith("/index"):
        return _title_from_slug(stem[: -len("/index")].rsplit("/", 1)[-1])
    return _title_from_slug(stem.rsplit("/", 1)[-1])


def _md_to_page(md: str) -> "tuple[str, str]":
    base = md[:-3] if md.endswith(".md") else md
    if base == "README":
        return "index.html", "InterGenOS Wiki"
    if base.endswith("/README"):
        section = base[: -len("/README")]
        return f"{section}/index.html", _title_from_slug(section.rsplit("/", 1)[-1])
    return f"{base}.html", _title_from_slug(base.rsplit("/", 1)[-1])


class WikiCitations:
    """Cite a curated answer's wiki page — only after verifying it hasn't changed.

    Construct once (guarded, like the howto corpus); reused per turn. With no
    signed manifest installed (dev box), :attr:`available` is False and
    :meth:`cite` returns ``None`` for every source — citations are simply off, not
    an error. With a PRESENT-but-unverifiable manifest, citations are off AND the
    reason is logged LOUD (that is tamper, not absence)."""

    def __init__(
        self,
        *,
        doc_root: "str | os.PathLike[str] | None" = None,
        url_base: str = WIKI_URL_BASE,
        fingerprint: str = OPERATOR_FINGERPRINT,
        gpg_verify: GpgVerify | None = None,
    ) -> None:
        self._root = Path(doc_root) if doc_root is not None else _default_doc_root()
        self._url_base = url_base
        manifest_path = self._root / _MANIFEST_NAME
        sig_path = str(manifest_path) + _SIG_SUFFIX
        manifest, outcome = load_verified_manifest_status(
            str(manifest_path), sig_path, fingerprint=fingerprint, gpg_verify=gpg_verify
        )
        self._outcome = outcome
        self._pages: dict[str, str] = {}
        if outcome is PolicyLoad.LOADED and isinstance(manifest, dict) \
                and isinstance(manifest.get("pages"), dict):
            self._pages = {str(k): str(v) for k, v in manifest["pages"].items()}
            logger.info("wiki-citations: verified page manifest loaded (%d pages)",
                        len(self._pages))
        elif outcome is PolicyLoad.UNTRUSTED:
            # Present but the signature/hash-map could not be trusted: tamper or
            # corruption. Fail LOUD and cite nothing — never launder unverifiable
            # docs through InterGen's voice.
            logger.error(
                "wiki-citations: the wiki page manifest is PRESENT but did not "
                "verify against the pinned operator key (tamper or corruption). "
                "Citations are DISABLED — refusing to cite any page until the "
                "signed manifest verifies.")
        # ABSENT -> quiet: no wiki package installed (dev/from-source). Citations
        # off; not a defect.

    @property
    def available(self) -> bool:
        """True when a verified manifest is loaded and can gate citations."""
        return bool(self._pages)

    def _verify_page(self, rel_html: str) -> bool:
        """Fail-closed: the shipped page must hash to its pinned manifest value."""
        pinned = self._pages.get(rel_html)
        if not pinned:
            # The page is not in the signed manifest — refuse (an unsigned page is
            # exactly the slipstream vector this gate exists to catch).
            return False
        page_file = self._root / rel_html
        try:
            actual = _sha256_file(page_file)
        except OSError:
            return False
        if actual != pinned:
            logger.error(
                "wiki-citations: page %s does not match its pinned sha256 in the "
                "signed manifest — refusing to cite (possible tamper).", rel_html)
            return False
        return True

    def cite(self, doc_source: str) -> "str | None":
        """A verified citation line for ``doc_source``, or ``None`` to not cite.

        ``None`` whenever: citations are unavailable, the source names no wiki
        page, or the page fails integrity verification. The returned string is a
        markdown citation — a link to the local installed page (primary) and the
        canonical URL (secondary) — ready to append to the answer.
        """
        if not self._pages or not doc_source:
            return None
        page = wiki_page_from_doc_source(doc_source)
        if page is None:
            return None
        rel_html, title = page
        if not self._verify_page(rel_html):
            return None
        local = self._root / rel_html
        url = self._url_base.rstrip("/") + "/" + rel_html
        return _format_citation(title, str(local), url)

    # ── free-form retrieval surface (ADDITIVE — same trust gate, no new semantics) ──
    #
    # The free-form wiki lookup (:mod:`intergen.wiki_retrieval`) reuses THIS chain
    # rather than reimplementing it: the verified manifest's ``pages`` map is both
    # the page inventory to index AND the per-page integrity pin. Every method
    # below gates on the SAME signed-manifest + sha256 check as :meth:`cite`; a
    # page that is not in the signed manifest, or whose shipped bytes no longer
    # match the pinned hash, is never surfaced. Nothing here relaxes the existing
    # trust rules — it exposes them to a second caller.

    def page_hashes(self) -> "dict[str, str]":
        """The verified page inventory ``{rel_html: pinned_sha256}`` (a copy).

        Empty when no signed manifest is loaded (dev/from-source box or a present-
        but-unverifiable manifest) — so a retrieval index built from it is empty
        and the feature is simply off, never a fabricated source."""
        return dict(self._pages)

    def verify_page(self, rel_html: str) -> bool:
        """Public, fail-closed integrity check for one page (see :meth:`_verify_page`)."""
        return self._verify_page(rel_html)

    def _read_if_verified(self, rel_html: str) -> "bytes | None":
        """Read a page's bytes and return them ONLY if they hash to the pin.

        Reads ONCE and hashes exactly the bytes it returns, so the caller grounds
        an answer in the same bytes that verified (no verify-then-reread TOCTOU
        window). ``None`` on: not in the signed manifest, read error, or mismatch
        (loud on mismatch — that is tamper, not absence)."""
        pinned = self._pages.get(rel_html)
        if not pinned:
            return None
        try:
            data = (self._root / rel_html).read_bytes()
        except OSError:
            return None
        if hashlib.sha256(data).hexdigest() != pinned:
            logger.error(
                "wiki-citations: page %s does not match its pinned sha256 in the "
                "signed manifest (read-verify) — refusing to use its bytes "
                "(possible tamper).", rel_html)
            return None
        return data

    def read_verified_page(self, rel_html: str) -> "str | None":
        """The decoded text of a verified page, or ``None`` when it does not verify.

        The grounding primitive for free-form retrieval: an answer is only ever
        grounded in bytes that passed the same gate a citation passes, so a
        tampered/unsigned page can neither be cited NOR laundered into the answer
        body. Decoded lenient (``errors="replace"``) — an integrity-verified page
        that is not clean UTF-8 is a rendering quirk, not a trust failure."""
        data = self._read_if_verified(rel_html)
        if data is None:
            return None
        return data.decode("utf-8", errors="replace")

    def cite_page(self, rel_html: str, title: "str | None" = None) -> "str | None":
        """A verified citation for a page named directly by its ``rel_html``.

        The retrieval counterpart to :meth:`cite` (which starts from a curated
        answer's ``doc_source``): the retrieval path already knows the exact page,
        so it cites by path. Same fail-closed gate — ``None`` unless the page is in
        the signed manifest and its bytes still match the pin. ``title`` defaults
        to a slug-derived label, mirroring :func:`_md_to_page`."""
        if not self._pages or not rel_html:
            return None
        if not self._verify_page(rel_html):
            return None
        if title is None:
            title = _title_for_page(rel_html)
        local = self._root / rel_html
        url = self._url_base.rstrip("/") + "/" + rel_html
        return _format_citation(title, str(local), url)


def _format_citation(title: str, local_path: str, url: str) -> str:
    """The citation line appended to a curated answer.

    Local installed copy FIRST (works fully offline — the Prime Directive), the
    canonical online page SECOND. Markdown links: the console surface (rich)
    renders them; the web surface linkifies the allow-listed ``file://`` and
    ``wiki.intergenos.org`` schemes (see intergen/web/app.js renderMarkdownSafe)."""
    return f"Source: [{title}](file://{local_path}) · [online]({url})"
