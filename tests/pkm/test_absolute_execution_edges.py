# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Package-manager subprocesses bind reviewed absolute executables."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from pkm import hooks, installer, remover, repo, services


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("builder", "args", "expected"),
    (
        (hooks._depmod_cmd, ("/", ["usr/lib/modules/demo/kernel/x.ko"]), "/usr/sbin/depmod"),
        (hooks._ldconfig_cmd, ("/", []), "/usr/sbin/ldconfig"),
        (hooks._glib_compile_schemas_cmd, ("/", []), "/usr/bin/glib-compile-schemas"),
        (hooks._fc_cache_cmd, ("/", []), "/usr/bin/fc-cache"),
        (hooks._update_desktop_database_cmd, ("/", []), "/usr/bin/update-desktop-database"),
        (hooks._update_mime_database_cmd, ("/", []), "/usr/bin/update-mime-database"),
        (hooks._systemctl_daemon_reload_cmd, ("/", []), "/usr/bin/systemctl"),
        (hooks._systemd_sysusers_cmd, ("/", ["usr/lib/sysusers.d/demo.conf"]), "/usr/bin/systemd-sysusers"),
        (hooks._systemd_tmpfiles_cmd, ("/", ["usr/lib/tmpfiles.d/demo.conf"]), "/usr/bin/systemd-tmpfiles"),
    ),
)
def test_canonical_command_builder_uses_reviewed_absolute_program(builder, args, expected):
    assert builder(*args)[0] == expected


def test_apparmor_builder_uses_reviewed_absolute_program(tmp_path, monkeypatch):
    interface = tmp_path / "apparmor"
    interface.mkdir()
    monkeypatch.setattr(hooks, "_APPARMOR_IFACE", interface)
    cmd = hooks._apparmor_parser_cmd("/", ["etc/apparmor.d/usr.bin.demo"])
    assert cmd[0] == "/usr/sbin/apparmor_parser"


def test_icon_cache_builder_uses_reviewed_absolute_program(tmp_path):
    (tmp_path / "usr/share/icons/demo").mkdir(parents=True)
    (tmp_path / "usr/share/icons/demo/index.theme").write_text("[Icon Theme]\n")
    cmd = hooks._gtk_update_icon_cache_cmd(
        tmp_path, ["usr/share/icons/demo/apps/icon.svg"]
    )
    assert cmd[0] == "/usr/bin/gtk-update-icon-cache"


def test_ca_builder_uses_reviewed_absolute_program():
    cmd = hooks._update_ca_trust_cmd("/", ["etc/ca-certificates/demo.crt"])
    assert cmd[0] == "/usr/bin/update-ca-trust"


def test_account_seed_binds_bash_and_install_to_reviewed_paths(tmp_path):
    (tmp_path / "usr/share/intergenos-base-files/account-skel").mkdir(parents=True)
    script = tmp_path / "usr/lib/intergenos/seed-account-skel.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/bash\n")
    cmd = hooks._account_skel_seed_cmd(tmp_path, ["usr/lib/sysusers.d/demo.conf"])
    assert cmd[0] == "/usr/bin/bash"

    shipped = (
        REPO_ROOT
        / "packages/core/intergenos-base-files/files/usr/lib/intergenos/seed-account-skel.sh"
    ).read_text()
    assert "/usr/bin/install " in shipped


def test_gpgv_command_uses_reviewed_absolute_program():
    builder = getattr(repo, "_gpgv_command", None)
    assert builder is not None, "gpgv argv has no independently testable constructor"
    assert builder(Path("index"), Path("index.sig"))[0] == "/usr/bin/gpgv"


def test_build_only_index_signer_uses_reviewed_absolute_program():
    assert getattr(repo, "GPG", None) == "/usr/bin/gpg"


def test_service_query_uses_reviewed_absolute_program(monkeypatch):
    calls = []
    monkeypatch.setattr(services, "_TRACE_AVAILABLE", False)
    monkeypatch.setattr(
        services.subprocess,
        "run",
        lambda argv, **_kwargs: calls.append(argv) or SimpleNamespace(returncode=0),
    )
    assert services.query_active_services(["demo.service"]) == ["demo.service"]
    assert calls == [["/usr/bin/systemctl", "is-active", "--quiet", "demo.service"]]


def test_service_restart_uses_reviewed_absolute_program(monkeypatch):
    calls = []
    monkeypatch.setattr(services, "_TRACE_AVAILABLE", False)
    monkeypatch.setattr(
        services.subprocess,
        "run",
        lambda argv, **_kwargs: calls.append(argv) or SimpleNamespace(returncode=0),
    )
    assert services.run_restart_services(["demo.service"]) == {"demo.service": True}
    assert calls == [["/usr/bin/systemctl", "restart", "demo.service"]]


def test_service_active_since_uses_reviewed_absolute_program(monkeypatch):
    calls = []
    monkeypatch.setattr(services, "_TRACE_AVAILABLE", False)
    monkeypatch.setattr(services, "precise_boot_epoch", lambda: None)
    monkeypatch.setattr(
        services.subprocess,
        "run",
        lambda argv, **_kwargs: calls.append(argv)
        or SimpleNamespace(returncode=0, stdout="1000000"),
    )
    assert services.unit_active_since_epoch("demo.service", 1000) == 1001
    assert calls == [[
        "/usr/bin/systemctl",
        "show",
        "-p",
        "ActiveEnterTimestampMonotonic",
        "--value",
        "demo.service",
    ]]


def test_install_and_remove_chroot_commands_use_reviewed_absolute_program(tmp_path):
    hook = tmp_path / "var/lib/pkm/hooks/demo/post-install"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/usr/bin/bash\n")

    install_builder = getattr(installer, "_post_install_hook_cmd", None)
    assert install_builder is not None, "install-hook argv has no testable constructor"
    assert install_builder(tmp_path, hook)[0] == "/usr/sbin/chroot"
    assert remover._remove_hook_cmd(tmp_path, hook)[0][0] == "/usr/sbin/chroot"


def test_archive_lifecycle_command_uses_reviewed_absolute_program(tmp_path):
    builder = getattr(hooks, "_archive_lifecycle_command", None)
    assert builder is not None, "archive-hook argv has no testable constructor"
    assert builder(tmp_path / "hook.sh")[0] == "/usr/bin/bash"


def test_missing_ca_program_names_the_expected_producer(monkeypatch):
    monkeypatch.setattr(hooks, "_TRACE_AVAILABLE", False)

    def missing(_argv, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", "/usr/bin/update-ca-trust")

    monkeypatch.setattr(hooks.subprocess, "run", missing)
    result = hooks.run_canonical_hooks(
        "/",
        ["etc/ca-certificates/demo.crt"],
        "demo",
        "1.0",
        "install",
    )
    assert result.critical_failures == ["ca-trust"]
    message = "\n".join(result.messages)
    assert "/usr/bin/update-ca-trust" in message
    assert "expected provider: ca-certificates" in message
