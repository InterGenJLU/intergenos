#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Samba ships a configuration its own client tools can load.

WHAT THIS COVERS. The samba package installs the upstream example as
/etc/samba/smb.conf.default and, before this, installed nothing at
/etc/samba/smb.conf. Every tool that links Samba's client library reads that
second path: on an installed system each smbclient run opened with
"Can't load /etc/samba/smb.conf - run testparm to debug it", `testparm -s`
exited 1 with "Error loading services", and no package owned the path, so
nothing would ever have supplied it. The file manager's network browsing sits
on that same client library, so the desktop had a working backend and no
configuration for it to read.

The properties held here are that the package ships a configuration at the
path the client library reads, that the configuration actually parses, that it
is a CLIENT configuration — no shares, nothing offered away from this machine
— that it does not let the client speak the first version of the protocol, and
that the package manager treats it as a user-editable configuration file so a
person's edits survive an upgrade.

WHAT THEY DO NOT PROVE: that any share is reachable. Nothing here mounts or
browses a real server; that belongs to the desktop validation leg on a machine
with a reachable share.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / "packages/desktop/samba"
SHIPPED = RECIPE / "files/etc/samba/smb.conf"


def read_shipped() -> str:
    assert SHIPPED.is_file(), (
        f"samba ships no configuration at {SHIPPED.relative_to(REPO)} — the "
        f"path its own client library reads is /etc/samba/smb.conf"
    )
    return SHIPPED.read_text()


def sections(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            out.append(line[1:-1].strip().lower())
    return out


def settings(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].split(";", 1)[0].strip()
        if "=" not in line or line.startswith("["):
            continue
        key, value = line.split("=", 1)
        out[" ".join(key.split()).lower()] = value.strip()
    return out


def test_the_package_ships_the_file_its_client_library_reads():
    """The overlay path is /etc/samba/smb.conf, rooted at /, so the builder
    deploys it and the package owns it."""
    text = read_shipped()
    assert text.strip(), "the shipped configuration is empty"
    rel = SHIPPED.relative_to(RECIPE / "files")
    assert rel.as_posix() == "etc/samba/smb.conf", (
        f"the overlay must deploy to /etc/samba/smb.conf, not /{rel.as_posix()}"
    )


def test_the_upstream_example_is_still_installed_beside_it():
    """The example with every server option stays where it was; this change
    adds a file rather than replacing the reference."""
    build = (RECIPE / "build.sh").read_text()
    assert "examples/smb.conf.default" in build, (
        "build.sh no longer installs the upstream example — the new "
        "configuration is meant to sit BESIDE it, not replace it"
    )


def test_it_declares_no_shares_and_offers_nothing_away():
    text = read_shipped()
    names = sections(text)
    assert names == ["global"], (
        f"a client configuration declares only [global]; this one declares "
        f"{names}"
    )
    values = settings(text)
    for key in ("path", "read only", "writable", "browseable", "guest ok"):
        assert key not in values, (
            f"[global] carries the share setting {key!r} — a configuration "
            f"that offers nothing away has no share settings at all"
        )


def test_the_client_does_not_speak_the_first_version_of_the_protocol():
    """SMB1 is the version with the known-bad history; the shipped file states
    the floor rather than leaving it to whatever the build defaulted to."""
    values = settings(read_shipped())
    floor = values.get("client min protocol")
    assert floor is not None, (
        "the shipped configuration does not state `client min protocol`, so "
        "the floor is whatever the build happened to default to"
    )
    assert floor.upper().startswith("SMB2") or floor.upper().startswith("SMB3"), (
        f"client min protocol = {floor} allows the first version of the "
        f"protocol; the floor must be SMB2 or higher"
    )


@pytest.mark.skipif(shutil.which("testparm") is None,
                    reason="testparm is part of samba and is not installed on this machine")
def test_samba_itself_can_load_the_shipped_configuration(tmp_path):
    """The real consumer: samba's own parser, run against the shipped bytes.

    `testparm -s` is what the error message on the installed system tells a
    person to run, so it is the tool that decides whether this file answers
    the complaint.
    """
    conf = tmp_path / "smb.conf"
    conf.write_text(read_shipped())
    proc = subprocess.run(
        ["testparm", "-s", "--suppress-prompt", str(conf)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        f"testparm -s exited {proc.returncode} against the shipped "
        f"configuration\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "Error loading services" not in (proc.stdout + proc.stderr), (
        f"testparm reported a load error:\n{proc.stdout}\n{proc.stderr}"
    )


def test_the_package_manager_protects_a_persons_edits_to_it(tmp_path):
    """Run the real classifier, not an assertion about the path string.

    pkm protects every /etc/* path a package ships: if the live file differs
    from the recorded baseline it is excluded from the deploy and the new
    stock is written beside it. This drives that code with the samba path.
    """
    import sys
    sys.path.insert(0, str(REPO))
    from pkm.configprotect import prepare_config_protection

    rel = "etc/samba/smb.conf"
    staging = tmp_path / "staging"
    live = tmp_path / "live"
    (staging / "etc/samba").mkdir(parents=True)
    (live / "etc/samba").mkdir(parents=True)
    (staging / rel).write_text(read_shipped())
    (live / rel).write_text("[global]\n\tworkgroup = EDITED-BY-THE-USER\n")

    class _Db:
        """Records a baseline that the live file no longer matches — the
        shape of a person having edited their own configuration."""
        def get_original_checksum(self, path):
            return "0" * 64

    plan = prepare_config_protection(staging, [rel], live, _Db())
    assert rel in plan["protect"], (
        f"pkm would overwrite a person's edited {rel}; the classifier "
        f"returned {plan}"
    )
    assert any(dest.endswith("smb.conf.pkmnew") for _, dest in plan["pkmnew_writes"]), (
        f"no .pkmnew sidecar is planned for {rel}: {plan['pkmnew_writes']}"
    )
    assert (live / rel).read_text() == "[global]\n\tworkgroup = EDITED-BY-THE-USER\n", (
        "the classifier must not touch the live file"
    )


@pytest.mark.skipif(shutil.which("testparm") is None,
                    reason="testparm is part of samba and is not installed on this machine")
def test_samba_reads_back_the_two_security_values_the_file_states(tmp_path):
    """Ask samba itself what the shipped file means, setting by setting.

    A configuration file is a request; the value that matters is the one
    samba resolves. `testparm --parameter-name` prints exactly that, on
    stdout, with its own banner and warnings on stderr. This checks that the
    two settings which decide what the client will agree to on a network come
    back the way the file states them — a value samba silently replaced would
    otherwise ship looking correct — and that neither of them lands on a
    setting that gives the protection away.
    """
    conf = tmp_path / "smb.conf"
    conf.write_text(read_shipped())
    stated = settings(read_shipped())

    def resolved(name: str) -> str:
        proc = subprocess.run(
            ["testparm", "-s", "--suppress-prompt", f"--parameter-name={name}",
             str(conf)],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, (
            f"testparm could not resolve {name!r}: exited {proc.returncode}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        return proc.stdout.strip()

    for name in ("client min protocol", "client signing"):
        assert name in stated, (
            f"the shipped configuration no longer states {name!r}, so its "
            f"value becomes whatever the build defaults to"
        )
        assert resolved(name) == stated[name], (
            f"samba resolves {name} to {resolved(name)!r} while the shipped "
            f"file states {stated[name]!r} — the file does not say what the "
            f"machine will do"
        )

    floor = resolved("client min protocol").upper()
    assert floor.startswith(("SMB2", "SMB3")), (
        f"samba resolves the protocol floor to {floor}, which allows the "
        f"first version of the protocol"
    )
    signing = resolved("client signing").lower()
    assert signing not in ("disabled", "off", "no", "false"), (
        f"samba resolves client signing to {signing!r}, which stops the "
        f"client from offering signing at all"
    )
