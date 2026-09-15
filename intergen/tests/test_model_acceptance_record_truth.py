# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Provisioning records use package-shipped descriptors, not catalog guesses."""

import hashlib
import json

import pytest

from intergen import model_setup_dispatch as dispatch
from intergen.model_manager import MODEL_CATALOG


APACHE_URL = "https://www.apache.org/licenses/LICENSE-2.0"
QWEN_URL = "https://github.com/QwenLM/Qwen3.5/blob/main/LICENSE"


@pytest.fixture
def provision_case(tmp_path):
    filename = "Qwen3.5-9B-intergen-round3-Q4_K_M.gguf"
    staged = tmp_path / filename
    staged.write_bytes(b"fixture model bytes")
    entry = {
        "name": "Descriptor model name",
        "filename": filename,
        "repo_id": "descriptor/model",
        "sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
        "license_ref": "Apache-2.0",
    }
    pins = tmp_path / "models-manifest.json"
    store = tmp_path / "store"
    legal = tmp_path / "legal"

    def run(entries=None):
        pins.write_text(json.dumps({"version": "0.1", "entries":
                                    [entry] if entries is None else entries}))
        return dispatch.provision(
            {"filename": filename, "staging_path": str(staged)},
            pins_path=pins, model_dir=store,
            manifest_path=tmp_path / "manifest.json",
            system_legal_dir=legal, accepted_by="consenting-user",
        )

    return entry, pins, store, legal, run


@pytest.mark.parametrize("license_ref,url", [
    ("Apache-2.0", APACHE_URL),
    ("LicenseRef-Tongyi-Qianwen", QWEN_URL),
])
def test_record_matches_descriptor(provision_case, license_ref, url):
    entry, pins, store, legal, run = provision_case
    entry["license_ref"] = license_ref
    ok, message = run()
    assert ok, message
    record = json.loads((legal / f"{entry['filename']}-accepted.json").read_text())
    assert record["model"] == entry["name"]
    assert record["repo_id"] == entry["repo_id"]
    assert record["filename"] == entry["filename"]
    assert record["sha256"] == entry["sha256"]
    assert record["license_ref"] == license_ref
    assert record["canonical_url"] == url
    assert record["accepted_by"] == "consenting-user"
    assert record["scope"] == "system"
    assert record["license_source"] == f"the shipped descriptor {pins} (package-verified)"
    assert "signature-verified" not in json.dumps(record)
    assert hashlib.sha256((store / entry["filename"]).read_bytes()).hexdigest() == entry["sha256"]


def test_rerun_replaces_inaccurate_record(provision_case):
    entry, _, _, legal, run = provision_case
    legal.mkdir()
    record_path = legal / f"{entry['filename']}-accepted.json"
    record_path.write_text(json.dumps({"license_ref": "unknown", "canonical_url": "wrong"}))
    ok, message = run()
    assert ok, message
    record = json.loads(record_path.read_text())
    assert record["license_ref"] == "Apache-2.0"
    assert record["canonical_url"] == APACHE_URL


@pytest.mark.parametrize("license_ref", [None, "", "unknown", "LicenseRef-Unmapped"])
def test_missing_or_unmapped_license_refuses_before_writes(provision_case, license_ref):
    entry, _, store, legal, run = provision_case
    if license_ref is None:
        del entry["license_ref"]
    else:
        entry["license_ref"] = license_ref
    ok, message = run()
    assert not ok
    assert entry["filename"] in message
    assert "license" in message.lower()
    assert not store.exists()
    assert not legal.exists()


def test_catalog_license_cannot_replace_missing_descriptor(provision_case, monkeypatch):
    entry, _, store, legal, run = provision_case
    model = next(m for m in MODEL_CATALOG.values() if m.filename == entry["filename"])
    monkeypatch.setattr(model, "license_ref", "Apache-2.0")
    ok, message = run([])
    assert not ok
    assert entry["filename"] in message
    assert not store.exists()
    assert not legal.exists()


def test_mismatched_sha_refuses_before_writes(provision_case):
    entry, _, store, legal, run = provision_case
    entry["sha256"] = "f" * 64
    ok, message = run()
    assert not ok
    assert "mismatch" in message.lower()
    assert not store.exists()
    assert not legal.exists()


def test_projector_pin_does_not_replace_primary_descriptor(provision_case):
    entry, _, store, legal, run = provision_case
    other = dict(entry, filename="other.gguf", mmproj_filename=entry["filename"],
                 mmproj_sha256=entry["sha256"])
    ok, message = run([other])
    assert not ok
    assert entry["filename"] in message
    assert "descriptor" in message.lower()
    assert not store.exists()
    assert not legal.exists()


def test_conflicting_projector_pin_cannot_replace_primary_sha(provision_case):
    entry, _, store, legal, run = provision_case
    other = dict(entry, filename="other.gguf", mmproj_filename=entry["filename"],
                 mmproj_sha256=entry["sha256"])
    entry["sha256"] = "f" * 64
    ok, message = run([entry, other])
    assert not ok
    assert "descriptor" in message.lower()
    assert not store.exists()
    assert not legal.exists()


def test_record_uses_the_snapshot_that_validated_the_artifact(provision_case, monkeypatch):
    entry, pins, _, legal, run = provision_case
    install = dispatch._install_staged

    def replace_descriptor_then_install(*args):
        changed = dict(entry, license_ref="LicenseRef-Tongyi-Qianwen")
        pins.write_text(json.dumps({"entries": [changed]}))
        return install(*args)

    monkeypatch.setattr(dispatch, "_install_staged", replace_descriptor_then_install)
    ok, message = run()
    assert ok, message
    record = json.loads((legal / f"{entry['filename']}-accepted.json").read_text())
    assert record["license_ref"] == "Apache-2.0"
    assert record["canonical_url"] == APACHE_URL
