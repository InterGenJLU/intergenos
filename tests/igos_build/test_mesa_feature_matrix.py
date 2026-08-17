# SPDX-License-Identifier: GPL-3.0-or-later
# Wedge suite for the RT-3 post-configure feature-matrix checker
# (igos-build/mesa_feature_matrix.py). Drives the REAL module via its CLI
# against synthetic meson-info introspection data — the five ratified wedge
# shapes from the authoring spec, red and green.

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECKER = REPO / "igos-build" / "mesa_feature_matrix.py"


def _write_intro(build_dir: Path, options: dict):
    info = build_dir / "meson-info"
    info.mkdir(parents=True)
    records = [{"name": k, "value": v} for k, v in options.items()]
    (info / "intro-buildoptions.json").write_text(json.dumps(records))


def _run(build_dir: Path, matrix: dict, tmp: Path):
    mpath = tmp / "feature-matrix.json"
    mpath.write_text(json.dumps(matrix))
    return subprocess.run(
        [sys.executable, str(CHECKER), "--build", str(build_dir),
         "--matrix", str(mpath), "--label", "wedge"],
        capture_output=True, text=True)


RESOLVED_GOOD = {
    "egl": "enabled",
    "gbm": "enabled",
    "glx": "dri",
    "gles1": "disabled",
    "gles2": "enabled",
    "platforms": ["x11", "wayland"],
    "vulkan-drivers": ["amd", "nouveau", "intel"],
    "gallium-drivers": ["radeonsi", "iris", "zink"],
    "shared-glapi": "enabled",
    "b_ndebug": True,  # boolean normalization case
}

MATRIX_GOOD = {
    "_comment": "wedge matrix — metadata keys are skipped",
    "egl": "enabled",
    "gbm": "enabled",
    "glx": "dri",
    "gles1": "disabled",
    "gles2": "enabled",
    "platforms": ["wayland", "x11"],  # order-independent
    "vulkan-drivers": ["amd", "nouveau", "intel"],
    "gallium-drivers": ["radeonsi", "iris", "zink"],
    "shared-glapi": "enabled",
    "b_ndebug": True,
}


def test_clean_matrix_passes(tmp_path):
    b = tmp_path / "build"
    _write_intro(b, RESOLVED_GOOD)
    r = _run(b, MATRIX_GOOD, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "RT-3 OK" in r.stdout


def test_wedge1_auto_feature_silently_disabled(tmp_path):
    # The RT-3 disease itself: egl pinned enabled, resolved disabled
    # (a missing lib32 dep auto-disabled it; everything else still green).
    b = tmp_path / "build"
    resolved = dict(RESOLVED_GOOD, egl="disabled")
    _write_intro(b, resolved)
    r = _run(b, MATRIX_GOOD, tmp_path)
    assert r.returncode == 1
    assert "egl" in r.stderr and "RT-3 REFUSE" in r.stderr


def test_wedge2_platform_dropped(tmp_path):
    # x11 silently fell out of platforms.
    b = tmp_path / "build"
    resolved = dict(RESOLVED_GOOD, platforms=["wayland"])
    _write_intro(b, resolved)
    r = _run(b, MATRIX_GOOD, tmp_path)
    assert r.returncode == 1
    assert "platforms" in r.stderr and "missing" in r.stderr


def test_wedge3_driver_crept_in_and_out(tmp_path):
    # lavapipe (swrast) crept INTO vulkan-drivers AND amd fell out —
    # both directions must be named.
    b = tmp_path / "build"
    resolved = dict(RESOLVED_GOOD, **{"vulkan-drivers": ["swrast", "nouveau", "intel"]})
    _write_intro(b, resolved)
    r = _run(b, MATRIX_GOOD, tmp_path)
    assert r.returncode == 1
    assert "unexpected" in r.stderr and "swrast" in r.stderr
    assert "missing" in r.stderr and "amd" in r.stderr


def test_wedge4_renamed_option_fails_closed(tmp_path):
    # A matrix key absent from the introspection data (upstream removed
    # the option on a version bump) must REFUSE, never silently skip —
    # osmesa/gallium-xa/gallium-vdpau vanished exactly this way in 25.x.
    b = tmp_path / "build"
    _write_intro(b, RESOLVED_GOOD)
    matrix = dict(MATRIX_GOOD, osmesa="false")
    r = _run(b, matrix, tmp_path)
    assert r.returncode == 1
    assert "ABSENT" in r.stderr and "osmesa" in r.stderr


def test_wedge5_missing_intro_fails_closed(tmp_path):
    b = tmp_path / "build"
    b.mkdir()
    r = _run(b, MATRIX_GOOD, tmp_path)
    assert r.returncode == 2
    assert "REFUSE" in r.stderr


def test_wedge5b_unparseable_intro_fails_closed(tmp_path):
    b = tmp_path / "build"
    (b / "meson-info").mkdir(parents=True)
    (b / "meson-info" / "intro-buildoptions.json").write_text("not json{")
    r = _run(b, MATRIX_GOOD, tmp_path)
    assert r.returncode == 2


def test_empty_or_bad_matrix_fails_closed(tmp_path):
    b = tmp_path / "build"
    _write_intro(b, RESOLVED_GOOD)
    r = _run(b, {}, tmp_path)
    assert r.returncode == 2
