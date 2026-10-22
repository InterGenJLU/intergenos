# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Local history and truthful queue output survive a refused backup target."""

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace

import pytest

from chronicle import api, cli, config, engine, gui, paths, sentinel


@pytest.fixture
def scratch(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    document = source / "settings"
    document.write_bytes(b"local settings\n")
    cfg = config.Config()
    cfg.user_data_paths = [str(source)]
    clock = [1_000_000.0]
    built = engine.Engine(local_root=tmp_path / "local", config=cfg,
                          now_fn=lambda: clock[0])
    target = tmp_path / "target"
    target.mkdir()
    built.target_adopt(str(target), target_class="directory", cap_bytes=10**8)
    return SimpleNamespace(engine=built, target=target, source=source,
                           document=document, clock=clock)


def refuse(scratch):
    scratch.target.chmod(0o775)
    with pytest.raises(engine.EngineError, match="is refused") as error:
        scratch.engine.target_root()
    return str(error.value)


def local_scope(scratch, layer):
    if layer == paths.LAYER_RESTORE_POINT:
        return {"verb": "upgrade", "packages": ["fixture"],
                "reason": "pre-transaction fixture",
                "paths": [str(scratch.document)]}
    return [str(scratch.document)]


class InProcessBackend:
    def __init__(self, built):
        self.engine = built

    def call(self, verb, **args):
        response = api.dispatch(self.engine, {"verb": verb, "args": args})
        assert response["ok"], response
        return response["result"]


@pytest.mark.parametrize("layer", paths.LOCAL_LAYERS)
def test_local_capture_keeps_its_version_and_reports_no_mirror(scratch, layer):
    reason = refuse(scratch)
    result = scratch.engine.capture(layer, scope=local_scope(scratch, layer))
    assert result["mirrored"] is False
    assert result["target_refused"] == reason
    assert [v["version_id"] for v in scratch.engine.list_versions(layer)] == [
        result["version_id"]]
    assert scratch.engine.verify(layer, result["version_id"])["ok"]
    assert scratch.engine.status()["target_refused"] == reason
    assert not any((scratch.target / paths.TARGET_DIR_NAME).rglob("*.json"))


def test_attached_target_control_has_a_mirrored_manifest(scratch):
    result = scratch.engine.capture(paths.LAYER_CONFIG_STATE,
                                    scope=[str(scratch.document)])
    manifests = list((scratch.target / paths.TARGET_DIR_NAME).rglob("*.json"))
    assert any(result["version_id"] in p.read_text() for p in manifests)


def test_queue_and_status_name_refusal_without_promising_attachment(scratch):
    reason = refuse(scratch)
    scratch.engine.capture(paths.LAYER_USER_DATA, sync=False)
    queued = scratch.engine.queue_status()
    assert reason in queued["summary"]
    assert "refused" in queued["summary"].lower()
    assert "once it is attached" not in queued["summary"]
    assert "will back up after" not in queued["summary"]
    assert scratch.engine.status()["queue"] == queued
    args = SimpleNamespace(json=True)
    output = io.StringIO()
    with redirect_stdout(output):
        cli.cmd_status(InProcessBackend(scratch.engine), args, cli.Reporter())
    assert json.loads(output.getvalue())["queue"]["summary"] == queued["summary"]


def test_window_queue_row_carries_the_refusal(scratch, monkeypatch):
    reason = refuse(scratch)
    scratch.engine.capture(paths.LAYER_USER_DATA, sync=False)
    rows = []
    monkeypatch.setattr(gui.Adw, "ActionRow", lambda **kwargs: kwargs)
    window = SimpleNamespace(_status_add=rows.append)
    gui.ChronicleWindow._add_status_rows(window, scratch.engine.status())
    row = next(row for row in rows if row["title"] == "Queue")
    assert reason in row["subtitle"]


def test_offpeak_drains_local_intents_and_keeps_refused_user_data(scratch, caplog):
    # Put user-data first: its refusal must not stop the later local intents.
    user = scratch.engine.capture(paths.LAYER_USER_DATA, sync=False)["queued"]
    for layer in paths.LOCAL_LAYERS:
        scratch.clock[0] += 1
        scratch.engine.capture(layer, scope=local_scope(scratch, layer), sync=False)
    reason = refuse(scratch)
    drained, remaining, unreadable = sentinel.drain_offpeak(scratch.engine, now_min=0)
    assert (drained, remaining, unreadable) == (len(paths.LOCAL_LAYERS), 1, [])
    assert [intent["id"] for intent in scratch.engine.queue.list()] == [user]
    assert reason in scratch.engine.queue_status()["summary"]
    assert reason in caplog.text
    for layer in paths.LOCAL_LAYERS:
        assert len(scratch.engine.list_versions(layer)) == 1


def test_scrub_checks_local_history_and_names_the_unchecked_target(scratch):
    result = scratch.engine.capture(paths.LAYER_CONFIG_STATE,
                                    scope=[str(scratch.document)])
    reason = refuse(scratch)
    report = scratch.engine.scrub()
    assert report["clean"] and not report["corrupt"]
    assert report["target_refused"] == reason
    assert scratch.engine.verify(paths.LAYER_CONFIG_STATE, result["version_id"])["ok"]


@pytest.mark.parametrize("json_output", [False, True])
def test_scrub_command_does_not_claim_the_refused_target_was_checked(scratch, json_output):
    reason = refuse(scratch)
    args = SimpleNamespace(scrub=True, json=json_output)
    output, errors = io.StringIO(), io.StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        status = cli.cmd_verify(InProcessBackend(scratch.engine), args, cli.Reporter())
    assert status == 1  # The requested all-store scrub could not check the target.
    if json_output:
        assert json.loads(output.getvalue())["target_refused"] == reason
    else:
        assert "local" in output.getvalue().lower()
        assert "every stored version verifies" not in output.getvalue()
        assert reason in errors.getvalue()


@pytest.mark.parametrize("layer", paths.LOCAL_LAYERS)
def test_retention_still_visits_the_local_store(scratch, layer):
    captured = scratch.engine.capture(layer, scope=local_scope(scratch, layer))
    refuse(scratch)
    result = scratch.engine.retention_apply(layer)
    assert captured["version_id"] in result["kept"]
    assert not result["pruned"]
    assert scratch.engine.verify(layer, captured["version_id"])["ok"]


@pytest.mark.parametrize("state", ["absent", "refused"])
def test_queued_user_data_command_says_what_it_waits_for(scratch, state):
    if state == "refused":
        reason = refuse(scratch)
    else:
        scratch.target.rename(scratch.target.with_name("target-away"))
        reason = "waiting for the backup target"
    args = SimpleNamespace(layer=paths.LAYER_USER_DATA, paths=[], reason="fixture",
                           async_=True, json=False)
    output = io.StringIO()
    with redirect_stdout(output):
        cli.cmd_capture(InProcessBackend(scratch.engine), args, cli.Reporter())
    assert reason in output.getvalue()
    assert "Queued for the off-peak window" not in output.getvalue()


def test_capture_toast_reports_that_local_history_was_not_mirrored():
    result = {"version_id": "local-version", "unreadable": [],
              "mirrored": False, "target_refused": "backup target is refused"}
    text = gui.capture_toast(paths.LAYER_CONFIG_STATE, result)
    assert "local-version" in text
    assert "not mirrored" in text.lower()
    assert result["target_refused"] in text
