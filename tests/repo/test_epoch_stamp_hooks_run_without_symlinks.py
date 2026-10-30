"""The epoch-stamp hooks must run in a clone that cannot hold symlinks.

.githooks/post-checkout and .githooks/post-merge exist only to run
.githooks/post-commit, which stamps build/.recipe-epoch with the checked-out
commit's time (scripts/build-intergenos.sh reads the stamp when live git is
unavailable). A clone made where symbolic links cannot be created
(core.symlinks=false: Windows without the symlink privilege, and file systems
without links) checks a symlink out as a plain file holding the target's
name. Git then ignores that file as not executable, or fails to spawn it
("cannot spawn .githooks/post-merge"), so the stamp is never written and
`git checkout` and `git worktree add` exit non-zero on the spawn failure.

The cases build a repository from this tree's own .githooks entries (blob
and mode exactly as committed), clone it with core.symlinks=false, and drive
git's own hook runner through a checkout and a fast-forward merge.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WIRED = ("post-checkout", "post-merge")
MERGED_TIME = "1700000000"


def _git(cwd, *args, env, check=True, data=None):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          check=check, env=env, input=data)


def _text(result):
    return (result.stdout + result.stderr).decode("utf-8", "replace")


class TestEpochStampHooksRunWithoutSymlinks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="igos-epoch-hooks-"))
        empty = cls.tmp / "empty-gitconfig"
        empty.write_text("", encoding="utf-8")
        cls.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        cls.env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(empty),
            "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
        })
        cls.listing = _git(REPO, "ls-files", "-s", "--", ".githooks",
                           env=cls.env).stdout.decode("utf-8")

        cls.origin = cls.tmp / "origin"
        _git(cls.tmp, "init", "-q", "--initial-branch=trunk", str(cls.origin), env=cls.env)
        for line in cls.listing.splitlines():
            meta, path = line.split("\t", 1)
            mode, blob, _stage = meta.split()
            data = _git(REPO, "cat-file", "blob", blob, env=cls.env).stdout
            written = _git(cls.origin, "hash-object", "-w", "--no-filters", "--stdin",
                           env=cls.env, data=data).stdout.decode("ascii").strip()
            assert written == blob, (path, written, blob)
            _git(cls.origin, "update-index", "--add", "--cacheinfo",
                 f"{mode},{blob},{path}", env=cls.env)
        _git(cls.origin, "commit", "-q", "-m", "hooks", env=cls.env)

        cls.clone = cls.tmp / "clone"
        _git(cls.tmp, "clone", "-q", "-c", "core.symlinks=false",
             str(cls.origin), str(cls.clone), env=cls.env)
        _git(cls.clone, "config", "core.hooksPath", ".githooks", env=cls.env)
        cls.stamp = cls.clone / "build" / ".recipe-epoch"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _head_time(self):
        return _git(self.clone, "log", "-1", "--format=%ct",
                    env=self.env).stdout.decode("ascii").strip()

    def _clear_stamp(self):
        if self.stamp.exists():
            self.stamp.unlink()

    def test_wired_hooks_are_committed_as_executable_files(self):
        modes = {}
        for line in self.listing.splitlines():
            meta, path = line.split("\t", 1)
            modes[path] = meta.split()[0]
        for name in WIRED:
            self.assertEqual(
                modes.get(f".githooks/{name}"), "100755",
                f".githooks/{name} is committed as mode {modes.get(f'.githooks/{name}')}; "
                "a symlink (120000) is checked out as a plain file where links "
                "cannot be made, and git cannot run it")

    def test_checkout_runs_the_stamp(self):
        self._clear_stamp()
        result = _git(self.clone, "checkout", "-q", "-b", "side", env=self.env, check=False)
        said = _text(result)
        self.assertEqual(result.returncode, 0, said)
        self.assertNotIn("cannot spawn", said)
        self.assertTrue(self.stamp.is_file(), "post-checkout did not write the stamp: " + said)
        self.assertEqual(self.stamp.read_text(encoding="utf-8").strip(), self._head_time())

    def test_fast_forward_merge_runs_the_stamp(self):
        note = self.origin / "note.txt"
        note.write_text("merged\n", encoding="utf-8")
        _git(self.origin, "add", "note.txt", env=self.env)
        env = dict(self.env, GIT_COMMITTER_DATE=f"@{MERGED_TIME} +0000",
                   GIT_AUTHOR_DATE=f"@{MERGED_TIME} +0000")
        _git(self.origin, "commit", "-q", "-m", "merged", env=env)
        _git(self.clone, "checkout", "-q", "trunk", env=self.env, check=False)
        _git(self.clone, "fetch", "-q", "origin", env=self.env)
        self._clear_stamp()
        result = _git(self.clone, "merge", "-q", "--ff-only", "origin/trunk",
                      env=self.env, check=False)
        said = _text(result)
        self.assertEqual(result.returncode, 0, said)
        self.assertNotIn("cannot spawn", said)
        self.assertEqual(self._head_time(), MERGED_TIME)
        self.assertTrue(self.stamp.is_file(), "post-merge did not write the stamp: " + said)
        self.assertEqual(self.stamp.read_text(encoding="utf-8").strip(), MERGED_TIME)


if __name__ == "__main__":
    unittest.main()
