#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Fcron 3.4.0 — Periodical command scheduler
# BLFS 13.0

configure() {
    set -e
    # Fix doc paths
    find doc -type f -exec sed -i 's:/usr/local::g' {} \;

    ./configure --prefix=/usr        \
                --sysconfdir=/etc    \
                --localstatedir=/var \
                --without-sendmail   \
                --with-piddir=/run   \
                --with-boot-install=no
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    # Pre-create the fcron group+user at the canonical system uid/gid 22
    # (matching files/usr/lib/sysusers.d/fcron.conf) BEFORE `make install`.
    # fcron's install target runs script/user-group, which creates the
    # account with `useradd -c 'fcron' -g fcron fcron` — passing NO explicit
    # uid, so at this build stage it lands on the first free regular uid 1000
    # and collides with the live `intergenos` user (also uid 1000), breaking
    # GDM autologin / sudo / ssh on the booted system. script/user-group
    # checks existence first and skips creation when the account already
    # exists, so pre-creating at 22 pins the id correctly (idempotent: a
    # no-op if the build-system sysusers pass already created it).
    getent group  fcron >/dev/null 2>&1 || groupadd -g 22 fcron
    getent passwd fcron >/dev/null 2>&1 || useradd  -u 22 -g fcron -d /dev/null -s /bin/false -c "Fcron User" fcron

    make DESTDIR="$DESTDIR" install

    # Upstream's install target creates localstatedir/run as a side effect
    # even though the pid dir is configured to /run (--with-piddir). Never
    # ship var/run/ members: /var/run is a symlink to /run on installed
    # systems (base-files r9) and an archive dir member would materialize
    # it as a real dir at install time (split-brain runtime dirs).
    rm -rf "$DESTDIR/var/run"

    # PAM configuration ships as owned payload in /etc/pam.d/, one file per
    # service name (hook-contract wave: hooks may not write package-ownable
    # bytes; same shape as the `at` recipe's /etc/pam.d/atd).
    #
    # WHAT WAS WRONG. Upstream's `make install` writes its PAM configuration to
    # ${DESTDIR}/etc/pam.conf — the monolithic file Linux-PAM reads ONLY when
    # /etc/pam.d does not exist. This system always has /etc/pam.d, so those
    # bytes have never had any effect on any installed system, and upstream's
    # own install document warns that this is what happens. With no service
    # file, PAM falls through to /etc/pam.d/other — pam_warn plus pam_deny —
    # and refused every caller:
    #     pam_warn(fcrontab:auth): ... user=[<the installed user>]
    #     ERROR Could not authenticate user using PAM (7): Authentication failure
    # (measured on the R001.1 installed system, 2026-08-24). A package also has
    # no business owning a system-wide /etc/pam.conf, so it is removed from the
    # staging tree rather than shipped.
    #
    # WHAT THE STACKS SAY. `auth required pam_permit.so` keeps upstream's own
    # default and its reason: WHO MAY use fcron is decided by /etc/fcron.allow
    # and /etc/fcron.deny, not by a password challenge — and there is nobody to
    # challenge, because the daemon runs unattended and fcrontab is also run
    # from scripts. A prompting module here would make this package able to
    # block waiting for a person. The account stack is pam_unix, so a locked or
    # expired account is still refused; that check needs no input from anyone.
    rm -f "${DESTDIR}/etc/pam.conf"
    install -dm755 "${DESTDIR}/etc/pam.d"
    for _svc in fcron fcrontab; do
        cat > "${DESTDIR}/etc/pam.d/${_svc}" << EOF
# Begin /etc/pam.d/${_svc}
auth     required pam_permit.so
account  required pam_unix.so
session  required pam_unix.so
# End /etc/pam.d/${_svc}
EOF
        chmod 644 "${DESTDIR}/etc/pam.d/${_svc}"
    done

    # The authorization files decide who may edit a crontab, and fcrontab must
    # be able to READ them. It is setuid root and setgid fcron, and it drops
    # the root it was given before opening them, keeping only the fcron group
    # the setgid bit grants — so root:root left them unopenable and fcrontab
    # refused every user with
    #     ERROR could not open /etc/fcron.allow: Permission denied
    # (measured on the R001.1 installed system, 2026-08-24). The `fcron` group
    # has no members by design: the setgid bit is how the grant travels, not
    # membership. Mode stays 0640 — group-readable, never world-readable, since
    # these files name who may schedule work. Group ownership is restored in
    # post_install, because the deploy-extract path's data filter strips
    # uid/gid, the same reason the binaries and the spool are repaired there.
    #
    # These two arrive from upstream's `make install`; asserting them here
    # makes an upstream change that drops them a loud build failure instead of
    # a package that silently denies every user.
    for _authfile in fcron.allow fcron.deny; do
        if [ ! -f "${DESTDIR}/etc/${_authfile}" ]; then
            echo "ERROR: ${DESTDIR}/etc/${_authfile} absent after make install — upstream changed its install set; fcrontab authorizes nobody without it." >&2
            exit 1
        fi
        chmod 640 "${DESTDIR}/etc/${_authfile}"
    done

    # Set setuid + setgid bits — fcrontab needs setuid root + setgid fcron
    # for per-user crontab edit; fcrondyn needs setuid root for dynamic
    # tab manipulation. Modes 6755 + 4755 per BLFS 13.0 canonical. Must
    # be set here because tar-based deployment strips setuid/setgid bits
    # during extraction (pkm restores them from tarball metadata
    # post-extract; see pkm/installer.py:475-490). Ownership is set in
    # post_install on the live system because the PEP 706 data filter in
    # the deploy-extract path strips uid/gid.
    chmod 6755 "${DESTDIR}/usr/bin/fcrontab"
    chmod 4755 "${DESTDIR}/usr/bin/fcrondyn"

    # fcronsighup is the third privileged binary and the one the post_install
    # ownership restore was missing. Assert it staged, so a rename or a
    # dropped install target upstream is a loud build failure here rather
    # than a silent no-op in the hook on every installed system.
    if [ ! -f "${DESTDIR}/usr/bin/fcronsighup" ]; then
        echo "ERROR: ${DESTDIR}/usr/bin/fcronsighup absent after make install — upstream changed its install set; update do_install and post_install together." >&2
        exit 1
    fi

    # Shipped-as-payload set (hook-contract wave: hooks may not write
    # package-ownable bytes). Byte/mode-identical to what the retired
    # post_install blocks wrote on live targets.

    # run-parts script (live mode 755)
    cat > "${DESTDIR}/usr/bin/run-parts" << "RUNPARTS"
#!/bin/sh
# run-parts: Runs all scripts found in a directory.
set +e

if [ $# -lt 1 ]; then
  echo "Usage: run-parts <directory>"
  exit 1
fi

if [ ! -d $1 ]; then
  echo "Not a directory: $1"
  exit 1
fi

IGNORE_SUFFIXES="~ ^ , .bak .new .rpmsave .rpmorig .rpmnew .swp"

for SCRIPT in $1/* ; do
  if [ ! -f $SCRIPT ]; then continue; fi
  SKIP=false
  for SUFFIX in $IGNORE_SUFFIXES ; do
    if [ ! "$(basename $SCRIPT $SUFFIX)" = "$(basename $SCRIPT)" ]; then
      SKIP=true; break
    fi
  done
  if [ "$SKIP" = "true" ]; then continue; fi
  if [ -x $SCRIPT ]; then
    $SCRIPT || echo "$SCRIPT failed."
  fi
done
exit 0
RUNPARTS
    chmod 755 "${DESTDIR}/usr/bin/run-parts"

    # `crontab` compatibility name for fcrontab. fcron is this system's only
    # periodic scheduler, and applications look for the standard `crontab`
    # command rather than for a particular implementation: timeshift, for
    # one, checks for it by name at startup and refuses to run without it.
    # The symlink is what BLFS recommends for exactly this reason. It grants
    # nothing extra — it resolves to the same setuid-root, setgid-fcron
    # binary, under the same permissions.
    ln -sf fcrontab "${DESTDIR}/usr/bin/crontab"

    # Periodic job directories (live mode 754)
    install -dm754 "${DESTDIR}"/etc/cron.{hourly,daily,weekly,monthly}

    # System fcrontab seed (live mode 644). Spool dir staged 770 — the
    # upstream spool mode, and the same mode the post_install hook asserts
    # on the live system, so archive metadata and on-disk state agree.
    install -dm770 "${DESTDIR}/var/spool/fcron"
    cat > "${DESTDIR}/var/spool/fcron/systab.orig" << "EOF"
&bootrun 01 * * * * root run-parts /etc/cron.hourly
&bootrun 02 4 * * * root run-parts /etc/cron.daily
&bootrun 22 4 * * 0 root run-parts /etc/cron.weekly
&bootrun 42 4 1 * * root run-parts /etc/cron.monthly
EOF
    chmod 644 "${DESTDIR}/var/spool/fcron/systab.orig"
}

post_install() {
    set -e
    # Process this package's /usr/lib/sysusers.d/fcron.conf entry now
    # so the fcron user/group exist before the chown below resolves.
    systemd-sysusers /usr/lib/sysusers.d/fcron.conf
    # chown the privileged binaries so the setgid bit grants effective
    # gid fcron (which owns /var/spool/fcron).
    #
    # fcronsighup joined this list 2026-08-19: it is the third privileged
    # binary upstream installs, its archive records root:fcron mode 4710, and
    # an installed system measured root:root 4710 — the group was lost the
    # same way the other two lose their mode bits, because the deploy-extract
    # path's data filter strips uid/gid and this hook did not restore it for
    # this one binary. With group root the 0710 permission bits grant execute
    # to nobody but root, so the fcron group cannot signal the daemon at all.
    #
    # fcrontab is owned by fcron, NOT by root, and that is the whole reason it
    # can work. It runs setuid to its owner, drops to the invoking user to do
    # the work, and then asks to become uid 22 to touch the spool. Dropping
    # privilege that way also sets the SAVED uid, so the only uid it can ever
    # return to is the one it started as. Owned by root it started as 0, could
    # never reach 22, and refused every caller with
    #     ERROR could not change euid to 22: Operation not permitted
    # Owned by fcron it starts as 22, returns to 22, and the command succeeds —
    # measured both ways on the R001.1 installed system, 2026-08-24. This also
    # takes root out of the picture entirely for the user-facing tool, which is
    # the lower privilege of the two.
    #
    # fcrondyn and fcronsighup keep root:fcron: their archive records that
    # ownership, and neither was measured here (the daemon is not running on
    # the machine this was measured on, so nothing that reaches its socket
    # could be exercised). If either turns out to want the same treatment, it
    # wants the same measurement first.
    chown fcron:fcron /usr/bin/fcrontab
    chown root:fcron  /usr/bin/fcrondyn /usr/bin/fcronsighup
    # The chown above clears setuid/setgid on a regular file (kernel behavior,
    # even for root) — so this hook has been shipping fcrontab/fcrondyn INERT
    # on every install to date. Restore the modes AFTER the chown. Package-local
    # twin of the L29 staging-chokepoint strip; 6755 + 4755 per do_install,
    # 4710 for fcronsighup per upstream's own install mode.
    chmod 6755 /usr/bin/fcrontab
    chmod 4755 /usr/bin/fcrondyn
    chmod 4710 /usr/bin/fcronsighup

    # The authorization files lose their group the same way everything else in
    # this hook does — the deploy-extract path's data filter strips uid/gid, so
    # they land root:root and fcrontab, which drops root before reading them,
    # cannot open them at all. Restore root:fcron 0640; see do_install for the
    # measurement.
    for _authfile in /etc/fcron.allow /etc/fcron.deny; do
        if [ -f "$_authfile" ]; then
            chown root:fcron "$_authfile"
            chmod 640        "$_authfile"
        fi
    done

    # fcron silently rejects /etc/fcron.conf if owner/perms are wrong.
    # Upstream installs it root:root mode 600; fcron expects root:fcron 644.
    if [ -f /etc/fcron.conf ]; then
        chown root:fcron /etc/fcron.conf
        chmod 644       /etc/fcron.conf
    fi

    # Spool ownership: archive deployment strips uid/gid (the same PEP 706
    # filter as the binaries above), so /var/spool/fcron lands root:root —
    # and fcrontab's setgid-fcron grant (the whole point of the 6755 mode
    # above) cannot write user tabs into a root:root 755 spool. Restore the
    # upstream fcron:fcron 770 spool. Measured root:root on an installed
    # ge9b-12 system.
    chown fcron:fcron /var/spool/fcron
    chmod 770         /var/spool/fcron

    # No `systemctl enable` here. Whether fcron.service runs by default is decided in
    # intergenos-base-files' /usr/lib/systemd/system-preset/80-intergenos-enable.preset
    # and applied by the `systemctl preset-all` the image build and the installer
    # both run. The enable that used to sit here was reverted by that pass on
    # every fresh install, but survived on any machine where this package was
    # later reinstalled or upgraded, because nothing re-runs preset-all after a
    # package operation — so two systems from the same medium ended up with
    # different defaults for a reason neither file recorded. Decided 2026-08-19:
    # the preset files own this, on both paths. The work above this line is
    # ownership and mode repair, not enablement, and is unchanged.
}
