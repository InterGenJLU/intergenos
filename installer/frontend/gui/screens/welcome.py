# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Welcome screen — hero treatment, first page of the 9-screen flow.

Design intent ("shock and awe" pass v3 — edge-to-edge hero, 2026-05-25):

  * Hero banner across the FULL WINDOW WIDTH via the base class's
    pre-clamp slot. The operator-authored
    `intergenos_pulse_forge_hero.png` (1920×640, ~3:1 ECG pulse on void
    with subtle circuit-pattern halo) renders as a full-bleed banner —
    no margins on the sides, scales with window width.

  * Title at typography rank-0: 2.2em weight 700, white. No subtitle
    competing — the title is the moment.

  * ECG-blue tagline as the single secondary line. Voice is operator-
    tuned ("forged for you", "built from source").

  * One narrative paragraph as invitation. Centered, ~56ch max.

  * Single "Let's begin" CTA, pill-shaped, ECG blue. The base footer's
    Next button is HIDDEN on this page (decided 2026-05-25:
    avoid duplicate "go forward" actions). Back is hidden as always
    on the first page.

  * Dim footer-line citing GPL-3.0-or-later + InterGenJLU.

Layout (per _base.py two-tier shape):

    pre-clamp slot:  [════════ hero banner full width ════════]
    clamp (720):                Welcome to InterGenOS
                                 Built from source...
                                 <body paragraph>
                                 [ Let's begin ]
                                 InterGenOS · GPL-3.0...
"""

import os

from gi.repository import Adw, Gtk

from ._base import _ForgePage


HERO_PATH_CANDIDATES = (
    "/usr/share/intergenos/intergenos_pulse_forge_hero.png",
    # Fallback for systems without the operator-authored hero (older
    # ISOs before the asset was packaged): the lockup logo at 1024px.
    "/usr/share/intergenos/intergenos_logo_transparent_1024.png",
)


def _read_build_id() -> str | None:
    """BUILD_ID from the running medium's os-release, or None."""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("BUILD_ID="):
                    return line.split("=", 1)[1].strip().strip('"') or None
    except OSError:
        pass
    return None


def _find_hero() -> str | None:
    for p in HERO_PATH_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


class WelcomePage(_ForgePage):
    tag = "welcome"
    title = "Welcome"

    # ─── Pre-clamp slot: edge-to-edge hero banner ────────────────────
    def _build_pre_clamp(self) -> Gtk.Widget | None:
        hero_path = _find_hero()
        if not hero_path:
            return None
        hero = Gtk.Picture.new_for_filename(hero_path)
        # ContentFit.COVER + height-request 320 ≈ uncropped at Forge's
        # default 960-wide window (3:1 source aspect — image at 960 wide
        # is naturally 320 tall, so the box matches and nothing crops).
        # Wider windows incur a slight top/bottom crop (waveform is
        # centered, stays visible); narrower windows scale proportionally.
        # Earlier 240-tall box cropped ~40px top + bottom at default size
        # (operator-flagged 2026-05-25).
        hero.set_content_fit(Gtk.ContentFit.COVER)
        hero.set_size_request(-1, 320)
        hero.set_halign(Gtk.Align.FILL)
        hero.set_hexpand(True)
        hero.add_css_class("forge-welcome-hero")
        return hero

    # ─── Clamp slot: title + tagline + body + CTA stack ──────────────
    def _build_body(self) -> Gtk.Widget:
        # No vexpand spacers — when hero pushes content close to window
        # height, spacers can't shrink below zero and instead force the
        # CTA off-screen. Use explicit tight margins between elements so
        # the layout collapses gracefully on shorter displays.
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # ─── Title ────────────────────────────────────────────────────
        title = Gtk.Label(label="Welcome to InterGenOS")
        title.set_halign(Gtk.Align.CENTER)
        title.set_justify(Gtk.Justification.CENTER)
        title.add_css_class("forge-welcome-title")
        title.set_margin_top(4)
        title.set_margin_bottom(2)
        outer.append(title)

        # ─── Tagline (narrative voice) ────────────────────────────────
        tagline = Gtk.Label(label="Built from source. Forged for you.")
        tagline.set_halign(Gtk.Align.CENTER)
        tagline.set_justify(Gtk.Justification.CENTER)
        tagline.add_css_class("forge-welcome-tagline")
        tagline.set_margin_bottom(14)
        outer.append(tagline)

        # ─── Body invitation ──────────────────────────────────────────
        body_text = (
            "Forge will guide you through eight steps to shape this machine "
            "into your own. Every default has been chosen deliberately, every "
            "package compiled with intent. You can step back at any time "
            "before the install begins."
        )
        body = Gtk.Label(label=body_text)
        body.set_halign(Gtk.Align.CENTER)
        body.set_justify(Gtk.Justification.CENTER)
        body.set_wrap(True)
        body.set_max_width_chars(56)
        body.add_css_class("forge-welcome-body")
        body.set_margin_bottom(18)
        outer.append(body)

        # ─── Big CTA — the SINGLE primary action on this page ─────────
        cta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        cta_row.set_halign(Gtk.Align.CENTER)
        cta = Gtk.Button(label="Let's begin")
        cta.add_css_class("suggested-action")
        cta.add_css_class("pill")
        cta.add_css_class("forge-welcome-cta")
        cta.set_margin_bottom(10)
        cta.connect("clicked", lambda _b: self._window.navigate_next())
        cta_row.append(cta)
        outer.append(cta_row)

        # ─── Footer legal/attribution line ────────────────────────────
        # The build id names WHICH medium is installing (N-6): dim, footer
        # rank, absent cleanly when the medium carries no stamp.
        legal_text = "InterGenOS · GPL-3.0-or-later · InterGenJLU"
        build_id = _read_build_id()
        if build_id:
            legal_text += f" · {build_id}"
        legal = Gtk.Label(label=legal_text)
        legal.set_halign(Gtk.Align.CENTER)
        legal.add_css_class("forge-welcome-legal")
        outer.append(legal)

        return outer

    def __init__(self, window):
        super().__init__(window)
        # First page — no prior screen to back into. Hide Back.
        self.back_button.set_visible(False)
        # The body's "Let's begin" pill is the single primary CTA; the
        # base-class footer Next would duplicate it (decided
        # 2026-05-25: "I'd keep your new one and ditch the generic
        # 'next' for this screen").
        self.next_button.set_visible(False)

    def on_load(self, state):
        super().on_load(state)
        state.welcome_acked = True
