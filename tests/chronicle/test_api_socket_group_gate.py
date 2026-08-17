#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The engine socket's group gate.

The socket at /run/chronicle/engine.sock is root:chronicle mode 0660, so a
process outside the group cannot open it at all. These tests pin the three
things that claim is made of:

  * the socket ends up with exactly that group and exactly that mode, read
    back off the inode rather than assumed from the calls returning;
  * it is never wider than that, not even for the instant between bind() and
    the chown/chmod — the mode observed immediately after bind is captured and
    asserted, which is the only way to test a creation-time property;
  * a missing group leaves it owner-only and says so, rather than falling back
    to something wider.

Everything runs unprivileged, headless, in a temporary directory. The group is
supplied through the api._GROUP_RESOLVER seam as one of the running user's own
groups, so the real chown/chmod path executes rather than being mocked out.
"""

import os
import socket
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from chronicle import api as _api
from chronicle import engine as _engine
from chronicle import protection as _protection

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _a_group_of_ours():
    """A gid this process may hand a file to.

    The running user's own primary group: chown to it succeeds unprivileged,
    so the real ownership path executes instead of being mocked away, and the
    resulting inode is the thing the assertions read.
    """
    return os.getgid()


class SocketPermissionsTest(unittest.TestCase):
    """secure_socket / prepare_runtime_dir in isolation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-sockperm-")
        self.gid = _a_group_of_ours()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bound_socket(self, name="s.sock"):
        path = os.path.join(self.tmp, name)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            srv.bind(path)
        finally:
            os.umask(old)
        self.addCleanup(srv.close)
        return path

    def test_socket_gets_the_group_and_the_mode(self):
        path = self._bound_socket()
        self.assertTrue(_api.secure_socket(path, self.gid))
        st = os.stat(path)
        self.assertEqual(st.st_gid, self.gid)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o660)

    def test_no_bit_is_granted_to_other(self):
        # The whole point of the change: a process outside the group gets
        # nothing. Asserted as a property of the mode, not just equality.
        path = self._bound_socket()
        _api.secure_socket(path, self.gid)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode & 0o007, 0, f"other has bits in {mode:04o}")

    def test_missing_group_leaves_the_socket_owner_only(self):
        path = self._bound_socket()
        self.assertFalse(_api.secure_socket(path, None))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_runtime_dir_is_group_traversable_only(self):
        d = os.path.join(self.tmp, "rt")
        _api.prepare_runtime_dir(d, self.gid)
        st = os.stat(d)
        self.assertEqual(st.st_gid, self.gid)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o750)
        self.assertEqual(stat.S_IMODE(st.st_mode) & 0o007, 0)

    def test_runtime_dir_without_a_group_is_owner_only(self):
        d = os.path.join(self.tmp, "rt-nogroup")
        _api.prepare_runtime_dir(d, None)
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)

    def test_runtime_dir_is_idempotent_over_an_existing_wide_directory(self):
        # RuntimeDirectoryPreserve=yes means the directory can already exist
        # from a previous run — possibly one that created it wider.
        d = Path(self.tmp) / "rt-existing"
        d.mkdir(mode=0o777)
        os.chmod(d, 0o777)
        _api.prepare_runtime_dir(d, self.gid)
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o750)


class ServeSocketModeTest(unittest.TestCase):
    """serve() end to end, on a temporary socket path."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-serve-")
        self.store = tempfile.mkdtemp(prefix="chronicle-store-")
        self.gid = _a_group_of_ours()
        self._orig_resolver = _api._GROUP_RESOLVER
        self._thread = None
        self._srv_path = os.path.join(self.tmp, "rt", "engine.sock")

    def tearDown(self):
        _api._GROUP_RESOLVER = self._orig_resolver
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.store, ignore_errors=True)

    def _run_engine(self, resolver):
        """Start serve() in a daemon thread; return when the socket is ready."""
        _api._GROUP_RESOLVER = resolver
        eng = _engine.Engine(local_root=self.store)
        ready = threading.Event()
        self._thread = threading.Thread(
            target=_api.serve,
            args=(eng,),
            kwargs={"socket_path": self._srv_path, "ready_cb": ready.set},
            daemon=True)
        self._thread.start()
        self.assertTrue(ready.wait(10), "engine never became ready")

    def test_serve_creates_the_socket_group_owned_and_0660(self):
        self._run_engine(lambda name: self.gid)
        st = os.stat(self._srv_path)
        self.assertEqual(st.st_gid, self.gid)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o660)
        # And the directory it sits in.
        self.assertEqual(
            stat.S_IMODE(os.stat(os.path.dirname(self._srv_path)).st_mode), 0o750)

    def test_the_socket_is_never_wider_than_its_final_mode(self):
        """The creation-time guarantee, measured.

        os.chmod is the first call that MODIFIES the inode after bind(), so the
        mode captured on entry to it IS the mode bind() created. Without the
        umask around bind this observes 0755 and the test fails — which is
        exactly the window the change exists to close.

        The observation used to hang off os.chown. It cannot any more: the
        chown became conditional (see secure_socket) because an unnecessary
        chown is SIGSYS under this daemon's seccomp filter, and in this test
        the socket is already owned by the target group, so no chown happens
        at all. Moving the seam to chmod keeps the same property under
        measurement instead of relaxing the assertion — chown never changed the
        mode, so nothing about what is being observed has changed.
        """
        observed = {}
        real_chmod = os.chmod

        def _watching_chmod(path, mode, **kw):
            if str(path) == self._srv_path and "mode" not in observed:
                observed["mode"] = stat.S_IMODE(os.stat(path).st_mode)
            return real_chmod(path, mode, **kw)

        _api.os.chmod = _watching_chmod
        try:
            self._run_engine(lambda name: self.gid)
        finally:
            _api.os.chmod = real_chmod
        self.assertIn("mode", observed, "chmod never reached the socket")
        self.assertEqual(observed["mode"] & 0o077, 0,
                         f"socket existed as {observed['mode']:04o} before it "
                         f"was narrowed — group and other saw it")

    def test_a_member_can_talk_to_the_engine(self):
        # polkit is the SECOND gate and is stubbed to yes here: this test is
        # about the first one. The tier split itself is covered by
        # test_api_authorization_tiers.py.
        orig_authz = _api._PEER_AUTHORIZER
        _api._PEER_AUTHORIZER = lambda pid, uid, action: True
        self.addCleanup(lambda: setattr(_api, "_PEER_AUTHORIZER", orig_authz))
        self._run_engine(lambda name: self.gid)
        client = _api.Client(socket_path=self._srv_path, timeout=10)
        self.assertTrue(client.available())
        resp = client.call("status")
        self.assertTrue(resp.get("ok"), resp)

    def test_a_missing_group_leaves_it_owner_only_and_reports_why(self):
        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._run_engine(lambda name: None)
        self.assertEqual(stat.S_IMODE(os.stat(self._srv_path).st_mode), 0o600)
        message = err.getvalue()
        self.assertIn("chronicle", message)
        self.assertIn("sysusers", message)


class AccessDeniedTest(unittest.TestCase):
    """What a non-member sees. Root bypasses every permission check, so the
    denial cannot be produced as root and the tests say so rather than
    pretending to have covered it."""

    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest("root bypasses socket permissions by design")
        self.tmp = tempfile.mkdtemp(prefix="chronicle-denied-")
        self.dir = Path(self.tmp) / "rt"
        self.dir.mkdir()
        self.path = self.dir / "engine.sock"
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(self.path))
        self.srv.listen(4)
        # A directory this process cannot traverse denies the connect() with
        # EACCES for exactly the same reason a non-member is denied by the
        # socket's own group bits: the kernel refuses before anything is read.
        os.chmod(self.dir, 0o000)

    def tearDown(self):
        os.chmod(self.dir, 0o700)
        self.srv.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_client_raises_access_denied_not_a_bare_oserror(self):
        client = _api.Client(socket_path=str(self.path), timeout=5)
        with self.assertRaises(_api.EngineAccessDenied) as caught:
            client.call("status")
        self.assertIn("chronicle", str(caught.exception))
        self.assertIn("usermod", str(caught.exception))

    def test_the_cli_reports_it_instead_of_falling_back_to_a_local_engine(self):
        from chronicle import cli as _cli
        backend = _cli.Backend(socket_path=str(self.path))
        with self.assertRaises(RuntimeError) as caught:
            backend.call("status")
        self.assertIn("chronicle", str(caught.exception))
        # And it must NOT be the access-denied OSError leaking through: main()
        # only catches RuntimeError, so anything else is a traceback at the
        # user.
        self.assertNotIsInstance(caught.exception, OSError)

    def test_a_denial_is_not_reported_as_an_absent_engine(self):
        # os.path.exists() answers False for a socket this account may not
        # stat, and every caller reads that as "the service is not running":
        # the window offers a Start button for a running service and the CLI
        # runs an engine in-process. available() has to say present.
        client = _api.Client(socket_path=str(self.path), timeout=5)
        self.assertTrue(client.available())

    def test_an_actually_absent_socket_is_reported_absent(self):
        client = _api.Client(socket_path=os.path.join(self.tmp, "absent.sock"),
                             timeout=5)
        self.assertFalse(client.available())

    def test_access_denied_is_distinguishable_from_the_engine_being_down(self):
        # A socket that does not exist is a different error class, so a
        # caller can tell "start the service" from "you are not allowed".
        client = _api.Client(socket_path=os.path.join(self.tmp, "absent.sock"),
                             timeout=5)
        with self.assertRaises(OSError) as caught:
            client.call("status")
        self.assertNotIsInstance(caught.exception, _api.EngineAccessDenied)


class UnhashableVerbTest(unittest.TestCase):
    """A verb that is a JSON object or array is unhashable, so the membership
    test raised TypeError instead of refusing: the connection guard kept the
    engine alive but the client got a dropped connection where a refusal line
    belonged."""

    def setUp(self):
        self._orig = _api._PEER_AUTHORIZER
        _api._PEER_AUTHORIZER = lambda pid, uid, action: True

    def tearDown(self):
        _api._PEER_AUTHORIZER = self._orig

    def test_authorize_verb_refuses_an_unhashable_verb(self):
        for verb in ({"a": 1}, ["status"], 7, None):
            with self.subTest(verb=verb):
                ok, tier, reason = _api.authorize_verb(1234, 1000, verb)
                self.assertFalse(ok)
                self.assertIsNone(tier)
                self.assertIn("unknown verb", reason)

    def test_dispatch_refuses_an_unhashable_verb(self):
        tmp = tempfile.mkdtemp(prefix="chronicle-verb-")
        try:
            eng = _engine.Engine(local_root=tmp)
            for verb in ({"a": 1}, ["status"], 7):
                with self.subTest(verb=verb):
                    resp = _api.dispatch(eng, {"verb": verb})
                    self.assertFalse(resp["ok"])
                    self.assertIn("unknown verb", resp["error"])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class ShippedGroupParityTest(unittest.TestCase):
    """The group name is written down in five places. They have to agree, and
    a test is the only thing that keeps them agreeing."""

    def test_engine_and_gui_copy_name_the_same_group(self):
        self.assertEqual(_api.ENGINE_SOCKET_GROUP, _protection.ENGINE_GROUP)

    def test_the_sysusers_fragment_declares_that_group(self):
        frag = (_REPO_ROOT / "assets" / "intergenos-backup" / "sysusers"
                / "chronicle.conf")
        self.assertTrue(frag.is_file(), f"{frag} is missing")
        declared = [ln.split()[1] for ln in frag.read_text().splitlines()
                    if ln.strip().startswith("g ")]
        self.assertIn(_api.ENGINE_SOCKET_GROUP, declared)

    def test_the_recipe_installs_the_fragment(self):
        """The INSTALL COMMAND, not a mention of the path.

        The first version of this test asserted only that the two path
        strings appeared somewhere in build.sh, and a mutation that deleted
        the install invocation outright still passed — both strings survive in
        the recipe's header comment. A test that a comment satisfies is
        vacuous. This one reads the install step's own line.
        """
        build = (_REPO_ROOT / "packages" / "desktop" / "intergenos-backup"
                 / "build.sh").read_text()
        install_lines = [ln.strip() for ln in build.splitlines()
                         if ln.strip().startswith("install ")]
        joined = " ".join(install_lines)
        self.assertIn("sysusers/chronicle.conf", joined,
                      "no install step consumes the fragment")
        self.assertIn("/usr/lib/sysusers.d/chronicle.conf", build.replace(
            "\\\n", ""), "the fragment is not installed to sysusers.d")
        # The continuation form splits the destination onto its own line, so
        # pin the pair together on the un-continued text.
        flat = " ".join(build.replace("\\\n", " ").split())
        self.assertIn(
            'install -Dm644 sysusers/chronicle.conf '
            '"${DESTDIR}/usr/lib/sysusers.d/chronicle.conf"', flat)

    def test_the_tarball_generator_stages_the_fragment(self):
        gen = (_REPO_ROOT / "scripts"
               / "build-intergenos-source-tarballs.sh").read_text()
        self.assertIn("sysusers/chronicle.conf", gen,
                      "the generator does not require the fragment")
        self.assertIn("for d in systemd sysusers", gen,
                      "the generator does not copy the sysusers dir")

    def test_the_recipe_creates_the_group_before_it_restarts_the_engine(self):
        """On a live upgrade the group has to exist before the engine restarts.

        Otherwise the restart finds no group, correctly leaves the socket
        owner-only, and the backup application stops working for the console
        user until the next boot brings systemd-sysusers.service around.
        """
        build = (_REPO_ROOT / "packages" / "desktop" / "intergenos-backup"
                 / "build.sh").read_text()
        post = build[build.index("post_install()"):]
        # Comments are stripped first. An earlier version of this test indexed
        # the whole function text, and a mutation that deleted the command
        # still passed because the comment above it also says
        # "systemd-sysusers" — a test a comment satisfies is vacuous.
        code = "\n".join(ln for ln in post.splitlines()
                         if not ln.strip().startswith("#"))
        self.assertIn("systemd-sysusers /usr/lib/sysusers.d/chronicle.conf",
                      code, "post_install does not create the group")
        self.assertLess(code.index("systemd-sysusers /usr/lib/sysusers.d/"),
                        code.index("systemctl start"),
                        "the engine is restarted before its group is created")
        self.assertNotIn("systemd-sysusers /usr/lib/sysusers.d/chronicle.conf "
                         "2>/dev/null", code,
                         "a failure to create the group must not be silent")

    def test_the_package_verifies_the_installed_fragment(self):
        import yaml
        pkg = yaml.safe_load(
            (_REPO_ROOT / "packages" / "desktop" / "intergenos-backup"
             / "package.yml").read_text())
        self.assertIn("/usr/lib/sysusers.d/chronicle.conf",
                      pkg.get("verify_paths", []))

    def test_the_unit_never_creates_a_wider_runtime_directory(self):
        unit = (_REPO_ROOT / "assets" / "intergenos-backup" / "systemd"
                / "chronicled.service").read_text()
        self.assertIn("RuntimeDirectoryMode=0750", unit)


class NoAccessStateTest(unittest.TestCase):
    """The sixth protection state. The module's own rule is that two states
    with different remedies are never collapsed into one message."""

    def test_the_state_is_complete_across_every_table(self):
        for table in (_protection.VERDICT_KEY, _protection.TONE,
                      _protection.TAG):
            self.assertIn(_protection.NO_ACCESS, table)
        self.assertIn(_protection.VERDICT_KEY[_protection.NO_ACCESS],
                      _protection.COPY)
        self.assertIn("banner.no_access", _protection.COPY)
        self.assertIn("no_access.remedy", _protection.COPY)

    def test_it_does_not_read_as_the_service_being_stopped(self):
        verdict = _protection.COPY[
            _protection.VERDICT_KEY[_protection.NO_ACCESS]]
        self.assertNotEqual(verdict,
                            _protection.COPY["verdict.service_down"])
        self.assertNotIn("isn't running", verdict)

    def test_the_remedy_names_the_group(self):
        self.assertIn(_protection.ENGINE_GROUP,
                      _protection.COPY["no_access.remedy"])

    def test_the_window_routes_the_denial_to_this_state(self):
        """A wiring pin, read from the source.

        chronicle/gui.py imports GTK and libadwaita at module scope and builds
        widgets against a display, so it cannot be imported in a headless
        suite; this reads the routing rather than executing it, and is honest
        about being the weaker of the two kinds of check. What it catches is
        the regression that matters: the access denial falling back into the
        service-down branch, which would tell the user to start a service that
        is already running.
        """
        src = (_REPO_ROOT / "assets" / "intergenos-backup" / "chronicle"
               / "gui.py").read_text()
        self.assertIn("except _api.EngineAccessDenied", src)
        self.assertIn("raise EngineNotPermitted", src)
        self.assertIn("except EngineNotPermitted as e:", src)
        self.assertIn("_protection.NO_ACCESS, str(e)", src)
        # EngineNotPermitted must not inherit EngineUnavailable, or every
        # existing service-down handler swallows it first.
        self.assertIn("class EngineNotPermitted(RuntimeError):", src)
        denial = src.index("except _api.EngineAccessDenied")
        unavailable = src.index("except OSError as e:")
        self.assertLess(denial, unavailable,
                        "the OSError handler would catch the denial first")


if __name__ == "__main__":
    sys.exit(unittest.main())
