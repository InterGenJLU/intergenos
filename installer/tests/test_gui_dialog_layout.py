# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Headless dialog construction and key-review sizing, paired with real GTK captures."""

import base64
import html
import importlib.util
from pathlib import Path
import struct
import sys
import types
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]


class Widget:
    def __init__(self, **kwargs):
        self.values = dict(kwargs)
        self.children = []
        self.responses = {}
        self.signals = {}

    def __getattr__(self, name):
        if name.startswith("set_"):
            return lambda *v: self.values.__setitem__(name[4:], v[0] if len(v) == 1 else v)
        if name.startswith("get_"):
            return lambda: self.values.get(name[4:], -1)
        return mock.Mock()

    def add(self, child):
        self.children.append(child)

    append = add
    add_prefix = add
    add_top_bar = add
    add_bottom_bar = add

    def add_response(self, key, label):
        self.responses[key] = label

    def connect(self, name, callback):
        self.signals[name] = callback

    def present(self, *args):
        self.values["presented"] = True

    def get_vadjustment(self):
        return Widget()

    def measure(self, orientation, for_size):
        # Deterministic natural widths; the actual font and layout are checked
        # separately with GTK at both target display sizes.
        width = max(max(len(html.unescape(row.values[name]))
                        for name in ("title", "subtitle")) * 8 + 96
                    for row in self.children)
        return 0, width, -1, -1


@pytest.fixture
def frontend(monkeypatch):
    widgets = []
    def factory(kind):
        def make(*args, **kwargs):
            w = Widget(**kwargs)
            w.kind = kind
            widgets.append(w)
            return w
        make.new = lambda heading, body: make(heading=heading, body=body)
        return make
    def module(name, **values):
        result = types.ModuleType(name)
        result.__dict__.update(values)
        result.__getattr__ = lambda attr: mock.MagicMock(name=name+"."+attr)
        return result
    gtk = module("Gtk", PolicyType=types.SimpleNamespace(AUTOMATIC=1, NEVER=2), **{n: factory(n) for n in ["CheckButton", "ScrolledWindow", "Box", "Label", "Button", "Entry"]})
    adw = module("Adw", **{n: factory(n) for n in ["AlertDialog", "MessageDialog", "Dialog", "PreferencesGroup", "ActionRow", "ToolbarView", "HeaderBar"]})
    glib = module("GLib", markup_escape_text=html.escape, idle_add=lambda fn: fn())
    gi = module("gi", require_version=lambda *args: None)
    repo = module("gi.repository", Gtk=gtk, Adw=adw, GLib=glib)
    gi.repository = repo
    for name, value in {"gi":gi, "gi.repository":repo,
                        "installer.frontend.gui.screens._base":module("_base", _ForgePage=object)}.items():
        monkeypatch.setitem(sys.modules, name, value)
    loaded = {}
    for short, path in [("doc_viewer", "doc_viewer.py"), ("integrity_dialog", "integrity_dialog.py"), ("packages", "screens/packages.py")]:
        name = "installer.frontend.gui." + ("screens." if short == "packages" else "") + short
        spec = importlib.util.spec_from_file_location(name, ROOT / "installer/frontend/gui" / path)
        loaded[short] = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, loaded[short])
        if short == "doc_viewer":
            import installer.frontend.gui as parent
            monkeypatch.setattr(parent, "doc_viewer", loaded[short], raising=False)
        spec.loader.exec_module(loaded[short])
    # The blocking callbacks schedule on the mocked main loop; this test only
    # checks construction, while the real frontend proof exercises presentation.
    monkeypatch.setattr(loaded["integrity_dialog"], "threading", types.SimpleNamespace(Event=mock.Mock))
    return types.SimpleNamespace(**loaded, widgets=widgets, gtk=gtk)


def key(n, comment):
    kind = b"ssh-ed25519"
    blob = struct.pack(">I", len(kind)) + kind + struct.pack(">I", 32) + bytes([n])*32
    return "ssh-ed25519 " + base64.b64encode(blob).decode() + " " + comment


def review(frontend, count=3, comment="Example key", width=1280, height=800):
    page = object.__new__(frontend.packages.PackagesPage)
    page._window = Widget(width=width, height=height)
    page._ssh_key_accepted = None
    page._current_key_text = lambda: "\n".join(key(n+1, comment+str(n)) for n in range(count))
    page._on_review_ssh_keys(None)
    return next(w for w in reversed(frontend.widgets) if w.kind == "AlertDialog")


def test_review_width_follows_its_longest_line(frontend):
    short = review(frontend)
    long = review(frontend, comment="A comment longer than a fingerprint "*3)
    assert 560 <= short.values.get("content_width", -1) < long.values.get("content_width", -1) <= 1232


def test_key_lines_are_single_line_and_selectable(frontend):
    review(frontend)
    rows = [w for w in frontend.widgets if w.kind == "ActionRow"]
    assert len(rows) == 3
    for row in rows:
        assert row.values.get("title_lines") == row.values.get("subtitle_lines") == 1
        assert row.values.get("title_selectable") is True
        assert row.values.get("subtitle_selectable") is True


def test_normal_key_lines_fit_the_initial_viewport(frontend):
    dialog = review(frontend)
    scroller = dialog.values["extra_child"]
    assert scroller.values.get("min_content_width", 0) >= scroller.values["child"].values["size_request"][0]
    assert scroller.values["focusable"] is True
    assert dialog.values["focus"] is scroller


@pytest.mark.parametrize("count", [1, 3, 40])
def test_review_height_tracks_rows_with_a_bounded_scroller(frontend, count):
    dialog = review(frontend, count=count)
    scroller = dialog.values["extra_child"]
    assert scroller.kind == "ScrolledWindow"
    assert scroller.values["propagate_natural_height"] is True
    assert 0 < scroller.values["max_content_height"] <= 480
    assert len(scroller.values["child"].children) == count


def test_an_overlong_comment_scrolls_without_widening_past_the_parent(frontend):
    dialog = review(frontend, comment="Long comment "*50)
    assert 0 < dialog.values.get("content_width", -1) <= 1232
    scroller = dialog.values["extra_child"]
    assert scroller.values["policy"] == (frontend.gtk.PolicyType.AUTOMATIC, frontend.gtk.PolicyType.AUTOMATIC)
    assert scroller.values["child"].values["size_request"][0] > 1280


def test_review_keeps_the_existing_default_choices(frontend):
    dialog = review(frontend)
    assert dialog.values["default_response"] == dialog.values["close_response"] == "cancel"
    assert set(dialog.responses) == {"cancel", "keep"}
    assert all(w.values["active"] is False for w in frontend.widgets if w.kind == "CheckButton")


@pytest.mark.parametrize("which", ["documentation", "missing-document", "warning-blocking", "warning-async", "ack-blocking", "ack-async"])
def test_other_dialog_constructors_and_requested_sizes(frontend, which):
    parent = Widget(width=1280, height=800)
    d, i = frontend.doc_viewer, frontend.integrity_dialog
    if which == "documentation":
        d.show_doc_dialog(parent, "Example", "Example body", "/example.md")
    elif which == "missing-document":
        d.show_missing_doc_dialog(parent, "Example", "https://example.invalid/doc")
    elif which == "warning-blocking":
        i._show_warning_dialog_blocking(parent, "example", "0"*64, "1"*64)
    elif which == "warning-async":
        i.open_integrity_dialog_async(parent, "example", "0"*64, "1"*64, mock.Mock())
    elif which == "ack-blocking":
        i._show_ack_dialog_blocking(parent, "example")
    else:
        i._show_ack_dialog_async(parent, "example", "example phrase", mock.Mock())
    dialog = next(w for w in frontend.widgets if w.kind in {"Dialog", "AlertDialog", "MessageDialog"})
    assert dialog.values["presented"] is True
    if which == "documentation":
        assert (dialog.values["content_width"], dialog.values["content_height"]) == (720, 560)
    elif which.startswith("warning-"):
        assert dialog.values.get("default_size", (-1, -1))[0] == 720
        scroller = dialog.values["extra_child"]
        assert scroller.kind == "ScrolledWindow"
        assert 0 < scroller.values["max_content_height"] <= 580
        label = scroller.values["child"]
        assert label.values["label"] == i._build_warning_text("example", "0"*64, "1"*64)
        assert label.values["selectable"] and label.values["wrap"]
        assert dialog.values["body"] == ""
    else:
        # Natural library sizing is the existing request for these dialogs;
        # the real GTK screenshots check their allocated size and readability.
        assert dialog.values.get("content_width", -1) == -1
        assert dialog.values.get("default_width", -1) == -1
