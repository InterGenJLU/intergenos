"""Record and verify the chat, vision-projector and embedding artifacts."""

import hashlib
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from intergen.dbus_daemon import InterGenDaemon
from intergen.model_manager import EMBEDDING_MODEL, MODEL_CATALOG, ModelManager
from intergen.model_setup_dispatch import provision


@pytest.fixture
def models(tmp_path):
    chat = next(m for m in MODEL_CATALOG.values() if m.has_vision)
    names = [chat.filename, "paired-projector.gguf", EMBEDDING_MODEL.filename]
    stage = tmp_path / "stage"
    stage.mkdir()
    pins = {}
    for name in names:
        data = ("verified " + name).encode()
        (stage / name).write_bytes(data)
        pins[name] = hashlib.sha256(data).hexdigest()
    entries = [dict(name=n, filename=n, sha256=pins[n], license_ref="Apache-2.0")
               for n in [names[0], names[2]]]
    entries[0].update(has_vision=True, mmproj_filename=names[1],
                      mmproj_sha256=pins[names[1]])
    pinfile = tmp_path / "pins.json"
    pinfile.write_text(json.dumps({"entries": entries}))
    store = tmp_path / "store"
    record = tmp_path / "manifest.json"
    return SimpleNamespace(names=names, stage=stage, pins=pins, pinfile=pinfile,
                           store=store, record=record, root=tmp_path)


def install(m):
    for name in [m.names[0], m.names[2]]:
        args = dict(filename=name, staging_path=str(m.stage / name))
        if name == m.names[0]:
            args.update(mmproj_filename=m.names[1],
                        mmproj_staging_path=str(m.stage / m.names[1]))
        ok, message = provision(args, pins_path=m.pinfile, model_dir=m.store,
                                manifest_path=m.record,
                                system_legal_dir=m.root / "legal")
        assert ok, message
    return ModelManager(m.store, m.record, m.pinfile)


def test_installer_records_every_artifact(models):
    mm = install(models)
    record = json.loads(models.record.read_text())
    assert set(record) == set(models.names)
    for name in models.names:
        assert record[name]["sha256"] == models.pins[name]
        assert record[name]["local_path"] == str(models.store / name)
    assert [m.filename for m in mm.list_downloaded()] == [models.names[0]]


@pytest.mark.parametrize("damaged", [0, 1])
def test_chat_start_verification_covers_the_projector(models, damaged):
    mm = install(models)
    assert mm.verify_arbitrary_path(models.store / models.names[0])
    (models.store / models.names[damaged]).write_bytes(b"changed artifact")
    assert not mm.verify_arbitrary_path(models.store / models.names[0])


@pytest.mark.parametrize("override", [False, True])
def test_daemon_refuses_changed_embedding_bytes(models, override):
    mm = install(models)
    daemon = object.__new__(InterGenDaemon)
    daemon._mm = mm
    path = models.store / models.names[2]
    env = {"INTERGEN_EMBED_MODEL_PATH": str(path)} if override else {}
    with mock.patch.dict("os.environ", env, clear=True):
        assert daemon._resolve_embed_model_path() == str(path)
        path.write_bytes(b"changed embedding")
        assert daemon._resolve_embed_model_path() == ""


def test_manifest_write_failure_is_not_success(models):
    models.record.mkdir()
    ok, message = provision(
        dict(filename=models.names[2],
             staging_path=str(models.stage / models.names[2])),
        pins_path=models.pinfile, model_dir=models.store,
        manifest_path=models.record, system_legal_dir=models.root / "legal",
    )
    assert not ok
    assert "files installed" in message
    assert "manifest" in message
