# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The mirror-index signing step waits for an explicit, one-use approval.

These cases run ``scripts/publish-repo.sh`` itself against a local fixture.
Only ``ssh`` is stood in: it exposes a local directory as the remote snapshot
and a small gzipped index as the served index.  The package archive, corpus
gate, index generator, document gate, and GPG secret-key check are real.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISH = REPO_ROOT / "scripts" / "publish-repo.sh"
FINGERPRINT = "D7AA641D81ACD690C5AD865E7276E14DD8886BFE"


class PublishSignHoldTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="publish-sign-hold-")
        self.root = Path(self._tmp.name)
        self.archives = self.root / "archives"
        self.archives.mkdir()
        self.tree = self.root / "tree"
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.remote = self.root / "remote" / "current"
        self.remote.mkdir(parents=True)
        self.gnupg = self.root / "gnupg"
        self.gnupg.mkdir(mode=0o700)
        self.approval = self.root / "sign.approval"

        self._write_archive("demo", "2.0", 1)
        archive = next(self.archives.glob("*.igos.tar.gz"))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.manifest = self.root / "chroot.sha256"
        self.manifest.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

        self.live_index = self.remote / "InterGenOS.db"
        self._write_index(self.live_index, "demo", "1.0", 1)
        self._write_release_fixture(digest)
        self._write_fake_ssh()

    def tearDown(self):
        self._tmp.cleanup()

    def _write_archive(self, name: str, version: str, release: int) -> None:
        path = self.archives / f"{name}-{version}.igos.tar.gz"
        data = (f"pkgname = {name}\n"
                f"pkgver = {version}\n"
                f"pkgrel = {release}\n"
                "pkgdesc = fixture package\n"
                "tier = extra\n"
                "license = MIT\n").encode()
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(data)
        with tarfile.open(path, "w:gz") as archive:
            archive.addfile(info, BytesIO(data))

    @staticmethod
    def _write_index(path: Path, name: str, version: str, release: int) -> None:
        doc = {
            "version": 1,
            "arch": "x86_64",
            "package_count": 1,
            "packages": {
                name: {
                    "version": version,
                    "release": release,
                    "sha256": "0" * 64,
                    "filename": f"{name}-{version}.igos.tar.gz",
                }
            },
        }
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(doc, handle)

    def _write_release_fixture(self, digest: str) -> None:
        scripts = self.tree / "scripts"
        scripts.mkdir(parents=True)
        validator = scripts / "check-release-validation.py"
        validator.write_text("import sys\nprint('fixture release validation: PASS')\nsys.exit(0)\n")

        intergen = self.tree / "packages" / "ai" / "intergen"
        intergen.mkdir(parents=True)
        (intergen / "package.yml").write_text(
            "name: intergen\nversion: 1.0\nrelease: 1\ncontent_hash: 0123456789abcdef\n")

        etc = (self.tree / "packages" / "core" / "intergenos-base-files" /
               "files" / "etc")
        etc.mkdir(parents=True)
        (etc / "igos-release").write_text("R001.2\n")
        (etc / "os-release").write_text(
            'NAME="InterGenOS"\nVERSION="R001.2 (Revival)"\nID=intergenos\n'
            'VERSION_ID=r001.2\nVERSION_CODENAME=revival\n'
            'PRETTY_NAME="InterGenOS R001.2 (Revival)"\n')
        (etc / "lsb-release").write_text(
            'DISTRIB_ID="InterGenOS"\nDISTRIB_RELEASE="R001.2"\n'
            'DISTRIB_CODENAME="revival"\n'
            'DISTRIB_DESCRIPTION="InterGenOS R001.2 (Revival)"\n')
        (etc / "issue").write_text(
            "\n  InterGenOS R001.2 (Revival)\n  Kernel \\r on \\m (\\l)\n")

        (self.tree / "README.md").write_text(
            "# Fixture\n\n"
            "> ### Download InterGenOS R001.2 "
            "(https://example.invalid/iso/intergenos-r001.2.iso)\n"
            f"> x86_64 live image · sha256 `{digest}` — "
            "[checksum](https://example.invalid/iso/intergenos-r001.2.iso.sha256) "
            "[signature](https://example.invalid/iso/intergenos-r001.2.iso.sha256.asc)\n\n"
            "## Upcoming\n\n- **Future tool** — planned.\n")
        (self.tree / "SECURITY.md").write_text(
            "# Security\n\n> **Project status:** the current release is the newest "
            "point release of the **R001.x** line, always stated at "
            "https://intergenos.org/news.html.\n")
        docs = self.tree / "docs"
        docs.mkdir()
        (docs / "release-policy.md").write_text(
            "# Policy\n\n## Release types\n\n"
            "- **Major releases — `R001`, `R002`, …** rebuild everything.\n"
            "- **Point releases — `R001.1`, `R001.2`, …** rebuild the delta.\n")
        (self.tree / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [Unreleased]\n\nNothing yet.\n\n"
            "## [R001.2] — 2026-09-03\n\n### Added\n- The fixture release.\n")
        self.wiki = self.root / "switching.md"
        self.wiki.write_text(
            "# Switching\n\n```bash\nsudo pkm install demo\n```\n")
        self.iso_sum = self.root / "intergenos-r001.2.iso.sha256"
        self.iso_sum.write_text(
            f"{digest}  intergenos-r001.2.iso\n", encoding="utf-8")

    def _write_fake_ssh(self) -> None:
        fake = self.fake_bin / "ssh"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "if args and args[-1] == 'true':\n"
            "    raise SystemExit(0)\n"
            "payload = sys.stdin.read()\n"
            "if 'df -Pk' in payload:\n"
            "    print('DF\\t104857600\\t94371840')\n"
            "elif 'readlink -f' in payload:\n"
            "    print(os.environ['FAKE_PUBLISH_REMOTE_CURRENT'])\n"
            "elif any('InterGenOS.db' in arg and 'cat' in arg for arg in args):\n"
            "    with open(os.environ['FAKE_PUBLISH_LIVE_INDEX'], 'rb') as handle:\n"
            "        sys.stdout.buffer.write(handle.read())\n"
            "raise SystemExit(0)\n")
        fake.chmod(0o755)

    def _command(self) -> list[str]:
        return [
            "/usr/bin/bash", str(PUBLISH),
            "--archive-dir", str(self.archives),
            "--skip-sources",
            "--chroot-manifest", str(self.manifest),
            "--iso-sha256", str(self.iso_sum),
            "--wiki-switching-page", str(self.wiki),
            "--sign-approval-file", str(self.approval),
            "--sign-hold-timeout", "10",
        ]

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "PATH": f"{self.fake_bin}:{env['PATH']}",
            "PYTHONUNBUFFERED": "1",
            "GNUPGHOME": str(self.gnupg),
            "IGOS_REPO_ROOT": str(self.tree),
            "RELEASE_VALIDATION_RECORD": str(self.root / "release-record"),
            "PUBLISH_REMOTE_HOST": "fixture.invalid",
            "PUBLISH_REMOTE_PATH": "/fixture/x86_64",
            "PUBLISH_MIN_FREE_PCT": "1",
            "FAKE_PUBLISH_REMOTE_CURRENT": str(self.remote),
            "FAKE_PUBLISH_LIVE_INDEX": str(self.live_index),
        })
        return env

    def _run_and_answer(self, word: str) -> tuple[int, str]:
        process = subprocess.Popen(
            self._command(), cwd=REPO_ROOT, env=self._environment(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, bufsize=1,
        )
        lines: list[str] = []
        deadline = time.monotonic() + 20
        answered = False
        assert process.stdout is not None
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if line:
                lines.append(line)
                if "Approval command:" in line and not answered:
                    self.approval.write_text(f"{word}\n", encoding="utf-8")
                    self.approval.chmod(0o600)
                    answered = True
            elif process.poll() is not None:
                break
        if process.poll() is None:
            process.kill()
            self.fail("publish fixture did not finish inside 20 seconds\n" + "".join(lines))
        lines.append(process.stdout.read())
        process.stdout.close()
        return process.returncode, "".join(lines)

    def test_any_word_other_than_sign_aborts_and_keeps_staging(self):
        rc, out = self._run_and_answer("no")
        self.assertEqual(rc, 3, out)
        self.assertIn("SIGNING HOLD", out)
        self.assertIn(str(self.archives / "InterGenOS.db"), out)
        self.assertIn("Archive rows indexed: 1", out)
        self.assertIn("demo: 1.0-1 -> 2.0-1", out)
        self.assertIn(FINGERPRINT, out)
        self.assertIn("exactly ONE PIN and ONE touch", out)
        self.assertIn("Signing was not approved", out)
        self.assertIn("Resume command:", out)
        self.assertTrue(next(self.archives.glob("*.igos.tar.gz")).is_file())
        self.assertTrue((self.archives / "InterGenOS.db").is_file())
        self.assertFalse(self.approval.exists(), "the one-use response is consumed")
        self.assertNotIn("GPG key available", out)

    def test_sign_reaches_the_real_key_check_and_fails_without_a_key(self):
        rc, out = self._run_and_answer("sign")
        self.assertEqual(rc, 1, out)
        self.assertIn("Signing approval received", out)
        self.assertIn(f"GPG key NK1 ({FINGERPRINT}) not available", out)
        self.assertIn("Resume command:", out)
        self.assertFalse((self.archives / "InterGenOS.db.sig").exists())
        self.assertFalse(self.approval.exists(), "the one-use response is consumed")

    def test_skip_sign_reuses_the_signature_without_entering_the_hold(self):
        shutil.copy2(self.live_index, self.archives / "InterGenOS.db")
        (self.archives / "InterGenOS.db.sig").write_text("fixture signature\n")
        gpg_log = self.root / "gpg.log"
        fake_gpg = self.fake_bin / "gpg"
        fake_gpg.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "with open(os.environ['FAKE_GPG_LOG'], 'a', encoding='utf-8') as handle:\n"
            "    handle.write(' '.join(sys.argv[1:]) + '\\n')\n"
            "raise SystemExit(0)\n")
        fake_gpg.chmod(0o755)
        env = self._environment()
        env["FAKE_GPG_LOG"] = str(gpg_log)
        result = subprocess.run(
            [
                "/usr/bin/bash", str(PUBLISH),
                "--archive-dir", str(self.archives),
                "--skip-sources", "--skip-sign", "--dry-run",
            ],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("--skip-sign: reusing existing signed index", out)
        self.assertNotIn("SIGNING HOLD", out)
        self.assertNotIn("Checking GPG key availability", out)
        self.assertIn("--verify", gpg_log.read_text())
        self.assertNotIn("--list-secret-keys", gpg_log.read_text())

    def test_a_new_index_requires_both_document_truth_inputs(self):
        command = self._command()
        for option in ("--iso-sha256", "--wiki-switching-page"):
            position = command.index(option)
            del command[position:position + 2]
        result = subprocess.run(
            command, cwd=REPO_ROOT, env=self._environment(),
            capture_output=True, text=True,
        )
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn(
            "--iso-sha256 and --wiki-switching-page are required", out)
        self.assertNotIn("Checking SSH connectivity", out)


if __name__ == "__main__":
    unittest.main()
