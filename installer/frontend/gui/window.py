"""Forge GUI — main window + 7-screen scaffolding.

Built on Adw.NavigationView. Each screen is a subclass that takes a shared
InstallerState, renders placeholder content + a real navigation footer, and
mutates state on next/back.

Phase 4 kickoff: navigation works end-to-end (Welcome → Done) with state
passing and back/next semantics; screen contents are placeholders. Later
phases fill in real widgets per screen (keyboard layout list, disk partition
view, password strength meter, etc.).
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk, GLib  # noqa: E402

from .state import InstallerState


# --------------------------------------------------------------------------
# Screen base class
# --------------------------------------------------------------------------


class _ForgePage(Adw.NavigationPage):
    """Base class for all 7 screens.

    Subclasses must:
      * define `tag` (used by NavigationView for back/forward routing)
      * implement `_build_body()` returning the screen's main content widget
      * optionally override `on_load(state)` (entry hook) and
        `on_next(state)` (validation + state mutation; return True to advance)
    """

    tag: str = ""
    title: str = ""

    def __init__(self, window):
        super().__init__(title=self.title, tag=self.tag)
        self._window = window  # reverse-ref so screens can advance via NavigationView

        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        body = self._build_body()
        body.set_margin_top(24)
        body.set_margin_bottom(24)
        body.set_margin_start(48)
        body.set_margin_end(48)
        body.set_vexpand(True)
        toolbar.set_content(body)

        # Footer: Back + Next on every screen (subclasses can override visibility)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        footer.set_halign(Gtk.Align.END)
        footer.set_margin_top(12)
        footer.set_margin_bottom(12)
        footer.set_margin_end(24)

        self.back_button = Gtk.Button(label="Back")
        self.back_button.connect("clicked", self._on_back_clicked)
        footer.append(self.back_button)

        self.next_button = Gtk.Button(label="Next")
        self.next_button.add_css_class("suggested-action")
        self.next_button.connect("clicked", self._on_next_clicked)
        footer.append(self.next_button)

        toolbar.add_bottom_bar(footer)

        self.set_child(toolbar)

    # ---- overrides ----

    def _build_body(self) -> Gtk.Widget:
        raise NotImplementedError

    def on_load(self, state: InstallerState):  # pragma: no cover  (subclass hook)
        """Called when this page becomes visible. Default: no-op."""
        return None

    def on_next(self, state: InstallerState) -> bool:
        """Called on Next click. Return True to advance, False to stay (e.g.
        validation failure). Default: advance."""
        return True

    # ---- private handlers ----

    def _on_back_clicked(self, _button):
        self._window.navigate_back()

    def _on_next_clicked(self, _button):
        if self.on_next(self._window.state):
            self._window.navigate_next()


# --------------------------------------------------------------------------
# 1. Welcome
# --------------------------------------------------------------------------


class WelcomePage(_ForgePage):
    tag = "welcome"
    title = "Welcome"

    def _build_body(self) -> Gtk.Widget:
        page = Adw.StatusPage()
        page.set_icon_name("system-software-install-symbolic")
        page.set_title("Welcome to InterGenOS")
        page.set_description(
            "Forge will guide you through the installation in seven short steps. "
            "You can go back at any time before the install begins."
        )
        return page

    def __init__(self, window):
        super().__init__(window)
        # First screen — no Back
        self.back_button.set_visible(False)


# --------------------------------------------------------------------------
# 2. Keyboard / Locale / Timezone
# --------------------------------------------------------------------------


class KeyboardLocalePage(_ForgePage):
    tag = "keyboard_locale"
    title = "Keyboard, Locale, Timezone"

    def _build_body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        heading = Gtk.Label(label="Keyboard, Locale, and Timezone")
        heading.add_css_class("title-1")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        placeholder = Gtk.Label(
            label="Placeholder — later phases will surface a keymap chooser, "
                  "locale list, and timezone picker. State currently defaults "
                  "to (us / en_US.UTF-8 / UTC)."
        )
        placeholder.set_wrap(True)
        placeholder.set_xalign(0)
        box.append(placeholder)

        # Minimal entries so state-passing is testable end-to-end during scaffolding.
        self._keymap_entry = Gtk.Entry(placeholder_text="us")
        self._locale_entry = Gtk.Entry(placeholder_text="en_US.UTF-8")
        self._tz_entry = Gtk.Entry(placeholder_text="UTC")

        box.append(_labeled("Keymap (xkb code, e.g. us)", self._keymap_entry))
        box.append(_labeled("Locale (e.g. en_US.UTF-8)", self._locale_entry))
        box.append(_labeled("Timezone (IANA tz, e.g. America/Chicago)", self._tz_entry))

        return box

    def on_load(self, state):
        self._keymap_entry.set_text(state.keymap)
        self._locale_entry.set_text(state.locale)
        self._tz_entry.set_text(state.timezone)

    def on_next(self, state):
        state.keymap = self._keymap_entry.get_text() or "us"
        state.locale = self._locale_entry.get_text() or "en_US.UTF-8"
        state.timezone = self._tz_entry.get_text() or "UTC"
        return True


# --------------------------------------------------------------------------
# 3. Disk
# --------------------------------------------------------------------------


class DiskPage(_ForgePage):
    tag = "disk"
    title = "Disk"

    def _build_body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        heading = Gtk.Label(label="Choose a disk")
        heading.add_css_class("title-1")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        warning = Gtk.Label(
            label="The selected disk WILL BE ERASED. Real disk-detection + "
                  "partition-view widget lands in a later phase. For now, "
                  "type the device path."
        )
        warning.set_wrap(True)
        warning.set_xalign(0)
        warning.add_css_class("warning")
        box.append(warning)

        self._disk_entry = Gtk.Entry(placeholder_text="/dev/sda")
        box.append(_labeled("Target disk", self._disk_entry))

        self._confirm_check = Gtk.CheckButton(
            label="I understand all data on this disk will be erased."
        )
        box.append(self._confirm_check)

        return box

    def on_load(self, state):
        if state.target_disk:
            self._disk_entry.set_text(state.target_disk)
        self._confirm_check.set_active(state.confirm_destructive)

    def on_next(self, state):
        path = self._disk_entry.get_text().strip()
        if not path:
            _toast(self._window, "Please enter a target disk path.")
            return False
        if not self._confirm_check.get_active():
            _toast(self._window, "Confirm the destructive operation to continue.")
            return False
        state.target_disk = path
        state.confirm_destructive = True
        return True


# --------------------------------------------------------------------------
# 4. User
# --------------------------------------------------------------------------


class UserPage(_ForgePage):
    tag = "user"
    title = "User"

    def _build_body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        heading = Gtk.Label(label="User account")
        heading.add_css_class("title-1")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        self._hostname_entry = Gtk.Entry(placeholder_text="intergenos")
        self._username_entry = Gtk.Entry(placeholder_text="user")
        self._user_pw_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self._user_pw_confirm_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self._root_pw_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self._root_pw_confirm_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self._mok_pw_entry = Gtk.PasswordEntry(show_peek_icon=True)

        box.append(_labeled("Hostname", self._hostname_entry))
        box.append(_labeled("Username", self._username_entry))
        box.append(_labeled("User password", self._user_pw_entry))
        box.append(_labeled("Confirm user password", self._user_pw_confirm_entry))
        box.append(_labeled("Root password", self._root_pw_entry))
        box.append(_labeled("Confirm root password", self._root_pw_confirm_entry))
        box.append(_labeled(
            "Secure Boot MOK enrollment password (one-time, EFI only)",
            self._mok_pw_entry,
        ))

        return box

    def on_load(self, state):
        self._hostname_entry.set_text(state.hostname)
        self._username_entry.set_text(state.username)

    def on_next(self, state):
        state.hostname = self._hostname_entry.get_text() or "intergenos"
        state.username = self._username_entry.get_text().strip()
        state.user_password = self._user_pw_entry.get_text()
        state.user_password_confirm = self._user_pw_confirm_entry.get_text()
        state.root_password = self._root_pw_entry.get_text()
        state.root_password_confirm = self._root_pw_confirm_entry.get_text()
        state.mok_password = self._mok_pw_entry.get_text()

        if not state.username:
            _toast(self._window, "Username is required.")
            return False
        if state.user_password != state.user_password_confirm:
            _toast(self._window, "User passwords don't match.")
            return False
        if state.root_password != state.root_password_confirm:
            _toast(self._window, "Root passwords don't match.")
            return False
        if not state.user_password or not state.root_password:
            _toast(self._window, "Both user and root passwords are required.")
            return False

        return True


# --------------------------------------------------------------------------
# 5. Confirm
# --------------------------------------------------------------------------


class ConfirmPage(_ForgePage):
    tag = "confirm"
    title = "Confirm"

    def _build_body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        heading = Gtk.Label(label="Confirm install")
        heading.add_css_class("title-1")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        self._summary_label = Gtk.Label(label="(populated at on_load)")
        self._summary_label.set_wrap(True)
        self._summary_label.set_xalign(0)
        self._summary_label.set_selectable(True)
        box.append(self._summary_label)

        warning = Gtk.Label(
            label="Clicking Next starts the install. This cannot be undone."
        )
        warning.add_css_class("warning")
        warning.set_wrap(True)
        warning.set_xalign(0)
        box.append(warning)

        return box

    def on_load(self, state):
        text = (
            f"Disk: {state.target_disk}\n"
            f"Hostname: {state.hostname}\n"
            f"Username: {state.username}\n"
            f"Keymap: {state.keymap}\n"
            f"Locale: {state.locale}\n"
            f"Timezone: {state.timezone}\n"
            f"Package groups: {', '.join(state.package_groups)}"
        )
        self._summary_label.set_label(text)
        self.next_button.set_label("Install")

    def on_next(self, state):
        if not state.is_ready_for_install():
            _toast(self._window, "Some required fields are missing — go back.")
            return False
        return True


# --------------------------------------------------------------------------
# 6. Progress
# --------------------------------------------------------------------------


class ProgressPage(_ForgePage):
    tag = "progress"
    title = "Installing"

    def _build_body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        heading = Gtk.Label(label="Installing InterGenOS")
        heading.add_css_class("title-1")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.set_show_text(True)
        self._progress_bar.set_text("Starting…")
        box.append(self._progress_bar)

        self._status_label = Gtk.Label(
            label="Placeholder — later phases will wire this to the backend "
                  "install pipeline (partition → extract → config → users → "
                  "bootloader → hooks)."
        )
        self._status_label.set_wrap(True)
        self._status_label.set_xalign(0)
        box.append(self._status_label)

        return box

    def __init__(self, window):
        super().__init__(window)
        # During install, no back/next — only "Cancel" via app close
        self.back_button.set_visible(False)
        self.next_button.set_label("Continue")
        self.next_button.set_sensitive(False)

    def on_load(self, state):
        state.install_started = True
        # Stub: pretend the install runs in 5 ticks.
        self._tick = 0
        GLib.timeout_add(400, self._fake_tick, state)

    def _fake_tick(self, state):
        self._tick += 1
        self._progress_bar.set_fraction(min(1.0, self._tick / 5.0))
        self._progress_bar.set_text(f"Step {self._tick}/5 (placeholder)")
        if self._tick >= 5:
            state.install_completed = True
            self.next_button.set_sensitive(True)
            self._status_label.set_label(
                "Placeholder install completed — click Continue to finish."
            )
            return False
        return True


# --------------------------------------------------------------------------
# 7. Done
# --------------------------------------------------------------------------


class DonePage(_ForgePage):
    tag = "done"
    title = "Done"

    def _build_body(self) -> Gtk.Widget:
        page = Adw.StatusPage()
        page.set_icon_name("emblem-ok-symbolic")
        page.set_title("Install complete")
        page.set_description(
            "Reboot, remove the install media, and (if EFI) follow the "
            "MokManager prompts to enroll the InterGenOS vendor cert."
        )
        return page

    def __init__(self, window):
        super().__init__(window)
        # Last screen — Back hidden; Next becomes "Quit"
        self.back_button.set_visible(False)
        self.next_button.set_label("Quit")

    def _on_next_clicked(self, _button):  # noqa: override
        self._window.close()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _labeled(label_text: str, widget: Gtk.Widget) -> Gtk.Widget:
    """Return a vertical box: small label above + widget."""
    container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    label = Gtk.Label(label=label_text)
    label.set_xalign(0)
    label.add_css_class("dim-label")
    container.append(label)
    container.append(widget)
    return container


def _toast(window, text: str):
    """Surface a transient message via the window's overlay."""
    if window.toast_overlay is None:
        # Headless smoke-test path — fall through to print.
        print(f"forge: {text}")
        return
    toast = Adw.Toast(title=text, timeout=4)
    window.toast_overlay.add_toast(toast)


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------


_SCREEN_ORDER = [
    WelcomePage,
    KeyboardLocalePage,
    DiskPage,
    UserPage,
    ConfirmPage,
    ProgressPage,
    DonePage,
]


class ForgeMainWindow(Adw.ApplicationWindow):
    def __init__(self, application, archive_dir, packages_dir, dry_run):
        super().__init__(application=application,
                         title="Forge — InterGenOS Installer")
        self.set_default_size(960, 720)

        self.archive_dir = archive_dir
        self.packages_dir = packages_dir
        self.dry_run = dry_run
        self.state = InstallerState()

        # Toast overlay wraps the navigation view so we can flash validation
        # messages without breaking the page layout.
        self.toast_overlay = Adw.ToastOverlay()
        self._nav_view = Adw.NavigationView()
        self.toast_overlay.set_child(self._nav_view)

        self.set_content(self.toast_overlay)

        # Pre-instantiate all 7 screens so navigate_back() returns to the same
        # widget instance the user previously saw (state persists in entries
        # without requiring on_load() to re-restore from InstallerState).
        self._screens = [cls(self) for cls in _SCREEN_ORDER]
        self._screen_index = 0

        # Push the welcome page; subsequent navigate_next pushes pull from
        # the pre-instantiated list.
        first = self._screens[0]
        first.on_load(self.state)
        self._nav_view.push(first)

    def navigate_next(self):
        if self._screen_index >= len(self._screens) - 1:
            return
        self._screen_index += 1
        next_page = self._screens[self._screen_index]
        next_page.on_load(self.state)
        self._nav_view.push(next_page)

    def navigate_back(self):
        if self._screen_index <= 0:
            return
        self._screen_index -= 1
        # NavigationView handles the visual pop; we just track the index.
        self._nav_view.pop()


class ForgeApplication(Adw.Application):
    def __init__(self, archive_dir, packages_dir, dry_run):
        super().__init__(application_id="org.intergenos.forge")
        self._archive_dir = archive_dir
        self._packages_dir = packages_dir
        self._dry_run = dry_run

    def do_activate(self):
        win = ForgeMainWindow(self, self._archive_dir, self._packages_dir,
                              self._dry_run)
        win.present()


def run_installer(archive_dir, packages_dir=None, dry_run=False):
    """Entry point invoked by installer/__main__.py dispatch when mode == 'gui'."""
    app = ForgeApplication(archive_dir, packages_dir, dry_run)
    app.run(None)
    return 0
