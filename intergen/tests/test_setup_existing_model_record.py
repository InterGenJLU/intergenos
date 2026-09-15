# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Existing-model setup re-verifies bytes before repairing its legal record."""

import hashlib
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from intergen import model_manager, model_setup_dispatch as dispatch, setup
from intergen.interfaces.types import HardwareTierLevel, ModelInfo


@pytest.fixture
def existing_model(tmp_path, monkeypatch):
    store = tmp_path / "models"
    store.mkdir()
    filename = "fixture-model.gguf"
    artifact = store / filename
    artifact.write_bytes(b"verified fixture weights")
    entry = {
        "name": "Fixture model", "filename": filename,
        "repo_id": "fixture/model", "license_ref": "Apache-2.0",
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }
    pins = tmp_path / "models-manifest.json"
    pins.write_text(json.dumps({"entries": [entry]}))
    legal = tmp_path / "legal"
    legal.mkdir()
    record = legal / f"{filename}-accepted.json"
    record.write_text(json.dumps({"license_ref": "unknown", "canonical_url": "wrong"}))
    manifest = tmp_path / "local-manifest.json"
    manager = model_manager.ModelManager(model_dir=store,
                                        manifest_path=manifest, pins_path=pins)
    model = ModelInfo(
        name=entry["name"], filename=filename, repo_id=entry["repo_id"],
        quant="Q4_K_M", size_gb=1, sha256=entry["sha256"],
        tier=HardwareTierLevel.TIER_1, downloaded=True, local_path=str(artifact),
    )
    manager.resolve_for_detected = lambda tier: model
    monkeypatch.setattr(model_manager, "ModelManager", lambda: manager)
    tier = SimpleNamespace(tier=HardwareTierLevel.TIER_1, ram_gb=16.0,
                           gpu_vendor=None, gpu_model=None, gpu_vram_mb=None)
    monkeypatch.setattr("intergen.hardware.HardwareDetector",
                        lambda: SimpleNamespace(detect=lambda: tier))
    monkeypatch.setattr(setup, "_invoking_user", lambda: (tmp_path, 1000, 1000))
    monkeypatch.setattr(setup, "_is_discrete_for", lambda tier: False)
    monkeypatch.setattr("intergen.model_choice.build_offer",
                        lambda **kwargs: SimpleNamespace(advisory=None))
    monkeypatch.setattr(setup, "_choose_tier", lambda *a, **kw: HardwareTierLevel.TIER_1)
    token, key, restart = Mock(), Mock(), Mock(return_value=True)
    monkeypatch.setattr(setup, "_generate_auth_token", token)
    monkeypatch.setattr(setup, "_generate_dispatch_key", key)
    monkeypatch.setattr(setup, "_restart_user_daemon", restart)
    monkeypatch.setattr(setup, "_ensure_embedding_model", lambda mm: True)

    def forbidden(*args, **kwargs):
        pytest.fail("record-only verification must not download or copy a model")

    monkeypatch.setattr(manager, "download_model", forbidden)
    monkeypatch.setattr(dispatch, "_install_staged", forbidden)
    calls = []

    def invoke_record(payload):
        return dispatch.provision(
            payload, pins_path=pins, model_dir=store, manifest_path=manifest,
            system_legal_dir=legal, accepted_by="consenting-user",
        )

    def privileged_transport(argv, **kwargs):
        calls.append(argv)
        assert argv[:2] == ["/usr/bin/pkexec", model_manager.PROVISION_RUNNER_PATH]
        payload = json.loads(argv[2])
        assert payload == {"filename": filename, "record_only": True}
        ok, message = invoke_record(payload)
        return subprocess.CompletedProcess(argv, 0 if ok else 1, message, "")

    monkeypatch.setattr(model_manager.subprocess, "run", privileged_transport)
    return SimpleNamespace(
        artifact=artifact, entry=entry, pins=pins, record=record, manifest=manifest,
        manager=manager, calls=calls, token=token, key=key, restart=restart,
        invoke_record=invoke_record,
    )


def test_setup_rewrites_stale_record_after_verifying_installed_bytes(existing_model, capsys):
    case = existing_model
    before = case.artifact.stat()
    assert setup.run_setup(auto_yes=True)
    record = json.loads(case.record.read_text())
    assert record["license_ref"] == "Apache-2.0"
    assert record["canonical_url"] == "https://www.apache.org/licenses/LICENSE-2.0"
    assert record["sha256"] == case.entry["sha256"]
    assert record["license_source"] == f"the shipped descriptor {case.pins} (package-verified)"
    assert case.calls
    after = case.artifact.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_size) == (before.st_ino, before.st_mtime_ns, before.st_size)
    assert not case.manifest.exists()
    output = capsys.readouterr().out
    print(output, end="")
    assert "re-verified" in output and "rewritten" in output
    case.restart.assert_called_once()


def test_setup_preserves_already_correct_record(existing_model, capsys):
    case = existing_model
    correct = {
        "model": case.entry["name"], "filename": case.entry["filename"],
        "repo_id": case.entry["repo_id"], "sha256": case.entry["sha256"],
        "license_ref": "Apache-2.0",
        "canonical_url": "https://www.apache.org/licenses/LICENSE-2.0",
        "license_source": f"the shipped descriptor {case.pins} (package-verified)",
        "accepted_by": "original-user", "accepted_at": "2026-01-01T00:00:00Z",
        "scope": "system",
    }
    case.record.write_text(json.dumps(correct))
    before_bytes, before_stat = case.record.read_bytes(), case.record.stat()
    assert setup.run_setup(auto_yes=True)
    assert case.calls
    assert case.record.read_bytes() == before_bytes
    after = case.record.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before_stat.st_ino, before_stat.st_mtime_ns)
    output = capsys.readouterr().out
    print(output, end="")
    assert "re-verified" in output and "already correct" in output


@pytest.mark.parametrize("problem", ["tampered", "missing_artifact", "missing_entry",
                                     "wrong_digest", "unmapped_license"])
def test_setup_refuses_damaged_or_unmatched_model_without_ready(existing_model, capsys, problem):
    case = existing_model
    if problem == "tampered":
        case.artifact.write_bytes(b"tampered model")
    elif problem == "missing_artifact":
        case.artifact.unlink()
    else:
        entry = dict(case.entry)
        if problem == "wrong_digest":
            entry["sha256"] = "f" * 64
        elif problem == "unmapped_license":
            entry["license_ref"] = "LicenseRef-Unmapped"
        case.pins.write_text(json.dumps({"entries": [] if problem == "missing_entry" else [entry]}))
    before = case.record.read_bytes()
    assert not setup.run_setup(auto_yes=True)
    assert case.record.read_bytes() == before
    assert case.calls
    assert not case.manifest.exists()
    case.token.assert_not_called()
    case.key.assert_not_called()
    case.restart.assert_not_called()
    output = capsys.readouterr().out
    print(output, end="")
    assert "refused" in output.lower()
    assert case.entry["filename"] in output
    assert "InterGen is ready" not in output


def test_setup_does_not_trust_a_correct_record_over_tampered_bytes(existing_model, capsys):
    case = existing_model
    assert setup.run_setup(auto_yes=True)
    original = case.record.read_bytes()
    case.artifact.write_bytes(b"damaged after the record was written")
    case.restart.reset_mock()
    assert not setup.run_setup(auto_yes=True)
    assert case.record.read_bytes() == original
    case.restart.assert_not_called()
    output = capsys.readouterr().out
    print(output, end="")
    assert "sha256 mismatch" in output


def test_setup_propagates_record_write_failure(existing_model, monkeypatch, capsys):
    case = existing_model
    original = case.record.read_bytes()

    def fail_replace(*args):
        raise OSError("fixture record replacement failure")

    monkeypatch.setattr(dispatch.os, "replace", fail_replace)
    assert not setup.run_setup(auto_yes=True)
    assert case.record.read_bytes() == original
    case.token.assert_not_called()
    case.key.assert_not_called()
    case.restart.assert_not_called()
    output = capsys.readouterr().out
    print(output, end="")
    assert "fixture record replacement failure" in output
    assert "refused" in output.lower()
    assert "InterGen is ready" not in output


@pytest.mark.parametrize("returncode,message", [(126, "authorization denied"),
                                               (127, "runner unavailable"),
                                               (0, "")])
def test_setup_propagates_missing_runner_result(existing_model, monkeypatch, capsys,
                                               returncode, message):
    case = existing_model
    original = case.record.read_bytes()
    monkeypatch.setattr(model_manager.subprocess, "run", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, returncode, message, ""))
    assert not setup.run_setup(auto_yes=True)
    assert case.record.read_bytes() == original
    case.token.assert_not_called()
    case.restart.assert_not_called()
    output = capsys.readouterr().out
    print(output, end="")
    assert "refused" in output.lower()
    assert "InterGen is ready" not in output


@pytest.mark.parametrize("extra", [{"staging_path": "/caller-selected/model.gguf"},
                                  {"mmproj_filename": "projector.gguf"},
                                  {"record_only": "yes"}])
def test_record_only_refuses_caller_paths_or_ambiguous_mode(existing_model, extra):
    case = existing_model
    original = case.record.read_bytes()
    payload = {"filename": case.entry["filename"], "record_only": True, **extra}
    ok, message = case.invoke_record(payload)
    assert not ok, message
    assert "refusing" in message
    assert case.record.read_bytes() == original
    assert not case.manifest.exists()
