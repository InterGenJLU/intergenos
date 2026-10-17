# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The real set_root_password step leaves no password hash in the install trace.

An R001.3 install (2026-10-16) wrote root's crypt hash into the target's
/var/log/forge-install-*.log: the step's return value, recorded by the trace
decorator. This runs the REAL step — the real hash_password, the real
`chpasswd --root <target> -e`, the read-back and verify_root_password_kept —
against a fixture target inside a user namespace (chpasswd --root needs
CAP_SYS_CHROOT, which the namespace's root has over its own files), with the
real trace module writing to a sink. The hash the step returns must be on the
target's shadow line and nowhere in the trace.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

CHILD = textwrap.dedent(r'''
    import json, os, sys
    repo, target, sink = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, repo)
    from installer.backend import users
    shared = sys.modules["_igos_trace_shared"]
    shared._SINKS.append(shared._open_600(__import__("pathlib").Path(sink)))
    returned = users.set_root_password(target, password="correct horse battery 7")
    on_disk = users.read_root_password_field(target)
    users.verify_root_password_kept(target, returned)
    json.dump({"returned": returned, "on_disk": on_disk}, sys.stdout)
''')


def _namespace_root_available():
    if shutil.which("unshare") is None or shutil.which("chpasswd") is None:
        return False
    r = subprocess.run(["unshare", "--map-root-user", "true"],
                       capture_output=True, timeout=30)
    return r.returncode == 0


@unittest.skipUnless(_namespace_root_available(),
                     "needs unshare --map-root-user and chpasswd")
class RealSetRootPasswordTrace(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="srp-trace-"))
        self.target = self.tmp / "target"
        etc = self.target / "etc"
        etc.mkdir(parents=True)
        (etc / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
        (etc / "group").write_text("root:x:0:\n")
        (etc / "shadow").write_text("root:!:19000:0:99999:7:::\n")
        (etc / "gshadow").write_text("root:::\n")
        os.chmod(etc / "shadow", 0o600)
        os.chmod(etc / "gshadow", 0o600)
        self.sink = self.tmp / "sink.jsonl"
        self.child = self.tmp / "child.py"
        self.child.write_text(CHILD)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returned_hash_is_on_the_target_and_not_in_the_trace(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("FORGE_DEBUG_VERBOSE", "IGOS_BUILD_DEBUG_VERBOSE")}
        r = subprocess.run(
            ["unshare", "--map-root-user", sys.executable, "-B", str(self.child),
             str(REPO), str(self.target), str(self.sink)],
            capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)
        returned = result["returned"]
        self.assertTrue(returned.startswith("$"), "the step returns a crypt hash")
        self.assertEqual(result["on_disk"], returned,
                         "the target's shadow field is the hash the step wrote")

        raw = self.sink.read_text()
        self.assertNotIn(returned, raw, "the password hash reached the trace")
        self.assertNotIn(returned.rsplit("$", 1)[-1], raw,
                         "the hash's digest reached the trace")
        events = [json.loads(l) for l in raw.splitlines() if l.strip()]
        exits = [e for e in events if e["type"] == "step_exit"
                 and e["step"] == "set_root_password"]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0]["return"], "<REDACTED>")
        self.assertEqual(exits[0]["return_redacted"],
                         "secret-bearing step: takes password")
        kinds = {e["type"] for e in events}
        self.assertTrue({"step_enter", "subprocess_start", "subprocess_end",
                         "root_password_written", "root_password_kept"} <= kinds,
                        f"the trace still records the step's work: {sorted(kinds)}")


if __name__ == "__main__":
    unittest.main()
