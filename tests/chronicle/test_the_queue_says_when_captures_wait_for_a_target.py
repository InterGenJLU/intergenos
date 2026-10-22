# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The queue says when captures wait for a backup target.

While no backup target is attached, the hourly user-data trigger queues one
capture an hour, and the off-peak drain leaves each of them queued: a
user-data capture cannot run without its target. The status line said every
queued capture "will back up after" the working hours, a time at which none of
them could run. It now says how many wait for the target and that they run
once it is attached; a capture that can run off-peak keeps the old sentence.
"""

import os

import pytest

from chronicle import cli, engine, paths, sentinel


@pytest.fixture
def eng(tmp_path):
    os.umask(0o022)
    clock = [1_000_000.0]
    built = engine.Engine(local_root=str(tmp_path / "local"),
                          now_fn=lambda: clock[0])
    built.test_clock = clock
    return built, tmp_path


def _summary(built):
    return built.status()["queue"]["summary"]


def test_hourly_captures_with_no_target_say_they_wait_for_one(eng):
    built, _tmp = eng
    for _ in range(3):
        built.test_clock[0] += 3600  # one trigger an hour, as the timer runs
        assert sentinel.userdata_trigger(built, now_min=600)["outcome"] == \
            "queued-absent"
    summary = _summary(built)
    assert "will back up after" not in summary
    assert summary == ("3 user-data captures are waiting for the backup "
                       "target and will run once one is adopted and "
                       "attached.")


def test_an_adopted_target_that_is_away_is_named_as_the_wait(eng):
    built, tmp = eng
    target = tmp / "target"
    target.mkdir()
    built.target_adopt(str(target), target_class="directory")
    os.rename(target, tmp / "target-away")
    sentinel.userdata_trigger(built, now_min=600)
    assert _summary(built) == ("1 user-data capture is waiting for the backup "
                               "target and will run once it is attached.")


def test_a_capture_that_can_run_off_peak_keeps_its_window(eng):
    built, tmp = eng
    sentinel.userdata_trigger(built, now_min=600)
    source = tmp / "conf"
    source.write_bytes(b"settings\n")
    built.capture(paths.LAYER_CONFIG_STATE, scope=[str(source)],
                  reason="large", sync=False)
    window = f"{built.config.work_start}–{built.config.work_end}"
    assert _summary(built) == (
        "1 user-data capture is waiting for the backup target and will run "
        "once one is adopted and attached. "
        f"1 change queued — will back up after {window}.")


def test_the_status_command_prints_the_wait(eng, capsys):
    built, _tmp = eng
    sentinel.userdata_trigger(built, now_min=600)

    class Backend:
        def call(self, verb, **args):
            return built.status()

    class Args:
        json = False

    cli.cmd_status(Backend(), Args(), cli.Reporter())
    out = capsys.readouterr().out
    assert "waiting for the backup target" in out
    assert "will back up after" not in out
