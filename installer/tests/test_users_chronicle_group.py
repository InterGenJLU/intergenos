# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The installer places the console account in the Chronicle access group.

The backup engine's socket is root:chronicle mode 0660, so an account outside
that group cannot open the backup application at all. The account this
installer creates is the machine's console user, so the group is granted at
account creation, next to wheel/audio/video/cdrom/input.

Every subprocess is mocked: these tests create no users and touch no system
databases. They pin the group set and the order of operations, which is what
actually broke installs before — useradd -G aborts the whole account creation
on a group that does not yet exist, so the group has to be ensured first.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.backend import users


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ChronicleGroupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-users-")
        etc = Path(self.tmp) / "etc"
        etc.mkdir(parents=True)
        # A target whose /etc/group holds only the groups a base system
        # already has, so every capability group looks absent.
        (etc / "group").write_text("root:x:0:\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_create_user(self, groups=None):
        calls = []

        def _fake_run(cmd, **kw):
            calls.append(list(cmd))
            return _Result()

        with patch.object(users.trace, "traced_run", side_effect=_fake_run), \
                patch.object(users, "_sha512crypt_hash", return_value="$6$x"):
            users.create_user(self.tmp, "alice", password="Correct.Horse.9",
                              groups=groups)
        return calls

    def test_chronicle_is_in_the_default_group_set(self):
        self.assertIn("chronicle", users.DEFAULT_USER_GROUPS)

    def test_the_default_set_still_carries_every_previous_group(self):
        # The group was ADDED; nothing was traded away for it.
        for grp in ("wheel", "audio", "video", "cdrom", "input"):
            self.assertIn(grp, users.DEFAULT_USER_GROUPS)

    def test_useradd_receives_the_group(self):
        calls = self._run_create_user()
        useradd = [c for c in calls if c and c[0] == "useradd"]
        self.assertEqual(len(useradd), 1, calls)
        self.assertIn("-G", useradd[0])
        granted = useradd[0][useradd[0].index("-G") + 1].split(",")
        self.assertIn("chronicle", granted)

    def test_the_group_is_created_before_useradd_runs(self):
        # useradd -G aborts the entire account creation on a missing group,
        # and the account is the only administrative login on the installed
        # system. Ordering is the whole safety of this.
        calls = self._run_create_user()
        groupadds = [i for i, c in enumerate(calls)
                     if c and c[0] == "groupadd" and c[-1] == "chronicle"]
        useradd_at = next(i for i, c in enumerate(calls)
                          if c and c[0] == "useradd")
        self.assertEqual(len(groupadds), 1, calls)
        self.assertLess(groupadds[0], useradd_at)

    def test_the_created_group_is_a_system_group(self):
        calls = self._run_create_user()
        groupadd = next(c for c in calls
                        if c and c[0] == "groupadd" and c[-1] == "chronicle")
        self.assertIn("--system", groupadd)

    def test_an_existing_group_is_not_recreated(self):
        (Path(self.tmp) / "etc" / "group").write_text(
            "root:x:0:\nchronicle:x:987:\n")
        calls = self._run_create_user()
        self.assertFalse(
            [c for c in calls if c and c[0] == "groupadd"
             and c[-1] == "chronicle"],
            "groupadd ran for a group that already exists")

    def test_an_explicit_list_without_the_group_is_not_silent(self):
        # The caller's list is honoured — it is not rewritten — but a user
        # who will not be able to open the backup application is named in the
        # install log rather than discovering it later.
        with self.assertLogs(users.log, level="WARNING") as caught:
            self._run_create_user(groups=["wheel"])
        self.assertTrue(any("chronicle" in m for m in caught.output),
                        caught.output)

    def test_an_explicit_list_is_used_verbatim(self):
        calls = self._run_create_user(groups=["wheel"])
        useradd = next(c for c in calls if c and c[0] == "useradd")
        granted = useradd[useradd.index("-G") + 1].split(",")
        self.assertEqual(granted, ["wheel"])


class LiveImageGroupTest(unittest.TestCase):
    """The live medium's user needs the same group for the same reason, and
    it is created by a shell script at image time rather than by this
    backend — read from the script, since running it needs a chroot."""

    def setUp(self):
        self.script = (Path(__file__).resolve().parents[2]
                       / "scripts" / "create-image.sh").read_text()

    def test_the_live_user_is_placed_in_the_group(self):
        self.assertIn('IMAGE_USER_GROUPS="wheel,video,audio,input,chronicle"',
                      self.script)
        self.assertIn('useradd -m -G "$IMAGE_USER_GROUPS"', self.script)

    def test_the_group_is_ensured_before_the_user_is_created(self):
        ensure = self.script.index("groupadd -r chronicle")
        create = self.script.index('useradd -m -G "$IMAGE_USER_GROUPS"')
        self.assertLess(ensure, create)

    def test_a_failure_to_create_the_group_stops_the_image(self):
        # systemd-sysusers does not run against the image until phase_squashfs,
        # so nothing later would repair a silently missing group; the live
        # medium would ship a backup application that cannot start.
        block = self.script[self.script.index("groupadd -r chronicle"):]
        self.assertIn("exit 1", block[:400])


if __name__ == "__main__":
    unittest.main()
