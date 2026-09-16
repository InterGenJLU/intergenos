# SPDX-License-Identifier: GPL-3.0-or-later
"""Installed file-capability restoration is target-side and dependency-complete."""

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CONSUMERS = (
    ("packages/desktop/gnome-keyring", "/usr/bin/gnome-keyring-daemon", "cap_ipc_lock+ep"),
    ("packages/core/inetutils", "/usr/bin/ping6", "cap_net_raw+ep"),
)


def _function(text: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(?P<body>.*?)^\}}$", text, re.MULTILINE | re.DOTALL)
    assert match, f"{name}() is absent or not in the canonical hook shape"
    return match.group("body")


def _manifest(relative: str) -> dict:
    return yaml.safe_load((ROOT / relative / "package.yml").read_text())


def test_capabilities_are_restored_and_read_back_only_after_deploy():
    for relative, target, capability in CONSUMERS:
        text = (ROOT / relative / "build.sh").read_text()
        staged = _function(text, "do_install")
        deployed = _function(text, "post_install")
        assert "/usr/sbin/setcap" not in staged
        assert '_cap_root="${PKM_PACKAGE_ROOT:-/}"' in deployed
        assert f'_cap_target="${{_cap_root%/}}{target}"' in deployed
        assert f'/usr/sbin/setcap {capability} "$_cap_target"' in deployed
        assert '/usr/sbin/getcap "$_cap_target"' in deployed
        assert "for _cap_tool in /usr/sbin/setcap /usr/sbin/getcap" in deployed
        assert "is absent or not executable" in deployed
        expected = capability.replace("+", "=")
        readback = '_installed_cap=$(/usr/sbin/getcap "$_cap_target")'
        comparison = f'if [ "$_installed_cap" != "$_cap_target {expected}" ]; then'
        assert comparison in deployed
        assert deployed.index(f'if ! /usr/sbin/setcap {capability}') < deployed.index(readback)
        assert deployed.index(readback) < deployed.index(comparison)


def test_ping6_stages_without_setuid():
    text = (ROOT / "packages/core/inetutils/build.sh").read_text()
    staged = _function(text, "do_install")
    assert 'chmod 755 "${DESTDIR}/usr/bin/ping6"' in staged
    assert 'chmod 4755 "${DESTDIR}/usr/bin/ping6"' not in staged
    assert "ping6 remains setuid" not in staged.lower()
    assert "ping6 uses a raw socket and receives only cap_net_raw+ep" in staged
    declared = [
        line.split()[0]
        for line in (ROOT / "config/setuid-inventory.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "/usr/bin/ping6" not in declared


def test_consumers_declare_the_capability_tool_provider():
    for relative, target, _capability in CONSUMERS:
        manifest = _manifest(relative)
        runtime = manifest["dependencies"]["runtime"]
        assert runtime.count("libcap") == 1
        assert manifest["verify_paths"].count(target) == 1


def test_provider_verifies_both_programs_the_hooks_execute():
    paths = _manifest("packages/core/libcap")["verify_paths"]
    assert paths.count("/usr/sbin/setcap") == 1
    assert paths.count("/usr/sbin/getcap") == 1


def test_installed_gate_names_both_targets_and_exact_capabilities():
    gate = (ROOT / "tests/installed/test_gate_file_capabilities.py").read_text()
    for _relative, target, capability in CONSUMERS:
        assert target in gate
        assert capability.replace("+", "=") in gate
