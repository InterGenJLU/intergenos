# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The Chronicle GTK4/libadwaita GUI (spec §11).

app-id org.intergenos.Chronicle, used identically as the GApplication id, the
StartupWMClass, the .desktop basename, and the Icon name (the load-bearing house
rule). Every action maps to a `chronicle` CLI verb with the same effect (spec
§1: the GUI and CLI are peers); the GUI is a thin front end over the same engine
IPC surface.

Three views:
  * Overview — target + capture health, and capture-now buttons.
  * Timeline — browse a layer's versions by time; restore (with confirmation,
    never a silent overwrite) or pin a version.
  * Setup    — scan candidate targets, see honest protection labels, adopt a
    whole volume or a size-capped directory, or take the guided GParted
    hand-off for a non-POSIX drive.

This module imports GTK, so it is never imported by the headless engine test
suite; it is exercised by the installer smoke check on a real session.
"""

import subprocess
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib  # noqa: E402

from . import api as _api          # noqa: E402
from . import paths as _paths       # noqa: E402
from . import protection as _protection  # noqa: E402

APP_ID = "org.intergenos.Chronicle"


class EngineUnavailable(RuntimeError):
    """The engine cannot be reached over its socket — absent, refusing, or
    timing out. Raised distinct from an engine-level error so callers render
    the service-down state instead of crashing on the raw socket exception:
    a stale socket file passes available() and then the first verb raises
    ConnectionRefusedError, which no RuntimeError-only handler caught."""


class EngineNotPermitted(RuntimeError):
    """This account may not open the engine socket at all.

    Deliberately NOT a subclass of EngineUnavailable: every handler that
    renders the service-down message would otherwise catch it and tell the
    user to start a service that is already running. The remedy here is a
    group membership, and only an administrator can grant it."""


class EngineClient:
    """Thin wrapper over the engine IPC client, raising on engine errors."""

    def __init__(self):
        self._c = _api.Client()

    def available(self):
        return self._c.available()

    def call(self, verb, **args):
        try:
            resp = self._c.call(verb, **args)
        except _api.EngineAccessDenied as e:
            # The socket is there and the engine is running; this account is
            # outside the group that may open it. A different state, because
            # it has a different remedy.
            raise EngineNotPermitted(str(e)) from e
        except OSError as e:
            # ConnectionRefusedError (stale socket), FileNotFoundError
            # (socket absent), timeouts — all the service-down state.
            raise EngineUnavailable(str(e) or type(e).__name__) from e
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "engine error"))
        return resp.get("result")


def _human(n):
    n = float(n or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}"
        n /= 1024


def _ts(epoch):
    if not epoch:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def _ts_verdict(epoch):
    """The verdict line's rendering of a capture time: "today at HH:MM" for
    the current day, otherwise exactly what _ts() shows."""
    if not epoch:
        return "never"
    now = time.localtime()
    then = time.localtime(epoch)
    if (then.tm_year, then.tm_yday) == (now.tm_year, now.tm_yday):
        return time.strftime("today at %H:%M", then)
    return _ts(epoch)


class ChronicleWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Chronicle")
        self.engine = EngineClient()
        self.set_default_size(920, 680)

        self.toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        self.toasts.set_child(toolbar)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        # Brand cluster — the app mark + name in the pack_start slot, leaving
        # the centred title-widget slot to the view switcher (the same
        # left-slot treatment the welcome application documents for its
        # wordmark). The mark is the themed size-specific 24 px raster; the
        # scalable variant carries the detailed mark's blur filter and loses
        # its silhouette at this size. The image carries the accessible name
        # and the text label is presentational, so the cluster reads once,
        # not twice, to a screen reader.
        brand = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        brand.set_margin_start(6)
        mark = Gtk.Image.new_from_icon_name(APP_ID)
        mark.set_pixel_size(24)
        mark.update_property([Gtk.AccessibleProperty.LABEL],
                             [_protection.COPY["header.name"]])
        brand.append(mark)
        name = Gtk.Label(label=_protection.COPY["header.name"],
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        name.add_css_class("title")
        brand.append(name)
        header.pack_start(brand)

        # Service-condition banner — a persistent surface for a standing
        # condition, directly under the header so it is visible from every
        # page. Revealed only in the service-down / waiting-for-permission
        # states (_render_banner).
        self._banner = Adw.Banner()
        self._banner.connect("button-clicked", self._on_banner_clicked)
        toolbar.add_top_bar(self._banner)

        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self.stack)
        header.set_title_widget(switcher)
        toolbar.set_content(self.stack)

        self.stack.add_titled_with_icon(
            self._overview_page(), "overview", "Overview", "security-high-symbolic")
        self.stack.add_titled_with_icon(
            self._timeline_page(), "timeline", "Timeline", "document-open-recent-symbolic")
        self.stack.add_titled_with_icon(
            self._setup_page(), "setup", "Setup", "drive-harddisk-symbolic")

        # Narrow windows: the switcher yields to a bottom bar; identity does
        # not — the brand cluster stays in the header at every width. The
        # empty placeholder keeps the header from rendering a fallback title
        # beside the cluster while the breakpoint holds.
        switcher_bar = Adw.ViewSwitcherBar()
        switcher_bar.set_stack(self.stack)
        toolbar.add_bottom_bar(switcher_bar)
        self._narrow_title = Gtk.Box()
        bp = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(switcher_bar, "reveal", True)
        bp.add_setter(header, "title-widget", self._narrow_title)
        self.add_breakpoint(bp)

        self._state = None
        self._start_failure = None
        self._refresh_overview()
        self._reload_timeline()

    # -- overview -------------------------------------------------------

    def _overview_page(self):
        page = Adw.PreferencesPage()
        self._status_group = Adw.PreferencesGroup(title="Protection status")
        self._status_rows = []
        page.add(self._status_group)

        actions = Adw.PreferencesGroup(title="Capture now")
        self._capture_buttons = []
        for label, layer in (("Back up configuration state", _paths.LAYER_CONFIG_STATE),
                             ("Back up my files", _paths.LAYER_USER_DATA)):
            row = Adw.ActionRow(title=label)
            btn = Gtk.Button(label="Capture", valign=Gtk.Align.CENTER)
            btn.add_css_class("suggested-action")
            btn.connect("clicked", self._on_capture, layer)
            row.add_suffix(btn)
            actions.add(row)
            self._capture_buttons.append(btn)
        page.add(actions)
        return page

    def _status_add(self, widget):
        # Track exactly the widgets this window adds, so a refresh removes
        # exactly those — the group's internal scaffolding is not ours to
        # walk.
        self._status_group.add(widget)
        self._status_rows.append(widget)

    def _refresh_overview(self):
        # Re-render from ONE probe: the verdict and the detail rows derive
        # from the same status payload, so the headline and the detail cannot
        # disagree. The invariant: the status group is never rendered with
        # zero rows — every path, including every error path, adds a verdict
        # row first.
        for w in self._status_rows:
            self._status_group.remove(w)
        self._status_rows = []

        st = None
        reason = None
        if not self.engine.available():
            state = _protection.SERVICE_DOWN
        else:
            try:
                st = self.engine.call("status")
                state = _protection.classify(st)
            except EngineNotPermitted as e:
                state, reason = _protection.NO_ACCESS, str(e)
            except EngineUnavailable as e:
                state, reason = _protection.SERVICE_DOWN, str(e)
            except RuntimeError as e:
                if _protection.is_unauthorized(str(e)):
                    state, reason = _protection.UNAUTHORIZED, str(e)
                else:
                    # An engine-level error on the status verb itself — the
                    # engine is running, so none of the five protection
                    # states describes it; the error IS the verdict row.
                    state, reason = None, str(e)
        self._state = state
        self._render_banner(state)
        self._set_capture_enabled(
            state not in (_protection.SERVICE_DOWN, _protection.UNAUTHORIZED,
                          _protection.NO_ACCESS),
            state)
        if state is None:
            self._status_add(Adw.ActionRow(title="Engine error", subtitle=reason))
            return
        self._status_add(self._verdict_row(state, st))
        if state == _protection.SERVICE_DOWN:
            self._add_service_down_rows()
        elif state == _protection.NO_ACCESS:
            self._add_no_access_rows()
        else:
            if st is not None:
                self._add_status_rows(st)
            action = self._action_button(state)
            if action is not None:
                self._status_add(action)

    def _verdict_row(self, state, status):
        text = _protection.COPY[_protection.VERDICT_KEY[state]]
        if state == _protection.PROTECTED:
            text = text.format(
                when=_ts_verdict(_protection.latest_capture_epoch(status)))
        row = Adw.ActionRow(title=text)
        dot = Gtk.Label(label="●", valign=Gtk.Align.CENTER)
        dot.add_css_class(_protection.TONE[state])
        row.add_prefix(dot)
        tag = Gtk.Label(label=_protection.TAG[state], valign=Gtk.Align.CENTER)
        tag.add_css_class("dim-label")
        row.add_suffix(tag)
        return row

    def _action_button(self, state):
        """The verdict's action, rendered at the card's foot. Each state has
        a different remedy; a button that could not work is never shown."""
        if state == _protection.NO_CAPTURES:
            label, cb = _protection.COPY["action.capture"], self._on_capture_all
        elif state == _protection.TARGET_ABSENT:
            label, cb = (_protection.COPY["action.choose_drive"],
                         self._on_choose_drive)
        elif state == _protection.SERVICE_DOWN:
            label, cb = (_protection.COPY["action.start"],
                         lambda *_: self._start_service())
        elif state == _protection.UNAUTHORIZED:
            # Re-issuing the status verb IS the remedy: the engine's polkit
            # check runs with user interaction permitted, so the retry is
            # what raises the authentication dialog.
            label, cb = (_protection.COPY["action.allow"],
                         lambda *_: self._refresh_overview())
        else:
            return None
        btn = Gtk.Button(label=label, halign=Gtk.Align.START)
        btn.add_css_class("pill")
        btn.add_css_class("suggested-action")
        btn.set_margin_top(6)
        btn.connect("clicked", cb)
        return btn

    def _add_status_rows(self, st):
        t = st.get("target")
        if t:
            present = "attached" if st.get("target_present") else "not attached"
            sub = f"{t.get('mountpoint')} ({t.get('class')}) — {present}"
        else:
            sub = "No external target — only the always-on local history is active."
        self._status_add(Adw.ActionRow(title="Target", subtitle=sub))
        free = st.get("target_free_bytes")
        if free is not None:
            self._status_add(Adw.ActionRow(
                title="Free space on target", subtitle=_human(free)))
        for layer, w in sorted(st.get("last_capture", {}).items()):
            self._status_add(Adw.ActionRow(
                title=f"Last {layer}", subtitle=_ts(w)))
        q = st.get("queue", {})
        if q.get("summary"):
            self._status_add(Adw.ActionRow(title="Queue", subtitle=q["summary"]))
        for ev in st.get("clock_skew_events", []):
            self._status_add(Adw.ActionRow(
                title="Clock warning",
                subtitle=f"system clock moved backward at sequence {ev['at_sequence']}"))

    def _add_service_down_rows(self):
        # The two things a non-expert needs to know, on the persistent card;
        # the daemon's name appears only behind the Technical-details
        # expander, never in a primary line.
        self._status_add(Adw.ActionRow(
            title="What this means",
            subtitle=_protection.COPY["card.meaning"]))
        self._status_add(Adw.ActionRow(
            title="Your existing backups",
            subtitle=_protection.COPY["card.existing"]))
        if self._start_failure:
            # A failed Start lands its reason here, on the persistent card —
            # not flashed in a toast.
            self._status_add(Adw.ActionRow(
                title="Start failed", subtitle=self._start_failure))
        self._status_add(self._action_button(_protection.SERVICE_DOWN))
        tech = Adw.ExpanderRow(title=_protection.COPY["expander.technical"])
        tech.add_row(Adw.ActionRow(
            title=_protection.SERVICE_UNIT,
            subtitle=" ".join(_protection.START_ARGV)))
        self._status_add(tech)

    def _add_no_access_rows(self):
        # No action button: nothing this window can do fixes a group
        # membership, and a button that could not work is the same defect
        # class as a bare heading. The remedy is stated instead.
        self._status_add(Adw.ActionRow(
            title="Your backups", subtitle=_protection.COPY["card.existing"]))
        self._status_add(Adw.ActionRow(
            title="How to allow it",
            subtitle=_protection.COPY["no_access.remedy"]))
        tech = Adw.ExpanderRow(title=_protection.COPY["expander.technical"])
        tech.add_row(Adw.ActionRow(
            title=str(_paths.SOCKET_PATH),
            subtitle=f"group {_protection.ENGINE_GROUP}, mode 0660"))
        self._status_add(tech)

    def _render_banner(self, state):
        if state == _protection.SERVICE_DOWN:
            self._banner.set_title(_protection.COPY["banner.service_down"])
            self._banner.set_button_label(_protection.COPY["banner.button"])
            self._banner.set_revealed(True)
        elif state == _protection.UNAUTHORIZED:
            self._banner.set_title(_protection.COPY["verdict.unauthorized"])
            self._banner.set_button_label(_protection.COPY["action.allow"])
            self._banner.set_revealed(True)
        elif state == _protection.NO_ACCESS:
            # Shown WITHOUT a button — the empty label is what hides it. There
            # is no action this session can take.
            self._banner.set_title(_protection.COPY["banner.no_access"])
            self._banner.set_button_label("")
            self._banner.set_revealed(True)
        else:
            self._banner.set_revealed(False)

    def _on_banner_clicked(self, _banner):
        if self._state == _protection.SERVICE_DOWN:
            self._start_service()
        else:
            self._refresh_overview()

    def _set_capture_enabled(self, enabled, state=None):
        # In the service-down, waiting-for-permission and not-allowed states
        # the capture buttons are insensitive with the reason on the tooltip —
        # an enabled button that cannot work is the same defect class as a
        # bare heading. The reason has to match the state: "the service must
        # be running" is the wrong sentence for an account that is not allowed
        # to reach a service that IS running.
        if state == _protection.NO_ACCESS:
            reason = _protection.COPY["verdict.no_access"]
        else:
            reason = _protection.COPY["tooltip.capture_disabled"]
        for btn in self._capture_buttons:
            btn.set_sensitive(enabled)
            btn.set_tooltip_text(None if enabled else reason)

    def _start_service(self):
        # Fixed argv, no shell — the same shape as the engine's unit runner.
        # An unprivileged session raises the standard administrator prompt
        # through polkit's own unit-management action; run asynchronously so
        # that dialog never freezes this window.
        try:
            proc = Gio.Subprocess.new(_protection.START_ARGV,
                                      Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as e:
            self._start_failure = e.message
            self._refresh_overview()
            return
        proc.communicate_utf8_async(None, None, self._on_start_service_done)

    def _on_start_service_done(self, proc, result):
        try:
            _ok, _out, err = proc.communicate_utf8_finish(result)
        except GLib.Error as e:
            err = e.message
        if proc.get_successful():
            self._start_failure = None
        else:
            self._start_failure = ((err or "").strip()
                                   or "systemctl start failed")
        # Re-probe and repopulate — on success the banner drops and the
        # status card fills without a restart.
        self._refresh_overview()

    def _on_capture_all(self, _btn):
        # "Capture now" from the not-protected-yet verdict: full protection —
        # both layers, the same verb the per-layer buttons issue.
        for layer in (_paths.LAYER_CONFIG_STATE, _paths.LAYER_USER_DATA):
            self._on_capture(None, layer)

    def _on_choose_drive(self, _btn):
        self.stack.set_visible_child_name("setup")
        self._reload_targets()

    def _on_capture(self, _btn, layer):
        try:
            res = self.engine.call("capture", layer=layer, sync=True,
                                   reason=f"manual {layer} capture")
            self._toast(f"Captured {layer} {res.get('version_id','')}")
            self._refresh_overview()
        except RuntimeError as e:
            self._toast(f"Capture failed: {e}")

    # -- timeline -------------------------------------------------------

    def _timeline_page(self):
        page = Adw.PreferencesPage()
        grp = Adw.PreferencesGroup(title="Browse by time")

        self._layer_model = Gtk.StringList()
        for layer in _paths.LAYERS:
            self._layer_model.append(layer)
        self._layer_drop = Gtk.DropDown(model=self._layer_model,
                                        valign=Gtk.Align.CENTER)
        self._layer_drop.connect("notify::selected", lambda *_: self._reload_timeline())
        picker = Adw.ActionRow(title="Layer")
        picker.add_suffix(self._layer_drop)
        grp.add(picker)
        page.add(grp)

        self._versions_group = Adw.PreferencesGroup(title="Versions")
        self._version_rows = []
        page.add(self._versions_group)
        return page

    def _versions_add(self, widget):
        self._versions_group.add(widget)
        self._version_rows.append(widget)

    def _reload_timeline(self):
        # Same invariant as the status group: this group is populated at
        # construction and on every reload — a titled group never renders
        # with nothing under it.
        for w in self._version_rows:
            self._versions_group.remove(w)
        self._version_rows = []
        layer = _paths.LAYERS[self._layer_drop.get_selected()]
        try:
            versions = self.engine.call("list", layer=layer)
        except EngineNotPermitted:
            self._versions_add(Adw.ActionRow(
                title=_protection.COPY["verdict.no_access"],
                subtitle=_protection.COPY["no_access.remedy"]))
            return
        except EngineUnavailable:
            self._versions_add(Adw.ActionRow(
                title=_protection.COPY["verdict.service_down"]))
            return
        except RuntimeError as e:
            self._versions_add(Adw.ActionRow(title="Engine error", subtitle=str(e)))
            return
        if not versions:
            self._versions_add(Adw.ActionRow(
                title="No versions yet", subtitle="Nothing has been captured for this layer."))
            return
        for v in reversed(versions):  # newest first
            row = Adw.ActionRow(
                title=_ts(v["wall_clock"]),
                subtitle=f"{v['version_id']} · {v['files']} files · {v.get('reason','')}")
            if v.get("pinned"):
                row.add_prefix(Gtk.Image.new_from_icon_name("view-pin-symbolic"))
            rbtn = Gtk.Button(label="Restore…", valign=Gtk.Align.CENTER)
            rbtn.connect("clicked", self._on_restore_clicked, layer, v["version_id"])
            row.add_suffix(rbtn)
            pbtn = Gtk.Button(
                label="Unpin" if v.get("pinned") else "Pin", valign=Gtk.Align.CENTER)
            pbtn.connect("clicked", self._on_pin_clicked, v["version_id"], v.get("pinned"))
            row.add_suffix(pbtn)
            self._versions_add(row)

    def _on_pin_clicked(self, _btn, version_id, pinned):
        try:
            self.engine.call("unpin" if pinned else "pin", version_id=version_id)
            self._reload_timeline()
        except RuntimeError as e:
            self._toast(str(e))

    def _on_restore_clicked(self, _btn, layer, version_id):
        try:
            manifest = self.engine.call("manifest", layer=layer, version_id=version_id)
        except RuntimeError as e:
            self._toast(str(e))
            return
        paths = [e["path"] for e in manifest.get("entries", [])
                 if e.get("type") == "file"]
        try:
            plan = self.engine.call("restore-plan", layer=layer,
                                    version_id=version_id, paths=paths,
                                    mode="replace-confirm")
        except RuntimeError as e:
            self._toast(str(e))
            return
        n = len([a for a in plan["actions"] if a["action"] == "restore"])
        overwrite = len([a for a in plan["actions"] if a.get("will_overwrite")])
        dlg = Adw.MessageDialog(
            transient_for=self, heading="Restore this version?",
            body=(f"This will restore {n} file(s) from {version_id}.\n"
                  f"{overwrite} existing file(s) would be overwritten. "
                  "Nothing is changed until you confirm."))
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("beside", "Restore beside originals")
        dlg.add_response("replace", "Overwrite (restore in place)")
        dlg.set_response_appearance("replace", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.connect("response", self._on_restore_response, layer, version_id, paths)
        dlg.present()

    def _on_restore_response(self, _dlg, response, layer, version_id, paths):
        if response == "cancel":
            return
        mode = "beside" if response == "beside" else "replace-confirm"
        try:
            res = self.engine.call("restore", layer=layer, version_id=version_id,
                                   paths=paths, mode=mode)
            ok = sum(1 for r in res["results"] if r["ok"])
            bad = [r for r in res["results"] if not r["ok"]]
            msg = f"Restored {ok} file(s)."
            if bad:
                msg += f" {len(bad)} failed integrity check and were skipped."
            self._toast(msg)
        except RuntimeError as e:
            self._toast(f"Restore failed: {e}")

    # -- setup ----------------------------------------------------------

    def _setup_page(self):
        page = Adw.PreferencesPage()
        grp = Adw.PreferencesGroup(
            title="Backup target",
            description="Scan for a volume to store backups. A separate physical "
                        "disk protects against disk failure; a partition on the "
                        "system disk protects against mistakes but not disk failure.")
        scan = Adw.ActionRow(title="Scan for candidate volumes")
        sbtn = Gtk.Button(label="Scan", valign=Gtk.Align.CENTER)
        sbtn.add_css_class("suggested-action")
        sbtn.connect("clicked", lambda *_: self._reload_targets())
        scan.add_suffix(sbtn)
        grp.add(scan)
        page.add(grp)

        # The Candidates group joins the page on the first scan — until it
        # can be populated it is not added at all, so no titled group ever
        # renders with nothing under it.
        self._targets_group = Adw.PreferencesGroup(title="Candidates")
        self._target_rows = []
        self._targets_group_added = False
        self._setup_pref_page = page
        return page

    def _targets_add(self, widget):
        self._targets_group.add(widget)
        self._target_rows.append(widget)

    def _reload_targets(self):
        if not self._targets_group_added:
            self._setup_pref_page.add(self._targets_group)
            self._targets_group_added = True
        for w in self._target_rows:
            self._targets_group.remove(w)
        self._target_rows = []
        try:
            cands = self.engine.call("target-scan")
        except EngineNotPermitted:
            self._targets_add(Adw.ActionRow(
                title=_protection.COPY["verdict.no_access"],
                subtitle=_protection.COPY["no_access.remedy"]))
            return
        except EngineUnavailable:
            self._targets_add(Adw.ActionRow(
                title=_protection.COPY["verdict.service_down"]))
            return
        except RuntimeError as e:
            self._targets_add(Adw.ActionRow(title="Engine error", subtitle=str(e)))
            return
        if not cands:
            self._targets_add(Adw.ActionRow(title="No candidate volumes found"))
            return
        for c in cands:
            row = Adw.ExpanderRow(
                title=f"{c['device']}  [{c['fstype']}]",
                subtitle=c["protection_text"])
            if c["disqualified"]:
                info = Adw.ActionRow(title="Not usable as-is",
                                     subtitle=c["disqualified"])
                row.add_row(info)
                for opt in (c.get("remediation") or {}).get("options", []):
                    self._add_remediation_row(row, c, opt)
            else:
                for tgt in c.get("supported_targets", []):
                    self._add_adopt_row(row, c, tgt)
            self._targets_add(row)

    def _add_adopt_row(self, expander, cand, tgt):
        if tgt["mode"] == "whole-volume":
            r = Adw.ActionRow(title="Dedicate the whole volume",
                              subtitle=tgt["description"])
            b = Gtk.Button(label="Adopt", valign=Gtk.Align.CENTER)
            b.connect("clicked", self._on_adopt, cand, "whole-volume", None)
            r.add_suffix(b)
            expander.add_row(r)
        else:
            r = Adw.ActionRow(title="Use a size-capped folder",
                              subtitle=tgt["description"])
            cap = Gtk.SpinButton.new_with_range(1, 1024 * 1024, 1)
            cap.set_value(50)  # GiB default suggestion
            cap.set_valign(Gtk.Align.CENTER)
            cap.set_tooltip_text("Size cap in GiB")
            b = Gtk.Button(label="Adopt", valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b: self._on_adopt(
                _b, cand, "directory", int(cap.get_value()) * 1024 ** 3))
            r.add_suffix(cap)
            r.add_suffix(b)
            expander.add_row(r)

    def _add_remediation_row(self, expander, cand, opt):
        r = Adw.ActionRow(title=opt["action"], subtitle=opt["description"])
        if opt["action"] == "gparted-guided":
            b = Gtk.Button(label="Open GParted", valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda *_: self._launch_gparted(cand))
            r.add_suffix(b)
        expander.add_row(r)

    def _on_adopt(self, _b, cand, klass, cap):
        mount = cand.get("mountpoint")
        if not mount:
            self._toast("This volume must be mounted before it can be adopted.")
            return
        try:
            self.engine.call("target-adopt", mountpoint=mount, target_class=klass,
                             device=cand["device"], cap_bytes=cap)
            self._toast(f"Adopted {cand['device']} as the backup target.")
            self._refresh_overview()
        except RuntimeError as e:
            self._toast(f"Adopt failed: {e}")

    def _launch_gparted(self, cand):
        # The guided hand-off: Chronicle never partitions itself — it opens the
        # partition editor and re-scans afterwards (addendum B).
        try:
            subprocess.Popen(["gparted", cand.get("parent_disk") or cand["device"]])
            self._toast("Opened GParted. Shrink the partition and create an ext4 "
                        "partition, then Scan again.")
        except OSError as e:
            self._toast(f"Could not launch GParted: {e}")

    def _toast(self, text):
        self.toasts.add_toast(Adw.Toast.new(text))


class ChronicleApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = ChronicleWindow(self)
        win.present()


def main(argv=None):
    import sys
    app = ChronicleApp()
    return app.run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
