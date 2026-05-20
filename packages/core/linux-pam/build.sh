#!/bin/bash
# Linux-PAM 1.7.2 — Pluggable Authentication Modules
# BLFS 13.0
# IMPORTANT: Shadow and Systemd must be reinstalled after this

configure() {
    set -e
    # Create test config for build
    install -v -m755 -d /etc/pam.d

    cat > /etc/pam.d/other << "EOF"
auth     required       pam_deny.so
account  required       pam_deny.so
password required       pam_deny.so
session  required       pam_deny.so
EOF

    mkdir -p build
    cd    build

    meson setup ..        \
      --prefix=/usr       \
      --libdir=/usr/lib   \
      --buildtype=release \
      -D docdir=/usr/share/doc/Linux-PAM-1.7.2
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ninja test
    # Remove test config
    rm -fv /etc/pam.d/other
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
    chmod -v 4755 "${DESTDIR}/usr/sbin/unix_chkpwd"
}

post_install() {
    set -e
    # Create PAM configuration files
    install -vdm755 /etc/pam.d

    # /etc/security configuration directory (faillock + pwquality + limits.d)
    install -vdm755 /etc/security
    install -vdm755 /etc/security/limits.d

    # /etc/security/faillock.conf — pam_faillock rate-limiting on auth attempts.
    # Operator-greenlit RHEL 9 baseline 2026-05-19: deny=5 (lock after 5 failures),
    # unlock_time=900 (15 minutes), even_deny_root (root subject to rate-limit too).
    # The PAM stack edits below reference the module by name only; values live
    # here as the single source of truth.
    cat > /etc/security/faillock.conf << "EOF"
# /etc/security/faillock.conf — pam_faillock configuration
# InterGenOS T0-4-B PAM-side closure (operator-greenlit RHEL 9 baseline 2026-05-19).
# Source-of-truth for faillock rate-limiting parameters.

# Lock account after this many consecutive failed authentication attempts.
deny = 5

# Auto-unlock after this many seconds.
unlock_time = 900

# Apply the rate-limit to root as well as regular users.
# Without this, root brute-force is rate-limit-exempt at the PAM layer.
even_deny_root

# Record lock state in this directory.
dir = /var/run/faillock
EOF

    # /etc/security/pwquality.conf — libpwquality password complexity policy.
    # Operator-greenlit RHEL 9 baseline 2026-05-19: minlen=12 dcredit=-1
    # ucredit=-1 lcredit=-1 ocredit=-1 (require >=1 digit + >=1 upper + >=1
    # lower + >=1 other character class; min length 12).
    cat > /etc/security/pwquality.conf << "EOF"
# /etc/security/pwquality.conf — libpwquality configuration
# InterGenOS T0-4-B PAM-side closure (operator-greenlit RHEL 9 baseline 2026-05-19).
# Source-of-truth for password complexity policy.

# Minimum password length.
minlen = 12

# Credit deduction per character class. Negative values are MINIMUMS:
# require at least this many characters of the class.
dcredit = -1
ucredit = -1
lcredit = -1
ocredit = -1

# Maximum number of consecutive identical characters.
maxrepeat = 3

# Reject passwords containing the username.
usercheck = 1

# Apply the policy to root as well as regular users.
enforce_for_root
EOF

    # /etc/security/limits.d/00-intergenos.conf — pam_limits per-user resource caps.
    # Operator-greenlit RHEL 9 + CIS-aligned baseline 2026-05-20:
    # - core 0 (suppress core dumps to prevent secret leakage; DISA STIG)
    # - maxlogins 10 (per-user concurrent login DoS mitigation; CIS 5.4.4)
    # - nproc 65535 (per-user process count; root unlimited per RHEL convention)
    # - nofile 65535 (per-user file descriptor cap)
    cat > /etc/security/limits.d/00-intergenos.conf << "EOF"
# /etc/security/limits.d/00-intergenos.conf — pam_limits.so per-user caps
# InterGenOS T0-4-B PAM-side closure (operator-greenlit RHEL 9 + CIS
# baseline 2026-05-20). Activated via pam_limits.so in system-session.

# Suppress core dumps system-wide.
# Security: prevents secret leakage via core files (DISA STIG RHEL-09-211045).
*    hard    core    0

# Per-user maximum concurrent logins.
# DoS mitigation; CIS 5.4.4 baseline.
*    hard    maxlogins    10

# Per-user process count.
# DoS mitigation; CIS 5.4.5 baseline. Root unlimited per RHEL convention.
*    soft    nproc    65535
*    hard    nproc    65535
root soft    nproc    unlimited
root hard    nproc    unlimited

# Per-user file descriptor cap.
*    soft    nofile    65535
*    hard    nofile    65535
EOF

    # PAM stack files (BLFS split-stack pattern: separate account/auth/session/
    # password). T0-4-B integrates pam_faillock + pam_pwquality + pam_limits
    # into the existing split-stack architecture; calling stacks (login, sshd,
    # GDM) already include each split file by name so the new modules apply
    # everywhere automatically.

    cat > /etc/pam.d/system-account << "EOF"
# Begin /etc/pam.d/system-account
# pam_faillock.so account: verifies the account is not currently locked.
account   required    pam_faillock.so
account   required    pam_unix.so
# End /etc/pam.d/system-account
EOF

    cat > /etc/pam.d/system-auth << "EOF"
# Begin /etc/pam.d/system-auth
# pam_faillock.so preauth: check lock state BEFORE pam_unix attempt.
# Locked accounts deny immediately; preauth on its own does not increment.
auth      required      pam_faillock.so preauth silent
# pam_unix.so sufficient: success short-circuits; failure falls through.
auth      sufficient    pam_unix.so try_first_pass
# pam_faillock.so authfail [default=die]: on pam_unix failure, increment
# the fail counter and exit with PAM_AUTH_ERR. Required for the lock to
# trip after `deny` failures.
auth      [default=die] pam_faillock.so authfail
# pam_faillock.so authsucc: on successful auth, reset the fail counter.
auth      sufficient    pam_faillock.so authsucc
# Catch-all deny.
auth      required      pam_deny.so
# End /etc/pam.d/system-auth
EOF

    cat > /etc/pam.d/system-session << "EOF"
# Begin /etc/pam.d/system-session
# pam_limits.so: enforce /etc/security/limits.conf + /etc/security/limits.d/*.
session   required    pam_limits.so
session   required    pam_unix.so
session   required    pam_loginuid.so
session   optional    pam_systemd.so
# End /etc/pam.d/system-session
EOF

    # systemd user session PAM config (required for GNOME/GDM)
    cat > /etc/pam.d/systemd-user << "EOF"
# Begin /etc/pam.d/systemd-user
account  required    pam_access.so
account  include     system-account
session  required    pam_env.so
session  required    pam_limits.so
session  required    pam_loginuid.so
session  optional    pam_keyinit.so force revoke
session  optional    pam_systemd.so
auth     required    pam_deny.so
password required    pam_deny.so
# End /etc/pam.d/systemd-user
EOF

    cat > /etc/pam.d/system-password << "EOF"
# Begin /etc/pam.d/system-password
# pam_pwquality.so requisite: enforce complexity policy from /etc/security/
# pwquality.conf BEFORE pam_unix performs the actual password change.
# requisite (not required) so failure short-circuits with no fall-through.
password  requisite   pam_pwquality.so retry=3 enforce_for_root
password  required    pam_unix.so       yescrypt shadow use_authtok
# End /etc/pam.d/system-password
EOF

    cat > /etc/pam.d/other << "EOF"
# Begin /etc/pam.d/other
auth        required        pam_warn.so
auth        required        pam_deny.so
account     required        pam_warn.so
account     required        pam_deny.so
password    required        pam_warn.so
password    required        pam_deny.so
session     required        pam_warn.so
session     required        pam_deny.so
# End /etc/pam.d/other
EOF
}
