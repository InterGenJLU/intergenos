#!/bin/bash
# Fcron 3.4.0 — Periodical command scheduler
# BLFS 13.0

configure() {
    set -e
    # Create fcron user/group
    groupadd -g 22 fcron 2>/dev/null || true
    useradd -d /dev/null -c "Fcron User" -g fcron -s /bin/false -u 22 fcron 2>/dev/null || true

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
    make DESTDIR="$DESTDIR" install

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
}

post_install() {
    set -e
    # Ensure fcron user/group exists on the live system + chown the
    # privileged binaries so the setgid bit grants effective gid fcron
    # (which owns /var/spool/fcron). Configure-stage groupadd/useradd
    # ran in the build chroot, not on the target.
    getent group fcron >/dev/null || groupadd -g 22 fcron
    getent passwd fcron >/dev/null || useradd -d /dev/null -c "Fcron User" -g fcron -s /bin/false -u 22 fcron
    chown root:fcron /usr/bin/fcrontab /usr/bin/fcrondyn

    # Create run-parts script
    cat > /usr/bin/run-parts << "RUNPARTS"
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
    chmod -v 755 /usr/bin/run-parts

    # Create periodic job directories
    install -vdm754 /etc/cron.{hourly,daily,weekly,monthly}

    # Create system fcrontab
    cat > /var/spool/fcron/systab.orig << "EOF"
&bootrun 01 * * * * root run-parts /etc/cron.hourly
&bootrun 02 4 * * * root run-parts /etc/cron.daily
&bootrun 22 4 * * 0 root run-parts /etc/cron.weekly
&bootrun 42 4 1 * * root run-parts /etc/cron.monthly
EOF

    # fcron silently rejects /etc/fcron.conf if owner/perms are wrong.
    # Upstream installs it root:root mode 600; fcron expects root:fcron 644.
    if [ -f /etc/fcron.conf ]; then
        chown root:fcron /etc/fcron.conf
        chmod 644       /etc/fcron.conf
    fi

    systemctl enable fcron
}
