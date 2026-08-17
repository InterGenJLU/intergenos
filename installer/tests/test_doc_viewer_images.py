# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Inline doc viewer — image rendering, and the doc/asset pairing.

The viewer was text-only: a doc's `![alt](path)` line rendered as
literal markdown text, which is why the MOK docs-row subtitle had to
stop saying "Screenshots". These tests cover the three things that must
hold now that it renders pictures:

  1. an image-bearing doc splits into the mixed text/picture sequence
     the dialog builds from, and the dialog builds it without a display;
  2. an image that cannot be resolved renders as a VISIBLE placeholder
     — never a crash, never a silent omission;
  3. a text-only doc renders byte-identically to the old text-only path.

Image references are resolved under the directory the doc was read
from, and nothing else: an absolute path, a `..` escape, a URL, or a
non-image suffix is refused. The viewer loads files off the running
system, so what it will open is kept narrow on purpose.

No Gtk/Adw/display required: gi is stubbed before import, in the same
shape as test_gui_done_rendered_string.py.
"""

import os
import re
import sys
import types
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))


class _FakeLabel:
    """Records what the viewer sets on a body label."""

    def __init__(self):
        self.markup = None
        self.css = []
        self.selected = None

    def set_use_markup(self, value):
        self.use_markup = value

    def set_markup(self, value):
        self.markup = value

    def set_selectable(self, value):
        self.selectable = value

    def set_wrap(self, value):
        self.wrap = value

    def set_xalign(self, value):
        pass

    def set_yalign(self, value):
        pass

    def set_margin_top(self, value):
        pass

    def set_margin_bottom(self, value):
        pass

    def set_margin_start(self, value):
        pass

    def set_margin_end(self, value):
        pass

    def add_css_class(self, name):
        self.css.append(name)

    def set_can_focus(self, value):
        self.can_focus = value

    def select_region(self, start, end):
        self.selected = (start, end)


class _FakePicture:
    """Records what the viewer sets on a rendered doc image."""

    def __init__(self):
        self.filename = None
        self.alt_text = None
        self.tooltip = None
        self.css = []
        self.size_request = None
        self.content_fit = None
        self.can_shrink = None

    def set_filename(self, value):
        self.filename = value

    def set_can_shrink(self, value):
        self.can_shrink = value

    def set_content_fit(self, value):
        self.content_fit = value

    def set_size_request(self, width, height):
        self.size_request = (width, height)

    def set_halign(self, value):
        pass

    def add_css_class(self, name):
        self.css.append(name)

    def set_alternative_text(self, value):
        self.alt_text = value

    def set_tooltip_text(self, value):
        self.tooltip = value


class _FakeBox:
    """Vertical container; records the child sequence in order."""

    def __init__(self, orientation=None, spacing=None):
        self.children = []

    def set_margin_top(self, value):
        pass

    def set_margin_bottom(self, value):
        pass

    def set_margin_start(self, value):
        pass

    def set_margin_end(self, value):
        pass

    def append(self, child):
        self.children.append(child)


def _tolerant_gi_module(name, **fixed):
    """gi.repository.<name> stand-in: named attributes are real, every
    other attribute resolves to a fresh MagicMock via PEP 562."""
    module = types.ModuleType(name)
    for attr, value in fixed.items():
        setattr(module, attr, value)
    module.__getattr__ = lambda attr: mock.MagicMock(name=f"{name}.{attr}")
    return module


def _escape(text):
    """GLib.markup_escape_text stand-in — the real XML escaping, so the
    tests assert on the markup the viewer would actually emit."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def _import_doc_viewer():
    """Import doc_viewer under gi stubs, fresh each call."""
    gi = types.ModuleType("gi")
    gi_repository = types.ModuleType("gi.repository")
    gi_repository.Adw = _tolerant_gi_module("Adw")
    gi_repository.Gtk = _tolerant_gi_module(
        "Gtk", Label=_FakeLabel, Picture=_FakePicture, Box=_FakeBox,
    )
    gi_repository.GLib = _tolerant_gi_module(
        "GLib", markup_escape_text=_escape,
    )
    gi.repository = gi_repository

    modules = {"gi": gi, "gi.repository": gi_repository}
    sys.modules.pop("installer.frontend.gui.doc_viewer", None)
    with mock.patch.dict(sys.modules, modules):
        import installer.frontend.gui.doc_viewer as dv
        sys.modules.pop("installer.frontend.gui.doc_viewer", None)
        return dv


def _write_doc(tmp_path, body, images=()):
    """Write a doc plus optional sibling images/ assets; return its path."""
    doc = tmp_path / "sample.md"
    doc.write_text(body, encoding="utf-8")
    if images:
        (tmp_path / "images").mkdir(exist_ok=True)
        for name in images:
            (tmp_path / "images" / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    return doc


class TestBlockSplitting:
    def test_image_line_becomes_its_own_image_block(self, tmp_path):
        dv = _import_doc_viewer()
        _write_doc(tmp_path, "", images=("shot.png",))
        blocks = dv.markdown_to_blocks(
            "Before\n\n![A screen](images/shot.png)\n\nAfter",
            base_dir=str(tmp_path),
        )
        kinds = [b[0] for b in blocks]
        assert kinds == ["text", "image", "text"]
        assert blocks[1][1] == str(tmp_path / "images" / "shot.png")
        assert blocks[1][2] == "A screen"
        assert "Before" in blocks[0][1]
        assert "After" in blocks[2][1]

    def test_indented_image_under_a_numbered_step_still_renders(self, tmp_path):
        """Our walkthrough indents each capture under its step."""
        dv = _import_doc_viewer()
        _write_doc(tmp_path, "", images=("shot.png",))
        blocks = dv.markdown_to_blocks(
            "1. Do the thing.\n\n   ![Step one](images/shot.png)\n",
            base_dir=str(tmp_path),
        )
        assert [b[0] for b in blocks] == ["text", "image"]

    def test_inline_image_inside_a_sentence_stays_text(self, tmp_path):
        """Only a WHOLE-LINE image becomes a picture; a reference inside
        prose must not tear the paragraph into pieces."""
        dv = _import_doc_viewer()
        _write_doc(tmp_path, "", images=("shot.png",))
        blocks = dv.markdown_to_blocks(
            "See ![this](images/shot.png) for details.",
            base_dir=str(tmp_path),
        )
        assert [b[0] for b in blocks] == ["text"]

    def test_image_syntax_inside_a_code_fence_is_not_an_image(self, tmp_path):
        dv = _import_doc_viewer()
        _write_doc(tmp_path, "", images=("shot.png",))
        blocks = dv.markdown_to_blocks(
            "```\n![A screen](images/shot.png)\n```\n",
            base_dir=str(tmp_path),
        )
        assert [b[0] for b in blocks] == ["text"]
        assert "<tt>" in blocks[0][1]

    def test_unresolvable_image_becomes_a_missing_block(self, tmp_path):
        dv = _import_doc_viewer()
        _write_doc(tmp_path, "")
        blocks = dv.markdown_to_blocks(
            "![Absent capture](images/gone.png)",
            base_dir=str(tmp_path),
        )
        assert blocks == [("image-missing", "images/gone.png",
                           "Absent capture")]

    def test_text_only_doc_is_one_block_identical_to_the_old_rendering(
            self, tmp_path):
        """The regression guard: a doc with no images renders exactly as
        it did before image support existed."""
        dv = _import_doc_viewer()
        source = (
            "# Title\n\n## Section\n\n- a bullet\n  - nested\n\n"
            "Some **bold** and *italic* and `code`.\n\n---\n\n"
            "```\nfenced\n```\n"
        )
        blocks = dv.markdown_to_blocks(source)
        assert len(blocks) == 1
        assert blocks[0][0] == "text"
        assert blocks[0][1] == dv.markdown_to_pango(source)
        assert "<span size='xx-large' weight='bold'>Title</span>" in blocks[0][1]
        assert "<b>bold</b>" in blocks[0][1]
        assert "  • a bullet" in blocks[0][1]

    def test_markdown_to_pango_renders_an_image_line_as_its_alt_text(self):
        """The flat-string form has no pictures, so the caption is what
        carries the meaning — it must not vanish."""
        dv = _import_doc_viewer()
        markup = dv.markdown_to_pango("![The enroll screen](images/x.png)")
        assert markup == "<i>The enroll screen</i>"


class TestImageResolution:
    """What the viewer will and will not open."""

    def test_resolves_a_relative_image_beside_the_doc(self, tmp_path):
        dv = _import_doc_viewer()
        _write_doc(tmp_path, "", images=("shot.png",))
        resolved = dv.resolve_doc_image("images/shot.png", str(tmp_path))
        assert resolved == str(tmp_path / "images" / "shot.png")

    def test_refuses_an_absolute_path(self, tmp_path):
        dv = _import_doc_viewer()
        target = tmp_path / "secret.png"
        target.write_bytes(b"\x89PNG\r\n\x1a\n")
        assert dv.resolve_doc_image(str(target), str(tmp_path)) is None

    def test_refuses_a_parent_directory_escape(self, tmp_path):
        dv = _import_doc_viewer()
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"\x89PNG\r\n\x1a\n")
        docs = tmp_path / "docs"
        docs.mkdir()
        assert dv.resolve_doc_image("../outside.png", str(docs)) is None

    def test_refuses_a_symlink_pointing_outside_the_doc_directory(
            self, tmp_path):
        """realpath resolution, not string matching — a link inside the
        doc dir must not become a window onto the rest of the disk."""
        dv = _import_doc_viewer()
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"\x89PNG\r\n\x1a\n")
        docs = tmp_path / "docs"
        docs.mkdir()
        link = docs / "linked.png"
        os.symlink(outside, link)
        assert dv.resolve_doc_image("linked.png", str(docs)) is None

    def test_refuses_a_url(self, tmp_path):
        dv = _import_doc_viewer()
        assert dv.resolve_doc_image(
            "https://example.invalid/x.png", str(tmp_path)) is None

    def test_refuses_a_non_image_suffix(self, tmp_path):
        dv = _import_doc_viewer()
        (tmp_path / "passwd.txt").write_text("root:x:0:0", encoding="utf-8")
        assert dv.resolve_doc_image("passwd.txt", str(tmp_path)) is None

    def test_refuses_when_no_base_dir_is_known(self):
        dv = _import_doc_viewer()
        assert dv.resolve_doc_image("images/shot.png", None) is None

    def test_missing_file_resolves_to_none(self, tmp_path):
        dv = _import_doc_viewer()
        assert dv.resolve_doc_image("images/gone.png", str(tmp_path)) is None


class TestBodyWidget:
    """The dialog body builds headlessly from the block sequence."""

    def test_blocks_build_labels_and_pictures_in_document_order(
            self, tmp_path):
        dv = _import_doc_viewer()
        _write_doc(tmp_path, "", images=("shot.png",))
        blocks = dv.markdown_to_blocks(
            "Step one\n\n![The enroll screen](images/shot.png)\n\nStep two",
            base_dir=str(tmp_path),
        )
        widget, labels = dv._build_body_widget(blocks)
        kinds = [type(c).__name__ for c in widget.children]
        assert kinds == ["_FakeLabel", "_FakePicture", "_FakeLabel"]
        picture = widget.children[1]
        assert picture.filename == str(tmp_path / "images" / "shot.png")
        assert picture.alt_text == "The enroll screen"
        assert picture.size_request == (-1, dv.IMAGE_DISPLAY_HEIGHT)
        assert len(labels) == 2

    def test_missing_image_renders_a_visible_placeholder(self, tmp_path):
        dv = _import_doc_viewer()
        blocks = dv.markdown_to_blocks(
            "![The enroll screen](images/gone.png)", base_dir=str(tmp_path),
        )
        widget, labels = dv._build_body_widget(blocks)
        assert [type(c).__name__ for c in widget.children] == ["_FakeLabel"]
        assert "image not available" in widget.children[0].markup
        assert "The enroll screen" in widget.children[0].markup

    def test_a_plain_pango_string_still_builds_the_single_label_body(self):
        """The historical call shape must keep working unchanged."""
        dv = _import_doc_viewer()
        widget, labels = dv._build_body_widget("<b>hello</b>")
        assert isinstance(widget, _FakeLabel)
        assert widget.markup == "<b>hello</b>"
        assert labels == [widget]


class TestShippedWalkthrough:
    """The doc and its assets are one deliverable — a reference with no
    file behind it would render a placeholder on a real install."""

    DOC = REPO_ROOT / "docs" / "users" / "secure-boot-and-mok.md"

    def test_every_image_the_mok_doc_references_exists_in_the_tree(self):
        text = self.DOC.read_text(encoding="utf-8")
        refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
        assert refs, "the MOK walkthrough should carry the enrollment captures"
        for ref in refs:
            assert (self.DOC.parent / ref).is_file(), f"missing asset: {ref}"

    def test_the_four_enrollment_captures_are_referenced(self):
        text = self.DOC.read_text(encoding="utf-8")
        for name in ("mok-1-enroll-panel.png", "mok-2-view-panel.png",
                     "mok-3-confirm-panel.png", "mok-4-reboot-panel.png"):
            assert f"images/{name}" in text

    def test_every_reference_resolves_through_the_viewer_itself(self):
        """Same path the installed system takes, against the real doc."""
        dv = _import_doc_viewer()
        text = self.DOC.read_text(encoding="utf-8")
        for ref in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
            assert dv.resolve_doc_image(ref, str(self.DOC.parent)) is not None

    def test_the_forge_package_installs_the_doc_images(self):
        """Shipping the markdown without its images would render the
        walkthrough as placeholders on a real install."""
        build_sh = (REPO_ROOT / "packages" / "desktop" / "forge"
                    / "build.sh").read_text(encoding="utf-8")
        assert "docs/users/images" in build_sh
        assert "/usr/share/doc/intergenos/users/images" in build_sh
