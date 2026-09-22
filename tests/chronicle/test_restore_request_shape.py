# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Malformed restore path lists are refused before engine work or escalation."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from chronicle import api, config, engine, escalate


@pytest.mark.parametrize("verb", ["restore-plan", "restore"])
@pytest.mark.parametrize("value", ["/saved", None, 123, {}, ("/saved",),
                                   [None], [123], [True], [["/saved"]], [b"/saved"]])
def test_dispatch_refuses_invalid_path_shapes(tmp_path, monkeypatch, verb, value):
    backend = engine.Engine(local_root=tmp_path / "store", config=config.Config())
    calls = []

    def reached_engine(*args, **kwargs):
        calls.append((args, kwargs))
        return {"unexpected": True}

    monkeypatch.setattr(backend, "restore_plan", reached_engine)
    monkeypatch.setattr(backend, "restore_apply", reached_engine)
    response = api.dispatch(backend, {"verb": verb, "args": {
        "layer": "restore-point", "version_id": "unused", "paths": value}})
    assert response == {"ok": False, "error": "Restore paths must be a list of strings."}
    assert calls == []


@pytest.mark.parametrize("verb", ["restore-plan", "restore"])
def test_missing_path_list_has_the_same_refusal(tmp_path, verb):
    backend = engine.Engine(local_root=tmp_path / "store", config=config.Config())
    response = api.dispatch(backend, {"verb": verb, "args": {
        "layer": "restore-point", "version_id": "unused"}})
    assert response == {"ok": False, "error": "Restore paths must be a list of strings."}


@pytest.mark.parametrize("verb", ["restore-plan", "restore"])
def test_valid_path_list_preserves_names_and_blank_rule(tmp_path, monkeypatch, verb):
    document = tmp_path / " name with spaces "
    document.write_bytes(b"saved\n")
    backend = engine.Engine(local_root=tmp_path / "store", config=config.Config())
    version = backend.capture("restore-point", scope={
        "paths": [str(document)], "packages": ["sample"]})["version_id"]
    monkeypatch.setattr(escalate, "has_cap_chown", lambda: True)
    monkeypatch.setattr(escalate, "run_restore_via_unit",
                        lambda *a, **kw: pytest.fail("unexpected service escalation"))
    args = {"layer": "restore-point", "version_id": version,
            "paths": [str(document)], "mode": "beside"}
    response = api.dispatch(backend, {"verb": verb, "args": args})
    assert response["ok"], response
    field = "actions" if verb == "restore-plan" else "results"
    assert response["result"][field][0]["path"] == str(document)
    args["paths"] = [" \t"]
    assert api.dispatch(backend, {"verb": verb, "args": args}) == {
        "ok": False, "error": "Restore paths must not be empty or blank."}


@pytest.fixture
def saved_point(tmp_path):
    document = tmp_path / "document"
    document.write_bytes(b"saved\n")
    backend = engine.Engine(local_root=tmp_path / "store", config=config.Config())
    version = backend.capture("restore-point", scope={
        "paths": [str(document)], "packages": ["sample"]})["version_id"]
    document.write_bytes(b"current\n")
    return backend, version, document


@pytest.mark.parametrize("method", ["restore_plan", "restore_apply"])
@pytest.mark.parametrize("shape", ["string", "integer-in-list", "none", "dict"])
def test_engine_refuses_invalid_shapes_before_work(saved_point, monkeypatch, method, shape):
    backend, version, document = saved_point
    value = {"string": str(document), "integer-in-list": [str(document), 7],
             "none": None, "dict": {str(document): True}}[shape]
    calls = []
    monkeypatch.setattr(escalate, "has_cap_chown", lambda: False)
    monkeypatch.setattr(escalate, "run_restore_via_unit",
                        lambda *a, **kw: calls.append((a, kw)))
    with pytest.raises(engine.EngineError, match=r"^Restore paths must be a list of strings\.$"):
        getattr(backend, method)("restore-point", version, value, "beside")
    assert calls == []
    assert document.read_bytes() == b"current\n"
    assert not list(document.parent.glob("*.chronicle-restored-*"))


@pytest.mark.parametrize("shape", ["string", "integer-in-list", "none", "dict", "missing"])
def test_daemon_request_refuses_invalid_shapes_cleanly(saved_point, tmp_path, shape):
    _, version, document = saved_point
    entry = Path(__file__).resolve().parents[2] / "assets/intergenos-backup/chronicled"
    conf = tmp_path / "chronicle.conf"
    conf.write_text("# Isolated request-file test.\n")
    req = {"layer": "restore-point", "version_id": version, "mode": "beside"}
    if shape != "missing":
        req["paths"] = {"string": str(document), "integer-in-list": [str(document), 7],
                        "none": None, "dict": {str(document): True}}[shape]
    request = tmp_path / "request.json"
    request.write_text(json.dumps(req))
    marker = tmp_path / "unexpected-escalation"
    # Run the real entry point in a subprocess, instrumenting only its
    # capability/service boundary so a regression cannot launch a host unit.
    launch = """
import runpy, sys
from pathlib import Path
entry, marker, *args = sys.argv[1:]
sys.path.insert(0, str(Path(entry).parent))
from chronicle import escalate
escalate.has_cap_chown = lambda: False
def unexpected(*args, **kwargs):
    Path(marker).write_text('reached')
    raise AssertionError('invalid request reached service escalation')
escalate.run_restore_via_unit = unexpected
sys.argv = [entry, *args]
runpy.run_path(entry, run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", launch, str(entry), str(marker),
         "--config", str(conf), "--local-root", str(tmp_path / "store"),
         "--task", "restore", "--request", str(request)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert result.stderr.strip() == "chronicled: Restore paths must be a list of strings."
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert not marker.exists()
    assert not request.with_suffix(".result.json").exists()
    assert document.read_bytes() == b"current\n"
    assert not list(document.parent.glob("*.chronicle-restored-*"))


@pytest.mark.parametrize("method", ["restore_plan", "restore_apply"])
def test_empty_list_keeps_existing_no_work_behavior(saved_point, monkeypatch, method):
    backend, version, document = saved_point
    monkeypatch.setattr(escalate, "has_cap_chown", lambda: True)
    result = getattr(backend, method)("restore-point", version, [], "beside")
    assert result["actions" if method == "restore_plan" else "results"] == []
    assert document.read_bytes() == b"current\n"
