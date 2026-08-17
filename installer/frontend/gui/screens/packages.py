# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Packages screen — fifth page of the 9-screen flow.

2026-05-26 Wave-3 "shock-and-awe" rewrite, with decided
ordering + wording overhaul at ~07:40 CDT same day. Reads
`installer.backend.packages.GROUPS` as the single source of truth for
available groups (5: core, base, desktop-gnome, extra, ai) +
required/default flags.

Page layout:

  * Hero "Software selection" card — big package icon on the left,
    live "N of M groups · K services" summary in monospace on the
    right, status-badge row that flips as the user toggles.

  * Operator-authored intro paragraph with inline-code rendering for
    the `sudo pkm install` / `sudo pkm remove` commands and a
    clickable "CLICK HERE" link that opens docs/users/intergen.md in
    the shared inline doc viewer.

  * Package groups PreferencesGroup — Adw.SwitchRow per group, in
    the operator-specified order: Core (locked) → GNOME Desktop
    (locked) → Base CLI tools → Extras. Locked groups (Core +
    GNOME Desktop) render switch active + sensitive=False with a
    "Required" badge in the title.

  * Optional services PreferencesGroup — Enable InterGen (service
    autostart, D-010) → Local AI runtime (the `ai` package group,
    rendered as a service-style toggle since it sits adjacent to
    InterGen) → Enable SSH server (D-019).

  * **InterGen ↔ AI runtime auto-couple** (decided
    2026-05-26): turning InterGen ON forces AI runtime ON (service
    can't run without binaries); turning AI runtime OFF forces
    InterGen OFF (same constraint, other direction). The other two
    transitions are independent.

  * SSH public-key Adw.ActionRow + multi-line Gtk.TextView in a
    card-styled ScrolledWindow reveals when SSH server toggles on
    (D-019 Option C).

D-010/D-019 contracts preserved; validators run in on_next() with the
same shape as the previous implementation.
"""

from gi.repository import Adw, GLib, Gtk

from installer.backend.packages import GROUPS

from .. import doc_viewer
from ._base import _ForgePage, _toast


# Human-friendly display names for the group keys.
_GROUP_LABELS = {
    "core": "Core system",
    "base": "Base CLI tools",
    "desktop-gnome": "GNOME Desktop",
    "extra": "Extras",
    "ai": "Local AI runtime",
}

# Decided layout: which groups render in the "Package groups"
# section + which render in the "Optional services" section. AI is a
# package group semantically (it ships intergen + llama-cpp binaries
# via the `ai` tier) but lives in the services section visually so it
# sits adjacent to the Enable-InterGen service toggle.
_GROUPS_IN_PACKAGE_SECTION = ("core", "desktop-gnome", "base", "extra")
_GROUPS_IN_SERVICE_SECTION = ("ai",)

# Inline-code formatting for `sudo pkm install` / `sudo pkm remove` in
# the intro paragraph — gives them a code-block visual treatment via
# Pango span + tt nesting. Pango doesn't have a true inline-code box
# primitive; bgcolor + fgcolor + monospace approximate it.
_CODE_OPEN = "<span bgcolor='#0a0e1a' fgcolor='#0099FF'><tt> "
_CODE_CLOSE = " </tt></span>"


class PackagesPage(_ForgePage):
    tag = "packages"
    title = "Software selection"

    def _build_body(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.set_hexpand(True)

        # ─── HERO SOFTWARE-SELECTION CARD ────────────────────────────
        self._hero_card = self._build_hero_card()
        page.append(self._hero_card)

        # ─── INSTRUCTION LINE (operator-authored, with rich markup) ──
        # Inline code blocks for the pkm commands + a clickable
        # CLICK HERE link routed through activate-link to the shared
        # doc viewer (opens docs/users/intergen.md).
        intro_markup = (
            "Below you'll find a few groups of packages that many users "
            "tend to install on their systems, so we've made them "
            "available during installation via a toggle if you'd like "
            "to include them. You can easily remove them from (or add "
            "more to) your running system with PKM (the InterGenOS "
            f"package manager) using {_CODE_OPEN}sudo pkm install "
            f"&lt;package name&gt;{_CODE_CLOSE} or {_CODE_OPEN}sudo pkm "
            f"remove &lt;package name&gt;{_CODE_CLOSE} after the system "
            "is installed. There are also a few configuration options "
            "listed — including enabling \"InterGen\", the system's "
            "dedicated AI. <a href='forge://doc/intergen'>CLICK HERE</a> "
            "to read more about him if you're curious."
        )
        intro = Gtk.Label()
        intro.set_use_markup(True)
        intro.set_markup(intro_markup)
        intro.add_css_class("forge-instruction")
        intro.set_halign(Gtk.Align.FILL)
        intro.set_justify(Gtk.Justification.LEFT)
        intro.set_xalign(0.0)
        intro.set_wrap(True)
        intro.set_max_width_chars(72)
        intro.connect("activate-link", self._on_intro_link)
        page.append(intro)

        # ─── PACKAGE GROUPS PREFERENCESGROUP ─────────────────────────
        groups_section = Adw.PreferencesGroup()
        groups_section.set_title("Package groups")
        groups_section.set_description(
            "Core + GNOME Desktop are required. Base + Extras are "
            "your choice; toggle them on now or install later."
        )

        self._group_switches: dict[str, Adw.SwitchRow] = {}
        for name in _GROUPS_IN_PACKAGE_SECTION:
            if name not in GROUPS:
                continue
            row = self._build_group_switch(name)
            groups_section.add(row)

        page.append(groups_section)

        # ─── OPTIONAL SERVICES PREFERENCESGROUP ──────────────────────
        services_section = Adw.PreferencesGroup()
        services_section.set_title("Optional services")
        services_section.set_description(
            "These don't auto-start by default; opt in here to enable "
            "them at first boot."
        )

        # D-010 InterGen AI service autostart.
        self._intergen_switch = Adw.SwitchRow()
        self._intergen_switch.set_title("Enable InterGen")
        self._intergen_switch.set_subtitle(
            "Starts the InterGen AI assistant at first login. First "
            "use downloads the local model (~4-5 GB on standard "
            "hardware, larger on premium tiers). No data leaves your "
            "machine. Enabling this auto-enables the Local AI runtime "
            "below (the service needs the binaries to run)."
        )
        self._intergen_switch.set_active(False)  # D-010 default OFF
        self._intergen_switch.connect("notify::active",
                                      self._on_intergen_toggled)
        services_section.add(self._intergen_switch)

        # AI runtime — a package group rendered as a service toggle so
        # it sits adjacent to its consumer (InterGen). State still
        # tracked via state.package_groups (it IS a package group); the
        # SwitchRow placement is a visual decision, not a semantic one.
        for name in _GROUPS_IN_SERVICE_SECTION:
            if name not in GROUPS:
                continue
            row = self._build_group_switch(name)
            services_section.add(row)
        # Connect AI-runtime → InterGen auto-couple after the row was
        # built so the handler reference is valid.
        if "ai" in self._group_switches:
            self._group_switches["ai"].connect(
                "notify::active", self._on_ai_runtime_toggled,
            )

        # D-019 SSH server opt-in.
        self._ssh_switch = Adw.SwitchRow()
        self._ssh_switch.set_title("Enable SSH server")
        self._ssh_switch.set_subtitle(
            "Starts sshd at boot and opens TCP port 22 in nftables so "
            "you can SSH in from another machine. Root login over SSH "
            "is always blocked — you log in as your user and sudo to "
            "root."
        )
        self._ssh_switch.set_active(False)  # D-019 default OFF
        self._ssh_switch.connect("notify::active", self._on_ssh_toggled)
        services_section.add(self._ssh_switch)

        # SSH public-key reveal rows (description + multi-line entry).
        self._ssh_key_row = Adw.ActionRow()
        self._ssh_key_row.set_title("SSH public key (optional)")
        self._ssh_key_row.set_subtitle(
            "Paste your SSH public key (e.g. `ssh-ed25519 AAAAC3...`). "
            "If provided, password-based SSH login is disabled and the "
            "server accepts key-based authentication only. Leave blank "
            "to allow password SSH login (easier first-time setup; "
            "weaker against brute-force)."
        )
        services_section.add(self._ssh_key_row)

        self._ssh_key_entry_row = Adw.ActionRow()
        self._ssh_key_entry_row.set_title("Public key")
        self._ssh_key_view = Gtk.TextView()
        self._ssh_key_view.set_wrap_mode(Gtk.WrapMode.CHAR)
        self._ssh_key_view.set_monospace(True)
        self._ssh_key_scroller = Gtk.ScrolledWindow()
        self._ssh_key_scroller.set_min_content_height(72)
        self._ssh_key_scroller.set_min_content_width(360)
        self._ssh_key_scroller.set_hexpand(True)
        self._ssh_key_scroller.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC,
        )
        self._ssh_key_scroller.add_css_class("forge-ssh-key-card")
        self._ssh_key_scroller.set_child(self._ssh_key_view)
        self._ssh_key_entry_row.add_suffix(self._ssh_key_scroller)
        services_section.add(self._ssh_key_entry_row)

        self._ssh_key_row.set_visible(False)
        self._ssh_key_entry_row.set_visible(False)

        page.append(services_section)

        # Re-entrance guard for the auto-couple handlers so they don't
        # bounce back and forth when one fires and forces the other.
        self._coupling = False

        return page

    def _build_group_switch(self, name: str) -> Adw.SwitchRow:
        """Build the Adw.SwitchRow for a package group + register it
        in self._group_switches. Locked groups (required=True) render
        switch active + sensitive=False with a 'Required' badge in
        the title; everything else respects spec.get('default')."""
        spec = GROUPS[name]
        row = Adw.SwitchRow()
        # These titles and subtitles are prose, not markup. libadwaita parses
        # them as Pango markup by default, and the Extras group's description
        # ("Applications & virtualization (…)") contains an ampersand — so it
        # failed to parse and rendered as NOTHING, with only a GTK warning to
        # say so. Measured 2026-08-05: "Entity did not end with a semicolon;
        # most likely you used an ampersand character without intending to
        # start an entity". Found while building the Graphics page, which hit
        # the identical class on its own copy; fixed here at the same time so
        # the Extras description is not invisible on every install.
        row.set_use_markup(False)
        human = _GROUP_LABELS.get(name, name)
        if spec.get("required", False):
            row.set_title(f"{human}  ·  Required")
            row.set_active(True)
            row.set_sensitive(False)
        else:
            row.set_title(human)
            row.set_active(bool(spec.get("default", False)))
        row.set_subtitle(spec.get("description", ""))
        row.connect("notify::active", self._on_group_changed)
        self._group_switches[name] = row
        return row

    # ─── HERO CARD ────────────────────────────────────────────────────
    def _build_hero_card(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("forge-packages-hero")
        card.set_hexpand(True)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)

        self._hero_icon = Gtk.Image.new_from_icon_name(
            "package-x-generic-symbolic"
        )
        self._hero_icon.set_pixel_size(76)
        self._hero_icon.add_css_class("forge-packages-icon")
        self._hero_icon.set_valign(Gtk.Align.CENTER)
        self._hero_icon.set_halign(Gtk.Align.CENTER)
        self._hero_icon.set_size_request(108, 108)
        top_row.append(self._hero_icon)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        info.set_hexpand(True)
        info.set_valign(Gtk.Align.CENTER)

        self._hero_count = Gtk.Label()
        self._hero_count.add_css_class("forge-packages-count")
        self._hero_count.set_halign(Gtk.Align.START)
        info.append(self._hero_count)

        self._hero_subtitle = Gtk.Label()
        self._hero_subtitle.add_css_class("forge-packages-subtitle")
        self._hero_subtitle.set_halign(Gtk.Align.START)
        self._hero_subtitle.set_label(
            "Pick what gets installed alongside the essential system."
        )
        info.append(self._hero_subtitle)

        top_row.append(info)
        card.append(top_row)

        self._hero_badges = Gtk.Label()
        self._hero_badges.add_css_class("forge-packages-badges")
        self._hero_badges.set_halign(Gtk.Align.START)
        self._hero_badges.set_use_markup(True)
        self._hero_badges.set_wrap(True)
        card.append(self._hero_badges)

        return card

    def _update_hero(self) -> None:
        """Repaint hero count + status badges from current switch
        state."""
        active_groups = [
            name for name, row in self._group_switches.items()
            if row.get_active()
        ]
        total_groups = len(self._group_switches)
        service_count = (
            int(self._intergen_switch.get_active())
            + int(self._ssh_switch.get_active())
        )

        self._hero_count.set_label(
            f"{len(active_groups)} of {total_groups} groups   ·   "
            f"{service_count} optional service"
            f"{'s' if service_count != 1 else ''}"
        )

        def badge(label: str, ok: bool, locked: bool = False) -> str:
            if locked:
                mark = "<span color='#0099FF' weight='bold'>🔒</span>"
                color = "#b6c3d2"
            elif ok:
                mark = "<span color='#34d399' weight='bold'>✓</span>"
                color = "#b6c3d2"
            else:
                mark = "<span color='#7a8ba8'>○</span>"
                color = "#7a8ba8"
            return (
                f"<span color='{color}'>{mark}  "
                f"{GLib.markup_escape_text(label)}</span>"
            )

        parts = []
        # Render package-section badges first, then service-section.
        for name in _GROUPS_IN_PACKAGE_SECTION + _GROUPS_IN_SERVICE_SECTION:
            if name not in self._group_switches:
                continue
            spec = GROUPS[name]
            row = self._group_switches[name]
            human = _GROUP_LABELS.get(name, name)
            parts.append(badge(
                human, row.get_active(),
                locked=spec.get("required", False),
            ))
        if self._intergen_switch.get_active():
            parts.append(badge("InterGen", True))
        if self._ssh_switch.get_active():
            parts.append(badge("SSH", True))

        self._hero_badges.set_markup("   ·   ".join(parts))

    # ─── HANDLERS ─────────────────────────────────────────────────────
    def _on_group_changed(self, *_):
        self._update_hero()

    def _on_intergen_toggled(self, switch_row, _pspec):
        # Auto-couple: turning InterGen ON forces AI runtime ON (the
        # service needs the binaries). Turning InterGen OFF does NOT
        # auto-turn-off AI runtime — user may want the binaries
        # installed without the service auto-starting at boot.
        if self._coupling:
            return
        active = switch_row.get_active()
        if active and "ai" in self._group_switches:
            ai_row = self._group_switches["ai"]
            if not ai_row.get_active():
                self._coupling = True
                try:
                    ai_row.set_active(True)
                finally:
                    self._coupling = False
        self._update_hero()

    def _on_ai_runtime_toggled(self, switch_row, _pspec):
        # Auto-couple: turning AI runtime OFF forces InterGen OFF (the
        # service can't run without the binaries). Turning AI runtime
        # ON does NOT auto-enable InterGen — user may want the
        # binaries installed without auto-starting the service.
        if self._coupling:
            return
        active = switch_row.get_active()
        if not active and self._intergen_switch.get_active():
            self._coupling = True
            try:
                self._intergen_switch.set_active(False)
            finally:
                self._coupling = False
        self._update_hero()

    def _on_ssh_toggled(self, switch_row, _pspec):
        active = switch_row.get_active()
        self._ssh_key_row.set_visible(active)
        self._ssh_key_entry_row.set_visible(active)
        if not active:
            # Zero the key buffer on toggle-off so a stale paste doesn't
            # survive an off-then-on cycle (same contract as the LUKS
            # passphrase fields on the Disk page).
            buf = self._ssh_key_view.get_buffer()
            buf.set_text("")
        self._update_hero()

    def _on_intro_link(self, _label, uri):
        # Custom URL scheme — route to the inline doc viewer instead of
        # gtk_show_uri (which would try to launch xdg-open). Returning
        # True marks the link as handled.
        if uri == "forge://doc/intergen":
            doc_viewer.open_doc_by_filename(
                self._window,
                filename="intergen.md",
                title="Meet InterGen — the InterGenOS AI assistant",
                doc_label="InterGen overview",
            )
            return True
        return False

    # ─── LIFECYCLE ────────────────────────────────────────────────────
    def on_load(self, state):
        for name, row in self._group_switches.items():
            spec = GROUPS[name]
            if spec.get("required", False):
                row.set_active(True)
                row.set_sensitive(False)
            else:
                row.set_active(name in state.package_groups)

        self._intergen_switch.set_active(state.intergen_ai_enable)
        self._ssh_switch.set_active(state.ssh_server_enable)
        self._ssh_key_row.set_visible(state.ssh_server_enable)
        self._ssh_key_entry_row.set_visible(state.ssh_server_enable)

        buf = self._ssh_key_view.get_buffer()
        buf.set_text(state.ssh_public_key or "")

        self._update_hero()

    def on_next(self, state):
        chosen = [
            name for name, row in self._group_switches.items()
            if row.get_active()
        ]
        # Defensive: re-apply the required-group invariants even though
        # __post_init__ already enforces them on the dataclass.
        for required_name in ("core", "desktop-gnome"):
            if (required_name in GROUPS
                    and GROUPS[required_name].get("required", False)
                    and required_name not in chosen):
                _toast(self._window,
                       f"The {required_name} group is required and "
                       "cannot be unchecked.")
                chosen.append(required_name)

        state.package_groups = chosen
        state.intergen_ai_enable = self._intergen_switch.get_active()
        state.ssh_server_enable = self._ssh_switch.get_active()

        # Sanity-check: the auto-couple handlers should keep this from
        # ever happening, but if the runtime got disabled by some other
        # path (state restoration, manual hand-edit of install.yaml on
        # back-then-forward) and InterGen is still on, warn the user
        # rather than ship a service-with-no-binaries.
        if state.intergen_ai_enable and "ai" not in chosen:
            _toast(self._window,
                   "InterGen is enabled but the Local AI runtime is not "
                   "selected. The runtime ships the binaries the service "
                   "needs to run — turn it on, or turn InterGen off.")
            return False

        # D-019 / sshd-password-auth closure: record the optional
        # public key (only meaningful when ssh_server_enable=True).
        if state.ssh_server_enable:
            buf = self._ssh_key_view.get_buffer()
            start, end = buf.get_bounds()
            key_text = buf.get_text(start, end, False).strip()
            if key_text and not self._looks_like_ssh_pubkey(key_text):
                _toast(self._window,
                       "That doesn't look like an SSH public key. It "
                       "should start with `ssh-ed25519` / `ssh-rsa` / "
                       "`ecdsa-sha2-nistp256` (etc.) followed by the "
                       "key material. Leave blank to keep password SSH "
                       "login enabled.")
                return False
            state.ssh_public_key = key_text
        else:
            state.ssh_public_key = ""
        return True

    @staticmethod
    def _looks_like_ssh_pubkey(text: str) -> bool:
        """Minimal validation of an SSH public-key line.

        Accepts the common key-type prefixes (ssh-rsa, ssh-ed25519,
        ecdsa-sha2-nistp{256,384,521}, sk-* for FIDO2 hardware tokens)
        followed by whitespace + at least one base64-ish chunk. Does
        NOT cryptographically verify the key — that happens at
        sshd-load time and produces a clear error in journalctl if the
        key is malformed."""
        line = text.strip().split("\n", 1)[0]
        parts = line.split(None, 2)
        if len(parts) < 2:
            return False
        key_type = parts[0]
        valid_prefixes = (
            "ssh-rsa",
            "ssh-ed25519",
            "ssh-dss",
            "ecdsa-sha2-nistp256",
            "ecdsa-sha2-nistp384",
            "ecdsa-sha2-nistp521",
            "sk-ssh-ed25519@openssh.com",
            "sk-ecdsa-sha2-nistp256@openssh.com",
        )
        return key_type in valid_prefixes
