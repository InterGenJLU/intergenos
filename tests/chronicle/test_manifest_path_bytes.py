# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every manifest reader preserves filename bytes in escaped and raw spellings."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from chronicle import api, cas, config, engine, escalate, manifest, paths, userdata


@pytest.mark.parametrize("layer", paths.LAYERS)
@pytest.mark.parametrize("name", [b"ordinary.bin", "café.bin".encode(), b"download\xffed.bin"])
@pytest.mark.parametrize("spelling", ["escaped", "raw"])
def test_manifest_readers_and_native_consumers_preserve_path_bytes(
        tmp_path, monkeypatch, layer, name, spelling):
    source = tmp_path / "home"
    source.mkdir()
    document = source / os.fsdecode(name)
    document.write_bytes(b"saved bytes\n")
    link = source / os.fsdecode(b"link-" + name)
    link.symlink_to(document.name)
    cfg = config.Config()
    cfg.user_data_paths = [str(source)]
    local = tmp_path / "local"
    backend = engine.Engine(local_root=local, config=cfg)
    target = tmp_path / "target"
    target.mkdir()
    backend.target_adopt(target, target_class="directory")
    scope = ({"paths": list(map(str, [source, document, link])), "packages": ["sample"]}
             if layer == "restore-point" else [str(source)])
    version = backend.capture(layer, scope=scope)["version_id"]
    root = backend._store_root_for(layer)
    saved = backend.get_manifest(layer, version)
    record = Path(saved.pop("_path"))
    if b"\xff" in name:
        assert b"\\udcff" in record.read_bytes()
    wire = json.dumps(saved, sort_keys=True, ensure_ascii=spelling == "escaped").encode(
        "utf-8", "surrogateescape")
    record.write_bytes(wire)
    if layer in paths.LOCAL_LAYERS:
        # The mirror is another input to scrub, cap accounting and retention.
        (paths.versions_dir(backend.target_root(), layer) / record.name).write_bytes(wire)

    assert manifest.load_manifest(record) == saved
    inventory, problems = manifest.inspect_versions(root, layer)
    assert problems == []
    assert [m["version_id"] for m in inventory] == [version]
    assert manifest.list_versions(root, layer) == inventory
    assert manifest.list_versions_complete(root, layer) == inventory
    assert manifest.find_version(root, layer, version) == inventory[0]
    entry = next(e for e in saved["entries"] if e["path"] == str(document))
    assert os.fsencode(entry["path"]) == os.fsencode(source) + b"/" + name
    assert manifest.referenced_shas(inventory) == {cas.sha256_bytes(b"saved bytes\n")}
    checker = (lambda e: None if cas.sha256_file(userdata.read_file(root, version, e))
               == e["sha256"] else "changed") if layer == "user-data" else None
    assert manifest.verify_version(root, saved, cas.ContentStore(root), checker) == (True, [])
    assert backend.verify(layer, version)["ok"]
    assert backend.list_versions(layer)[0]["version_id"] == version
    assert backend.get_manifest(layer, version)["entries"] == saved["entries"]
    assert backend.scrub()["clean"]
    backend._cap_inventory_preflight(root)
    backend._finalize_target_candidate(root, layer, version)
    assert not backend.diff(layer, version, str(document))["changed"]
    assert backend.restore_plan(layer, version, [str(document)])["actions"][0]["path"] == str(document)
    # CLI, GUI and socket requests all use this same dispatcher and engine.
    response = api.dispatch(backend, {"verb": "manifest", "args": {
        "layer": layer, "version_id": version}})
    assert response["ok"] and response["result"]["entries"] == saved["entries"]
    conf = tmp_path / "chronicle.conf"
    conf.write_text("# Isolated read commands.\n")
    cli = Path(__file__).resolve().parents[2] / "assets/intergenos-backup/chronicle-cli"
    result = subprocess.run([
        sys.executable, str(cli), "--local-root", str(local), "--config", str(conf),
        "--socket", str(tmp_path / "absent.sock"), "restore", layer, version,
        str(document), "--dry-run", "--json",
    ], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["actions"][0]["path"] == str(document)
    if b"\xff" in name:
        assert b"\\udcff" in result.stdout and b"\xff" not in result.stdout

    if layer in paths.LOCAL_LAYERS:
        mirror = tmp_path / "second-target"
        mirror.mkdir()
        backend.target_adopt(mirror, target_class="directory")
        backend._mirror_to_target(layer, version)
        assert manifest.find_version(backend.target_root(), layer, version)["root_hash"] == saved["root_hash"]
        assert backend.scrub()["clean"]

    # Reuse reads the prior manifest; these files are unchanged and share data.
    if layer == "user-data":
        successor = backend.capture(layer)["version_id"]
        old_file = userdata.read_file(root, version, entry)
        new_file = userdata.read_file(root, successor, entry)
        assert old_file.stat().st_ino == new_file.stat().st_ino
    document.write_bytes(b"changed\n")
    assert backend.diff(layer, version, str(document))["changed"]
    monkeypatch.setattr(escalate, "has_cap_chown", lambda: True)
    monkeypatch.setattr(escalate, "run_restore_via_unit",
                        lambda *a, **kw: pytest.fail("unexpected service escalation"))
    restored = backend.restore_apply(layer, version, [str(document), str(link)], mode="beside")
    assert all(r["ok"] for r in restored["results"]), restored
    assert Path(restored["results"][0]["written_to"]).read_bytes() == b"saved bytes\n"
    assert os.fsencode(os.readlink(restored["results"][1]["written_to"])) == name
    # Reclamation reads the complete inventory; it must retain all referenced data.
    backend._gc(root)
    assert backend.verify(layer, version)["ok"]
    backend.pin(version)
    backend.retention_apply(layer)
    assert backend.verify(layer, version)["ok"]
    backend.unpin(version)
    if layer in paths.LOCAL_LAYERS:
        backend._rollback_local_capture(layer, version)
    else:
        backend._prune_versions(root, layer, [manifest.find_version(root, layer, version)], reason="test")
        assert backend.verify(layer, successor)["ok"]
    assert manifest.find_version(root, layer, version) is None


def test_canonical_hash_uses_original_filename_bytes():
    entries = [{"path": os.fsdecode(b"/home/download\xffed.bin"), "type": "dir"}]
    expected = b'[{"path":"/home/download\xffed.bin","type":"dir"}]'
    assert manifest.canonical_bytes(entries) == expected
    assert manifest.compute_root_hash(entries) == hashlib.sha256(expected).hexdigest()
    other = [{"path": "/home/downloadÿed.bin", "type": "dir"}]
    assert manifest.compute_root_hash(other) != manifest.compute_root_hash(entries)


def test_valid_unicode_keeps_the_previous_canonical_hash():
    entries = [{"path": "/home/café", "type": "dir"}, {"path": "/home/a", "type": "dir"}]
    previous_bytes = json.dumps(sorted(entries, key=lambda e: e["path"]),
                                sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False).encode("utf-8")
    assert manifest.canonical_bytes(entries) == previous_bytes


def test_load_manifest_accepts_raw_filename_bytes_and_json_escapes(tmp_path):
    record = tmp_path / "manifest.json"
    expected = {"path": os.fsdecode(b"/home/download\xffed.bin")}
    for wire in [b'{"path":"/home/download\xffed.bin"}',
                 b'{"path":"/home/download\\udcffed.bin"}']:
        record.write_bytes(wire)
        assert manifest.load_manifest(record) == expected
