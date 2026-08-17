#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Generate the per-page sha256 manifest for the InterGenOS wiki package.

The rendered mdBook ``book/`` HTML tree ships in the ``intergenos-wiki`` package
and InterGen CITES individual pages back to the user. Before InterGen relays a
page as the canonical source for a curated answer it must prove that page has not
been altered since the operator signed the release — otherwise an attacker who
slipstreams modified content into the installed docs launders it through
InterGen's trust ("undone by our own documentation").

This tool emits a JSON manifest mapping each rendered **HTML page** (the only
artifact InterGen cites; images/fonts/css are not cited) to its sha256, computed
over the exact bytes that ship. The operator signs the manifest with the release
key (``scripts/sign-with-gpg.sh``) to produce the detached ``.asc``; at answer
time ``intergen.wiki_citations`` verifies the signature (pinned operator key,
fail-closed) and then hashes the shipped page against the pinned value before
citing it. The pin therefore binds *the page bytes the operator signed at
release* to *the page bytes present at cite time* — checked against the shipped
read-only copy, defense-in-depth over dm-verity and independent of the
filesystem's own trust.

Deterministic: pages are hashed in sorted order and the JSON is emitted with
sorted keys, so the same ``book/`` yields byte-identical manifest bytes
run-to-run (a stable input to the signature).

Usage:
    build-wiki-page-manifest.py <book-dir> <out-manifest.json>
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

MANIFEST_VERSION = 1
_CHUNK = 1 << 20  # 1 MiB streaming read — never load a page fully into memory


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(book_dir: Path) -> dict:
    """Map every ``*.html`` page under ``book_dir`` (rel path) -> sha256.

    The key is the POSIX rel path from the book root (e.g.
    ``packages/package-manager.html``, ``index.html``) — the same identifier
    ``intergen.wiki_citations`` derives from a curated answer's ``doc_source``.
    """
    if not book_dir.is_dir():
        raise SystemExit(f"build-wiki-page-manifest: book dir not found: {book_dir}")
    pages: dict[str, str] = {}
    for html in sorted(book_dir.rglob("*.html")):
        rel = html.relative_to(book_dir).as_posix()
        pages[rel] = _sha256_file(html)
    if not pages:
        raise SystemExit(f"build-wiki-page-manifest: no *.html pages under {book_dir}")
    return {
        "manifest_version": MANIFEST_VERSION,
        "generator": "scripts/build-wiki-page-manifest.py",
        "page_count": len(pages),
        # Sorted at emit time (json dump sort_keys) — declared here for readers.
        "pages": pages,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write(__doc__ or "")
        sys.stderr.write("\nerror: expected <book-dir> <out-manifest.json>\n")
        return 2
    book_dir = Path(argv[1])
    out = Path(argv[2])
    manifest = build_manifest(book_dir)
    # sort_keys + trailing newline => byte-stable across runs for the signature.
    out.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    sys.stderr.write(
        f"build-wiki-page-manifest: {manifest['page_count']} pages -> {out}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
