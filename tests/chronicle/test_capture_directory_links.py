"""Capture directory symlinks as links, without walking their targets."""

import os
import stat

import pytest

from chronicle import config, configstate, engine, manifest, paths, userdata


@pytest.mark.parametrize("layer", [paths.LAYER_CONFIG_STATE, paths.LAYER_USER_DATA])
def test_capture_preserves_directory_links_without_traversal(tmp_path, layer):
    source = tmp_path / "source"
    source.mkdir()
    real = source / "real"
    real.mkdir()
    (real / "document").write_text("captured through the real directory")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "external-document").write_text("not in the capture scope")
    targets = {
        "relative-directory": "real",
        "absolute-directory": str(outside),
        "parent-directory": "..",
        "broken-link": "missing",
        "file-link": "real/document",
    }
    for name, target in targets.items():
        (source / name).symlink_to(target)

    cfg = config.Config()
    cfg.user_data_paths = [str(source)]
    eng = engine.Engine(local_root=tmp_path / "local", config=cfg)
    target_root = tmp_path / "target"
    target_root.mkdir()
    eng.target_adopt(target_root, target_class="directory")
    version = eng.capture(layer, scope=[str(source)])["version_id"]
    captured = eng.get_manifest(layer, version)
    entries = {entry["path"]: entry for entry in captured["entries"]}

    assert entries[str(real)]["type"] == manifest.T_DIR
    assert entries[str(real / "document")]["type"] == manifest.T_FILE
    assert len(entries) == len(captured["entries"])
    for name, target in targets.items():
        link = source / name
        entry = entries[str(link)]
        metadata = link.lstat()
        assert entry["type"] == manifest.T_SYMLINK
        assert entry["target"] == target
        assert entry["mode"] == stat.S_IMODE(metadata.st_mode)
        assert entry["uid"] == metadata.st_uid
        assert entry["gid"] == metadata.st_gid
        assert entry["mtime"] == int(metadata.st_mtime)
        assert not any(p.startswith(str(link) + "/") for p in entries)
        if layer == paths.LAYER_USER_DATA:
            stored = userdata.read_file(eng.target_root(), version, entry)
            assert stored.is_symlink()
            assert os.readlink(stored) == target

    assert not any(p.startswith(str(outside)) for p in entries)
    assert eng.verify(layer, version)["ok"]


@pytest.mark.parametrize("layer", [paths.LAYER_CONFIG_STATE, paths.LAYER_USER_DATA])
def test_capture_respects_excluded_directory_links(tmp_path, layer):
    source = tmp_path / "source"
    source.mkdir()
    real = source / "real"
    real.mkdir()
    (real / "document").write_text("keep")
    ignored = source / "ignored"
    ignored.symlink_to("real")
    cfg = config.Config()
    eng = engine.Engine(local_root=tmp_path / "local", config=cfg)

    if layer == paths.LAYER_CONFIG_STATE:
        root = eng.local_root
        version = configstate.capture(
            [str(source)], root, eng.local_store, 1, 1_000_000, "excluded link",
            excludes=[str(ignored)],
        )
    else:
        root = tmp_path / "target"
        version = userdata.capture(
            [str(source)], root, None, 1, 1_000_000, "excluded link",
            is_excluded=lambda path: path.rstrip("/") == str(ignored),
        )
        stored = userdata.userdata_tree(root, version) / str(ignored).lstrip("/")
        assert not os.path.lexists(stored)

    captured = manifest.find_version(root, layer, version)
    entries = {entry["path"]: entry for entry in captured["entries"]}
    assert str(ignored) not in entries
    assert entries[str(real / "document")]["type"] == manifest.T_FILE
