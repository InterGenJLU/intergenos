#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A21 regression: gpg_verify=false is refused (verification is mandatory).

_load_repos parsed `gpg_verify` into the repo cfg, but no code path ever
consulted it — repo-index signature verification is unconditionally on. So a
user who set `gpg_verify = false` got verification anyway, with no warning: a
security knob that silently does nothing, letting the user believe they
disabled a protection that is in fact still active.

Fixed (fail-closed, security-first): `gpg_verify = false` is REFUSED at config
load with a clear message; `true` (or omitted) is accepted.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import pkm.repo as repo_mod
from pkm.repo import RepoManager

_CONF = """[intergenos-current]
url = https://repo.intergenos.org/x86_64/current/
enabled = true
%s
"""


def _load_with_config(line):
    mgr = RepoManager.__new__(RepoManager)
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "repos.conf"
        cfg.write_text(_CONF % line)
        with patch.object(repo_mod, "REPO_CONFIG_PATH", cfg):
            return mgr._load_repos()


class GpgVerifyTest(unittest.TestCase):

    def test_gpg_verify_false_is_refused(self):
        buf = io.StringIO()
        with redirect_stderr(buf), self.assertRaises(ValueError) as ctx:
            _load_with_config("gpg_verify = false")
        self.assertIn("mandatory", str(ctx.exception).lower())
        err = buf.getvalue()
        self.assertIn("gpg_verify = false", err)
        self.assertIn("cannot be disabled", err)

    def test_gpg_verify_true_accepted(self):
        repos = _load_with_config("gpg_verify = true")
        self.assertIn("intergenos-current", repos)
        self.assertTrue(repos["intergenos-current"]["gpg_verify"])

    def test_gpg_verify_absent_loads_fine(self):
        repos = _load_with_config("")
        self.assertIn("intergenos-current", repos)
        # absent -> not in cfg, but verification is on regardless.
        self.assertNotIn("gpg_verify", repos["intergenos-current"])


if __name__ == "__main__":
    unittest.main()
