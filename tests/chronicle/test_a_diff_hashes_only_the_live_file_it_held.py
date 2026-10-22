# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A diff hashes only the live file it held, never what a link names.

The diff verb runs as root and hashes the live file at an entry's recorded
path, which for a scope or user-data entry lies in a directory its account
controls. Each case lets the account change its own tree: the file made a link
to a file standing for one only root may read, its directory made such a link,
or the file exchanged for a FIFO just as the diff acts on it (the FIFO's writer
is blocked in its own open, released only if the diff opens the FIFO). The
diff must not return the other file's digest nor open the FIFO, and says why
it did not read the live path.
"""

import os
import sys
import threading
import time

import pytest

from chronicle import cas, engine, paths

ONLY_ROOT = b"BYTES ONLY ROOT MAY READ (stand-in)\n"
OWN = b"the account's own settings\n"

_HOOK = {"on": None}


def _audit(event, args):
    action = _HOOK["on"]
    if action is not None and event == "open" and args \
            and isinstance(args[0], str):
        action(args[0])


sys.addaudithook(_audit)


@pytest.fixture
def captured(tmp_path):
    d = tmp_path / "acct" / "d"
    d.mkdir(parents=True)
    conf = d / "conf"
    conf.write_bytes(OWN)
    rootish = tmp_path / "rootish"
    rootish.mkdir(mode=0o700)
    (rootish / "conf").write_bytes(ONLY_ROOT)
    os.chmod(rootish / "conf", 0o600)
    eng = engine.Engine(local_root=str(tmp_path / "local"), now_fn=lambda: 0.0)
    version = eng.capture(paths.LAYER_CONFIG_STATE, scope=[str(conf)],
                          reason="diff")["version_id"]
    yield tmp_path, eng, version, d, conf, rootish
    _HOOK["on"] = None


def _diff(eng, version, conf):
    return eng.diff(paths.LAYER_CONFIG_STATE, version, str(conf))


def test_control_an_unchanged_live_file_reads_as_unchanged(captured):
    _tmp, eng, version, _d, conf, _rootish = captured
    result = _diff(eng, version, conf)
    assert result["live_sha256"] == cas.sha256_bytes(OWN)
    assert result["changed"] is False


def test_a_live_file_made_a_link_is_not_followed(captured):
    _tmp, eng, version, _d, conf, rootish = captured
    conf.unlink()
    conf.symlink_to(rootish / "conf")
    result = _diff(eng, version, conf)
    assert result["live_sha256"] != cas.sha256_bytes(ONLY_ROOT)
    assert result["live_sha256"] is None
    assert "a link" in result["live_refused"]


def test_a_directory_made_a_link_is_not_followed(captured):
    _tmp, eng, version, d, conf, rootish = captured
    os.rename(d, d.with_name("d.was"))
    d.symlink_to(rootish)
    result = _diff(eng, version, conf)
    assert result["live_sha256"] != cas.sha256_bytes(ONLY_ROOT)
    assert result["live_sha256"] is None
    assert "a link" in result["live_refused"]


def test_a_fifo_exchanged_in_as_the_diff_acts_is_never_opened(captured):
    tmp, eng, version, _d, conf, _rootish = captured
    waiting = tmp / "waiting-fifo"
    os.mkfifo(waiting, 0o600)
    released = []

    def writer():
        fd = os.open(waiting, os.O_WRONLY)
        released.append(True)
        os.close(fd)

    peer = threading.Thread(target=writer, daemon=True)
    peer.start()
    time.sleep(0.3)
    exchanged = []

    def exchange(path):
        if not exchanged and path in (str(conf), "conf"):
            os.rename(conf, conf.with_name("conf.was"))
            os.rename(waiting, conf)
            exchanged.append(path)

    _HOOK["on"] = exchange
    try:
        result = _diff(eng, version, conf)
    finally:
        _HOOK["on"] = None
        peer.join(1.0)
        was_released = bool(released)
        if not was_released:
            fifo = conf if exchanged else waiting
            mine = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
            peer.join(2.0)
            os.close(mine)
    assert exchanged, "the exchange never fired"
    assert not was_released, "the diff opened the FIFO at the live path"
    assert result["live_sha256"] is None
    assert "a FIFO" in result["live_refused"]
