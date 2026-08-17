# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen branded consent dialog — standalone GTK4 renderer.

This is the small single-purpose binary the daemon shells out to in place of
zenity, for both desktop consent surfaces:

  * review  — the tool-call review gate  (Allow once / Allow this conversation / Not now)
  * consent — show-before-send egress    (Send / Cancel)

It reproduces the approved branded look (`docs/research/branding/consent-dialog/`)
as NATIVE GTK4 + GTK CSS — deliberately NOT WebKitGTK: a browser engine must not
render the partially-attacker-controlled egress payload in the one dialog that
gates egress. The whole module does exactly ONE thing (read framed stdin → render
inert → return a decision via exit code) and imports only GTK + the stdlib, per
the security review's §5.3 minimality constraint.

Security properties enforced HERE (the daemon enforces the spawn-side ones —
scrubbed env, no-new-privs, watchdog — in `intergen.consent_dialog`):

  * inert payload (§E / inv 4)   — GtkTextView, editable=False, cursor-visible=False,
    monospace, buffer set via set_text(); NO Pango-markup path. Fixed chrome uses
    plain GtkLabels with use-markup/use-underline left off.
  * visual integrity (§B / inv 8) — every payload/destination string is run through
    `sanitize_for_display`, which makes bidi-control, zero-width, and other
    non-printing characters VISIBLE (badged ⟨U+XXXX⟩) so a human cannot be shown
    one thing while different bytes are sent (Trojan-Source / zero-width hiding).
  * chrome from trusted metadata only (§D / inv 9) — title, provenance badge,
    destination pill, reason, button labels come ONLY from daemon-supplied trusted
    keys; no CONTENT byte (payload/arguments/excerpt/reasoning) touches the chrome.
  * affirmative-only decision (§F / inv 11) — result_code starts at EXIT_DENY and
    becomes an affirmative code ONLY inside an explicit Send/Allow handler. Esc,
    window-close, falling off the loop → deny/cancel.
  * never truncate the egress payload (§E) — an over-MAX payload is refused
    (fail-closed), never shown truncated.

The amber secret highlight is an AID, not a gate: it adds GtkTextBuffer tags over
the verbatim shown bytes (never transforms text); the FULL payload is shown and
the "review ALL of it" copy stands regardless of what the detector finds.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys

from intergen import consent_dialog_proto as proto
from intergen.consent_dialog_sanitize import (
    detect_secret_ranges,
    provenance_badge_class,
    review_risk_copy,
    sanitize_for_display,
    sanitize_with_ranges,
)


def _set_no_new_privs() -> None:
    """Drop the ability to gain privileges (setuid/setgid exec) for this process.
    Run as the FIRST action of this fresh, single-threaded binary — BEFORE GTK and
    its dynamic module loading come in — so it avoids the fork-unsafe preexec_fn
    hazard of doing it from the multithreaded daemon. Fail-closed: if prctl cannot
    be set, exit (127) before rendering anything, and the daemon falls back to zenity."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        PR_SET_NO_NEW_PRIVS = 38
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            os._exit(127)
    except Exception:
        os._exit(127)


# Harden THIS process before GTK (and its dynamic module loading) initialise —
# only when actually run as the dialog binary, never as an incidental import.
if __name__ == "__main__":
    _set_no_new_privs()

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, Gio, GLib, Pango  # noqa: E402


# ── InterGen palette (mirrors intergen/web/style.css tokens) ─────────────────
_ACCENT = "#0099FF"
_ACCENT_BRIGHT = "#33b1ff"
_VOID = "#050810"
_SURFACE = "#080c18"
_TEXT = "#e2e8f0"
_TEXT_DIM = "#7a8ba8"
_SUCCESS = "#10b981"
_WARNING = "#f59e0b"
_DESTRUCTIVE = "#ef4444"

_CSS = f"""
window, window:backdrop {{ background-color: {_SURFACE}; }}
/* Force the dark header in BOTH focus states. The active-window theme draws a
   light headerbar background that overrode our `background-color` (only the
   backdrop state showed our dark fill) — so use the `background` shorthand to
   also clear the theme's background-image, and cover :backdrop explicitly. */
headerbar.igc-header, headerbar.igc-header:backdrop {{
    background: #0c1322; background-image: none; box-shadow: none;
    border-bottom: 1px solid rgba(0,153,255,0.25); min-height: 44px;
    color: {_TEXT}; }}
.igc-brand {{ font-weight: 700; color: {_TEXT}; }}
.igc-brand-gen {{ font-weight: 700; color: {_ACCENT_BRIGHT}; }}
.igc-mode {{ color: {_TEXT_DIM}; }}
.igc-title {{ font-size: 15pt; font-weight: 700; color: {_TEXT}; }}
.igc-subtitle {{ color: {_TEXT_DIM}; }}
/* Plain-language action headline — the OBJECT of consent, the card's LARGEST
   element so the user reads WHAT is being asked before any classification. */
.igc-headline {{ font-size: 18pt; font-weight: 700; color: {_TEXT}; }}
/* Template-missing fallback: the verbatim tool: args in monospace — the exact
   form, visually marked as raw (not a friendly summary). */
.igc-headline-mono {{ font-family: monospace; font-size: 13pt; font-weight: 700;
    color: {_ACCENT_BRIGHT}; }}
.igc-dim {{ color: {_TEXT_DIM}; font-size: 10pt; }}
.igc-badge {{ font-family: monospace; font-size: 9pt; padding: 3px 9px;
    border-radius: 9999px; }}
.igc-badge-direct {{ color: {_SUCCESS}; background-color: rgba(16,185,129,0.12);
    border: 1px solid rgba(16,185,129,0.30); }}
.igc-badge-warn {{ color: {_WARNING}; background-color: rgba(245,158,11,0.12);
    border: 1px solid rgba(245,158,11,0.35); }}
.igc-badge-danger {{ color: {_DESTRUCTIVE}; background-color: rgba(239,68,68,0.12);
    border: 1px solid rgba(239,68,68,0.40); }}
.igc-badge-dest {{ color: {_ACCENT_BRIGHT}; background-color: rgba(0,153,255,0.15);
    border: 1px solid rgba(0,153,255,0.25); }}
.igc-row {{ color: {_TEXT}; }}
.igc-leakwarn {{ color: {_WARNING}; font-size: 10pt; }}
/* Plain-language risk callout (review gate) — color + weight convey importance. */
.igc-risk {{ border-radius: 10px; padding: 11px 13px; border-left-width: 4px;
    border-left-style: solid; }}
.igc-risk-danger {{ background-color: rgba(239,68,68,0.10);
    border-left-color: {_DESTRUCTIVE}; }}
.igc-risk-warn {{ background-color: rgba(245,158,11,0.10);
    border-left-color: {_WARNING}; }}
.igc-risk-ok {{ background-color: rgba(16,185,129,0.08);
    border-left-color: {_SUCCESS}; }}
.igc-risk-head {{ font-weight: 700; }}
.igc-risk-head-danger {{ color: {_DESTRUCTIVE}; }}
.igc-risk-head-warn {{ color: {_WARNING}; }}
.igc-risk-head-ok {{ color: {_SUCCESS}; }}
.igc-risk-detail {{ color: {_TEXT}; }}
.igc-choices {{ color: {_TEXT_DIM}; font-size: 9pt; }}
scrolledwindow.igc-payload {{ border: 1px solid rgba(0,153,255,0.10);
    border-radius: 12px; }}
.igc-payload textview, .igc-payload textview text {{ background-color: {_VOID};
    color: #aab6cc; padding: 6px; }}
button.igc-allow {{ color: {_SUCCESS}; border: 1px solid rgba(16,185,129,0.50);
    background-image: none; background-color: {_VOID}; }}
button.igc-allowconv {{ color: #ffffff; border: 1px solid {_ACCENT};
    background-image: none; background-color: {_ACCENT}; font-weight: 700; }}
button.igc-allowconv-muted {{ color: {_ACCENT_BRIGHT}; border: 1px solid {_ACCENT};
    background-image: none; background-color: {_VOID}; }}
button.igc-deny {{ color: {_DESTRUCTIVE};
    border: 1px solid rgba(239,68,68,0.45);
    background-image: none; background-color: {_VOID}; }}
button.igc-primary {{ color: #ffffff; border: 1px solid {_ACCENT};
    background-image: none; background-color: {_ACCENT}; font-weight: 700; }}
button.igc-ghost {{ color: {_TEXT_DIM}; background-image: none;
    background-color: {_VOID}; border: 1px solid rgba(0,153,255,0.10); }}
.igc-footnote {{ color: #3d4f6a; font-size: 9pt; }}
"""


def _badge(text: str, css_classes: list[str]) -> Gtk.Label:
    """A plain, inert label — markup and mnemonics OFF (§E). Used for CHROME and
    for short content fields that are sanitised before being passed in."""
    lbl = Gtk.Label(label=text)
    lbl.set_use_markup(False)
    lbl.set_use_underline(False)
    lbl.set_xalign(0.0)
    lbl.set_wrap(True)
    lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    lbl.set_selectable(False)
    for c in css_classes:
        lbl.add_css_class(c)
    return lbl


class _DialogApp(Gtk.Application):
    def __init__(self, spec: dict, mode: str) -> None:
        super().__init__(
            application_id="org.intergenos.InterGenConsent",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.spec = spec
        self.mode = mode
        # Affirmative-only: deny is the default and only an explicit handler lifts it.
        self.result_code = proto.EXIT_DENY
        # The safe (Deny/Cancel) control — initial focus rests here so a reflexive
        # Enter/Space can never send or allow.
        self._safe_widget = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def do_activate(self) -> None:  # noqa: D401 (GTK vfunc)
        self._force_dark()
        self._load_css()
        win = self._build_window()
        win.connect("close-request", self._on_close_request)
        win.connect("map", self._on_mapped)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        win.add_controller(key)
        win.present()

    def _on_mapped(self, *_a) -> bool:
        # Rest focus on the safe (Deny/Cancel) control now the window is realised,
        # so a reflexive Enter/Space hits the fail-closed action, never Send/Allow.
        try:
            if self._safe_widget is not None:
                self._safe_widget.grab_focus()
        except Exception:
            pass
        # Window is on screen → fire the daemon's pre-render watchdog signal so it
        # switches from "fail fast if never rendered" to "wait for the human".
        try:
            sys.stdout.write(proto.RENDERED_MARKER + "\n")
            sys.stdout.flush()
        except Exception:
            pass
        return False

    def _on_close_request(self, *_a) -> bool:
        # Window X / compositor close → keep the deny default and quit.
        self.quit()
        return False

    def _on_key(self, _ctrl, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.quit()  # result_code stays EXIT_DENY
            return True
        return False

    def _decide(self, code: int) -> None:
        self.result_code = code
        self.quit()

    # ── styling ──────────────────────────────────────────────────────────────
    def _force_dark(self) -> None:
        """Ask for the dark color scheme so the chrome the theme draws (headerbar,
        window controls, default text) matches the dark InterGen palette in both
        focus states — independent of the system light/dark preference."""
        try:
            settings = Gtk.Settings.get_default()
            if settings is not None:
                settings.set_property("gtk-application-prefer-dark-theme", True)
        except Exception:
            pass

    def _load_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_string(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _hlabel(self, text: str, css_classes: list[str]) -> Gtk.Label:
        """A single-line, non-wrapping header label — wrapping a label inside the
        tight headerbar title area is what made the brand/mode text fall out."""
        lbl = Gtk.Label(label=text)
        lbl.set_use_markup(False)
        lbl.set_use_underline(False)
        lbl.set_wrap(False)
        lbl.set_ellipsize(Pango.EllipsizeMode.NONE)
        lbl.set_selectable(False)
        lbl.set_valign(Gtk.Align.CENTER)
        for c in css_classes:
            lbl.add_css_class(c)
        return lbl

    def _header(self, mode_label: str) -> Gtk.HeaderBar:
        hb = Gtk.HeaderBar()
        hb.add_css_class("igc-header")
        hb.set_show_title_buttons(True)
        # Suppress the centered window-title (we draw our own brand on the left).
        hb.set_title_widget(Gtk.Label())
        mark = Gtk.DrawingArea()
        mark.set_content_width(44)
        mark.set_content_height(26)
        mark.set_valign(Gtk.Align.CENTER)
        mark.set_draw_func(self._draw_mark)
        # Left-aligned, non-wrapping brand row: mark · InterGen · <mode>. Packed
        # at the START so it never competes with the window-control buttons for
        # the constrained centered-title slot (that contention forced the wrap).
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        box.append(mark)
        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        brand.append(self._hlabel("Inter", ["igc-brand"]))
        brand.append(self._hlabel("Gen", ["igc-brand-gen"]))
        box.append(brand)
        box.append(self._hlabel("·", ["igc-mode"]))
        box.append(self._hlabel(mode_label, ["igc-mode"]))
        hb.pack_start(box)
        return hb

    def _draw_mark(self, _area, cr, w, h, *_a) -> None:
        # The InterGen ECG pulse mark — short lead, spike left of centre, long
        # trailing tail — drawn from the same path as intergen/web/favicon.svg.
        pts = [(4, 16), (12, 16), (15, 9), (18, 24), (21, 16), (48, 16)]
        sx, sy = w / 52.0, h / 32.0
        cr.set_source_rgb(0.0, 0.6, 1.0)
        cr.set_line_width(2.4)
        cr.set_line_cap(1)   # round
        cr.set_line_join(1)  # round
        first = True
        for px, py in pts:
            if first:
                cr.move_to(px * sx, py * sy)
                first = False
            else:
                cr.line_to(px * sx, py * sy)
        cr.stroke()

    # ── inert payload view (§E) ──────────────────────────────────────────────
    def _payload_view(self, raw: str, highlight: bool,
                      min_height: int = 180, expand: bool = True) -> Gtk.ScrolledWindow:
        # Sanitise HERE so we hold the real badge ranges: badge spans are tagged a
        # distinct colour so literal payload text mimicking a 〈U+XXXX〉 badge cannot
        # be confused with a genuinely system-neutralised character (anti-spoof).
        shown, badge_ranges = sanitize_with_ranges(raw)
        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_left_margin(8)
        tv.set_right_margin(8)
        tv.set_top_margin(6)
        tv.set_bottom_margin(6)
        buf = tv.get_buffer()
        buf.set_text(shown)  # NOT insert_markup — inert
        if badge_ranges:
            btag = buf.create_tag("badge", foreground=_ACCENT_BRIGHT,
                                  background="#0e2a45", weight=Pango.Weight.BOLD)
            for start, end in badge_ranges:
                buf.apply_tag(btag, buf.get_iter_at_offset(start),
                              buf.get_iter_at_offset(end))
        if highlight:
            tag = buf.create_tag("secret", foreground=_WARNING, weight=Pango.Weight.BOLD)
            for start, end in detect_secret_ranges(shown):
                buf.apply_tag(
                    tag,
                    buf.get_iter_at_offset(start),
                    buf.get_iter_at_offset(end),
                )
        sw = Gtk.ScrolledWindow()
        sw.add_css_class("igc-payload")
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(min_height)
        sw.set_max_content_height(max(min_height, 320))
        sw.set_propagate_natural_height(True)
        sw.set_vexpand(expand)
        sw.set_child(tv)
        return sw

    def _button(self, label: str, css: str, code: int) -> Gtk.Button:
        btn = Gtk.Button(label=label)
        btn.set_use_underline(False)
        btn.add_css_class(css)
        btn.set_hexpand(True)
        btn.connect("clicked", lambda _b: self._decide(code))
        return btn

    # ── window builders ──────────────────────────────────────────────────────
    def _build_window(self) -> Gtk.ApplicationWindow:
        win = Gtk.ApplicationWindow(application=self)
        win.set_modal(True)
        win.set_default_size(520, 460)
        if self.mode == proto.MODE_REVIEW:
            win.set_title("InterGen — Review")
            win.set_titlebar(self._header("Review"))
            win.set_child(self._review_body())
        else:
            win.set_title("InterGen — Confirm send")
            win.set_titlebar(self._header("Confirm send"))
            win.set_child(self._consent_body())
        return win

    def _outer(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(18)
        box.set_margin_bottom(16)
        box.set_margin_start(20)
        box.set_margin_end(20)
        return box

    def _review_body(self) -> Gtk.Box:
        s = self.spec
        box = self._outer()
        box.append(_badge("Allow this action?", ["igc-title"]))
        box.append(_badge(
            "InterGen wants to run a tool on your machine.", ["igc-subtitle"]))

        # Action headline — the OBJECT of consent in plain words, the card's
        # largest element, so the user reads WHAT is asked before parsing the
        # tool name + args. A DETERMINISTIC per-(tool,action) template from the
        # daemon (K_HEADLINE), sanitised for display like any content field. When
        # no template matched (empty) fall CLOSED to the verbatim `tool: args`
        # monospace form — the exact call, never a fabricated friendly label. The
        # verbatim tool+args box stays below as the always-verifiable original.
        headline_text = str(s.get(proto.K_HEADLINE) or "").strip()
        if headline_text:
            box.append(_badge(
                sanitize_for_display(headline_text), ["igc-headline"]))
        else:
            tool_raw = sanitize_for_display(str(s.get(proto.K_TOOL) or "?"))
            args_raw = sanitize_for_display(
                " ".join(str(s.get(proto.K_ARGUMENTS) or "").split())[:200])
            box.append(_badge(
                f"{tool_raw}: {args_raw}" if args_raw else tool_raw,
                ["igc-headline-mono"]))

        # Plain-language risk breakdown — translate the jargon provenance into what
        # this IS and what allowing it CARRIES, with color + weight that convey
        # importance (the bare classification meant nothing to a new user). Unknown
        # provenance → danger (fail-safe).
        # A MISSING/empty provenance falls to "unknown" → danger via the mapper,
        # never to a reassuring green default (WC re-pass micro-note; fail-safe).
        prov = str(s.get(proto.K_PROVENANCE) or "unknown")
        severity, headline, detail = review_risk_copy(
            prov, s.get(proto.K_RISK_TIER) or None)
        risk = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        risk.add_css_class("igc-risk")
        risk.add_css_class(f"igc-risk-{severity}")
        risk.append(_badge(headline, ["igc-risk-head", f"igc-risk-head-{severity}"]))
        risk.append(_badge(detail, ["igc-risk-detail"]))
        box.append(risk)

        # Keep the raw classification too — small + dim — for the technical reader.
        badge_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        badge_row.append(_badge(
            sanitize_for_display(prov),
            ["igc-badge", provenance_badge_class(prov)]))
        badge_row.set_halign(Gtk.Align.START)
        box.append(badge_row)

        tool = sanitize_for_display(str(s.get(proto.K_TOOL) or "?"))
        box.append(_badge(f"Tool   {tool}", ["igc-row"]))
        args = s.get(proto.K_ARGUMENTS)
        if args:
            box.append(self._payload_view(str(args), True, min_height=56, expand=False))
        reason = s.get(proto.K_REASON)
        if reason:
            box.append(_badge(
                f"Held by   {sanitize_for_display(str(reason))}", ["igc-dim"]))
        src = s.get(proto.K_SOURCE)
        if src:
            box.append(_badge(
                f"Source   {sanitize_for_display(str(src))}", ["igc-dim"]))
        if s.get(proto.K_NEEDS_PKEXEC):
            box.append(_badge("Privilege   pkexec required", ["igc-dim"]))
        excerpt = s.get(proto.K_EXCERPT)
        if excerpt:
            box.append(_badge("Excerpt that triggered this:", ["igc-dim"]))
            box.append(self._payload_view(str(excerpt), True, min_height=52, expand=False))
        reasoning = s.get(proto.K_REASONING)
        if reasoning:
            box.append(_badge("InterGen's reasoning:", ["igc-dim"]))
            box.append(self._payload_view(str(reasoning), False, min_height=44, expand=False))

        # What each choice carries — so the decision is informed, not a guess.
        box.append(_badge(
            "Allow once runs only this action  ·  Allow this session stops asking "
            "for ones like it until the session ends  ·  Not now refuses it.",
            ["igc-choices"]))

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        actions.append(self._button(
            "Allow once", "igc-allow", proto.EXIT_REVIEW_ALLOW_ONCE))
        # Emphasize the broad-grant button ONLY when provenance is trusted. On a
        # risky action a filled accent would nudge toward over-granting, so there
        # it is a plain outline — nothing is pushed, the safe action holds focus.
        allowconv_css = "igc-allowconv" if severity == "ok" else "igc-allowconv-muted"
        actions.append(self._button(
            "Allow this session", allowconv_css,
            proto.EXIT_REVIEW_ALLOW_CONVERSATION))
        deny_btn = self._button("Not now", "igc-deny", proto.EXIT_DENY)
        self._safe_widget = deny_btn  # initial focus rests here (fail-safe)
        actions.append(deny_btn)
        box.append(actions)
        box.append(_badge(
            "Esc or closing the window denies — fail-closed.", ["igc-footnote"]))
        return box

    def _consent_body(self) -> Gtk.Box:
        s = self.spec
        box = self._outer()
        box.append(_badge("Send this to a cloud model?", ["igc-title"]))
        box.append(_badge(
            "This leaves your machine. Nothing is scanned on this hop.",
            ["igc-subtitle"]))

        provider = sanitize_for_display(str(s.get(proto.K_PROVIDER) or "your frontier model"))
        badge_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        badge_row.append(_badge(
            f"↗ {provider}", ["igc-badge", "igc-badge-dest"]))
        badge_row.set_halign(Gtk.Align.START)
        box.append(badge_row)

        reason = s.get(proto.K_REASON)
        if reason:
            box.append(_badge(
                f"Why   {sanitize_for_display(str(reason))}", ["igc-dim"]))

        box.append(_badge(
            "Exactly what will be sent — review ALL of it, "
            "including anything sensitive already in the conversation:",
            ["igc-dim"]))
        payload = str(s.get(proto.K_PAYLOAD) or "")
        box.append(self._payload_view(payload, True))
        box.append(_badge(
            "⚠  Anything shown above will be sent. Amber marks possible "
            "secrets — absence of a mark is not an all-clear.", ["igc-leakwarn"]))

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        cancel_btn = self._button("Cancel", "igc-ghost", proto.EXIT_DENY)
        self._safe_widget = cancel_btn  # initial focus rests here (fail-safe)
        actions.append(cancel_btn)
        actions.append(self._button(
            "Send", "igc-primary", proto.EXIT_CONSENT_SEND))
        box.append(actions)
        box.append(_badge(
            "Esc or closing the window cancels — nothing is sent.",
            ["igc-footnote"]))
        return box


def main() -> None:
    # Read the ENTIRE stdin object (framed JSON; payload is an opaque string value
    # a real parser reads — never line-scanned, so content bytes can't forge
    # protocol). Any failure to parse/validate → render-failed (the daemon falls
    # back to zenity; the user is never left without a prompt).
    try:
        raw = sys.stdin.buffer.read()
        spec = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        os._exit(proto.EXIT_RENDER_FAILED)
    if not isinstance(spec, dict):
        os._exit(proto.EXIT_RENDER_FAILED)
    mode = spec.get(proto.K_MODE)
    if mode not in (proto.MODE_REVIEW, proto.MODE_CONSENT):
        os._exit(proto.EXIT_RENDER_FAILED)
    # Never truncate the egress payload (§E): too-large = fail-closed deny, not a
    # truncated render. (The daemon enforces this too — defense in depth.)
    payload = spec.get(proto.K_PAYLOAD) or ""
    if len(str(payload).encode("utf-8", "surrogatepass")) > proto.MAX_PAYLOAD_BYTES:
        os._exit(proto.EXIT_DENY)

    app = _DialogApp(spec, mode)
    try:
        app.run([sys.argv[0]])  # hand GTK only the prog name — no arg parsing
    except Exception:
        # A failure to even initialise GTK = never showed the user anything →
        # render-failed so the daemon can fall back to zenity.
        os._exit(proto.EXIT_RENDER_FAILED)
    sys.exit(app.result_code)


if __name__ == "__main__":
    main()
