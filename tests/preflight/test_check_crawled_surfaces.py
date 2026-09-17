#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The release-tail gate over the pages the outside world actually reads.

Every test here runs against a LOCAL fixture server on 127.0.0.1 — never against the
live site or the live wiki. A test that reached the real surfaces would pass or fail
with whatever was published that morning, which is not a test of this gate.

The shapes are the ones that decide whether the gate is worth running:

  * a served page carrying a release string the tree does not declare — refused;
  * the same string inside a DATED history entry, naming an older release — allowed,
    because that is what a news archive is;
  * a dated entry naming something that is not older — refused, so the history
    exemption cannot be used to park a stale claim about today;
  * a status sentence that disagrees — refused; the release-invariant form — allowed;
  * a sitemap that cannot be fetched — refused as unmeasurable, NOT read as a surface
    with no pages;
  * a redirect — followed, with the finding naming where the page actually is.
"""
import http.server
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts/check-crawled-surfaces.py"

CLEAN, FINDINGS, UNMEASURABLE = 0, 1, 2
DECLARED = "R001.3"

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{entries}
</urlset>
"""

PAGE = """<!doctype html>
<html><head><title>{title}</title>
<style>/* ---- R001 download panel ---- */ .download {{ color: red; }}</style>
<script>var stale = "R001.1";</script>
</head>
<body>
{body}
</body></html>
"""


class Redirecting(http.server.SimpleHTTPRequestHandler):
    """Serves the fixture directory, and answers /moved.html with a redirect so the
    gate's redirect handling is exercised by a real 302 rather than by a mock."""

    def do_GET(self):                                   # noqa: N802 — stdlib name
        if self.path == "/moved.html":
            self.send_response(302)
            self.send_header("Location", "/settled.html")
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, *args):                       # keep the test output quiet
        pass


@pytest.fixture
def surface(tmp_path):
    """A fixture web surface: write pages, get back a base URL and a runner."""
    root = tmp_path / "www"
    root.mkdir()
    (tmp_path / "igos-release").write_text(DECLARED + "\n")

    import functools
    handler = functools.partial(Redirecting, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    class Surface:
        def __init__(self):
            self.root = root
            self.base = base

        def page(self, name, body, title="InterGenOS"):
            (root / name).write_text(PAGE.format(title=title, body=body))
            return f"{base}/{name}"

        def sitemap(self, *urls, name="sitemap.xml"):
            entries = "\n".join(f"  <url><loc>{u}</loc></url>" for u in urls)
            (root / name).write_text(SITEMAP.format(entries=entries))
            return f"{base}/{name}"

        def run(self, sitemap_url, release_file=None):
            return subprocess.run(
                [sys.executable, str(GATE),
                 "--surface", f"fixture={sitemap_url}",
                 "--release-file", str(release_file or (tmp_path / "igos-release")),
                 "--delay", "0", "--timeout", "10"],
                capture_output=True, text=True)

    try:
        yield Surface()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_gate_exists_and_is_executable():
    assert GATE.is_file(), f"{GATE} is missing"
    assert GATE.stat().st_mode & 0o111, f"{GATE} is not executable"


def test_a_surface_stating_the_declared_release_passes(surface):
    """Otherwise every refusal below could be the gate refusing everything."""
    url = surface.page("index.html",
                       "<h1>InterGenOS R001.3 released</h1>"
                       "<p>Download InterGenOS R001.3 x86_64 UEFI live ISO.</p>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == CLEAN, f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "1 page(s)" in result.stdout, result.stdout


def test_a_stale_release_string_is_refused_and_named_by_url_and_line(surface):
    """The measured shape: the served home page says R001.2 while the tree says R001.3."""
    url = surface.page("index.html",
                       "<h1>InterGenOS R001.2 released</h1>\n"
                       "<p>live ISO and binary mirror online</p>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == FINDINGS, result.stdout
    assert "R001.2" in result.stdout
    assert re.search(rf"{re.escape(url)}:\d+", result.stdout), (
        "the finding does not name the page by URL and line:\n" + result.stdout)
    assert DECLARED in result.stdout


def test_an_older_release_inside_a_dated_entry_is_allowed(surface):
    """A news archive is supposed to name the releases it is reporting on."""
    url = surface.page("news.html",
                       "<p>R001.3 is the current release</p>\n"
                       "<article><time>2026-09-03</time>\n"
                       "<h2>InterGenOS R001.2 released</h2>\n"
                       "<p>R001.2 replaces R001.1 as the recommended download.</p>"
                       "</article>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == CLEAN, (
        "a dated history entry naming older releases was refused:\n" + result.stdout)


def test_a_dated_entry_naming_something_not_older_is_still_refused(surface):
    """Otherwise the history exemption is a place to park a stale claim about today."""
    url = surface.page("news.html",
                       "<article><time>2026-09-03</time>\n"
                       "<h2>InterGenOS R001.4 released</h2></article>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == FINDINGS, result.stdout
    assert "R001.4" in result.stdout


def test_a_status_sentence_that_disagrees_is_refused(surface):
    url = surface.page("index.html", "<p>R001.2 is the current release.</p>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == FINDINGS, result.stdout
    assert "status sentence" in result.stdout, result.stdout


def test_the_release_invariant_status_sentence_is_allowed(surface):
    """The form the tree itself uses in SECURITY.md: the line, not the point release,
    with the current release delegated to the news page."""
    url = surface.page(
        "index.html",
        "<p>R001.x is the first public release line; the project is in active "
        "development. The current release is always stated at "
        "https://intergenos.org/news.html .</p>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == CLEAN, result.stdout


def test_a_sitemap_that_cannot_be_fetched_is_refused_not_read_as_empty(surface):
    result = surface.run(f"{surface.base}/no-such-sitemap.xml")
    assert result.returncode == UNMEASURABLE, result.stdout
    assert "could not be fetched" in result.stderr, result.stderr
    assert "not a surface with no pages" in result.stderr, (
        "the refusal must say that an unfetchable list is not an empty one:\n" + result.stderr)


def test_a_sitemap_listing_no_pages_is_refused(surface):
    empty = surface.sitemap(name="empty.xml")
    result = surface.run(empty)
    assert result.returncode == UNMEASURABLE, result.stdout
    assert "lists no pages" in result.stderr, result.stderr


def test_a_sitemap_that_is_not_xml_is_refused(surface):
    (surface.root / "broken.xml").write_text("<urlset><loc>oops")
    result = surface.run(f"{surface.base}/broken.xml")
    assert result.returncode == UNMEASURABLE, result.stdout
    assert "not parseable XML" in result.stderr, result.stderr


def test_a_redirect_is_followed_and_the_finding_names_where_the_page_really_is(surface):
    surface.page("settled.html", "<h1>InterGenOS R001.2 released</h1>")
    result = surface.run(surface.sitemap(f"{surface.base}/moved.html"))
    assert result.returncode == FINDINGS, result.stdout
    assert "settled.html" in result.stdout, (
        "the gate reported the redirecting URL rather than the page it was served:\n"
        + result.stdout)
    assert "served from" in result.stdout, result.stdout


def test_a_listed_page_that_cannot_be_fetched_is_a_finding_not_a_silent_skip(surface):
    good = surface.page("index.html", "<p>InterGenOS R001.3</p>")
    result = surface.run(surface.sitemap(good, f"{surface.base}/missing.html"))
    assert result.returncode == FINDINGS, result.stdout
    assert "FETCH FAILED" in result.stdout, result.stdout
    assert "1 page(s)" in result.stdout, (
        "the count of pages actually fetched must be printed, so a partial crawl is "
        "visible:\n" + result.stdout)


def test_a_release_string_in_script_or_style_is_not_read_as_page_text(surface):
    """The served home page carries `/* ---- R001 download ---- */` in its stylesheet.
    A gate that read it would refuse every page for something no reader sees."""
    url = surface.page("index.html", "<p>InterGenOS R001.3 is here.</p>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == CLEAN, (
        "a release string inside <style>/<script> was read as page text:\n" + result.stdout)


def test_an_unreadable_declaration_is_refused(surface, tmp_path):
    url = surface.page("index.html", "<p>InterGenOS R001.3</p>")
    result = surface.run(surface.sitemap(url), release_file=tmp_path / "absent")
    assert result.returncode == UNMEASURABLE, result.stdout
    assert "not readable" in result.stderr, result.stderr


def test_a_declaration_that_is_not_a_single_release_is_refused(surface, tmp_path):
    bad = tmp_path / "bad-release"
    bad.write_text("R001.3 and maybe R001.2\n")
    url = surface.page("index.html", "<p>InterGenOS R001.3</p>")
    result = surface.run(surface.sitemap(url), release_file=bad)
    assert result.returncode == UNMEASURABLE, result.stdout
    assert "single release declaration" in result.stderr, result.stderr


def test_the_default_surfaces_are_the_project_site_and_wiki_and_come_from_sitemaps():
    """The page list must never be typed into the gate: a hand-kept list stops covering
    pages the moment someone adds one."""
    text = GATE.read_text()
    assert "https://intergenos.org/sitemap.xml" in text
    assert "https://wiki.intergenos.org/sitemap.xml" in text
    body = text.split("DEFAULT_SURFACES = (", 1)[1].split("\n)", 1)[0]
    assert body.count("http") == 2, (
        "DEFAULT_SURFACES should name the two sitemaps and nothing else:\n" + body)
    assert "sitemap" in body and body.count("loc") == 0


def test_a_sitemap_index_is_followed_into_the_sitemaps_it_names(surface):
    """An index read naively lists sitemap URLs, not pages. Fetching those AS pages finds
    no release string in any of them and reports the surface clean — an empty crawl
    wearing a pass."""
    stale = surface.page("index.html", "<h1>InterGenOS R001.2 released</h1>")
    inner = surface.sitemap(stale, name="pages.xml")
    (surface.root / "index-of-sitemaps.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <sitemap><loc>{inner}</loc></sitemap>\n</sitemapindex>\n")
    result = surface.run(f"{surface.base}/index-of-sitemaps.xml")
    assert result.returncode == FINDINGS, (
        "the index was not followed into its sitemaps, so the stale page was never "
        "read:\n" + result.stdout)
    assert "R001.2" in result.stdout


def test_an_empty_sitemap_index_is_refused(surface):
    (surface.root / "empty-index.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "</sitemapindex>\n")
    result = surface.run(f"{surface.base}/empty-index.xml")
    assert result.returncode == UNMEASURABLE, result.stdout
    assert "names no sitemaps" in result.stderr, result.stderr


def test_an_xml_document_that_is_neither_is_refused(surface):
    (surface.root / "not-a-sitemap.xml").write_text(
        '<?xml version="1.0"?><rss><channel><loc>x</loc></channel></rss>')
    result = surface.run(f"{surface.base}/not-a-sitemap.xml")
    assert result.returncode == UNMEASURABLE, result.stdout
    assert "root element" in result.stderr, result.stderr


def test_a_naming_form_example_is_not_read_as_a_claim(surface):
    """Measured on the served wiki: a page explaining how releases are named writes
    "A major release (R001, R002, …)". That is the form, not a claim about today, and a
    gate that refused it could never reach exit 0 on a documentation surface."""
    url = surface.page(
        "naming.html",
        "<p>A major release (R001, R002, …) is produced by the full from-scratch build. "
        "A point release (R001.1, R001.2, …) rebuilds only the changed packages.</p>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == CLEAN, (
        "a naming-form enumeration was read as a release claim:\n" + result.stdout)


def test_the_form_example_rule_does_not_swallow_a_real_claim(surface):
    """The narrow reading, pinned: an ellipsis elsewhere on the page must not exempt a
    download line or a current-release sentence."""
    url = surface.page(
        "mixed.html",
        "<p>A major release (R001, R002, …) is produced by the full build.</p>\n"
        "<p>Download InterGenOS R001.2 x86_64 UEFI live ISO.</p>\n"
        "<p>R001.2 is the current release.</p>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == FINDINGS, result.stdout
    assert "R001.2" in result.stdout
    assert "status sentence" in result.stdout, result.stdout


def test_a_sentence_that_dates_itself_is_history(surface):
    """Measured on the served wiki: "The first public release, R001, was published
    2026-08-16; for the current release, see the main repository README." The date
    follows the release string instead of opening a block above it, and the sentence is
    a history entry the size of a sentence."""
    url = surface.page(
        "security-review.html",
        "<p>Project status. The first public release, R001, was published 2026-08-16; "
        "for the current release, see the main repository README.</p>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == CLEAN, (
        "a sentence carrying its own date was read as a claim about today:\n"
        + result.stdout)


def test_a_date_in_one_block_does_not_exempt_the_next_block(surface):
    """The hole this gate's dating rule had to close. An entry that ran from its date to
    the next date meant one date near the top of a page exempted every stale claim below
    it. An entry ends where the element carrying the date ends."""
    url = surface.page(
        "mixed-dates.html",
        "<article><time>2026-08-16</time>"
        "<p>The first public release, R001, was published then.</p></article>\n"
        "<p>R001 ships GNOME 49 on Wayland as its graphical session.</p>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == FINDINGS, (
        "a date in an earlier element exempted a claim in a later one:\n" + result.stdout)
    assert "R001" in result.stdout


def test_a_page_wide_wrapper_carrying_one_date_does_not_exempt_the_page(surface):
    """A date in a footer or a page-wide <main> must not buy the whole page a pass."""
    url = surface.page(
        "wrapped.html",
        "<main><section><p>R001 ships GNOME 49 on Wayland.</p></section>"
        "<footer><p>Last updated 2026-08-16</p></footer></main>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == FINDINGS, (
        "a date inside a page-wide wrapper exempted the page:\n" + result.stdout)


def test_a_date_in_an_entrys_rail_dates_the_whole_entry(surface):
    """The served news page's shape: the date sits in a short rail beside the entry's
    body, not inside it. Read as dating only the rail, every release string in the
    archive becomes a finding and the archive can never pass."""
    url = surface.page(
        "news.html",
        '<div class="wrap">\n'
        '<article class="entry" id="point-release-r001-2"><div class="rail">'
        '<span class="date">2026-09-03</span><span class="kind">Point release</span>'
        '<a class="permalink" href="#point-release-r001-2" '
        'aria-label="Link to this entry: InterGenOS R001.2 released">#</a></div>\n'
        '<div class="body"><h2>InterGenOS R001.2 released</h2>'
        "<p>R001.2 replaces R001.1 as the recommended download.</p></div></article>\n"
        '<article class="entry"><div class="rail"><span class="date">2026-08-16</span>'
        '</div><div class="body"><p>R001 was the first public release.</p></div></article>'
        "</div>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == CLEAN, (
        "a dated archive entry was read as a claim about today:\n" + result.stdout)


def test_the_rail_rule_does_not_date_a_sibling_entry(surface):
    """Each entry's date covers that entry. A stale claim in the NEXT entry, which has
    no date of its own, is still refused."""
    url = surface.page(
        "half-dated.html",
        '<div class="wrap">\n'
        '<article class="entry"><div class="rail"><span class="date">2026-09-03</span>'
        '</div><div class="body"><p>R001.2 replaces R001.1.</p></div></article>\n'
        '<article class="entry"><div class="body">'
        "<p>R001 ships GNOME 49 on Wayland today.</p></div></article></div>")
    result = surface.run(surface.sitemap(url))
    assert result.returncode == FINDINGS, (
        "an undated entry was covered by its neighbour's date:\n" + result.stdout)
    assert "R001" in result.stdout
