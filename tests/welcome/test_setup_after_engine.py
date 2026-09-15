# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Engine completion reveals the existing setup card on either GPU path.

Package checks and terminal-completion callbacks run with inert stand-ins.
Widget cases build the real page on Broadway and call only its completion
callback: no install or setup button is pressed and no transaction starts.
"""

import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


SCRIPT = (Path(__file__).resolve().parents[2]
          / "assets/intergen-welcome/intergen-welcome.py")
_spec = importlib.util.spec_from_file_location("welcome_engine_placement", SCRIPT)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)

SCROLL_HINT = "Scroll down to set InterGen up and choose his size."


def _record(vendor, supported=True):
    return {
        "version": welcome._GPU_RECORD_VERSION,
        "vendor": vendor,
        "upgrade_engine": "hip" if vendor == "amd" else "cuda",
        "upgrade_outranks_shipped": True,
        "upgrade_engine_supported": supported,
    }


@pytest.mark.parametrize("vendor,states,expected", [
    ("amd", {"llama-cpp-hip": True}, True),
    ("amd", {"llama-cpp-hip": False}, False),
    ("amd", {"llama-cpp-hip": None}, False),
    ("nvidia", {"cuda-toolkit": True, "llama-cpp-cuda": True}, True),
    ("nvidia", {"cuda-toolkit": False, "llama-cpp-cuda": True}, False),
    ("nvidia", {"cuda-toolkit": None, "llama-cpp-cuda": True}, False),
    ("nvidia", {"cuda-toolkit": True, "llama-cpp-cuda": False}, False),
    ("nvidia", {"cuda-toolkit": True, "llama-cpp-cuda": None}, False),
])
def test_engine_leg_requires_every_offered_package_confirmed(vendor, states, expected):
    with patch.object(welcome, "_gpu_detection_record", return_value=_record(vendor)), \
            patch.object(welcome, "_package_is_installed",
                         side_effect=lambda name: states.get(name, False)):
        assert welcome._engine_leg_is_done() is expected


@pytest.mark.parametrize("record", [
    None,
    {"version": 2, "vendor": "intel", "upgrade_engine": None},
    {"version": 2, "vendor": "amd", "upgrade_engine": None},
    _record("amd", supported=False),
])
def test_without_an_engine_offer_installed_packages_cannot_complete_a_leg(record):
    with patch.object(welcome, "_gpu_detection_record", return_value=record), \
            patch.object(welcome, "_package_is_installed", return_value=True):
        assert welcome._engine_leg_is_done() is False


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_missing_or_unreadable_record_keeps_the_default_order(error):
    with patch.object(welcome, "open", create=True, side_effect=error), \
            patch.object(welcome, "_package_is_installed", return_value=True):
        assert welcome._engine_leg_is_done() is False
        assert welcome._driver_leg_is_done() is False


def _completion_poll(outcome, events, running=False, attached=True, callback=True):
    """Compile only the shipped completion poll, without entering an action.

    The enclosing click handler can launch an install, so tests never call it.
    The poll's dependencies are supplied exactly as its closure supplies them.
    """
    module = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    builder = next(node for node in module.body
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "_build_gpu_install_offer")
    watch = next(node for node in builder.body
                 if isinstance(node, ast.FunctionDef) and node.name == "_watch")
    poll = next(node for node in watch.body
                if isinstance(node, ast.FunctionDef) and node.name == "_poll")
    button = Mock()
    button.get_root.return_value = object() if attached else None
    status = Mock()
    status.set_text.side_effect = lambda message: events.append(("status", message))
    namespace = {
        "btn": button,
        "proc": Mock(poll=Mock(return_value=None if running else 0)),
        "chosen": ["compute_engine"],
        "offers": [],
        "status": status,
        "_install_outcome": Mock(return_value=outcome),
        "on_engine_installed": ((lambda: events.append(("setup-focus", None)))
                                if callback else None),
        "_GPU_INSTALL_RETRY_LABEL": welcome._GPU_INSTALL_RETRY_LABEL,
        "GLib": SimpleNamespace(SOURCE_REMOVE=False, SOURCE_CONTINUE=True),
    }
    exec(compile(ast.Module(body=[poll], type_ignores=[]), str(SCRIPT), "exec"),
         namespace)
    return namespace["_poll"]


@pytest.mark.parametrize("installed", [True, False, None])
@pytest.mark.parametrize("activation", ["service-restart", "reboot", "none"])
def test_only_successful_engine_outcome_reveals_setup_after_its_message(
        installed, activation):
    events = []
    outcome = {"installed": installed, "activation": activation,
               "message": "The verified package outcome."}
    assert _completion_poll(outcome, events)() is False
    expected = [("status", outcome["message"])]
    if installed is True and activation == "service-restart":
        expected.append(("setup-focus", None))
    assert events == expected


@pytest.mark.parametrize("running,attached", [(True, True), (False, False)])
def test_pending_terminal_or_closed_page_cannot_advance_to_setup(running, attached):
    events = []
    outcome = {"installed": True, "activation": "service-restart", "message": "Done"}
    _completion_poll(outcome, events, running, attached)()
    assert events == []


def test_standalone_offer_can_report_success_without_a_page_callback():
    events = []
    outcome = {"installed": True, "activation": "service-restart", "message": "Done"}
    assert _completion_poll(outcome, events, callback=False)() is False
    assert events == [("status", "Done")]


_PAGE_CHECK = r'''
import importlib.util, json, os, sys
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
spec = importlib.util.spec_from_file_location("w", os.environ["IGOS_WELCOMER_SRC"])
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
w._gpu_detection_record = lambda: RECORD
states = {"nvidia": RECORD["vendor"] == "nvidia"}
if ENGINE_DONE:
    states.update({"llama-cpp-hip": True, "cuda-toolkit": True, "llama-cpp-cuda": True})
w._package_is_installed = lambda name: states.get(name, False)
w._intergen_is_set_up = lambda: SET_UP
w._intergen_engine_probe = lambda callback: callback("down")
w._model_offer = lambda: {"tiers": [1, 2], "download_bytes": {"1": 2300000000, "2": 6100000000}}
w._qwen_attribution = lambda: None
w._launch_intergen_setup = lambda *a, **k: (_ for _ in ()).throw(AssertionError("setup called"))
w._open_terminal_running = lambda *a, **k: (_ for _ in ()).throw(AssertionError("terminal called"))
captured = {}
real_offer = w._build_gpu_install_offer
def capture_offer(*args, **kwargs):
    captured["callback"] = kwargs.get("on_engine_installed")
    captured["offer"] = real_offer(*args, **kwargs)
    return captured["offer"]
w._build_gpu_install_offer = capture_offer

def descend(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from descend(child)
        child = child.get_next_sibling()

class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.intergenos.test.enginesetup",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.failed = False

    def do_activate(self):
        try:
            self.window = Adw.ApplicationWindow(application=self)
            self.window.add_css_class("welcome-window")
            self.window.set_default_size(760, 720)
            self.page = w.build_intergen_page()
            self.window.set_content(self.page)
            self.window.present()
            GLib.timeout_add(150, self.check)
        except Exception:
            self.fail()

    def fail(self):
        import traceback
        traceback.print_exc()
        self.failed = True
        self.quit()

    def check(self):
        try:
            widgets = list(descend(self.page))
            buttons = [widget for widget in widgets if isinstance(widget, Gtk.Button)
                       and widget.get_label() == "Set up InterGen now"]
            hints = [widget for widget in widgets if isinstance(widget, Gtk.Label)
                     and widget.get_text() == HINT]
            callback = captured["callback"]
            assert callable(callback), "page did not connect the engine completion callback"
            if SET_UP:
                assert not buttons, "setup offered even though a model is present"
                callback()
                print("CHECK " + json.dumps({"setup_absent": True}), flush=True)
                self.quit()
                return False
            assert len(buttons) == 1, "setup action must remain unique"
            assert len(hints) == 1, "there must be one scroll instruction"
            hint_before = not ENGINE_DONE and RECORD["vendor"] == "amd"
            assert hints[0].get_visible() is hint_before, "scroll direction must match the initial card position"
            page_box = next(widget for widget in widgets if isinstance(widget, Gtk.Box)
                            and isinstance(widget.get_first_child(), Gtk.Label)
                            and widget.get_first_child().get_text() == "Meet InterGen")
            setup_box = buttons[0].get_parent()
            before = []
            child = page_box.get_first_child()
            while child is not None:
                before.append(child)
                child = child.get_next_sibling()
            if ENGINE_DONE:
                assert before.index(setup_box) in (1, 2), "installed engine did not lift setup on initial page construction"
            elif RECORD["vendor"] == "amd":
                assert before.index(setup_box) > 2, "incomplete engine must keep the disclosure before setup"
            states.update({"llama-cpp-hip": True, "cuda-toolkit": True, "llama-cpp-cuda": True})
            callback()
            assert not hints[0].get_visible(), "scroll-down hint remained below the lifted setup card"
            after = []
            child = page_box.get_first_child()
            while child is not None:
                after.append(child)
                child = child.get_next_sibling()
            assert after[0].get_text() == "Meet InterGen", "heading moved below setup"
            assert after.index(setup_box) in (1, 2), "engine completion left setup below the introductory copy"
            assert self.window.get_focus() == buttons[0], "completion did not focus the setup button"
            assert sum(widget == setup_box for widget in after) == 1
            self.heading = after[0]
            self.setup_button = buttons[0]
            self.observed = {"setup_index": after.index(setup_box),
                             "focused": True, "hint": hints[0].get_text()}
            # Focus can scroll toward the button's allocation before its card
            # was reordered. Check after GTK has laid out and animated the
            # actual viewport; a focused widget alone does not prove context.
            self.layout_ticks = 0
            def after_frame(_widget, _clock, *_unused):
                self.layout_ticks += 1
                if self.layout_ticks < 2:
                    return True
                GLib.idle_add(self.check_viewport)
                return False
            self.layout_tick = self.window.add_tick_callback(after_frame)
            self.layout_timeout = GLib.timeout_add(8000, self.layout_expired)
        except Exception:
            self.fail()
        return False

    def layout_expired(self):
        self.window.remove_tick_callback(self.layout_tick)
        try:
            raise AssertionError("viewport did not complete two frame updates: "
                                 + str(self.layout_ticks))
        except Exception:
            self.fail()
        return False

    def check_viewport(self):
        GLib.source_remove(self.layout_timeout)
        try:
            viewport = self.setup_button.get_ancestor(Gtk.Viewport)
            assert viewport is not None, "setup button has no scrolling viewport"
            assert self.window.get_focus() == self.setup_button, "setup lost keyboard focus"
            dimensions = [viewport.get_width(), viewport.get_height()]
            bounds = {}
            for name, widget in (("heading", self.heading), ("setup", self.setup_button)):
                ok, rectangle = widget.compute_bounds(viewport)
                assert ok, name + " has no bounds in the viewport"
                x, y = rectangle.get_x(), rectangle.get_y()
                width, height = rectangle.get_width(), rectangle.get_height()
                bounds[name] = [x, y, width, height]
                assert (widget.get_mapped() and x >= -1 and y >= -1
                        and x + width <= dimensions[0] + 1
                        and y + height <= dimensions[1] + 1), (
                    name + " is outside the viewport after engine completion: "
                    + json.dumps({"viewport": dimensions, "bounds": bounds}))
            self.observed.update({"viewport": dimensions, "bounds": bounds})
            print("CHECK " + json.dumps(self.observed), flush=True)
            self.quit()
        except Exception:
            self.fail()
        return False

Adw.init()
provider = Gtk.CssProvider()
provider.load_from_string(w.CUSTOM_CSS)
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
app = Application()
result = app.run([])
sys.exit(1 if app.failed else result)
'''


@pytest.mark.parametrize("vendor", ["amd", "nvidia"])
@pytest.mark.parametrize("set_up,engine_done", [(False, False), (False, True), (True, True)],
                         ids=["completing-engine", "installed-engine", "set-up"])
def test_real_page_moves_and_focuses_its_existing_setup_button(vendor, set_up, engine_done):
    helper_path = Path(__file__).with_name("test_welcomer_after_the_driver_leg.py")
    spec = importlib.util.spec_from_file_location("welcome_engine_display", helper_path)
    display = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(display)
    code = (f"RECORD = {_record(vendor)!r}\nSET_UP = {set_up!r}\n"
            f"ENGINE_DONE = {engine_done!r}\n"
            f"HINT = {SCROLL_HINT!r}\n" + _PAGE_CHECK)
    result = display._run_in_display(code)
    assert result.returncode == 0, result.stderr + result.stdout
    checks = [line[len("CHECK "):] for line in result.stdout.splitlines()
              if line.startswith("CHECK ")]
    assert len(checks) == 1, result.stdout
    observed = json.loads(checks[0])
    if set_up:
        assert observed == {"setup_absent": True}
    else:
        assert observed["focused"] is True
        assert observed["hint"] == SCROLL_HINT
