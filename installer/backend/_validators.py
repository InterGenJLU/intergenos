"""Input validators for installer-backend boundary surfaces.

Single source of truth for grammar checks that cross-cut frontend (TUI / GUI)
and backend (yaml-loaded values). Frontends call these at user-input time
to re-prompt on invalid entries; the orchestrator validates yaml-loaded
values defensively even after frontend validation, so a hand-edited
install.yaml that bypasses the frontend cannot inject bad values into
downstream config-generation steps.
"""

import re

# RFC 1123 single-label hostname: 1-63 chars, alphanumeric + hyphens, must
# start and end with alphanumeric. Excludes shell metacharacters and
# /etc/hosts injection vectors (newline / semicolon / hash etc.). FQDN
# with dots is rejected intentionally — InterGenOS hostname is single-label;
# users add domain search separately if needed.
_HOSTNAME_RE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$')

# POSIX-portable username grammar (per IEEE Std 1003.1-2017 §3.437 + §3.282):
# first char lower-letter or underscore, remaining lower-letter / digit /
# underscore / hyphen, optional trailing dollar sign (for samba machine
# accounts — historically allowed but excluded here, see below). Linux is
# more permissive in practice, but useradd's default policy and most
# distro conventions follow the POSIX shape.
_USERNAME_RE = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')

# Names that conflict with system accounts on a fresh InterGenOS install.
# These are either always present (`root`, `daemon`, `nobody`) or are
# created by core packages we ship (`systemd-*`, `messagebus`, `polkitd`,
# `gdm`, `geoclue`, `colord`, `cups`, `nm-openvpn`, etc.). The list
# intentionally errs broad — a user wanting `cups` as their unprivileged
# account name is fixing their workflow, not catching a typo.
_RESERVED_USERNAMES = frozenset({
    "root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail",
    "news", "uucp", "proxy", "www-data", "backup", "list", "irc", "gnats",
    "nobody",
    "systemd-network", "systemd-resolve", "systemd-timesync",
    "systemd-coredump", "systemd-journal", "systemd-journal-remote",
    "systemd-journal-upload", "systemd-oom",
    "messagebus", "polkitd", "gdm", "geoclue", "colord", "cups",
    "rtkit", "avahi", "rpc", "rpcuser", "nm-openvpn", "nm-openconnect",
    "tss", "saned", "sshd", "uuidd", "pulse", "_apt", "tcpdump",
    "intergen",
})


def validate_hostname(value):
    """Validate a single-label hostname value.

    Returns None if valid, or a human-readable error message if invalid.
    Frontends use the error message verbatim in re-prompt UX; the
    orchestrator surfaces it inside its aggregate ValueError.

    Examples:
        validate_hostname("intergenos") -> None
        validate_hostname("foo-bar")    -> None
        validate_hostname("a")          -> None
        validate_hostname("")           -> "hostname must be 1-63 characters"
        validate_hostname("a"*64)       -> "hostname must be 1-63 characters"
        validate_hostname("foo.bar")    -> "hostname must contain only ..."
        validate_hostname("-foo")       -> "hostname cannot start or end ..."
        validate_hostname("foo\\nbar")  -> "hostname must contain only ..."
    """
    if not isinstance(value, str) or not value:
        return "hostname must be 1-63 characters"
    if len(value) > 63:
        return "hostname must be 1-63 characters"
    if not _HOSTNAME_RE.fullmatch(value):
        if value[0] == '-' or value[-1] == '-':
            return "hostname cannot start or end with a hyphen"
        return (
            "hostname must contain only letters, digits, and hyphens "
            "(no dots, spaces, or special characters)"
        )
    return None


def validate_username(value):
    """Validate a Linux username.

    Returns None if valid, or a human-readable error message if invalid.
    Three rejection classes:
      - grammar (`_USERNAME_RE`): catches uppercase, leading digit/hyphen,
        and shell-unsafe characters (`:`, `$`, `\\n`, `/`, etc.) that would
        corrupt `/etc/passwd` or escape to the shell layer.
      - length: 1-32 chars matching useradd/login.defs `LOGIN_NAME_MAX`
        on glibc systems.
      - reserved (`_RESERVED_USERNAMES`): system accounts that exist or
        will be created during install.

    Examples:
        validate_username("ethan")     -> None
        validate_username("alice_b")   -> None
        validate_username("root")      -> "username 'root' is reserved ..."
        validate_username("Alice")     -> "username must start with a ..."
        validate_username("1user")     -> "username must start with a ..."
        validate_username("a:b")       -> "username must start with a ..."
        validate_username("")          -> "username must be 1-32 characters"
        validate_username("a"*33)      -> "username must be 1-32 characters"
    """
    if not isinstance(value, str) or not value:
        return "username must be 1-32 characters"
    if len(value) > 32:
        return "username must be 1-32 characters"
    if value.lower() in _RESERVED_USERNAMES:
        return (
            f"username '{value}' is reserved for a system account. "
            "Pick a different name."
        )
    if not _USERNAME_RE.fullmatch(value):
        return (
            "username must start with a lowercase letter or underscore "
            "and contain only lowercase letters, digits, underscores, "
            "and hyphens (no uppercase, dots, spaces, or special characters)"
        )
    return None
