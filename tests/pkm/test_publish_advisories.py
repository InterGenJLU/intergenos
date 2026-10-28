# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The publisher writes curated advisories into the generated index."""

import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
ADVISORIES = Path("docs/governance/security-advisories.yml")
CALLERS = ["publish-repo.sh", "dry-run-first-publish.sh"]


def index_step(script):
    lines = (ROOT / "scripts" / script).read_text().splitlines()
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
    body = b"pkgname = demo\npkgver = 2.0\npkgrel = 1\n"
    with tarfile.open(archives / "demo-2.0-1.igos.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo(".PKGINFO")
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))
    (sources / "demo-2.0-1.igos.src.tar.gz").write_bytes(b"source fixture\n")
    project = tmp_path / "project' with spaces"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts/generate-repodb.py").symlink_to(ROOT / "scripts/generate-repodb.py")
    (project / "pkm").symlink_to(ROOT / "pkm", target_is_directory=True)
    advisory = project / ADVISORIES
    advisory.parent.mkdir(parents=True)
    advisory.write_text("demo-2.0:\n  cves: [CVE-2026-12345]\n")
    return project, archives, os.environ | {
        "ARCHIVE_DIR": str(archives), "SOURCES_DIR": str(sources),
        "ARCHIVES_DIR": str(archives), "INDEX_OUTPUT": str(archives / "InterGenOS.db"),
        "REPO_ROOT": str(project), "PROJECT_ROOT": str(project),
        "SCRIPT_PATH": str(ROOT / "scripts/publish-repo.sh"),
        "PYTHONPATH": str(ROOT),
    }


def run_step(script, cwd, environment):
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", index_step(script)],
        cwd=cwd, env=environment, capture_output=True, text=True, timeout=30,
    )


def assert_stamped(index):
    with gzip.open(index, "rt") as handle:
        packages = json.load(handle)["packages"]
    assert sum(entry.get("security") is True for entry in packages.values()) == 1
    assert packages["demo"]["cves"] == ["CVE-2026-12345"]
    assert "source_sha256" in packages["demo"]


@pytest.mark.parametrize("script", CALLERS)
def test_published_index_carries_fixture_advisory(publish_inputs, script):
    project, archives, environment = publish_inputs
    result = run_step(script, ROOT, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert_stamped(archives / "InterGenOS.db")


@pytest.mark.parametrize("script", CALLERS)
def test_index_step_uses_project_paths_from_an_unrelated_cwd(tmp_path, publish_inputs, script):
    project, archives, environment = publish_inputs
    unrelated = tmp_path / "unrelated working directory"
    unrelated.mkdir()
    result = run_step(script, unrelated, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert_stamped(archives / "InterGenOS.db")


def test_publisher_accepts_a_data_tree_without_code(publish_inputs):
    project, archives, environment = publish_inputs
    (project / "scripts/generate-repodb.py").unlink()
    (project / "scripts").rmdir()
    (project / "pkm").unlink()
    result = run_step("publish-repo.sh", ROOT, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert_stamped(archives / "InterGenOS.db")


@pytest.mark.parametrize("script", CALLERS)
@pytest.mark.parametrize("failure", ["missing", "unreadable", "malformed"])
def test_unavailable_advisories_stop_index_generation(publish_inputs, failure, script):
    project, archives, environment = publish_inputs
    advisory = project / ADVISORIES
    advisory.unlink()
    if failure == "unreadable":
        advisory.mkdir()
    elif failure == "malformed":
        advisory.write_text("demo-2.0: [unterminated\n")
    result = run_step(script, project, environment)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "security-advisories.yml" in result.stdout + result.stderr
    assert not (archives / "InterGenOS.db").exists()


@pytest.mark.parametrize("failure", [
    "missing", "directory", "malformed", "non_utf8", "list", "empty_list", "false",
    "cves_scalar", "cves_non_string", "entry_list", "entry_string",
])
def test_loader_reports_one_error_line_for_invalid_input(tmp_path, failure):
    advisory = tmp_path / "advisories.yml"
    if failure == "directory":
        advisory.mkdir()
    elif failure != "missing":
        advisory.write_bytes({
            "malformed": b"demo-2.0: [unterminated\n", "non_utf8": b"\xff",
            "list": b"- demo\n", "empty_list": b"[]\n", "false": b"false\n",
            "cves_scalar": b"demo-2.0:\n  cves: CVE-2026-12345\n",
            "cves_non_string": b"demo-2.0:\n  cves: [CVE-2026-12345, 12345]\n",
            "entry_list": b"demo-2.0: [CVE-2026-12345]\n",
            "entry_string": b"demo-2.0: CVE-2026-12345\n",
        }[failure])
    result = subprocess.run(
        [sys.executable, "-c",
         "import runpy,sys; runpy.run_path(sys.argv[1])['load_security_advisories']"
         "(sys.argv[2], required=True)",
         str(ROOT / "scripts/generate-repodb.py"), str(advisory)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    error, = result.stderr.splitlines()
    assert error.startswith("ERROR:") and str(advisory) in error
    assert "Traceback" not in result.stderr
    if failure in {"cves_scalar", "cves_non_string", "entry_list", "entry_string"}:
        assert error.startswith(f"ERROR: {advisory}: demo-2.0: ")


def test_loader_preserves_shipped_advisories(tmp_path):
    import yaml

    advisory = ROOT / ADVISORIES
    original = advisory.read_bytes()
    expected = yaml.safe_load(original)
    result = subprocess.run(
        [sys.executable, "-c",
         "import json,runpy,sys; print(json.dumps(runpy.run_path(sys.argv[1])"
         "['load_security_advisories'](sys.argv[2], required=True)))",
         str(ROOT / "scripts/generate-repodb.py"), str(advisory)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == expected
    count = sum(1 for entry in expected.values() if entry.get("cves"))
    assert result.stderr == f"Loaded {count} security advisory entries from {advisory}\n"
    assert advisory.read_bytes() == original
