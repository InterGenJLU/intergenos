"""The greeter cannot route a login onto a PAM module this system does not ship.

This covers BOTH services whose upstream stacks name a module no recipe here
builds: gdm-smartcard and gdm-fingerprint. They are the same defect, and the
assertions below are parametrized over both rather than duplicated, so a third
such service is one list entry away from being covered.

WHAT HAPPENED. The lfs PAM configuration that the display-manager recipe builds
installs /etc/pam.d/gdm-smartcard containing
`auth required pam_pkcs11.so wait_for_card card_only`. That module is not built
here. The supporting smartcard stack IS built and running, so inserting a
PKCS#11 token was enough for the greeter to switch authentication to that
service — and PAM then marked the stack faulty because the required module could
not be loaded. Authentication could not succeed by any means, and the greeter
displayed nothing to explain it: the owner of the machine could not log in until
the token was physically removed.

Measured on a real installed system, authenticating against the shipped stack
with the correct password: `pam_authenticate` returned 28, "Module is unknown".
With the replacement stack, the same password returned 0, and a wrong password
returned 6 — so the replacement authenticates rather than permitting.

The fingerprint service is the same shape and was measured the same way:
pam_fprintd.so is absent from every security directory, nothing answers the
fprintd bus name, and authenticating against the shipped stack with the correct
password returned 28, "Module is unknown". The replacement returned 0, and a
wrong password returned 6. It has not locked anyone out yet only because the
greeter offers fingerprint when fprintd answers on the system bus and nothing
does — a dependency on an absence, which is why it is fixed rather than watched.

TWO INDEPENDENT LAYERS, and this file asserts both, because a fix that depends
on one setting being right is one setting away from the same lockout:

  1. the PAM stack itself cannot name a module that is not installed;
  2. the greeter's own setting for selecting that path is off.

Deleting the stack instead of replacing it was considered and rejected on
evidence: /etc/pam.d/other is pam_warn + pam_deny, so an absent service produces
the same lockout with a different cause.

Nothing here reads the network, needs privilege, or writes inside the tree.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GDM_BUILD = REPO_ROOT / "packages" / "desktop" / "gdm" / "build.sh"
GREETER_DCONF = (REPO_ROOT / "packages" / "core" / "intergenos-default-settings"
                 / "assets" / "gdm.d" / "00-intergenos-greeter")

# Modules that no recipe in this tree builds. A PAM stack we ship must not
# require one: PAM fails the whole stack when a required module is missing, and
# the greeter reports nothing.
UNSHIPPED_PAM_MODULES = ("pam_pkcs11.so", "pam_fprintd.so", "pam_sss.so")

# The gdm PAM services whose upstream stacks require a module this tree does
# not build, and the greeter key that stops each being selected. The two
# lists are positional partners and a test below asserts they stay aligned.
SERVICES = ("gdm-smartcard", "gdm-fingerprint")
GREETER_KEYS = ("enable-smartcard-authentication",
                "enable-fingerprint-authentication")


def _shipped_stack(service: str) -> str:
    """The named gdm PAM stack exactly as the recipe installs it."""
    text = GDM_BUILD.read_text()
    m = re.search(
        rf"^# Begin /etc/pam\.d/{re.escape(service)}$.*?^# End /etc/pam\.d/{re.escape(service)}$",
        text, re.M | re.S)
    assert m, (f"packages/desktop/gdm/build.sh no longer installs a {service} "
               f"PAM stack. If that is deliberate, note that /etc/pam.d/other is "
               f"pam_deny, so an ABSENT stack locks the owner out exactly as the "
               f"unshipped-module stack did.")
    return m.group(0)


def _active_lines(stack: str) -> str:
    """The stack's RULES — comment lines removed.

    The file explains in its own header why the upstream module is not used, and
    naming that module is the whole point of the explanation. A check that reads
    the comments as configuration would force the file to be silent about the
    defect it exists to prevent, which is how the next reader ends up
    re-introducing it.
    """
    return "\n".join(l for l in stack.splitlines() if not l.lstrip().startswith("#"))


@pytest.mark.parametrize("service", SERVICES)
def test_recipe_replaces_the_upstream_stack(service):
    """The recipe must overwrite the installed file, not leave upstream's."""
    text = GDM_BUILD.read_text()
    target = f"$DESTDIR/etc/pam.d/{service}"
    assert target in text, (
        f"the recipe does not install its own /etc/pam.d/{service}, so "
        f"upstream's unshipped-module stack is what lands on installed systems")
    # It has to happen after the upstream install, or upstream overwrites it.
    assert text.index("ninja install") < text.index(target), (
        f"the {service} replacement is written BEFORE `ninja install`, which "
        f"then overwrites it with upstream's stack")


@pytest.mark.parametrize("service", SERVICES)
def test_shipped_stack_names_no_unshipped_module(service):
    stack = _active_lines(_shipped_stack(service))
    for module in UNSHIPPED_PAM_MODULES:
        assert module not in stack, (
            f"the shipped {service} stack requires {module}, which no recipe "
            f"in this tree builds. PAM fails the whole stack when a required "
            f"module is missing, so a login routed here cannot succeed and the "
            f"greeter says nothing about why.")


@pytest.mark.parametrize("service", SERVICES)
def test_shipped_stack_actually_authenticates(service):
    """It must perform real authentication — not permit, and not deny.

    A stack that cannot lock anyone out because it lets everyone in would pass
    the test above and be far worse than the defect it replaced.
    """
    stack = _active_lines(_shipped_stack(service))
    assert re.search(r"^auth\s+include\s+system-auth\b", stack, re.M), (
        f"the {service} replacement does not include system-auth, so it is not "
        f"performing the password authentication it claims to")
    assert "pam_permit.so" not in stack, (
        f"the {service} replacement contains pam_permit.so — it would admit anyone")
    assert re.search(r"^account\s+include\s+system-account\b", stack, re.M), (
        f"the {service} replacement does not run account management, so a "
        f"disabled or expired account would still be admitted")


@pytest.mark.parametrize("key", GREETER_KEYS)
def test_greeter_default_turns_the_path_off(key):
    """The second layer: the greeter must not select the path at all."""
    text = GREETER_DCONF.read_text()
    assert "[org/gnome/login-screen]" in text, (
        "the greeter database no longer carries a login-screen section")
    assert re.search(rf"^{re.escape(key)}=false\s*$", text, re.M), (
        f"the greeter database does not set {key}=false. The upstream default "
        f"is true, and a greeter that selects a login path with no module "
        f"behind it cannot authenticate anyone routed to it.")


def test_both_layers_cover_the_same_set_of_services():
    """Neither layer may cover a service the other does not.

    The two layers are deliberately independent so that neither has to be right
    for login to work. That argument only holds while they cover the SAME
    services — a stack replaced with no matching greeter key, or a greeter key
    with no matching stack, is one layer, not two.
    """
    assert len(SERVICES) == len(GREETER_KEYS), (
        f"{len(SERVICES)} PAM stacks are replaced but {len(GREETER_KEYS)} "
        f"greeter keys are set; every replaced stack needs its greeter key and "
        f"vice versa")
    for service, key in zip(SERVICES, GREETER_KEYS):
        method = service.removeprefix("gdm-")
        assert method in key, (
            f"stack {service} and greeter key {key} do not describe the same "
            f"login method")
