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


# Composite weak-password floor (audit rows C-050 + C-051): the
# installer-side mirrors MOK's 8-char floor at the GUI + backend entry
# points so the user cannot enter a sub-floor password at install and
# have the install fail late at PHASE_MOK. The PAM-side ships the
# stronger RHEL 9 baseline (libpwquality minlen=12 + complexity +
# faillock deny=5 unlock_time=900 even_deny_root) for the running
# system; the installer-side 8-char floor is a UX fail-fast that
# guarantees the install can at least reach PHASE_MOK without late
# failure. Upper bound 256 matches MOK's mokutil ceiling.
_PASSWORD_MIN_LEN = 8
_PASSWORD_MAX_LEN = 256


def validate_password(value, role="password"):
    """Validate a user-supplied password against the installer-side floor.

    Returns None if valid, or a human-readable error message if invalid.
    Frontends call this at user-input time to re-prompt on invalid
    entries; the backend defensively validates again before set_root_password
    and create_user invoke chpasswd, so a hand-edited install.yaml that
    bypasses the frontend cannot inject a sub-floor password.

    Args:
        value: the password string (typed at the GUI / TUI / loaded from yaml)
        role: a short label used in the error message ("user password",
              "root password") for human-readable re-prompt UX.

    Returns None on valid input. Note empty-string handling is the
    caller's responsibility — user.py on_next checks emptiness with a
    separate error message ("Both user and root passwords are required.")
    so this function only validates length on non-empty input. Callers
    that allow empty (e.g. MOK skip-enrollment) should short-circuit
    before calling.

    Examples:
        validate_password("hunter22")            -> None  # 8 chars
        validate_password("hunter2")             -> "user password ..."
        validate_password("a"*257)               -> "user password ..."
        validate_password("p4ssw0rd!!", "root password")  -> None
    """
    if not isinstance(value, str):
        return f"{role} must be a string"
    if len(value) < _PASSWORD_MIN_LEN:
        return (
            f"{role} must be at least {_PASSWORD_MIN_LEN} characters "
            f"(installer-side floor mirrors the MOK enrollment "
            f"requirement; running-system PAM enforces RHEL 9 baseline "
            f"libpwquality minlen=12 + complexity separately)"
        )
    if len(value) > _PASSWORD_MAX_LEN:
        return (
            f"{role} must be at most {_PASSWORD_MAX_LEN} characters "
            f"(matches mokutil ceiling)"
        )
    return None


def validate_mok_password(value):
    """Validate a MOK enrollment password against mokutil's constraints.

    Mirrors installer/backend/mok.py:write_mok_enrollment validation so
    the GUI can fail-fast at user-input time rather than have the
    install fail late at PHASE_MOK. Empty input is accepted (MOK
    enrollment is intentionally optional — leaving the field empty
    skips MOK enrollment per the on_next contract).

    mokutil stdin pipeline requires 8-256 chars + printable ASCII only;
    control chars (NUL, newline, CR, tab) break the two-line stdin pipe
    because mokutil reads two password lines separated by '\\n' and an
    embedded newline splits the password into two false reads. Non-ASCII
    is rejected because the user must re-type the password at MokManager
    on first boot and MokManager's keyboard scan does not handle non-ASCII
    reliably across firmware vendors.

    Examples:
        validate_mok_password("")                -> None  # MOK skip is valid
        validate_mok_password("hunter22")        -> None
        validate_mok_password("hunter2")         -> "MOK ... must be 8-256 ..."
        validate_mok_password("a"*257)           -> "MOK ... must be 8-256 ..."
        validate_mok_password("héllo123")        -> "MOK ... printable ASCII ..."
        validate_mok_password("foo\\nbar1")      -> "MOK ... printable ASCII ..."
    """
    if not isinstance(value, str):
        return "MOK enrollment password must be a string"
    if value == "":
        return None
    if not 8 <= len(value) <= 256:
        return (
            f"MOK enrollment password must be 8-256 characters "
            f"(got {len(value)}); leave the field empty to skip MOK "
            f"enrollment entirely"
        )
    if not all(32 <= ord(c) <= 126 for c in value):
        return (
            "MOK enrollment password must be printable ASCII only "
            "(no control chars, tabs, newlines, or non-ASCII — "
            "mokutil reads via stdin and the user must re-type at "
            "MokManager on first boot)"
        )
    return None
