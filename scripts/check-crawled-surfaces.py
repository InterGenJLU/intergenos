#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""check-crawled-surfaces.py — the SERVED pages must state the release the tree declares.

WHY THIS EXISTS. Two gates already read release claims, and neither reads what a
visitor or a crawler actually gets:

  * scripts/check-doc-claims.py reads README.md, SECURITY.md, docs/release-policy.md
    and the CHANGELOG **in the tree** at publish time, and says so in its own
    docstring: it does not fetch or verify served bytes, the website or the wiki.
  * scripts/check-release-identity.py reads the four identity files, the chroot, the
    ISO name and the stamp **at image build**.

The website and the rendered wiki are the surfaces the outside world reads, and
nothing checked them. Measured 2026-09-17: the served site states R001.2 while the
tree declares R001.3, and the rendered wiki carries R001.1 and R001.2 in equal
measure. Independent reviewers reading only those surfaces described the project by a
release string the tree does not contain at all.

WHAT IT CHECKS. For every page listed in each surface's OWN sitemap:

  release strings   every `RNNN` or `RNNN.N` in the page text must equal the single
                    declaration in packages/core/intergenos-base-files/files/etc/igos-release
                    — the same file check-release-identity.py reads — unless it sits
                    inside a DATED HISTORY ENTRY and names an OLDER release, which is
                    what a news archive or a changelog is for.
  status sentences  a sentence that states what the current release IS must either name
                    the declared release or be the release-invariant form the tree uses
                    in SECURITY.md: the `RNNN.x` line, with the current release
                    delegated to the news page. A status sentence naming a point release
                    that is not the declared one is a stale claim about today.

FAIL-CLOSED, AND NEVER ON AN EMPTY READ. The page list comes from the sitemap each
surface serves, never from a list typed here — a hand-kept list silently stops
covering pages that are added later. A sitemap that cannot be fetched, cannot be
parsed, or yields no pages is a REFUSAL (exit 2), never an empty crawl reported as
clean: an instrument that saw nothing must not certify that it saw nothing wrong. The
fetched set and its count are printed, so a partial crawl is visible in the output
rather than hidden behind a verdict.

POLITENESS. Each surface's robots.txt is read and its Crawl-delay honoured (default
1 second when it states none). `--delay` overrides it, which is what the tests use
against their local fixture server.

Exit codes:
  0  every crawled page agrees with the declared release
  1  disagreements, each named by URL and line
  2  the gate could not measure (unreadable declaration, unfetchable or empty sitemap,
     a surface that served nothing)
"""

from __future__ import annotations

import argparse
import html.parser
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SURFACES = (
    ("site", "https://intergenos.org/sitemap.xml"),
    ("wiki", "https://wiki.intergenos.org/sitemap.xml"),
)
DEFAULT_RELEASE_FILE = "packages/core/intergenos-base-files/files/etc/igos-release"
DEFAULT_CRAWL_DELAY = 1.0
USER_AGENT = "InterGenOS-release-tail-gate/1 (+https://intergenos.org/)"

# A release string of this project's form: R001, R001.2. The `RNNN.x` LINE form is
# matched separately — it names the line, not a release, and is what the
# release-invariant status sentence is supposed to carry.
RELEASE_RE = re.compile(r"\bR(\d{3})(?:\.(\d+))?\b")
LINE_FORM_RE = re.compile(r"\bR\d{3}\.x\b")
DECLARATION_RE = re.compile(r"^R\d{3}(?:\.\d+)?$")

# A FORM EXAMPLE, not a claim: an enumeration that ends in an ellipsis, the way a
# documentation page explains how releases are named — "A major release (R001, R002, …)"
# and "A point release (R001.1, R001.2, …)". Measured on the served wiki 2026-09-17. The
# rule is deliberately narrow: the ellipsis must follow within the same enumeration, so
# "Download InterGenOS R001.2" and "the current release (R001)" are untouched by it.
EXAMPLE_ENUM_RE = re.compile(
    r"R\d{3}(?:\.\d+)?(?:\s*,\s*R\d{3}(?:\.\d+)?)*\s*,\s*(?:…|\.\.\.)")

# A dated history entry opens at a date the page itself carries: an ISO date, or the
# short `14 SEP` form the news page uses in its index. Everything from one date to the
# next belongs to that entry.
DATE_RE = re.compile(
    r"\b(?:20\d{2}-\d{2}-\d{2}"
    r"|\d{1,2}\s+(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
    r"|(?:January|February|March|April|May|June|July|August|September|October"
    r"|November|December)\s+\d{1,2},\s+20\d{2})\b",
    re.I,
)

# A sentence that tells the reader what the current release IS.
STATUS_MARKER_RE = re.compile(
    r"(?:current release|latest release|newest release|project status|release line)",
    re.I,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Tags whose CONTENT is not page text. Their bytes are blanked rather than removed, so
# every offset still maps to the source line it came from.
NON_TEXT_BLOCK_RE = re.compile(
    r"<(script|style|template)\b[^>]*>.*?</\1\s*>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]*>", re.S)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


class Unmeasurable(Exception):
    """The gate cannot see. Refuse; never report a clean crawl."""


@dataclass(frozen=True)
class Finding:
    url: str
    line: int
    kind: str
    claim: str
    expected: str


def _blank(match: re.Match) -> str:
    """Replace a span with spaces of the same length, keeping newlines."""
    return re.sub(r"[^\n]", " ", match.group(0))


def page_text(html: str) -> str:
    """Page text with every non-text span blanked in place.

    Offsets are preserved exactly, so a match's line number is the line it occupies in
    the served bytes — which is what a person fixing the page needs to be told."""
    text = COMMENT_RE.sub(_blank, html)
    text = NON_TEXT_BLOCK_RE.sub(_blank, text)
    return TAG_RE.sub(_blank, text)


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def declared_release(path: Path) -> str:
    try:
        raw = path.read_text().strip()
    except OSError as exc:
        raise Unmeasurable(
            f"the release declaration is not readable at {path}: {exc}. This gate "
            "compares served pages against it, so it refuses rather than assume one."
        ) from exc
    if not DECLARATION_RE.match(raw):
        raise Unmeasurable(
            f"{path} does not hold a single release declaration; it reads {raw!r}. "
            "A gate that cannot say what the release is must not pass pages."
        )
    return raw


def release_sort_key(major: str, point: str | None) -> tuple:
    return (int(major), int(point) if point is not None else -1)


def fetch(url: str, timeout: float) -> tuple[str, str]:
    """Returns (final_url, body). Redirects are followed by urllib and the final URL is
    reported, so a page that moved is named by where it actually is."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return response.geturl(), body.decode(charset, errors="replace")


def crawl_delay_for(sitemap_url: str, timeout: float) -> float:
    """The surface's own robots.txt decides how fast this gate may read it."""
    parts = urllib.parse.urlsplit(sitemap_url)
    robots = urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    try:
        _, body = fetch(robots, timeout)
    except (urllib.error.URLError, OSError, ValueError):
        return DEFAULT_CRAWL_DELAY
    match = re.search(r"^\s*Crawl-delay:\s*([0-9.]+)\s*$", body, re.M | re.I)
    if not match:
        return DEFAULT_CRAWL_DELAY
    try:
        return max(0.0, float(match.group(1)))
    except ValueError:
        return DEFAULT_CRAWL_DELAY


def sitemap_pages(sitemap_url: str, timeout: float, depth: int = 0) -> list:
    """Every page a surface lists, following a sitemap INDEX into the sitemaps it names.

    An index is the shape a growing site adopts without telling anyone. Read naively, its
    `<loc>` entries are sitemap URLs, and fetching them AS pages would find no release
    string in any of them and report the surface clean — a silent empty crawl wearing a
    pass. So the root element decides: `urlset` lists pages, `sitemapindex` lists
    sitemaps and is followed, and anything else is refused."""
    try:
        _, body = fetch(sitemap_url, timeout)
    except Exception as exc:                      # noqa: BLE001 — every failure refuses
        raise Unmeasurable(
            f"the sitemap {sitemap_url} could not be fetched: {exc}. A surface whose "
            "page list cannot be read is not a surface with no pages."
        ) from exc
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise Unmeasurable(
            f"the sitemap {sitemap_url} is not parseable XML: {exc}"
        ) from exc
    root_tag = root.tag.rsplit("}", 1)[-1]
    locs = [
        element.text.strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "loc" and element.text and element.text.strip()
    ]
    if root_tag == "sitemapindex":
        if depth >= 2:
            raise Unmeasurable(
                f"{sitemap_url} is a sitemap index nested more than two deep; refusing "
                "rather than following it further.")
        if not locs:
            raise Unmeasurable(
                f"the sitemap index {sitemap_url} names no sitemaps. An empty crawl must "
                "never be reported as a clean one.")
        pages: list = []
        for child in locs:
            pages.extend(sitemap_pages(child, timeout, depth + 1))
        return pages
    if root_tag != "urlset":
        raise Unmeasurable(
            f"the sitemap {sitemap_url} has root element {root_tag!r}; this gate reads "
            "`urlset` and `sitemapindex` documents and refuses anything else rather than "
            "guess what it is looking at.")
    if not locs:
        raise Unmeasurable(
            f"the sitemap {sitemap_url} lists no pages. An empty crawl must never be "
            "reported as a clean one."
        )
    return locs


BLOCK_TAGS = {"article", "section", "li", "div", "tr", "td", "p", "main", "aside",
              "details", "blockquote", "header", "footer", "nav", "figure", "dd"}
DATED_ANCESTOR_LEVELS = 2


class _BlockSpans(html.parser.HTMLParser):
    """Source spans of the block elements, so a dated entry has an END.

    An earlier cut of this gate treated an entry as running from its date to the next
    date, which meant one date near the top of a page exempted everything below it —
    a hole wide enough to park a stale claim in. The entry is the element that carries
    the date, and it ends where that element ends."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.line_starts = [0]
        for index, char in enumerate(text):
            if char == "\n":
                self.line_starts.append(index + 1)
        self.stack: list = []
        self.spans: list = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag in BLOCK_TAGS:
            self.stack.append((tag, self._offset()))

    def handle_endtag(self, tag):
        if tag not in BLOCK_TAGS:
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                start = self.stack[index][1]
                del self.stack[index:]
                self.spans.append((start, self._offset()))
                return

    def close(self):
        super().close()
        for _, start in self.stack:
            self.spans.append((start, len(self.text)))
        self.stack = []


def dated_spans(text: str, raw_html: str) -> list:
    """Every block element's span, with whether it carries a date of its own.

    Returns (start, end, dated) sorted innermost-first by size, which is what the
    ancestor walk in in_dated_entry() needs."""
    parser = _BlockSpans(raw_html)
    try:
        parser.feed(raw_html)
        parser.close()
    except Exception:                                 # noqa: BLE001 — malformed markup
        parser.spans = parser.spans or []
    spans = [(start, end, bool(DATE_RE.search(text[start:end])))
             for start, end in parser.spans]
    spans.sort(key=lambda span: span[1] - span[0])
    return spans


def in_dated_entry(offset: int, spans: list) -> bool:
    """The element holding this text, or its immediate parent, carries a date.

    Only those two levels count. A date in a footer or a page-wide wrapper would
    otherwise buy the whole page a pass, which is the same hole in a different shape as
    an entry with no end."""
    containing = [span for span in spans if span[0] <= offset < span[1]]
    return any(dated for _, _, dated in containing[:DATED_ANCESTOR_LEVELS])


def dated_sentence(text: str, offset: int) -> bool:
    """True when the sentence around @offset carries its own date.

    Measured on the served wiki 2026-09-17: "The first public release, R001, was
    published 2026-08-16; for the current release, see the main repository README."
    dates itself, and the date follows the release string rather than opening a block
    above it. A sentence that says when something happened is a history entry the size
    of a sentence. The release still has to be OLDER than the declared one — the caller
    checks that — so this cannot exempt a stale claim about today."""
    start = max(text.rfind(". ", 0, offset), text.rfind("\n", 0, offset)) + 1
    end = text.find(". ", offset)
    end = len(text) if end < 0 else end + 1
    newline = text.find("\n", offset)
    if 0 <= newline < end:
        end = newline
    return bool(DATE_RE.search(text[start:end]))


def check_page(url: str, html: str, declared: str, findings: list) -> int:
    """Appends findings; returns the number of release strings examined."""
    text = page_text(html)
    spans = dated_spans(text, html)
    declared_key = release_sort_key(*RELEASE_RE.match(declared).groups())
    line_form = declared.split(".")[0] + ".x"
    examined = 0

    example_spans = [(m.start(), m.end()) for m in EXAMPLE_ENUM_RE.finditer(text)]

    for match in RELEASE_RE.finditer(text):
        if LINE_FORM_RE.match(text, match.start()):
            continue                                   # `RNNN.x` names the line
        if any(start <= match.start() < end for start, end in example_spans):
            continue                                   # a naming-form example, not a claim
        examined += 1
        found = match.group(0)
        if found == declared:
            continue
        key = release_sort_key(match.group(1), match.group(2))
        if key < declared_key and (in_dated_entry(match.start(), spans)
                                   or dated_sentence(text, match.start())):
            continue                                   # history, correctly dated
        line = line_of(text, match.start())
        if in_dated_entry(match.start(), spans):
            expected = (f"{declared}, or an OLDER release inside this dated entry "
                        f"(this one is not older)")
        else:
            expected = f"{declared} (the tree's declaration), or a dated history entry"
        findings.append(Finding(url, line, "release string", found, expected))

    for sentence, offset in sentences(text):
        if not STATUS_MARKER_RE.search(sentence):
            continue
        points = [m.group(0) for m in RELEASE_RE.finditer(sentence)
                  if not LINE_FORM_RE.match(sentence, m.start())]
        stale = [p for p in points if p != declared]
        if not stale:
            continue
        if line_form in sentence and "news" in sentence.lower():
            continue                                   # the release-invariant form
        if in_dated_entry(offset, spans):
            continue
        older = [p for p in stale
                 if release_sort_key(*RELEASE_RE.match(p).groups()) < declared_key]
        if len(older) == len(stale) and DATE_RE.search(sentence):
            continue                                   # a sentence that dates itself
        findings.append(Finding(
            url, line_of(text, offset), "status sentence",
            " ".join(sentence.split())[:200],
            f"a sentence naming {declared}, or the release-invariant "
            f"{line_form} form that delegates the current release to the news page"))
    return examined


def sentences(text: str):
    """(sentence, offset) pairs, offsets into the original text."""
    for block in re.finditer(r"[^\n]+", text):
        cursor = block.start()
        for part in SENTENCE_SPLIT_RE.split(block.group(0)):
            if part.strip():
                yield part, cursor + block.group(0).index(part, cursor - block.start())
            cursor += len(part) + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--surface", action="append", metavar="NAME=SITEMAP_URL",
                        help="repeatable; defaults to the project's site and wiki")
    parser.add_argument("--release-file", type=Path,
                        default=REPO_ROOT / DEFAULT_RELEASE_FILE)
    parser.add_argument("--delay", type=float,
                        help="seconds between page fetches, overriding robots.txt")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    surfaces = []
    for entry in (args.surface or []):
        name, _, sitemap = entry.partition("=")
        if not sitemap:
            print(f"  --surface expects NAME=SITEMAP_URL, got {entry!r}", file=sys.stderr)
            return 2
        surfaces.append((name, sitemap))
    surfaces = surfaces or list(DEFAULT_SURFACES)

    print("=" * 70)
    print("CRAWLED-SURFACE RELEASE GATE")
    print("=" * 70)

    findings: list = []
    fetched = 0
    examined = 0
    try:
        declared = declared_release(args.release_file)
        print(f"declared release : {declared}  ({args.release_file})")
        for name, sitemap in surfaces:
            delay = args.delay if args.delay is not None else crawl_delay_for(sitemap, args.timeout)
            pages = sitemap_pages(sitemap, args.timeout)
            print()
            print(f"surface {name}: {sitemap} -> {len(pages)} pages, "
                  f"{delay:g}s between fetches")
            for index, page in enumerate(pages):
                if index and delay:
                    time.sleep(delay)
                try:
                    final_url, body = fetch(page, args.timeout)
                except Exception as exc:              # noqa: BLE001
                    findings.append(Finding(page, 0, "fetch", str(exc),
                                            "a page the sitemap lists must be servable"))
                    print(f"  FETCH FAILED  {page}: {exc}")
                    continue
                fetched += 1
                moved = "" if final_url == page else f"  (served from {final_url})"
                count = check_page(final_url, body, declared, findings)
                examined += count
                print(f"  {final_url}{moved}  [{count} release strings]")
    except Unmeasurable as exc:
        print("", file=sys.stderr)
        print("  REFUSING — the crawled-surface gate cannot measure:", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        return 2

    print()
    print(f"fetched {fetched} page(s); {examined} release string(s) examined")
    if not fetched:
        print("", file=sys.stderr)
        print("  REFUSING — no page was fetched at all; an empty crawl is not a clean "
              "one.", file=sys.stderr)
        return 2

    if findings:
        print()
        print("=" * 70)
        print(f"DISAGREEMENTS — {len(findings)} found")
        print("=" * 70)
        for finding in findings:
            print(f"  {finding.url}:{finding.line}  [{finding.kind}]")
            print(f"      served   : {finding.claim}")
            print(f"      required : {finding.expected}")
        return 1

    print()
    print("=" * 70)
    print(f"CLEAN — every crawled page states {declared}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
