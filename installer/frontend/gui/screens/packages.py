"""Packages screen — fifth page of the 8-screen flow.

Inserted between User and Confirm per owner ratification 2026-05-16:
"all expected features present" → users get a software-selection screen
in v1.0, not a defaults-only install.

Reads `installer.backend.packages.GROUPS` as the single source of truth
for available groups + their descriptions + required/default flags.
Renders each group as a check row; `required=True` groups (currently
just `core`) render as locked-on so the user sees what's mandatory
without being able to break the install by un-toggling it. The
InstallerState invariant in __post_init__ also enforces core-presence
defensively, but the UI lock is the clearer UX.
"""

from gi.repository import Gtk

from installer.backend.packages import GROUPS

from ._base import _ForgePage, _toast


class PackagesPage(_ForgePage):
    tag = "packages"
    title = "Software selection"

    def _build_body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        heading = Gtk.Label(label="Pick what gets installed")
        heading.add_css_class("title-1")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        intro = Gtk.Label(
            label="Choose which package groups to install alongside the "
                  "essential system. You can install or remove anything later "
                  "with `pkm install <name>` / `pkm remove <name>`."
        )
        intro.add_css_class("dim-label")
        intro.set_wrap(True)
        intro.set_xalign(0)
        box.append(intro)

        # Group rows. Stable iteration via sorted name so the UI order is
        # deterministic across Python versions. `core` is sorted alphabetically
        # but its visual treatment (locked-on, dim) makes the ordering obvious.
        self._checks = {}
        for name in sorted(GROUPS.keys()):
            spec = GROUPS[name]
            row = self._build_group_row(name, spec)
            box.append(row)

        # --- Optional services subsection (D-010 InterGen AI opt-in) ---
        # AI opt-in is NOT a package group (package_groups is a list of
        # tier names that select what to install); it's a service-enable
        # decision that flows through install_io.intergen_ai_enable. The
        # subheading disambiguates UI semantics so the user understands
        # this row is different from the group rows above.
        services_heading = Gtk.Label(label="Optional services")
        services_heading.add_css_class("heading")
        services_heading.set_halign(Gtk.Align.START)
        services_heading.set_margin_top(18)
        box.append(services_heading)

        intergen_row = self._build_intergen_row()
        box.append(intergen_row)

        return box

    def _build_intergen_row(self):
        """D-010 InterGen AI opt-in row.

        Default OFF per the operator directive. The state field
        `intergen_ai_enable` carries the choice; PHASE_SERVICES
        conditionally runs `systemctl --global enable
        intergen.service` in the target chroot on YES.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_top(6)
        row.set_margin_bottom(6)

        check = Gtk.CheckButton()
        check.set_valign(Gtk.Align.START)
        check.set_active(False)  # D-010: default NO
        row.append(check)
        self._intergen_check = check

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        title_label = Gtk.Label(
            label="Enable the InterGen AI assistant? (default: NO)"
        )
        title_label.set_xalign(0)
        title_label.add_css_class("heading")
        text_box.append(title_label)

        desc_label = Gtk.Label(
            label="When enabled, the InterGen AI assistant starts "
                  "at first login. You can opt in later by running "
                  "`systemctl --user enable intergen.service`."
        )
        desc_label.set_xalign(0)
        desc_label.set_wrap(True)
        desc_label.add_css_class("dim-label")
        text_box.append(desc_label)

        row.append(text_box)
        return row

    def _build_group_row(self, name, spec):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_top(6)
        row.set_margin_bottom(6)

        check = Gtk.CheckButton()
        check.set_valign(Gtk.Align.START)
        # Pre-set based on spec: required=True → on + insensitive (locked);
        # required=False → on/off per spec.get('default', False); on_load
        # later overrides from existing state if user re-entered.
        if spec.get("required", False):
            check.set_active(True)
            check.set_sensitive(False)
        else:
            check.set_active(bool(spec.get("default", False)))
        row.append(check)
        self._checks[name] = check

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        title_text = name
        if spec.get("required"):
            title_text += "   (required)"
        title_label = Gtk.Label(label=title_text)
        title_label.set_xalign(0)
        title_label.add_css_class("heading")
        text_box.append(title_label)

        desc_label = Gtk.Label(label=spec.get("description", ""))
        desc_label.set_xalign(0)
        desc_label.set_wrap(True)
        desc_label.add_css_class("dim-label")
        text_box.append(desc_label)

        row.append(text_box)
        return row

    def on_load(self, state):
        # Re-load checkbox state from InstallerState — handles the
        # back-then-forward case where the user toggled before going back.
        for name, check in self._checks.items():
            spec = GROUPS[name]
            if spec.get("required", False):
                # Required groups stay locked-on regardless of state. The
                # __post_init__ invariant also enforces core; this is the
                # UI mirror of the same rule.
                check.set_active(True)
                check.set_sensitive(False)
            else:
                check.set_active(name in state.package_groups)

        # D-010 InterGen AI opt-in row — restore state on back-then-forward
        self._intergen_check.set_active(state.intergen_ai_enable)

    def on_next(self, state):
        chosen = []
        for name, check in self._checks.items():
            if check.get_active():
                chosen.append(name)
        # Defensive: re-apply the core invariant even though __post_init__
        # already enforces it on the dataclass. If a future schema change
        # marks core as non-required, the orchestrator's validate phase
        # would catch the omission — but surfacing it on this screen with
        # a toast is friendlier than waiting for the validate-phase failure.
        if "core" not in chosen:
            _toast(self._window,
                   "The core package group is required and cannot be unchecked.")
            chosen = ["core"] + chosen

        state.package_groups = chosen
        # D-010: record the InterGen opt-in choice
        state.intergen_ai_enable = self._intergen_check.get_active()
        return True
