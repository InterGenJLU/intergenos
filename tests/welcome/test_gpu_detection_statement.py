# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The real page explains detected graphics before offering software."""

import importlib.util
import json
from pathlib import Path

import pytest


def record(vendor, upgrade=None, supported=None, targets=()):
    return {"version": 2, "vendor": vendor, "pci_vendors": [],
            "shipped_engine": "vulkan", "upgrade_engine": upgrade,
            "upgrade_outranks_shipped": bool(upgrade),
            "upgrade_engine_supported": supported,
            "gfx_targets": list(targets)}


PAGE = r'''
import importlib.util, json, os
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
spec = importlib.util.spec_from_file_location("welcome", os.environ["IGOS_WELCOMER_SRC"])
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
w._gpu_detection_record = lambda: RECORD
w._model_offer = lambda: MODEL
w._package_is_installed = lambda name: INSTALLED
w._intergen_is_set_up = lambda: False
w._qwen_attribution = lambda: None
w._probe_download_sources = lambda: type("P", (), {"cause": None})()
Adw.init()
page = w.build_intergen_page()
def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()
widgets = list(walk(page))
labels = [widget.get_text() for widget in widgets if isinstance(widget, Gtk.Label)]
rows = [i for i, widget in enumerate(widgets) if isinstance(widget, Adw.SwitchRow)]
statements = [(i, widget.get_text()) for i, widget in enumerate(widgets)
              if isinstance(widget, Gtk.Label)
              and widget.has_css_class("intergen-graphics-statement")]
print("CHECK " + json.dumps({"labels": labels, "rows": rows,
                            "statements": statements}), flush=True)
'''


def page_state(gpu_record, model=None, installed=False):
    path = Path(__file__).with_name("test_welcomer_after_the_driver_leg.py")
    spec = importlib.util.spec_from_file_location("graphics_display", path)
    display = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(display)
    code = (f"RECORD = {gpu_record!r}\nMODEL = {model!r}\n"
            f"INSTALLED = {installed!r}\n" + PAGE)
    result = display._run_in_display(code)
    assert result.returncode == 0, result.stdout + result.stderr
    checks = [line[6:] for line in result.stdout.splitlines()
              if line.startswith("CHECK ")]
    assert len(checks) == 1, result.stdout
    return json.loads(checks[0])


@pytest.mark.parametrize("gpu_record,model,installed,expected,rows", [
    (record("nvidia", "cuda"), {"tiers": [1], "gpu_model": "NVIDIA Example GPU"},
     False, ["NVIDIA Example GPU", "Vulkan"], 2),
    (record("amd", "hip", True, ["gfx1100"]), {"tiers": [1]},
     False, ["AMD", "gfx1100", "Vulkan"], 1),
    (record("amd", "hip", False, ["gfx90c"]), {"tiers": [1]},
     False, ["AMD", "gfx90c", "No HIP/ROCm engine is offered",
             "does not support this GPU architecture"], 0),
    (record("intel"), {"tiers": [1], "gpu_model": "Intel UHD Graphics 620"},
     False, ["Intel UHD Graphics 620", "Vulkan",
             "No additional graphics software is offered for this hardware"], 0),
    (record(None), None, False,
     ["Graphics hardware was not identified", "No additional graphics software"], 0),
    (None, None, False,
     ["Graphics detection is unavailable", "detection record is unavailable"], 0),
    (record("nvidia", "cuda"), {"tiers": [1]}, True,
     ["NVIDIA", "graphics software offered for this GPU is already installed"], 0),
    (record("nvidia", "cuda"), None, False, ["NVIDIA", "Vulkan"], 2),
], ids=["nvidia", "amd-supported", "amd-unsupported", "intel",
        "unidentified", "missing-record", "already-installed", "missing-model-offer"])
def test_page_states_detection_and_explains_an_empty_offer(
        gpu_record, model, installed, expected, rows):
    state = page_state(gpu_record, model, installed)
    assert len(state["statements"]) == 1, state["labels"]
    position, text = state["statements"][0]
    for phrase in expected:
        assert phrase in text
    assert len(state["rows"]) == rows
    assert all(position < row for row in state["rows"])


def test_graphics_offers_still_render_when_the_model_offer_is_unavailable():
    state = page_state(record("nvidia", "cuda"))
    assert len(state["rows"]) == 2
    assert "NVIDIA proprietary graphics driver" in state["labels"]
