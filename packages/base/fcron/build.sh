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
    chown root:fcron /usr/bin/fcrontab /usr/bin/fcrondyn /usr/bin/fcronsighup
    # The chown above clears setuid/setgid on a regular file (kernel behavior,
    # even for root) — so this hook has been shipping fcrontab/fcrondyn INERT
    # on every install to date. Restore the modes AFTER the chown. Package-local
    # twin of the L29 staging-chokepoint strip; 6755 + 4755 per do_install,
    # 4710 for fcronsighup per upstream's own install mode.
    chmod 6755 /usr/bin/fcrontab
    chmod 4755 /usr/bin/fcrondyn
    chmod 4710 /usr/bin/fcronsighup

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
