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
