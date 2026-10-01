"""Signing, loading and interrupted restoration preserve literal path bytes."""

import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATHS = ["ordinary", "directory with spaces", "directory'quoted"]


def inline_block(script, imported_name):
    lines = (ROOT / "scripts" / script).read_text().splitlines()
    position = lines.index("from pkm.repo import " + imported_name)
    start = max(i for i in range(position) if lines[i].lstrip().startswith("python3 "))
    end = next(i for i in range(position, len(lines))
               if lines[i].startswith('"') or lines[i] in ("PYSIGN", "PYLOAD"))
    return "\n".join(lines[start:end + 1]) + "\n"


def write_index(path):
    data = {"version": 1, "arch": "x86_64", "package_count": 1, "packages": {
        "demo": {"version": "2.0", "release": 1, "filename": "demo-2.0-1.igos.tar.gz",
                 "sha256": "0" * 64, "size": 7}}}
    with gzip.open(path, "wt") as handle:
        json.dump(data, handle)


@pytest.fixture(scope="module")
def signing_key(tmp_path_factory):
    home = tmp_path_factory.mktemp("handoff-key")
    home.chmod(0o700)
    env = os.environ | {"GNUPGHOME": str(home)}
    try:
        created = subprocess.run(
            ["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
             "--quick-gen-key", "test-publish-paths", "default", "default", "never"],
            env=env, capture_output=True, text=True,
        )
        assert created.returncode == 0, created.stderr
        keys = subprocess.check_output(["gpg", "--with-colons", "--list-secret-keys"],
                                       env=env, text=True)
        fingerprint = next(line.split(":")[9] for line in keys.splitlines()
                           if line.startswith("fpr:"))
        yield env, fingerprint
    finally:
        subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "all"], check=True)


@pytest.mark.parametrize("directory", PATHS)
def test_signing_block_signs_literal_path(tmp_path, signing_key, directory):
    parent = tmp_path / directory
    parent.mkdir()
    index = parent / "InterGenOS.db"
    write_index(index)
    env, fingerprint = signing_key
    env = env | {"INDEX_PATH": str(index), "GPG_FP": fingerprint, "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        ["bash", "-e", "-c", inline_block("publish-repo.sh", "sign_index")],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    signature = Path(str(index) + ".sig")
    verified = subprocess.run(["gpg", "--verify", str(signature), str(index)],
                              env=env, capture_output=True, text=True)
    assert verified.returncode == 0, verified.stderr
    assert "Good signature" in verified.stderr


@pytest.mark.parametrize("directory", PATHS)
def test_final_loader_reads_literal_paths(tmp_path, directory):
    parent = tmp_path / directory
    parent.mkdir()
    index = parent / "InterGenOS.db"
    write_index(index)
    project = parent / "project"
    project.symlink_to(ROOT, target_is_directory=True)
    env = os.environ | {"PROJECT_ROOT": str(project), "INDEX_OUTPUT": str(index),
                        "ARCHIVE_COUNT": "1", "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        ["bash", "-e", "-c", inline_block("dry-run-first-publish.sh", "RepoIndex")],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "demo: sha256=" in result.stdout
    assert "filename=demo-2.0-1.igos.tar.gz" in result.stdout
    assert result.stdout.endswith("OK\n")


@pytest.mark.parametrize("directory", PATHS)
def test_interrupted_preview_restores_saved_index(tmp_path, directory):
    parent = tmp_path / directory
    parent.mkdir()
    index = parent / "InterGenOS.db"
    saved = parent / "saved-index.db"
    write_index(index)
    original = index.read_bytes()
    saved.write_bytes(original)
    lines = (ROOT / "scripts/publish-repo.sh").read_text().splitlines()
    trap, = [line.strip() for line in lines
             if line.strip().startswith("trap ") and "_DR_SAVED_INDEX" in line]
    env = os.environ | {"_DR_SAVED_INDEX": str(saved), "INDEX_PATH": str(index)}
    result = subprocess.run(
        ["bash", "-c", "set -e\n" + trap + '\nprintf replaced > "$INDEX_PATH"\nkill -TERM "$$"\n'],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0, "the controlled interrupt did not stop the preview"
    assert hashlib.sha256(index.read_bytes()).digest() == hashlib.sha256(original).digest(), result.stderr
    assert not saved.exists(), "successful restoration left its temporary saved index"
