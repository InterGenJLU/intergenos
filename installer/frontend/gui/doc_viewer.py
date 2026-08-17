# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Inline markdown doc viewer — shared across Forge installer screens.

Originally lived in user.py for the MOK first-boot walkthrough; pulled
out 2026-05-26 when PackagesPage needed the same viewer for its
InterGen "CLICK HERE" link. Any future screen that wants to open a
user-facing markdown doc in an `Adw.Dialog` should import from here
rather than copying the dialog code.

API:
    find_doc(filename)              -> path or None
    resolve_doc_image(ref, base_dir) -> path or None
    markdown_to_blocks(text, base_dir=None)
        -> list of ("text", pango) / ("image", path, alt) /
           ("image-missing", ref, alt) blocks
    markdown_to_pango(text)         -> Pango markup string (text-only
        rendering; an image line renders as its alt text)
    show_doc_dialog(window, title, body, source_path)
        -> opens the modal viewer; `body` is either a Pango markup
           string or a block list from markdown_to_blocks()
    show_missing_doc_dialog(window, doc_label, github_url)
        -> opens the fallback "doc not installed" dialog
    open_doc_by_filename(window, filename, title=None, github_url=None)
        -> one-call helper: find + read + render, with fallback
"""

import os
import re

from gi.repository import Adw, GLib, Gtk


# Paths the viewer tries, in order, to locate a shipped doc.
# Production: /usr/share/doc/intergenos/users/ (set by Forge package's
# do_install — see packages/desktop/forge/build.sh). Live-ISO read-only
# squashfs path. Dev fallbacks for source-tree runs.
DOC_SEARCH_PATHS = (
    "/usr/share/doc/intergenos/users",
    "/usr/local/share/doc/intergenos/users",
    "/run/squashfs/usr/share/doc/intergenos/users",
    os.path.expanduser("~/intergenos/docs/users"),
    "/mnt/intergenos/docs/users",
)


def find_doc(filename: str) -> str | None:
    """Return absolute path of the first DOC_SEARCH_PATHS entry that
    contains `filename`, or None."""
    for d in DOC_SEARCH_PATHS:
        candidate = os.path.join(d, filename)
        if os.path.exists(candidate):
            return candidate
    return None


# Image references a doc may carry. Anything outside this set is left
# unrendered — the viewer loads files off the running system, so the
# accepted shapes are kept deliberately narrow (see resolve_doc_image).
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")

# A whole-line image: `![alt](path)`, optionally indented (our docs
# indent captures under a numbered step). Only whole-line images become
# picture blocks; an image inside a sentence stays text.
_IMAGE_LINE_RE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\s]+)\)\s*$")


def resolve_doc_image(ref: str, base_dir: str | None) -> str | None:
    """Resolve a markdown image reference to a file on disk, or None.

    Accepts ONLY a relative path, with an image suffix, that resolves
    to an existing regular file inside `base_dir` (the directory the
    doc itself was read from). Rejected, deliberately: absolute paths,
    any `..` segment, URLs/URI schemes, and every suffix outside
    IMAGE_SUFFIXES. The viewer renders whatever markdown it is handed,
    so a doc must not be able to point it at an arbitrary file on the
    running system; everything that is refused renders as a visible
    placeholder rather than being silently dropped."""
    if not ref or base_dir is None:
        return None
    if "://" in ref or ref.startswith("data:") or ref.startswith("#"):
        return None
    if os.path.isabs(ref) or ref.startswith("~"):
        return None
    parts = ref.replace("\\", "/").split("/")
    if any(p == ".." for p in parts):
        return None
    if not ref.lower().endswith(IMAGE_SUFFIXES):
        return None
    base_real = os.path.realpath(base_dir)
    candidate = os.path.realpath(os.path.join(base_real, ref))
    if candidate != base_real and not candidate.startswith(base_real + os.sep):
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


def markdown_to_blocks(md: str, base_dir: str | None = None) -> list:
    """Split markdown into the render sequence the dialog builds from.

    Returns a list of blocks, in document order:

        ("text", pango_markup)        — a run of rendered text
        ("image", abs_path, alt)      — a whole-line image that resolved
        ("image-missing", ref, alt)   — a whole-line image that did not

    Text is rendered by the same tiny converter markdown_to_pango()
    uses: ATX headings (# / ## / ###), fenced code blocks (```),
    inline code (`...`), bold (**...**), italic (*...*), bullet lists
    (`- `). Everything else passes through as plain (escaped) text. No
    HTML/embeds, no Markdown tables, no autolinks.

    A doc with no images yields exactly one text block whose markup is
    identical to markdown_to_pango()'s output — the text-only path is
    unchanged by image support."""
    blocks: list = []
    out: list[str] = []
    in_code = False

    def _flush() -> None:
        if out:
            blocks.append(("text", "\n".join(out)))
            out.clear()

    for raw_line in md.splitlines():
        stripped = raw_line.strip()
        # Fenced code blocks toggle
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            out.append(f"<tt>{GLib.markup_escape_text(raw_line)}</tt>")
            continue
        # Whole-line image → its own block (code blocks excluded above)
        image_match = _IMAGE_LINE_RE.match(raw_line)
        if image_match:
            alt, ref = image_match.group(1), image_match.group(2)
            path = resolve_doc_image(ref, base_dir)
            _flush()
            if path is None:
                blocks.append(("image-missing", ref, alt))
            else:
                blocks.append(("image", path, alt))
            continue
        # Horizontal rules → blank line
        if stripped in ("---", "***", "___"):
            out.append("")
            continue
        # ATX headings (deepest first so ### doesn't match # / ##)
        if raw_line.startswith("### "):
            text = GLib.markup_escape_text(raw_line[4:])
            out.append(f"<span size='large' weight='bold'>{text}</span>")
            continue
        if raw_line.startswith("## "):
            text = GLib.markup_escape_text(raw_line[3:])
            out.append(f"<span size='x-large' weight='bold'>{text}</span>")
            continue
        if raw_line.startswith("# "):
            text = GLib.markup_escape_text(raw_line[2:])
            out.append(f"<span size='xx-large' weight='bold'>{text}</span>")
            continue
        # Bullet list rewriting (top-level + 1 nested level)
        line = raw_line
        if re.match(r"^- ", line):
            line = "  • " + line[2:]
        elif re.match(r"^  - ", line):
            line = "    ◦ " + line[4:]
        # Escape, then inject Pango markup for the inline patterns.
        # The markdown delimiters (*, `) aren't HTML-special so escape-
        # first is safe.
        escaped = GLib.markup_escape_text(line)
        escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
        escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", escaped)
        escaped = re.sub(r"`([^`]+)`", r"<tt>\1</tt>", escaped)
        out.append(escaped)
    _flush()
    return blocks


def markdown_to_pango(md: str) -> str:
    """Text-only rendering of the same markdown subset.

    Kept as the flat-string form of markdown_to_blocks(): callers that
    just want Pango markup (no pictures) get every text block joined,
    with a whole-line image rendered as its alt text in italics so the
    caption still reads. Docs without images render exactly as they
    always have."""
    pieces: list[str] = []
    for block in markdown_to_blocks(md):
        if block[0] == "text":
            pieces.append(block[1])
        else:
            alt = GLib.markup_escape_text(block[2] or block[1])
            pieces.append(f"<i>{alt}</i>")
    return "\n".join(pieces)


# On-screen height of a rendered doc image, in pixels. The dialog body
# is 720 px wide, so the cropped 640x456 MokManager panel captures draw
# about 505 px wide at this height and fit without horizontal scrolling;
# Gtk.Picture preserves aspect ratio.
IMAGE_DISPLAY_HEIGHT = 360


def _body_label(pango_markup: str) -> "Gtk.Label":
    """One text run of the doc body.

    Selectable Gtk.Labels grab focus by default; combined with
    set_selectable that lands the caret at the END of the label and
    pulls ScrolledWindow's vadjustment all the way down. Marking the
    label non-focusable prevents the initial-focus path while still
    allowing click + drag selection once the dialog is open."""
    label = Gtk.Label()
    label.set_use_markup(True)
    label.set_markup(pango_markup)
    label.set_selectable(True)
    label.set_wrap(True)
    label.set_xalign(0.0)
    label.set_yalign(0.0)
    label.add_css_class("forge-doc-body")
    label.set_can_focus(False)
    return label


def _build_body_widget(body) -> tuple:
    """Build the scrollable doc body from a Pango string or a block list.

    Returns (widget, text_labels). A plain string keeps the historical
    single-Label body verbatim. A block list becomes a vertical Gtk.Box
    of Labels and Gtk.Pictures — the mixed form image rendering needs.
    An image that failed to resolve renders as a visible placeholder
    line, never a crash and never a silent omission."""
    if isinstance(body, str):
        label = _body_label(body)
        label.set_margin_top(20)
        label.set_margin_bottom(20)
        label.set_margin_start(24)
        label.set_margin_end(24)
        return label, [label]

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(20)
    box.set_margin_bottom(20)
    box.set_margin_start(24)
    box.set_margin_end(24)
    labels = []
    for block in body:
        kind = block[0]
        if kind == "text":
            label = _body_label(block[1])
            labels.append(label)
            box.append(label)
        elif kind == "image":
            picture = Gtk.Picture()
            picture.set_filename(block[1])
            picture.set_can_shrink(True)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_size_request(-1, IMAGE_DISPLAY_HEIGHT)
            picture.set_halign(Gtk.Align.START)
            picture.add_css_class("forge-doc-image")
            if block[2]:
                picture.set_alternative_text(block[2])
                picture.set_tooltip_text(block[2])
            box.append(picture)
        else:  # "image-missing"
            alt = block[2] or block[1]
            placeholder = _body_label(
                "<i>[image not available: "
                f"{GLib.markup_escape_text(alt)}]</i>"
            )
            placeholder.add_css_class("dim-label")
            labels.append(placeholder)
            box.append(placeholder)
    return box, labels


def show_doc_dialog(window, title: str, body, source_path: str) -> None:
    """Open an Adw.Dialog rendering the doc body.

    `body` is either a Pango markup string (the historical text-only
    form) or a block list from markdown_to_blocks(), which renders as
    mixed text and images.

    Opens scrolled to TOP with NOTHING selected and focus on Close.
    A selectable Gtk.Label that grabs focus auto-selects all its text,
    which then drags the ScrolledWindow's vadjustment to the caret
    (end of doc). Worked around by (a) marking the body labels
    non-focusable so they can't be the initial focus target, and (b)
    using GLib.idle_add to reset scroll + clear selection + move focus
    to Close once layout completes."""
    dialog = Adw.Dialog()
    dialog.set_title(title)
    dialog.set_content_width(720)
    dialog.set_content_height(560)

    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    header.add_css_class("forge-header")
    toolbar.add_top_bar(header)

    body_widget, text_labels = _build_body_widget(body)

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_vexpand(True)
    scroller.set_child(body_widget)
    toolbar.set_content(scroller)

    footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    footer.set_halign(Gtk.Align.END)
    footer.set_margin_top(8)
    footer.set_margin_bottom(12)
    footer.set_margin_end(16)
    path_label = Gtk.Label(label=f"Source: {source_path}")
    path_label.add_css_class("dim-label")
    path_label.set_selectable(True)
    path_label.set_can_focus(False)
    path_label.set_halign(Gtk.Align.START)
    path_label.set_hexpand(True)
    path_label.set_margin_start(16)
    footer.append(path_label)
    close = Gtk.Button(label="Close")
    close.add_css_class("forge-nav-button")
    close.connect("clicked", lambda _b: dialog.close())
    footer.append(close)
    toolbar.add_bottom_bar(footer)

    dialog.set_child(toolbar)
    dialog.present(window)

    # Wait until after layout completes so vadjustment knows its real
    # upper bound. Reset scroll to top, clear any selection that may
    # have landed on the body, ensure focus sits on Close (standard
    # dialog default-action target).
    def _after_present():
        scroller.get_vadjustment().set_value(0.0)
        for label in text_labels:
            label.select_region(0, 0)
        close.grab_focus()
        return False  # one-shot idle callback

    GLib.idle_add(_after_present)


def show_missing_doc_dialog(window, doc_label: str, github_url: str) -> None:
    """Fallback dialog when find_doc() returned None — typically older
    ISOs that don't ship docs on-disk yet."""
    dialog = Adw.AlertDialog.new(
        f"{doc_label} doc not installed in this Forge build",
        (
            f"This live ISO doesn't ship the {doc_label} doc on-disk "
            "yet (older ISOs pre-2026-05-26). You can read it from "
            "another device at:\n\n" + github_url
        ),
    )
    dialog.add_response("ok", "OK")
    dialog.set_default_response("ok")
    dialog.set_close_response("ok")
    dialog.present(window)


def open_doc_by_filename(window, filename: str, title: str | None = None,
                        github_url: str | None = None,
                        doc_label: str | None = None) -> None:
    """One-call helper: find_doc + read + show_doc_dialog with a
    show_missing_doc_dialog fallback. Use this from screen handlers
    when you just want "open this doc in the viewer"."""
    path = find_doc(filename)
    if path is None:
        if github_url is None:
            github_url = (
                "https://github.com/InterGenJLU/intergenos/blob/master/"
                f"docs/users/{filename}"
            )
        show_missing_doc_dialog(
            window, doc_label or filename, github_url,
        )
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            markdown = f.read()
    except OSError:
        show_missing_doc_dialog(
            window, doc_label or filename,
            github_url or f"file://{path}",
        )
        return
    # Images are resolved against the directory the doc was READ from,
    # so the same markdown works from the live ISO's read-only squashfs
    # path, the installed /usr/share/doc path, and a source-tree run.
    show_doc_dialog(
        window,
        title=title or filename,
        body=markdown_to_blocks(markdown, base_dir=os.path.dirname(path)),
        source_path=path,
    )
