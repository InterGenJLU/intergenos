"""Publish callers retain path bytes and report the index they actually wrote."""

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def write_binary(path, name="demo", version="2.0", release=1):
    body = f"pkgname = {name}\npkgver = {version}\npkgrel = {release}\n".encode()
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(".PKGINFO")
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))


def generation_block(script):
    lines = (ROOT / "scripts" / script).read_text().splitlines()
    position, = [i for i, line in enumerate(lines)
                 if line == "from pkm.repo import generate_index"]
    start = max(i for i in range(position) if lines[i].lstrip().startswith("python3 "))
    end = next(i for i in range(position, len(lines))
               if lines[i].startswith('"') or lines[i] == "PYINDEX")
    return "\n".join(lines[start:end + 1]) + "\n"


@pytest.mark.parametrize("script", ["publish-repo.sh", "dry-run-first-publish.sh"])
@pytest.mark.parametrize("directory", ["archives with spaces", "archives'quoted"])
def test_generation_passes_all_paths_as_data(tmp_path, script, directory):
    binaries = tmp_path / directory
    sources = tmp_path / "build" / directory
    binaries.mkdir()
    sources.mkdir(parents=True)
    write_binary(binaries / "demo-2.0-1.igos.tar.gz")
    write_binary(binaries / "missing-1.0-1.igos.tar.gz", "missing", "1.0")
    source = sources / "demo-2.0-1.igos.src.tar.gz"
    source.write_bytes(b"corresponding source fixture\n")
    project = tmp_path / (directory + " project")
    project.symlink_to(ROOT, target_is_directory=True)
    output = binaries / "InterGenOS.db"
    env = os.environ | {
        "ARCHIVE_DIR": str(binaries), "ARCHIVES_DIR": str(binaries),
        "SOURCES_DIR": str(sources), "PUBLISH_SOURCES_DIR": str(sources),
        "INDEX_OUTPUT": str(output), "PROJECT_ROOT": str(project),
        "PYTHONPATH": str(ROOT),
    }
    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", generation_block(script)],
                            env=env, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    with gzip.open(output, "rt") as handle:
        entries = json.load(handle)["packages"]
    assert entries["demo"]["source_filename"] == source.name
    assert entries["demo"]["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert "source_sha256" not in entries["missing"]
    if script == "dry-run-first-publish.sh":
        assert "Source index read-back: 1 with source_sha256; 1 without" in result.stdout
        assert "Packages without source_sha256: missing" in result.stdout
