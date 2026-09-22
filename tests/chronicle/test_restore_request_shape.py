# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Malformed restore path lists are refused before engine work or escalation."""

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
    assert response == {"ok": False, "error": "Restore paths must not be empty or blank."}
    assert calls == []


@pytest.mark.parametrize("verb", ["restore-plan", "restore"])
def test_missing_path_list_has_the_same_refusal(tmp_path, verb):
    backend = engine.Engine(local_root=tmp_path / "store", config=config.Config())
    response = api.dispatch(backend, {"verb": verb, "args": {
        "layer": "restore-point", "version_id": "unused"}})
    assert response == {"ok": False, "error": "Restore paths must not be empty or blank."}


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
