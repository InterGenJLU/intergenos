# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A store lists no names to another account.

Every file the engine writes in a store is 0600, but its directories were
0755: any account could list the content store's blob names, each the sha256
of a captured file's content, which answers whether a guessed file is in the
store. Nothing that reads a store runs as another account (the engine and its
tasks run as root; the window and the command line reach the engine through its
socket), so every directory the engine makes in a store is 0700, an older
store's root and skeleton are brought to 0700 when the engine opens it, and
every unit that creates the local store asks systemd for the same mode.
"""

import os
import stat
from pathlib import Path

import pytest

from chronicle import engine, paths, userdata

UNITS = Path(__file__).resolve().parents[2] / "assets" / "intergenos-backup" \
    / "systemd"


def _mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


def _directories(root):
    yield Path(root)
    for dirpath, dirnames, _files in os.walk(root):
        for name in dirnames:
            yield Path(dirpath) / name


@pytest.fixture
def eng(tmp_path):
    os.umask(0o022)
    source = tmp_path / "etc-ish"
    source.mkdir()
    (source / "conf").write_bytes(b"settings\n")
    built = engine.Engine(local_root=str(tmp_path / "local"),
                          now_fn=lambda: 0.0)
    return tmp_path, built, source


def test_every_directory_of_the_local_store_is_the_engines_alone(eng):
    tmp, built, source = eng
    built.capture(paths.LAYER_CONFIG_STATE, scope=[str(source)], reason="m")
    built.capture(paths.LAYER_CONFIG_STATE, scope=[str(source)], reason="q",
                  sync=False)
    modes = {str(d): _mode(d) for d in _directories(tmp / "local")}
    assert len(modes) >= 7, modes
    assert set(modes.values()) == {0o700}, modes


def test_every_directory_of_a_target_store_is_the_engines_alone(eng):
    tmp, built, source = eng
    target = tmp / "target"
    target.mkdir()
    built.target_adopt(str(target), target_class="directory")
    built.capture(paths.LAYER_CONFIG_STATE, scope=[str(source)], reason="m")
    home = tmp / "home"
    (home / "a").mkdir(parents=True)
    (home / "a" / "file").write_bytes(b"own\n")
    userdata.capture([str(home)], built.target_root(), None, 9, 0, "u")
    store = target / paths.TARGET_DIR_NAME
    for directory in [store, store / "cas", store / "versions",
                      store / "userdata"] + \
            [d for d in (store / "cas").iterdir()] + \
            [store / "versions" / layer for layer in paths.LAYERS]:
        assert _mode(directory) == 0o700, directory


def test_an_older_store_is_closed_when_the_engine_opens_it(tmp_path):
    local = tmp_path / "local"
    for directory in [local, local / "cas", local / "versions"] + \
            [local / "versions" / layer for layer in paths.LAYERS]:
        directory.mkdir(mode=0o755, parents=True)
        os.chmod(directory, 0o755)
    engine.Engine(local_root=str(local), now_fn=lambda: 0.0)
    assert _mode(local) == 0o700
    assert _mode(local / "cas") == 0o700
    assert _mode(local / "versions" / paths.LAYER_USER_DATA) == 0o700


@pytest.mark.parametrize("unit", sorted(p.name for p in UNITS.glob("*.service")
                                        if "StateDirectory=chronicle"
                                        in p.read_text()))
def test_every_unit_that_makes_the_local_store_asks_for_the_same_mode(unit):
    text = (UNITS / unit).read_text()
    assert "\nStateDirectoryMode=0700\n" in text, unit


def test_the_units_that_make_the_local_store_are_the_five_shipped():
    named = sorted(p.name for p in UNITS.glob("*.service")
                   if "StateDirectory=chronicle" in p.read_text())
    assert named == ["chronicle-offpeak.service", "chronicle-restore@.service",
                     "chronicle-scrub.service", "chronicle-userdata.service",
                     "chronicled.service"]
