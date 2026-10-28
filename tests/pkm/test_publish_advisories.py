# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The publisher writes curated advisories into the generated index."""

import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
ADVISORIES = Path("docs/governance/security-advisories.yml")


def index_step():
    lines = (ROOT / "scripts/publish-repo.sh").read_text().splitlines()
    position = lines.index("from pkm.repo import generate_index")
    start = max(i for i in range(position)
                if lines[i].lstrip().startswith("python3 "))
    end = lines.index("PYINDEX", position)
    return "\n".join(lines[start:end + 1]) + "\n"


@pytest.fixture
def publish_inputs(tmp_path):
    archives = tmp_path / "archives' with spaces"
    sources = tmp_path / "sources with spaces"
    archives.mkdir()
    sources.mkdir()
    body = b"pkgname = curl\npkgver = 8.23.0\npkgrel = 1\n"
    with tarfile.open(archives / "curl-8.23.0-1.igos.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo(".PKGINFO")
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))
    (sources / "curl-8.23.0-1.igos.src.tar.gz").write_bytes(b"source fixture\n")
    return archives, os.environ | {
        "ARCHIVE_DIR": str(archives), "SOURCES_DIR": str(sources),
        "PYTHONPATH": str(ROOT),
    }


def run_step(cwd, environment):
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", index_step()],
        cwd=cwd, env=environment, capture_output=True, text=True, timeout=30,
    )


def test_published_index_carries_curated_advisory(publish_inputs):
    archives, environment = publish_inputs
    result = run_step(ROOT, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    with gzip.open(archives / "InterGenOS.db", "rt") as handle:
        packages = json.load(handle)["packages"]
    advisory = yaml.safe_load((ROOT / ADVISORIES).read_text())["curl-8.23.0"]
    assert sum(entry.get("security") is True for entry in packages.values()) == 1
    assert packages["curl"]["cves"] == advisory["cves"]
    assert "source_sha256" in packages["curl"]


@pytest.mark.parametrize("failure", ["missing", "unreadable", "malformed"])
def test_unavailable_advisories_stop_index_generation(tmp_path, publish_inputs, failure):
    archives, environment = publish_inputs
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts/generate-repodb.py").symlink_to(ROOT / "scripts/generate-repodb.py")
    advisory = project / ADVISORIES
    advisory.parent.mkdir(parents=True)
    if failure == "unreadable":
        advisory.mkdir()
    elif failure == "malformed":
        advisory.write_text("curl-8.23.0: [unterminated\n")
    result = run_step(project, environment)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "security-advisories.yml" in result.stderr
    assert not (archives / "InterGenOS.db").exists()
