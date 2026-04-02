#!/bin/bash
# Fcron 3.4.0 — Periodical command scheduler
# BLFS 13.0

configure() {
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
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}

post_install() {
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

    systemctl enable fcron
}
