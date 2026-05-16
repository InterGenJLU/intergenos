"""Forge GUI — main window + screen navigation.

Built on Adw.NavigationView. The 7 screens are imported from
`installer.frontend.gui.screens` and pre-instantiated at window construct
time so back/forward navigation returns to the same widget instance the
user previously saw. State flows through a single `InstallerState`
dataclass (see `gui.state`).

Phase 6 (post-extraction): screen classes live in `gui/screens/*.py`,
shared base + helpers in `gui/screens/_base.py`, palette CSS in
`gui/style.py`. This module contains only the window + application
boilerplate and the `run_installer` entry point invoked from
`installer/__main__.py` dispatch.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from .screens import SCREEN_ORDER
from .state import InstallerState
from .style import apply_intergenos_style


class ForgeMainWindow(Adw.ApplicationWindow):
    def __init__(self, application, archive_dir, packages_dir, dry_run):
        super().__init__(application=application,
                         title="Forge — InterGenOS Installer")
        self.set_default_size(960, 720)

        self.archive_dir = archive_dir
        self.packages_dir = packages_dir
        self.dry_run = dry_run
        self.state = InstallerState()

        # Toast overlay wraps the navigation view so screens can flash
        # validation messages without breaking the page layout.
        self.toast_overlay = Adw.ToastOverlay()
        self._nav_view = Adw.NavigationView()
        self.toast_overlay.set_child(self._nav_view)

        self.set_content(self.toast_overlay)

        # Pre-instantiate all 7 screens so navigate_back() returns to the
        # same widget instance the user previously saw — Gtk entries
        # remember their text without re-loading from InstallerState.
        self._screens = [cls(self) for cls in SCREEN_ORDER]

        # In-flight nav-button gating. Adw.NavigationView's push/pop
        # animations are async; without this flag, a rapid double-click
        # on Back can fire navigate_back twice — the first pop is still
        # in progress when the second arrives, so we'd pop twice but
        # only one visual transition lands. Block re-entry until the
        # `popped`/`pushed` signal confirms the previous transition is
        # done.
        self._nav_busy = False
        self._nav_view.connect("popped", self._on_nav_popped)
        self._nav_view.connect("pushed", self._on_nav_pushed)

        first = self._screens[0]
        first.on_load(self.state)
        self._nav_view.push(first)

    @property
    def _current_screen_index(self):
        """Index of the currently-visible screen in `_screens`.

        Derived from `_nav_view.get_visible_page()` rather than tracked
        as a separate counter, so we can never desync from the visual
        stack on rapid-click race.
        """
        visible = self._nav_view.get_visible_page()
        for i, page in enumerate(self._screens):
            if page is visible:
                return i
        # If NavigationView hasn't pushed anything yet (constructor race),
        # the first screen is implicit.
        return 0

    def _on_nav_pushed(self, _view):
        self._nav_busy = False

    def _on_nav_popped(self, _view, _page):
        self._nav_busy = False

    def navigate_next(self):
        if self._nav_busy:
            return
        idx = self._current_screen_index
        if idx >= len(self._screens) - 1:
            return
        self._nav_busy = True
        next_page = self._screens[idx + 1]
        next_page.on_load(self.state)
        self._nav_view.push(next_page)

    def navigate_back(self):
        if self._nav_busy:
            return
        if self._current_screen_index <= 0:
            return
        self._nav_busy = True
        self._nav_view.pop()


class ForgeApplication(Adw.Application):
    def __init__(self, archive_dir, packages_dir, dry_run):
        super().__init__(application_id="org.intergenos.forge")
        self._archive_dir = archive_dir
        self._packages_dir = packages_dir
        self._dry_run = dry_run
        self._css_provider = None  # held to prevent GC

    def do_activate(self):
        # Apply the InterGenOS visual-language CSS provider once per
        # application activation. Held on self so it isn't GC'd.
        if self._css_provider is None:
            self._css_provider = apply_intergenos_style()

        win = ForgeMainWindow(self, self._archive_dir, self._packages_dir,
                              self._dry_run)
        win.present()


def run_installer(archive_dir, packages_dir=None, dry_run=False):
    """Entry point invoked by installer/__main__.py dispatch when mode == 'gui'."""
    app = ForgeApplication(archive_dir, packages_dir, dry_run)
    app.run(None)
    return 0
