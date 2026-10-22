# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The installer backend acts only on the install medium, and there only for a
caller polkit authorizes, call by call.

installer/backend_service.py is a root service on the system bus. Its bus
policy admits the administrators' group, and that admission was the whole
check: nothing in the service asked who the caller was, an installed system
started it on activation, and a dry run started the install worker, which
mounted the host's /dev, /dev/pts, proc, sysfs and a tmpfs into the target and
generated a signing key before anything asked whether it should.

These tests drive the service's own method handler, _on_method_call, with a
fake caller, a fake polkit authority and a scratch root standing in for the
machine. Every process the backend would start is recorded and not started.
  - Off the medium (no trust set and no dev marker under the root's /install)
    every method is refused before anything is written or started, polkit is
    not asked, the service refuses to start, and the shipped unit carries the
    same two paths as its only start conditions.
  - On the medium each acting method asks polkit for
    org.intergenos.forge.install with the caller's bus name, per call; a
    refusal leaves nothing written, cancelled or accepted.
  - A dry run is refused on the bus before any write or worker, and the
    install pipeline itself runs no phase after the disk phase's own dry run,
    so no process starts under one.
Nothing here reaches a bus, polkit or a real process.
"""

import configparser
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from gi.repository import GLib

from installer import backend_service
from installer.backend import _trace
from installer.backend import disks as backend_disks
from installer.backend import install as backend_install
from installer.backend import integrity as backend_integrity

REPO = Path(__file__).resolve().parents[2]
UNIT = REPO / "installer" / "data" / "forge-installer-backend.service"
ACTION = "org.intergenos.forge.install"
NOT_ON_MEDIUM = "org.intergenos.ForgeInstaller1.Error.NotOnInstallMedium"
NOT_AUTHORIZED = "org.intergenos.ForgeInstaller1.Error.NotAuthorized"
DRY_RUN_REFUSED = "org.intergenos.ForgeInstaller1.Error.DryRunRefused"
CALLER = ":1.4242"
YAML = ("version: 1\nlocale: en_US.UTF-8\ntimezone: UTC\nhostname: intergenos\n"
        "package_groups: [core]\n")


def _install_io():
    return {"disk": GLib.Variant("s", "/dev/vdz"),
            "username": GLib.Variant("s", "person"),
            "user_password": GLib.Variant("s", "pw1234"),
            "root_password": GLib.Variant("s", "root1234"),
            "mok_key_passphrase": GLib.Variant("s", "a-signing-passphrase")}


def _start(dry_run):
    return GLib.Variant("(sa{sv}b)", (YAML, _install_io(), dry_run))


class _Invocation:
    """Stands in for the Gio.DBusMethodInvocation: keeps the one answer."""

    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class _Authority:
    """Stands in for polkit: answers every check the same way, and keeps who
    asked for what."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def check(self, sender, action_id, on_result):
        self.asked.append((sender, action_id))
        on_result(self.answer, None if self.answer else "refused in the test")


class _ProcessRecorder:
    """Replaces subprocess.Popen: records each command and starts nothing
    (rc 0, empty output). os.system, os.exec* and os.spawn* refuse."""

    def __init__(self):
        self.commands = []
        recorder = self

        class _Popen:
            def __init__(self, args, *a, **kw):
                argv = args if isinstance(args, (list, tuple)) else ["sh", "-c", str(args)]
                recorder.commands.append([str(x) for x in argv])
                self.args = args
                self._text = bool(kw.get("text") or kw.get("universal_newlines")
                                  or kw.get("encoding") or kw.get("errors"))
                self.returncode = 0
                self.pid = 4242
                self.stdin = self.stdout = self.stderr = None

            def communicate(self, input=None, timeout=None):
                empty = "" if self._text else b""
                return empty, empty

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

            terminate = kill

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        self.popen = _Popen

    def count(self, word):
        return sum(1 for argv in self.commands if any(word in part for part in argv))

    def summary(self):
        return (f"{len(self.commands)} process(es): mount {self.count('mount')}, "
                f"mokutil {self.count('mokutil')}, efibootmgr {self.count('efibootmgr')}; "
                + "; ".join(" ".join(argv)[:120] for argv in self.commands[:6]))


def _refuse_exec(*args, **kwargs):
    raise AssertionError(f"a process start outside subprocess.Popen: {args!r}")


class _BackendCase(unittest.TestCase):
    """A scratch root for the machine, the service on it with a recording bus,
    every process recorded and none started; the YAML directory and the
    install target are under the scratch root."""

    MEDIUM = False
    AUTHORIZE = True

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        if self.MEDIUM:
            (self.root / "install").mkdir()
            (self.root / "install" / "IGOS_DEV_ALLOW_UNVERIFIED").write_text("dev marker\n")
        self.target = self.root / "mnt-target"
        self.target.mkdir()
        self.yaml_dir = self.root / "var-lib-forge"
        self.processes = _ProcessRecorder()
        patches = [
            mock.patch.object(subprocess, "Popen", self.processes.popen),
            mock.patch.object(backend_service, "DEFAULT_YAML_DIR", str(self.yaml_dir)),
            mock.patch.object(_trace.module, "_VERBOSE", False),
            # The pipeline's global dry-run switch is restored after each test.
            mock.patch.object(backend_disks, "_DRY_RUN", False),
        ]
        for name in ("system", "execv", "execve", "execvp", "execvpe",
                     "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp"):
            if hasattr(os, name):
                patches.append(mock.patch.object(os, name, _refuse_exec))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        (self.root / "archives").mkdir()
        (self.root / "packages").mkdir()
        self.service = backend_service.ForgeInstallerService(
            archive_dir=str(self.root / "archives"), packages_dir=str(self.root / "packages"))
        # Set as attributes, so the same test reads at a tree whose service
        # takes neither: the machine's root for the medium check, and polkit.
        self.service._medium_root = self.root
        self.authority = _Authority(self.AUTHORIZE)
        self.service._authority = self.authority
        self.signals = []
        self.service._bus = mock.Mock(emit_signal=lambda *a: self.signals.append(a))
        self.addCleanup(self._join_worker)

    def _join_worker(self):
        worker = self.service._worker_thread
        if worker is not None:
            worker.join(timeout=120)
            self.assertFalse(worker.is_alive(), "the install worker did not finish")

    def call(self, method, params=None):
        invocation = _Invocation()
        self.service._on_method_call(None, CALLER, backend_service.OBJECT_PATH,
                                     backend_service.INTERFACE, method, params, invocation)
        return invocation

    def assert_nothing_acted(self):
        self._join_worker()
        self.assertEqual(self.processes.commands, [], self.processes.summary())
        self.assertFalse((self.yaml_dir / "install.yaml").exists(),
                         "the install YAML was written")


class OffTheMediumNothingActs(_BackendCase):
    MEDIUM = False

    def test_every_method_is_refused_before_it_acts_and_polkit_is_not_asked(self):
        for method, params in (("StartInstall", _start(False)),
                               ("StartInstall", _start(True)),
                               ("CancelInstall", GLib.Variant("(s)", ("an-install",))),
                               ("AckIntegrityOverride",
                                GLib.Variant("(sss)", ("an-install", "glibc", "a phrase"))),
                               ("GetStatus", None)):
            with self.subTest(method=method):
                invocation = self.call(method, params)
                self.assertIsNone(invocation.value, f"{method} answered {invocation.value}")
                self.assertEqual((invocation.error or ("none",))[0], NOT_ON_MEDIUM)
                self.assert_nothing_acted()
                self.assertIsNone(self.service._worker_thread)
                self.assertFalse(self.service._cancel_event.is_set())
        self.assertEqual(self.authority.asked, [])

    def test_the_service_refuses_to_start(self):
        started = []

        class _Service:
            def __init__(self, *a, **kw):
                pass

            def run(self):
                started.append(True)
                return 0

        absent = self.root / "absent"
        with mock.patch.object(backend_service.os, "geteuid", return_value=0), \
                mock.patch.object(backend_service, "ForgeInstallerService", _Service), \
                mock.patch.object(backend_integrity, "INSTALL_MEDIA_MANIFEST",
                                  absent / "intergenos-archive-manifest.txt", create=True), \
                mock.patch.object(backend_integrity, "INSTALL_MEDIA_DEV_MARKER",
                                  absent / "IGOS_DEV_ALLOW_UNVERIFIED"):
            rc = backend_service.main([])
        self.assertEqual(started, [], "the service ran off the medium")
        self.assertNotEqual(rc, 0)


class TheUnitStartsOnlyOnTheMedium(unittest.TestCase):

    def test_its_only_start_conditions_are_the_two_paths_the_service_checks(self):
        unit = configparser.ConfigParser(strict=False, interpolation=None)
        unit.optionxform = str
        unit.read_string(UNIT.read_text())
        conditions = [line.strip() for line in UNIT.read_text().splitlines()
                      if line.strip().startswith(("Condition", "Assert"))]
        manifest = "/install/intergenos-archive-manifest.txt"
        marker = "/install/IGOS_DEV_ALLOW_UNVERIFIED"
        self.assertEqual(sorted(conditions), sorted([
            "ConditionPathExists=|" + manifest, "ConditionPathExists=|" + marker]))
        # The same two paths the service's own check reads.
        self.assertEqual({str(getattr(backend_integrity, "INSTALL_MEDIA_MANIFEST", "")),
                          str(backend_integrity.INSTALL_MEDIA_DEV_MARKER)}, {manifest, marker})
        self.assertIn("Unit", unit.sections())


class OnTheMediumARefusedCallerActsOnNothing(_BackendCase):
    MEDIUM = True
    AUTHORIZE = False

    def test_start_install_asks_polkit_and_starts_nothing_when_refused(self):
        with mock.patch.object(backend_service.backend_install, "run_install") as run_install:
            invocation = self.call("StartInstall", _start(False))
            self.assert_nothing_acted()
            run_install.assert_not_called()
        self.assertEqual((invocation.error or ("none",))[0], NOT_AUTHORIZED, invocation.value)
        self.assertEqual(self.authority.asked, [(CALLER, ACTION)])
        self.assertIsNone(self.service._worker_thread)

    def test_cancel_and_ack_ask_polkit_and_change_nothing_when_refused(self):
        self.service._worker_install_id = "an-install"
        phrase = backend_integrity.expected_override_phrase("glibc")
        cancel = self.call("CancelInstall", GLib.Variant("(s)", ("an-install",)))
        ack = self.call("AckIntegrityOverride", GLib.Variant("(sss)", ("an-install", "glibc", phrase)))
        self.assertEqual((cancel.error or ("none",))[0], NOT_AUTHORIZED)
        self.assertFalse(self.service._cancel_event.is_set(), "the install was cancelled")
        self.assertEqual((ack.error or ("none",))[0], NOT_AUTHORIZED, ack.value)
        self.assertTrue(self.service._ack_bridge.get_queue("glibc").empty(),
                        "the override reached the install worker")
        self.assertEqual(self.authority.asked, [(CALLER, ACTION), (CALLER, ACTION)])

    def test_get_status_is_a_read_and_asks_nothing(self):
        invocation = self.call("GetStatus")
        self.assertIsNone(invocation.error)
        self.assertEqual(self.authority.asked, [])


class OnTheMediumAnAuthorizedCallerIsAskedForEachCall(_BackendCase):
    MEDIUM = True
    AUTHORIZE = True

    def test_each_acting_call_asks_polkit_with_the_callers_bus_name(self):
        finished = threading.Event()
        with mock.patch.object(backend_service.backend_install, "run_install",
                               side_effect=lambda **kw: finished.set() or backend_install.InstallResult(success=True)):
            start = self.call("StartInstall", _start(False))
            self.assertIsNone(start.error)
            install_id = start.value.unpack()[0]
            self.assertTrue(finished.wait(15), "the authorized install did not start")
            self._join_worker()
        cancel = self.call("CancelInstall", GLib.Variant("(s)", (install_id,)))
        ack = self.call("AckIntegrityOverride", GLib.Variant(
            "(sss)", (install_id, "glibc", backend_integrity.expected_override_phrase("glibc"))))
        self.assertIsNone(cancel.error)
        self.assertTrue(self.service._cancel_event.is_set())
        self.assertIsNone(ack.error)
        self.assertEqual(self.authority.asked, [(CALLER, ACTION)] * 3)


class ADryRunRunsNothing(_BackendCase):
    """The host reads that would stop the pipeline before its acting phases on
    a machine that is not the medium are stubbed (the preflights, the EFI
    probe, the disk phase's own dry run); everything after them is the
    pipeline's own code, so what a dry run starts is what is recorded."""

    MEDIUM = True
    AUTHORIZE = True

    def setUp(self):
        super().setUp()
        partitions = {"esp": "/dev/vdz1", "root": "/dev/vdz2", "efi": True}
        target = self.target
        real_run_install = backend_install.run_install
        stubs = [
            mock.patch.object(backend_install, "preflight_check_binaries", lambda *a, **kw: None),
            mock.patch.object(backend_install, "preflight_check_archive_availability", lambda *a, **kw: None),
            mock.patch.object(backend_install, "preflight_check_install_set_complete", lambda *a, **kw: None),
            mock.patch.object(backend_install, "preflight_check_efi_boot_chain", lambda *a, **kw: None),
            mock.patch.object(backend_install.bootloader, "verify_shim_assets_present", lambda *a, **kw: None),
            mock.patch.object(backend_install.disks, "is_efi", lambda: True),
            mock.patch.object(backend_install.disks, "partition_disk", lambda *a, **kw: dict(partitions)),
            mock.patch.object(backend_install.packages, "install_packages",
                              lambda *a, **kw: (1, 0, [], ["linux-kernel"])),
            # The worker runs the pipeline against the scratch target.
            mock.patch.object(backend_service.backend_install, "run_install",
                              lambda **kw: real_run_install(**dict(kw, target=str(target)))),
        ]
        for p in stubs:
            p.start()
            self.addCleanup(p.stop)
        self.real_run_install = real_run_install

    def test_the_bus_refuses_a_dry_run_before_any_write_or_worker(self):
        invocation = self.call("StartInstall", _start(True))
        self.assert_nothing_acted()
        self.assertEqual((invocation.error or ("none",))[0], DRY_RUN_REFUSED, invocation.value)
        self.assertIsNone(self.service._worker_thread)
        self.assertEqual(self.authority.asked, [], "a refused dry run still asked polkit")

    def test_the_pipeline_runs_no_phase_after_the_disk_phase(self):
        yaml_path = self.root / "install.yaml"
        yaml_path.write_text(YAML)
        install_io = {k: v.unpack() for k, v in _install_io().items()}
        result = self.real_run_install(
            str(yaml_path), install_io, str(self.root / "archives"), str(self.root / "packages"),
            dry_run=True, target=str(self.target))
        self.assertEqual(self.processes.commands, [], self.processes.summary())
        self.assertEqual(result.phase_completed, backend_install.PHASE_PARTITION)
        self.assertFalse(result.success, "a dry run reported an install")
        self.assertIn("dry run", result.error_message or "")


class TheShippedActionAndTheMediumOnlyRule(unittest.TestCase):
    """The action the backend asks for, the rule that grants the live account
    and where each one lands: the action in polkit's actions directory, the
    rule inert in the package and a rule only in the medium's live overlay."""

    FORGE = REPO / "packages" / "desktop" / "forge"
    POLICY = FORGE / "polkit" / "org.intergenos.forge.policy"
    RULE = FORGE / "polkit" / "49-intergenos-forge-live-install.rules"

    def test_the_action_asks_an_administrator_at_the_active_session_and_nobody_elsewhere(self):
        self.assertTrue(self.POLICY.is_file(), f"no action file at {self.POLICY}")
        import xml.etree.ElementTree as ET
        actions = ET.parse(self.POLICY).getroot().findall("action")
        self.assertEqual([a.get("id") for a in actions], [ACTION])
        defaults = {d.tag: d.text for d in actions[0].find("defaults")}
        self.assertEqual(defaults, {"allow_any": "no", "allow_inactive": "no",
                                    "allow_active": "auth_admin"})

    def test_the_rule_grants_only_the_live_account_at_its_local_active_session(self):
        self.assertTrue(self.RULE.is_file(), f"no rule file at {self.RULE}")
        text = self.RULE.read_text()
        code = " ".join(line.split("//")[0] for line in text.splitlines()).split()
        self.assertEqual(" ".join(code), (
            'polkit.addRule(function(action, subject) { if (action.id == '
            '"org.intergenos.forge.install" && subject.user == "intergenos" && '
            'subject.local && subject.active) { return polkit.Result.YES; } });'))

    def test_the_package_installs_the_action_and_ships_the_rule_inert(self):
        build = (self.FORGE / "build.sh").read_text()
        self.assertIn('"${PKG_DIR}/polkit/org.intergenos.forge.policy" \\\n'
                      '        "${DESTDIR}/usr/share/polkit-1/actions/org.intergenos.forge.policy"', build)
        self.assertIn('"${PKG_DIR}/polkit/49-intergenos-forge-live-install.rules" \\\n'
                      '        "${DESTDIR}/usr/share/intergenos/forge/49-intergenos-forge-live-install.rules"', build)
        installed = [line for line in build.splitlines()
                     if "rules.d" in line and not line.lstrip().startswith("#")]
        self.assertEqual(installed, [], "the package installs a polkit rule where polkit reads it")

    def test_only_the_medium_boot_makes_it_a_rule(self):
        init = (REPO / "installer" / "init" / "init.sh").read_text()
        live = init.index('if [ "$MODE" = "live" ] || [ "$MODE" = "install-gui" ]; then')
        copy = init.index('cp "$FORGE_LIVE_RULE" /newroot/etc/polkit-1/rules.d/'
                          '49-intergenos-forge-live-install.rules')
        self.assertLess(live, copy)
        self.assertIn("FORGE_LIVE_RULE=/newroot/usr/share/intergenos/forge/"
                      "49-intergenos-forge-live-install.rules", init)


class TheAuthorityAsksPolkitForTheCaller(unittest.TestCase):
    """PolkitAuthority's call as polkit receives it, and what each answer
    becomes: a grant only on is_authorized, a refusal on anything else."""

    def _ask(self, finish):
        calls, answers = [], []

        class _Bus:
            def call(self, *args):
                calls.append(args)
                args[9](self, "a result", None)

            def call_finish(self, _res):
                return finish()

        authority = backend_service.PolkitAuthority(_Bus())
        authority.check(CALLER, ACTION, lambda ok, why: answers.append((ok, why)))
        return calls, answers

    def test_the_subject_is_the_callers_bus_name_with_interaction_allowed(self):
        calls, answers = self._ask(lambda: GLib.Variant("((bba{ss}))", ((True, False, {}),)))
        name, path, iface, method, params, reply_type, _flags, timeout = calls[0][:8]
        self.assertEqual((name, path, iface, method), (
            "org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
            "org.freedesktop.PolicyKit1.Authority", "CheckAuthorization"))
        self.assertEqual(params.get_type_string(), "((sa{sv})sa{ss}us)")
        subject, action, details, flags, cancel_id = params.unpack()
        self.assertEqual(subject, ("system-bus-name", {"name": CALLER}))
        self.assertEqual((action, details, flags, cancel_id), (ACTION, {}, 1, ""))
        self.assertEqual(reply_type.dup_string(), "((bba{ss}))")
        self.assertEqual(timeout, GLib.MAXINT32)
        self.assertEqual(answers, [(True, None)])

    def test_a_refusal_or_an_error_is_never_a_grant(self):
        _calls, refused = self._ask(lambda: GLib.Variant("((bba{ss}))", ((False, True, {}),)))
        self.assertEqual([ok for ok, _ in refused], [False])

        def _error():
            raise GLib.Error("the authority is not there")
        _calls, failed = self._ask(_error)
        self.assertEqual([ok for ok, _ in failed], [False])
        self.assertIn("polkit could not decide", failed[0][1])


if __name__ == "__main__":
    unittest.main()
