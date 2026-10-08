"""Publish callers retain path bytes and report the index they actually wrote."""

import gzip
import hashlib
import importlib.util
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


@pytest.fixture
def publisher():
    spec = importlib.util.spec_from_file_location(
        "source_publish_fixture", ROOT / "tests/preflight/test_publish_sign_hold.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.PublishSignHoldTest("test_a_new_index_requires_both_document_truth_inputs")
    fixture.setUp()
    fixture.sources = fixture.root / "source archives'quoted"
    fixture.sources.mkdir()
    (fixture.sources / "demo-2.0-1.igos.src.tar.gz").write_bytes(b"fixture source\n")
    fixture._write_archive("missing", "1.0", 1)
    fixture.manifest.write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in sorted(fixture.archives.glob("*.igos.tar.gz"))))
    environment = fixture._environment
    fixture._environment = lambda: environment() | {"PUBLISH_SOURCES_DIR": str(fixture.sources)}
    try:
        yield fixture
    finally:
        fixture.tearDown()


def test_publish_reads_back_counts_and_names_before_signing(publisher):
    result = subprocess.run(publisher._command() + ["--dry-run"], cwd=ROOT,
                            env=publisher._environment(), capture_output=True, text=True,
                            timeout=20)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Source index read-back: 1 with source_sha256; 1 without" in output
    assert "Packages without source_sha256: missing" in output
    assert "Source upload skipped; index source coverage is still required." in output
    assert "=== DRY RUN" in output
    assert "SIGNING HOLD" not in output
    assert not (publisher.archives / "InterGenOS.db.sig").exists()


@pytest.mark.parametrize("mode", ["absent", "zero"])
def test_missing_source_coverage_stops_before_signing_hold(publisher, mode):
    if mode == "absent":
        publisher.sources = publisher.root / "absent sources"
    else:
        (publisher.sources / "demo-2.0-1.igos.src.tar.gz").unlink()
    code, output = publisher._run_and_answer("no")
    assert code == 1, output
    assert "Source index read-back: 0 with source_sha256; 2 without" in output
    assert "Packages without source_sha256: demo, missing" in output
    reason = "sources directory is missing" if mode == "absent" else "no indexed package carries source_sha256"
    assert reason in output
    assert "SIGNING HOLD" not in output
    assert "Checking GPG key availability" not in output
    assert not (publisher.archives / "InterGenOS.db.sig").exists()


def test_resumed_publish_reads_existing_index_and_refuses_zero(publisher):
    publisher._write_index(publisher.archives / "InterGenOS.db", "demo", "2.0", 1)
    (publisher.archives / "InterGenOS.db.sig").write_text("fixture signature\n")
    gpg = publisher.fake_bin / "gpg"
    gpg.write_text('#!/bin/sh\n[ "$1" = "--verify" ]\n')
    gpg.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/publish-repo.sh"), "--archive-dir", str(publisher.archives),
         "--skip-sources", "--skip-sign", "--dry-run"], cwd=ROOT,
        env=publisher._environment(), capture_output=True, text=True, timeout=20,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "Source index read-back: 0 with source_sha256; 1 without" in output
    assert "no indexed package carries source_sha256" in output
    assert "=== DRY RUN" not in output


def run_rehearsal(directory, arguments, sources_env=None):
    directory.mkdir()
    environment = os.environ.copy()
    environment.pop("PUBLISH_SOURCES_DIR", None)
    if sources_env is not None:
        environment["PUBLISH_SOURCES_DIR"] = str(sources_env)
    keyring = directory / "dry-run-output/gpg-home"
    try:
        return subprocess.run(
            ["bash", str(ROOT / "scripts/dry-run-first-publish.sh"), *arguments],
            cwd=directory, env=environment, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=90,
        )
    finally:
        if keyring.exists():
            subprocess.run(["gpgconf", "--homedir", str(keyring), "--kill", "all"],
                           check=True, capture_output=True)


@pytest.mark.parametrize("mode", ["absent", "zero"])
@pytest.mark.parametrize("synthetic", [False, True])
@pytest.mark.parametrize("source_option", [False, True])
def test_rehearsal_refuses_uncovered_sources_before_signing(tmp_path, mode, synthetic, source_option):
    sources = tmp_path / "sources' directory"
    if mode == "zero":
        sources.mkdir()
    if synthetic:
        arguments = ["--synthetic"]
    else:
        binaries = tmp_path / "binary archives"
        binaries.mkdir()
        write_binary(binaries / "demo-2.0-1.igos.tar.gz")
        arguments = ["--archives-dir", str(binaries)]
    directory = tmp_path / "rehearsal"
    if source_option:
        arguments += ["--sources-dir", str(sources)]
    result = run_rehearsal(directory, arguments, sources_env=None if source_option else sources)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    reason = "sources directory is missing" if mode == "absent" else "no indexed package carries source_sha256"
    assert reason in output
    assert "[4/6] Signing" not in output
    assert not (directory / "dry-run-output/InterGenOS.db.sig").exists()
    assert not sources.exists() if mode == "absent" else not list(sources.iterdir())


def test_synthetic_rehearsal_indexes_the_sources_it_creates(tmp_path):
    directory = tmp_path / "synthetic rehearsal"
    result = run_rehearsal(directory, ["--synthetic"])
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Total: 6 checks, 6 pass, 0 fail" in output
    assert "GPG signature verified" in output
    assert "pkm.repo.RepoIndex roundtrip parse OK" in output
    with gzip.open(directory / "dry-run-output/InterGenOS.db", "rt") as handle:
        packages = json.load(handle)["packages"]
    assert len(packages) == 2
    assert {name: (entry["version"], entry["release"]) for name, entry in packages.items()} == {
        "testpkg": ("1.0", 1), "testpkg2": ("2.0", 1),
    }
    for entry in packages.values():
        source = directory / "dry-run-output/synthetic-sources" / entry["source_filename"]
        assert entry["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        with tarfile.open(source, "r:gz") as archive:
            assert archive.getnames() == ["SOURCE.txt"]


def test_synthetic_rehearsal_preserves_exported_tmpdir(tmp_path, monkeypatch):
    scratch_parent = tmp_path / "temporary parent"
    scratch_parent.mkdir()
    sentinel = scratch_parent / "retain.txt"
    sentinel.write_text("inherited temporary directory\n")
    monkeypatch.setenv("TMPDIR", str(scratch_parent))
    directory = tmp_path / "exported tmpdir rehearsal"
    result = run_rehearsal(directory, ["--synthetic"])
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Total: 6 checks, 6 pass, 0 fail" in output
    assert "GPG signature verified" in output
    assert "pkm.repo.RepoIndex roundtrip parse OK" in output
    assert os.environ["TMPDIR"] == str(scratch_parent)
    assert sentinel.read_text() == "inherited temporary directory\n"
    assert list(scratch_parent.iterdir()) == [sentinel]
    with gzip.open(directory / "dry-run-output/InterGenOS.db", "rt") as stream:
        packages = json.load(stream)["packages"]
    assert len(packages) == 2
    for entry in packages.values():
        source = directory / "dry-run-output/synthetic-sources" / entry["source_filename"]
        assert entry["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
