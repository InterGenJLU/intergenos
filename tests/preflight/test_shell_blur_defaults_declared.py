"""A default-enabled shell extension may not be left on a third-party's defaults.

WHAT HAPPENED. The image enables the blur extension for every new user — it is
listed in enabled-extensions in config/gsettings/91_intergenos-extensions.gschema
.override — and the tree shipped no settings for it at all. Every default of a
default-enabled component was therefore whatever the vendored third-party bundle
happened to carry, and could move on any version bump with nothing in the tree
recording what was intended.

One of those defaults has a measured structural consequence. Read from the
shipped component sources rather than inferred from the key name
(components/panel.js and components/dash_to_dock.js in the extension bundle):
each blurred surface is built as a Meta.BackgroundGroup constructed at
width 0 / height 0, and when static blur is on the surface additionally creates
a real background manager bound to a MONITOR INDEX captured at the moment the
surface is first blurred. With static blur off the same surface takes a live
blur effect with no background manager and no monitor binding — the blur is
present either way, so this selects an implementation rather than removing a
feature.

On a machine with several GPUs and several displays, that index is captured
while the monitor set is still being assembled, and the binding does not follow
a later change to the set. It matches the one recovery known to work on the
affected machine (re-applying the whole display configuration, which rebuilds
every background manager) and the one known not to work (changing the wallpaper
setting alone, which re-reads the image and rebuilds no binding).

MEASURED 2026-08-06, from two full journal captures on the affected three-GPU
machine, on both boots that showed the fault: every offscreen-effect allocation
failure names one of these zero-sized blur groups or the widget inside it, and
the two boots fail with the two different shapes one degenerate allocation
produces ('Failed to create texture 2d due to size/format constraints' on one,
"cogl_framebuffer_set_viewport: assertion 'width > 0 && height > 0' failed" on
the other).

NOT CLAIMED HERE. No log line in either capture records a wallpaper paint
failure, so the step from those allocation failures to a desktop falling back to
its flat primary colour is not established by measurement. These assertions
cover what the tree declares, which is the thing a test in this tree can decide.

Nothing here reads the network, needs privilege, or writes inside the tree.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PKG = REPO_ROOT / "packages" / "core" / "intergenos-default-settings"
FRAGMENT = SETTINGS_PKG / "assets" / "dconf" / "db" / "local.d" / "02-intergenos-shell-effects"
BUILD_SH = SETTINGS_PKG / "build.sh"
PACKAGE_YML = SETTINGS_PKG / "package.yml"
EXT_OVERRIDE = (REPO_ROOT / "config" / "gsettings"
                / "91_intergenos-extensions.gschema.override")

# The extension whose defaults this file is about, as it is named in the
# enable list and in the dconf path.
EXT_UUID = "blur-my-shell@aunetx"

# The surfaces that create a monitor-index-bound background manager when static
# blur is on. Both take the identical code path in their own component source.
BOUND_SURFACES = ("panel", "dash-to-dock")

INSTALLED_FRAGMENT = "/etc/dconf/db/local.d/02-intergenos-shell-effects"


def _fragment_text() -> str:
    return FRAGMENT.read_text(encoding="utf-8")


def _groups(text: str) -> dict[str, dict[str, str]]:
    """Parse a dconf keyfile fragment into {group: {key: value}}.

    Deliberately strict: a line that is neither blank, a comment, a group
    header, nor a key=value pair is an error rather than something to skip,
    because silently skipping is how a typo becomes an unset default.
    """
    groups: dict[str, dict[str, str]] = {}
    current: str | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            groups.setdefault(current, {})
            continue
        if "=" in line:
            assert current is not None, (
                f"{FRAGMENT.name}:{lineno}: key outside any group: {line!r}"
            )
            key, _, value = line.partition("=")
            groups[current][key.strip()] = value.strip()
            continue
        raise AssertionError(
            f"{FRAGMENT.name}:{lineno}: unparseable line in a dconf fragment: {line!r}"
        )
    return groups


# ---------------------------------------------------------------------------
# The class detector itself. Without this, every assertion below can keep
# passing while the premise has quietly stopped being true — the extension gets
# dropped from the enable list, and a file pinning its defaults still parses
# perfectly while covering nothing.
# ---------------------------------------------------------------------------

def test_the_extension_this_file_is_about_is_still_default_enabled():
    """If it stops being a shipped default, this whole file is covering nothing."""
    assert EXT_OVERRIDE.is_file(), f"missing: {EXT_OVERRIDE}"
    text = EXT_OVERRIDE.read_text(encoding="utf-8")
    enable_lines = [ln for ln in text.splitlines()
                    if ln.startswith("enabled-extensions=")]
    assert len(enable_lines) == 1, (
        "expected exactly one enabled-extensions line in "
        f"{EXT_OVERRIDE.name}, found {len(enable_lines)}"
    )
    assert EXT_UUID in enable_lines[0], (
        f"{EXT_UUID} is no longer in the shipped enabled-extensions list. "
        "Either it stopped being a default (delete this test file and the "
        "dconf fragment with it) or the list was edited by accident."
    )


def test_the_fragment_exists_and_is_non_trivial():
    assert FRAGMENT.is_file(), (
        f"missing the shipped defaults fragment: {FRAGMENT}. Without it the "
        f"image ships {EXT_UUID} enabled with no settings of its own."
    )
    groups = _groups(_fragment_text())
    assert groups, "the fragment parses to no groups at all — it declares nothing"


@pytest.mark.parametrize("surface", BOUND_SURFACES)
def test_static_blur_is_pinned_off_for_every_monitor_bound_surface(surface):
    """Each surface that captures a monitor index must have static blur pinned off.

    This is the assertion with the consequence. A surface left unpinned inherits
    the third-party default, which is on.
    """
    groups = _groups(_fragment_text())
    path = f"org/gnome/shell/extensions/blur-my-shell/{surface}"
    assert path in groups, (
        f"the fragment declares no defaults for the {surface} surface "
        f"(expected a [{path}] group). That surface then takes the vendored "
        "default, which creates a monitor-index-bound background manager."
    )
    assert groups[path].get("static-blur") == "false", (
        f"[{path}] must set static-blur=false; it is "
        f"{groups[path].get('static-blur')!r}"
    )


@pytest.mark.parametrize("surface", BOUND_SURFACES)
def test_blur_itself_is_not_turned_off(surface):
    """Selecting an implementation is the intent; removing the feature is not.

    A future edit that reaches for blur=false to quiet the same symptom is
    disabling a shipped feature to bypass a defect, which this tree does not do.
    """
    groups = _groups(_fragment_text())
    path = f"org/gnome/shell/extensions/blur-my-shell/{surface}"
    value = groups.get(path, {}).get("blur")
    assert value in (None, "true"), (
        f"[{path}] sets blur={value!r}. Pinning the static-blur implementation "
        "off is the fix; turning the blur feature off is not."
    )


def test_build_script_installs_the_fragment_and_asserts_its_content():
    """Shipping it is not enough — the recipe must fail loudly if it is empty.

    An empty or truncated keyfile installs and compiles without complaint and
    looks identical to a correct one from the outside.
    """
    text = BUILD_SH.read_text(encoding="utf-8")
    assert "02-intergenos-shell-effects" in text, (
        "build.sh never mentions the fragment, so it is not installed"
    )
    assert re.search(
        r'install -m644 "\$\{assets\}/dconf/db/local\.d/02-intergenos-shell-effects"',
        text,
    ), "build.sh does not install the fragment from the assets dir"
    assert INSTALLED_FRAGMENT in text, (
        "build.sh does not check the fragment's presence in DESTDIR"
    )
    assert "static-blur=false" in text, (
        "build.sh installs the fragment but never asserts its content, so an "
        "empty file would ship silently"
    )


def test_installed_path_is_declared_in_verify_paths():
    """DESTDIR presence and installed-system presence are different layers."""
    text = PACKAGE_YML.read_text(encoding="utf-8")
    assert INSTALLED_FRAGMENT in text, (
        f"{INSTALLED_FRAGMENT} is not in the package's verify_paths, so the "
        "pre-squashfs audit would not notice it missing from a built image"
    )


def test_the_fragment_is_not_locked():
    """These are defaults a user may override, not policy.

    A lock here would take a working setting away from the user to work around a
    defect, which is the opposite of what the fragment is for.
    """
    locks_dir = SETTINGS_PKG / "assets" / "dconf" / "db" / "local.d" / "locks"
    if not locks_dir.is_dir():
        return
    for lock in locks_dir.iterdir():
        if not lock.is_file():
            continue
        body = lock.read_text(encoding="utf-8")
        assert "blur-my-shell" not in body, (
            f"{lock.name} locks a blur setting. These ship as overridable "
            "defaults deliberately."
        )
