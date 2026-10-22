# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A capture scope is taken only as files, directories and links it held.

The config-state capture with a scope, the restore-point capture with a PATH
list and the queued intents that carry either run as root over paths the
caller names. Each case names a FIFO whose writer is blocked in its own open
(released only if the capture opens the FIFO), a device (an audit hook records
every open with its flags, so an O_PATH hold, which opens nothing, is told
apart from an open for reading), or a file reached through a directory link.
The capture must record such a path as not captured and never open or store
it; the controls show the instruments see a real open.
"""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from chronicle import cas, configstate, engine, manifest, paths, restorepoint
from chronicle import sentinel

ONLY_ROOT = b"BYTES ONLY ROOT MAY READ (stand-in)\n"

_OPENS = []
_RECORDING = [False]


def _record_opens(event, args):
    if _RECORDING[0] and event == "open" and isinstance(args[0], str):
        _OPENS.append((args[0], args[2] if len(args) > 2 and args[2] else 0))


sys.addaudithook(_record_opens)


def opened_for_io(path):
    name = os.path.basename(path)
    return [(p, f) for p, f in _OPENS
            if (p == path or p == name) and not f & os.O_PATH]


class _BlockedWriter:
    def __init__(self, fifo):
        self.fifo, self.released = fifo, False
        self._thread = threading.Thread(target=self._wait, daemon=True)
        self._thread.start()
        time.sleep(0.3)

    def _wait(self):
        fd = os.open(self.fifo, os.O_WRONLY)
        self.released = True
        os.close(fd)

    def settle(self):
        self._thread.join(1.0)
        released = self.released
        if not released:
            mine = os.open(self.fifo, os.O_RDWR | os.O_NONBLOCK)
            self._thread.join(2.0)
            os.close(mine)
        return released


@pytest.fixture
def scratch(tmp_path):
    store_root = tmp_path / "store"
    store_root.mkdir()
    fifo = tmp_path / "a-fifo"
    os.mkfifo(fifo, 0o600)
    _OPENS.clear()
    yield tmp_path, store_root, cas.ContentStore(store_root), fifo
    _RECORDING[0] = False


def _version(root, layer, version):
    return manifest.find_version(root, layer, version)


def run_with_deadline(fn, fifo, seconds=3.0):
    """Run `fn` in a thread; a call still waiting after `seconds` is blocked
    in an open of the FIFO (it was opened with no peer left): a writer
    opened from here lets it go so the case can end. Returns fn's result."""
    out = {}

    def target():
        try:
            out["result"] = fn()
        except BaseException as error:
            out["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        thread.join(5.0)
        out.setdefault("waited", True)
    if "error" in out:
        raise out["error"]
    return out.get("result")


def test_control_the_writer_instrument_sees_an_open(scratch):
    _tmp, _root, _store, fifo = scratch
    writer = _BlockedWriter(fifo)
    os.close(os.open(fifo, os.O_RDONLY | os.O_NONBLOCK))
    assert writer.settle()


def test_control_the_audit_instrument_sees_an_open_of_a_device(scratch):
    _RECORDING[0] = True
    os.close(os.open("/dev/null", os.O_RDONLY))
    _RECORDING[0] = False
    assert opened_for_io("/dev/null")


def test_a_config_state_scope_naming_a_fifo_never_opens_it(scratch):
    _tmp, root, store, fifo = scratch
    writer = _BlockedWriter(fifo)
    version = run_with_deadline(
        lambda: configstate.capture([str(fifo)], root, store, 1, 0, "scope"),
        fifo)
    assert not writer.settle(), "the capture opened the FIFO in its scope"
    found = _version(root, paths.LAYER_CONFIG_STATE, version)
    assert [e for e in found["entries"] if e["path"] == str(fifo)] == []
    assert [u["path"] for u in found["unreadable"]] == [str(fifo)]
    assert "a FIFO" in found["unreadable"][0]["error"]


def test_a_config_state_scope_naming_a_device_never_opens_it(scratch):
    _tmp, root, store, _fifo = scratch
    _RECORDING[0] = True
    version = configstate.capture(["/dev/null"], root, store, 1, 0, "device")
    _RECORDING[0] = False
    assert opened_for_io("/dev/null") == [], _OPENS
    found = _version(root, paths.LAYER_CONFIG_STATE, version)
    assert [e for e in found["entries"] if e["path"] == "/dev/null"] == []
    assert "a character device" in found["unreadable"][0]["error"]


def test_a_device_inside_a_scoped_directory_is_never_opened(scratch):
    tmp, root, store, fifo = scratch
    scoped = tmp / "scoped"
    scoped.mkdir()
    os.rename(fifo, scoped / "a-fifo")
    (scoped / "plain").write_bytes(b"plain bytes\n")
    writer = _BlockedWriter(scoped / "a-fifo")
    version = run_with_deadline(
        lambda: configstate.capture([str(scoped)], root, store, 1, 0, "tree"),
        scoped / "a-fifo")
    assert not writer.settle(), "the capture opened the FIFO in the tree"
    found = _version(root, paths.LAYER_CONFIG_STATE, version)
    captured = {e["path"]: e for e in found["entries"]}
    assert captured[str(scoped / "plain")]["sha256"] == cas.sha256_bytes(
        b"plain bytes\n")
    assert str(scoped / "a-fifo") not in captured
    assert [u["path"] for u in found["unreadable"]] == [str(scoped / "a-fifo")]


def test_a_scope_reached_through_a_directory_link_stores_nothing_of_it(
        scratch):
    tmp, root, store, _fifo = scratch
    rootish = tmp / "rootish"
    rootish.mkdir(mode=0o700)
    (rootish / "secret").write_bytes(ONLY_ROOT)
    os.chmod(rootish / "secret", 0o600)
    (tmp / "linked").symlink_to(rootish)
    version = configstate.capture([str(tmp / "linked" / "secret")], root,
                                  store, 1, 0, "linked")
    found = _version(root, paths.LAYER_CONFIG_STATE, version)
    assert found["entries"] == []
    assert not store.exists(cas.sha256_bytes(ONLY_ROOT))
    assert [u["path"] for u in found["unreadable"]] == [
        str(tmp / "linked" / "secret")]


def test_a_restore_point_path_list_naming_a_fifo_never_opens_it(scratch):
    _tmp, root, store, fifo = scratch
    writer = _BlockedWriter(fifo)
    footprint = restorepoint.on_demand_footprint([str(fifo)], "on demand",
                                                 db_path=str(fifo) + ".db")
    version = run_with_deadline(
        lambda: restorepoint.capture_from_footprint(footprint, root, store,
                                                    1, 0),
        fifo)
    assert not writer.settle(), "the restore point opened the FIFO"
    found = _version(root, paths.LAYER_RESTORE_POINT, version)
    assert [e for e in found["entries"] if e["path"] == str(fifo)] == []
    assert [u["path"] for u in found.get("unreadable", [])] == [str(fifo)]


def test_a_queued_intent_carrying_a_scope_is_drained_the_same_way(scratch):
    tmp, _root, _store, fifo = scratch
    eng = engine.Engine(local_root=str(tmp / "local"), now_fn=lambda: 0.0)
    queued = eng.capture(paths.LAYER_CONFIG_STATE, scope=[str(fifo)],
                         reason="queued scope", sync=False)
    assert queued.get("queued")
    writer = _BlockedWriter(fifo)
    off_peak = next(m for m in range(0, 1440, 30)
                    if eng.config.is_off_peak(m))
    drained, remaining, _unreadable = run_with_deadline(
        lambda: sentinel.drain_offpeak(eng, off_peak), fifo)
    assert not writer.settle(), "the drain opened the FIFO in the scope"
    assert (drained, remaining) == (1, 0)
    newest = eng.list_versions(paths.LAYER_CONFIG_STATE)[-1]
    assert newest["unreadable"] == 1
