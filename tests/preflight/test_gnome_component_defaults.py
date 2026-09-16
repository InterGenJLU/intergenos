"""Unavailable screen reading stays off; shipped modem activation is wired."""

import configparser
import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_screen_reader_payload_and_writability(tmp_path):
    recipe = REPO / "packages/core/intergenos-default-settings"
    subprocess.run(
        ["/usr/bin/bash", "-e", "-c",
         'source "$1"; DESTDIR="$2"; ASSETS="$3"; IGOS_SOURCE_ROOT="$4"; do_install',
         "desktop-defaults-test", str(recipe / "build.sh"), str(tmp_path),
         str(recipe / "assets"), str(REPO)],
        check=True, capture_output=True, text=True,
    )
    settings = configparser.ConfigParser(interpolation=None)
    settings.read(tmp_path / "usr/share/glib-2.0/schemas/90_intergenos.gschema.override")
    assert settings["org.gnome.desktop.a11y.applications"]["screen-reader-enabled"] == "false"
    fragment = tmp_path / "etc/dconf/db/local.d"
    lock = fragment / "locks/01-screen-reader-availability"
    assert "/org/gnome/desktop/a11y/applications/screen-reader-enabled" in lock.read_text()
    db = tmp_path / "local"
    subprocess.run(["/usr/bin/dconf", "compile", str(db), str(fragment)], check=True)
    profile = tmp_path / "profile"
    profile.write_text(f"user-db:user\nfile-db:{db}\n")
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    env = dict(os.environ, DCONF_PROFILE=str(profile), XDG_RUNTIME_DIR=str(runtime),
               XDG_CONFIG_HOME=str(tmp_path / "config"))
    result = subprocess.run(
        ["/usr/bin/gsettings", "writable", "org.gnome.desktop.a11y.applications",
         "screen-reader-enabled"], env=env, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "false"
    assert not result.stderr


def test_modemmanager_preset_enables_activation_alias(tmp_path):
    units = tmp_path / "usr/lib/systemd/system"
    units.mkdir(parents=True)
    (units / "ModemManager.service").write_text(
        "[Service]\nExecStart=/usr/sbin/ModemManager\n[Install]\n"
        "WantedBy=multi-user.target\nAlias=dbus-org.freedesktop.ModemManager1.service\n")
    (units / "multi-user.target").write_text("[Unit]\nDescription=Multiuser\n")
    recipe = REPO / "packages/desktop/modemmanager/build.sh"
    subprocess.run(["/usr/bin/bash", "-e", "-c",
                    'source "$1"; DESTDIR="$2"; install_activation_policy',
                    "modem-policy-test", str(recipe), str(tmp_path)],
                   check=True, capture_output=True, text=True)
    presets = tmp_path / "usr/lib/systemd/system-preset"
    presets.mkdir()
    source = REPO / "packages/core/intergenos-base-files/files/usr/lib/systemd/system-preset/80-intergenos-enable.preset"
    (presets / source.name).write_bytes(source.read_bytes())
    subprocess.run(["/usr/bin/systemctl", f"--root={tmp_path}", "preset", "ModemManager.service"],
                   check=True, capture_output=True, text=True)
    alias = tmp_path / "etc/systemd/system/dbus-org.freedesktop.ModemManager1.service"
    assert alias.is_symlink()
    assert os.readlink(alias) == "/usr/lib/systemd/system/ModemManager.service"
    assert not (tmp_path / "etc/systemd/system/multi-user.target.wants/ModemManager.service").exists()
