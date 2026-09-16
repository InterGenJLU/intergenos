"""Service-specific hardening must ship without removing daemon capabilities."""

import configparser
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[2]
UNIT_DIRECTORY = Path("usr/lib/systemd/system")
# Each service retains its own floor and its known compatibility boundaries.
CASES = [
    (
        'networkmanager', 'NetworkManager.service',
        {
            'PrivateTmp': 'true',
            'ProtectClock': 'true',
            'ProtectControlGroups': 'true',
            'ProtectKernelLogs': 'true',
            'RestrictRealtime': 'true',
            'RestrictSUIDSGID': 'true',
        },
        (
            'PrivateNetwork',
            'PrivateDevices',
            'ProtectKernelTunables',
        ),
    ),
    (
        'bluez', 'bluetooth.service',
        {
            'RestrictNamespaces': 'true',
            'RestrictSUIDSGID': 'true',
            'SystemCallArchitectures': 'native',
            'LockPersonality': 'true',
        },
        (
            'PrivateNetwork',
            'PrivateDevices',
        ),
    ),
    (
        'rtkit', 'rtkit-daemon.service',
        {
            'NoNewPrivileges': 'yes',
            'PrivateDevices': 'yes',
            'PrivateTmp': 'yes',
            'ProtectHome': 'yes',
            'ProtectSystem': 'strict',
            'ProtectControlGroups': 'yes',
            'ProtectKernelTunables': 'yes',
            'ProtectKernelModules': 'yes',
            'RestrictAddressFamilies': 'AF_UNIX',
        },
        (
            'RestrictRealtime',
            'PrivateUsers',
            'PrivatePIDs',
            'ProtectProc',
            'ProcSubset',
        ),
    ),
    (
        'switcheroo-control', 'switcheroo-control.service',
        {
            'ProtectSystem': 'strict',
            'ProtectControlGroups': 'yes',
            'ProtectHome': 'yes',
            'ProtectKernelModules': 'yes',
            'PrivateTmp': 'yes',
            'RestrictAddressFamilies': 'AF_UNIX AF_LOCAL AF_NETLINK',
            'MemoryDenyWriteExecute': 'yes',
            'RestrictRealtime': 'yes',
        },
        (
            'ProtectKernelTunables',
            'PrivateDevices',
            'ProcSubset',
        ),
    ),
    (
        'udisks2', 'udisks2.service',
        {
            'ProtectHostname': 'true',
            'RestrictRealtime': 'true',
        },
        (
            'PrivateTmp',
            'PrivateDevices',
            'PrivateMounts',
            'ProtectSystem',
            'ProtectHome',
            'ProtectKernelTunables',
            'ProtectControlGroups',
            'ProtectKernelLogs',
            'ReadOnlyPaths',
            'ReadWritePaths',
            'InaccessiblePaths',
            'BindPaths',
            'BindReadOnlyPaths',
            'TemporaryFileSystem',
            'RootDirectory',
            'RootImage',
        ),
    ),
]


def dropin(package, unit):
    relative = UNIT_DIRECTORY / (unit + ".d") / "20-hardening.conf"
    return REPO / "packages/desktop" / package / "files" / relative, relative


def service_settings(path):
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    with path.open() as stream:
        parser.read_file(stream)
    assert parser.sections() == ["Service"]
    return dict(parser["Service"])


@pytest.mark.parametrize("package,unit,minimum,forbidden", CASES)
def test_hardening_dropin_is_shipped(package, unit, minimum, forbidden):
    path, relative = dropin(package, unit)
    assert path.is_file() and not path.is_symlink(), str(path)
    manifest = yaml.safe_load((REPO / "packages/desktop" / package / "package.yml").read_text())
    assert "/" + relative.as_posix() in manifest["verify_paths"]


@pytest.mark.parametrize("package,unit,minimum,forbidden", CASES)
def test_service_specific_minimum(package, unit, minimum, forbidden):
    path, _ = dropin(package, unit)
    settings = service_settings(path)
    for key, value in minimum.items():
        assert settings.get(key) == value, (package, key)


@pytest.mark.parametrize("package,unit,minimum,forbidden", CASES)
def test_hardening_preserves_daemon_interfaces(package, unit, minimum, forbidden):
    path, _ = dropin(package, unit)
    settings = service_settings(path)
    # Startup, identity and upstream capability lists remain in the vendor unit.
    reserved = {"ExecStart", "ExecStop", "ExecReload", "User", "Group", "CapabilityBoundingSet"}
    assert not settings.keys() & (reserved | set(forbidden)), package
