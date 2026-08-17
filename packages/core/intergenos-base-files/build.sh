#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# intergenos-base-files 1.0.0 — InterGenOS canonical /etc + FHS skeleton +
# systemd preset + tmpfiles ownership.
# https://github.com/InterGenJLU/intergenos
#
# Installs the baseline /etc/* content + FHS top-level dirs + UsrMerge
# symlinks + systemd preset policy files + tmpfiles.d snippet that
# previously lived as heredoc-writes in scripts/chroot-build.sh +
# scripts/chroot-config-ch9.sh + scripts/create-image.sh. Class 11
# chroot-state-not-packaged canonical owner.
#
# Cross-distro analog: Fedora setup, Debian base-files + base-passwd,
# Arch filesystem, Void base-files, Gentoo baselayout. See the
# 2026-05-27 install-pipeline structural-defect research dossier in
# the InterGenOS private development repo.
#
# Build approach: no sources, no compilation. The files/ tree
# committed alongside this build.sh IS the canonical content. do_install
# walks it via cp -a then applies mode/symlink normalization. Same
# files/ tree is consumed by scripts/chroot-build.sh + scripts/chroot-
# config-ch9.sh during the build-chroot bootstrap phase (single source
# of truth — eliminates the drift hazard that produced install attempts
# #21-#22 first-boot cascading failures).
#
# Recipe shape mirrors packages/core/intergenos-keyring (build_style:
# custom, source: [], content shipped with the package dir).

PKG_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

build() {
    set -e
    # No compilation step.
    return 0
}

do_install() {
    set -e

    local files_dir="${PKG_DIR}/files"
    if [ ! -d "${files_dir}" ]; then
        echo "FATAL: package content tree missing at ${files_dir}" >&2
        return 1
    fi

    # Walk the files/ tree and copy verbatim into DESTDIR. cp -a
    # preserves mode + ownership + symlinks + timestamps.
    cp -av "${files_dir}/." "${DESTDIR}/"

    # Mode normalization for security-sensitive files.
    local skel="${DESTDIR}/usr/share/intergenos-base-files/account-skel"
    chmod 0640 "${skel}/shadow"
    chmod 0640 "${skel}/gshadow"
    chmod 0644 "${skel}/passwd"
    chmod 0644 "${skel}/group"
    chmod 0755 "${DESTDIR}/usr/lib/intergenos/seed-account-skel.sh"
    chmod 0755 "${DESTDIR}/usr/bin/lsb_release"
    chmod 0755 "${DESTDIR}/etc/profile.d/prompt.sh"

    # ---- Account databases are NOT deploy-target bytes (decided 2026-07-24) --
    # passwd/group/shadow/gshadow ship as REFERENCE data under
    # /usr/share/intergenos-base-files/account-skel/, never as /etc/* payload.
    # When they were ordinary payload, installing this package on a system that
    # had never had it deployed the pristine skeleton straight over the live
    # databases and every real account row was lost. pkm's config protection
    # now refuses that overwrite; this split is the other half — the bytes are
    # not aimed at /etc at all, so a protection regression cannot re-create the
    # loss. /usr/lib/intergenos/seed-account-skel.sh is the only path from the
    # skeleton to /etc, and it creates only what is missing.
    #
    # Fail-closed: if a future files/-tree edit puts an account database back
    # under etc/, HALT rather than ship an archive that can overwrite accounts.
    local _db
    for _db in passwd group shadow gshadow; do
        if [ -e "${DESTDIR}/etc/${_db}" ]; then
            echo "FATAL: base-files regression — etc/${_db} is in the archive payload." >&2
            echo "  Account databases ship as reference data under" >&2
            echo "  usr/share/intergenos-base-files/account-skel/ and reach /etc only via" >&2
            echo "  usr/lib/intergenos/seed-account-skel.sh. Deploying them directly" >&2
            echo "  overwrites live accounts (the 2026-07-23 loss)." >&2
            return 1
        fi
        if [ ! -f "${skel}/${_db}" ]; then
            echo "FATAL: base-files regression — account skeleton missing ${_db}" >&2
            return 1
        fi
    done

    # FHS root directory (mode 0750 per LFS 7.5.7).
    install -dm0750 "${DESTDIR}/root"

    # ---- FHS / LFS-7.5 directory skeleton (PI-Z22) -----------------------
    # scripts/chroot-build.sh §7.5 ("Creating Directories") builds the LFS
    # skeleton in the BUILD CHROOT, but Forge materializes installs from
    # package archives — so any skeleton directory that no archive carries was
    # simply ABSENT on installed systems (measured identically on two boxes:
    # /media, /media/cdrom, /usr/local/{bin,lib,sbin}, /etc/opt,
    # /var/{local,opt}, /mnt). base-files is the Class-11 canonical skeleton
    # owner, so it ships them. This mirrors the chroot-build.sh §7.5 list
    # EXACTLY (LFS-exact, Rule 13) with LFS modes, minus three lines owned
    # elsewhere (verified):
    #   - /lib/firmware        -> linux-firmware owns it (installs into
    #                             FIRMWAREDIR=/usr/lib/firmware); /lib is a
    #                             UsrMerge symlink so a /lib/firmware create
    #                             would write through it. Excluded.
    #   - /root                -> created above (LFS 7.5.7, mode 0750).
    install -dm0755 "${DESTDIR}"/{boot,home,mnt,opt,srv}
    install -dm0755 "${DESTDIR}"/etc/{opt,sysconfig}
    install -dm0755 "${DESTDIR}"/media/{floppy,cdrom}
    install -dm0755 "${DESTDIR}"/usr/{,local/}{include,src}
    install -dm0755 "${DESTDIR}"/usr/lib/locale
    install -dm0755 "${DESTDIR}"/usr/local/{bin,lib,sbin}
    install -dm0755 "${DESTDIR}"/usr/{,local/}share/{color,dict,doc,info,locale,man}
    install -dm0755 "${DESTDIR}"/usr/{,local/}share/{misc,terminfo,zoneinfo}
    install -dm0755 "${DESTDIR}"/usr/{,local/}share/man/man{1..8}
    install -dm0755 "${DESTDIR}"/var/{cache,local,log,mail,opt,spool}
    install -dm0755 "${DESTDIR}"/var/lib/{color,misc,locate}
    # Sticky world-writable temp dirs (LFS 7.5.7, mode 1777).
    install -dm1777 "${DESTDIR}"/tmp "${DESTDIR}"/var/tmp

    # ---- /var/run + /var/lock compat symlinks (r9) ------------------------
    # The build chroot gets these from chroot-build.sh (`ln -sfv /run
    # /var/run`, `ln -sfv /run/lock /var/lock`) but no archive shipped them,
    # so installed systems never got the symlinks: the first archive carrying
    # a var/run/ member mkdir'd a REAL directory, and the systemd-tmpfiles
    # `L` lines cannot replace a populated real dir at first boot. Result:
    # split-brain runtime dirs (/var/run/X vs /run/X) on every install —
    # measured 2026-07-17 (libvirt clients, pam faillock, samba, pkm.lock).
    # base-files installs FIRST (installer essentials order), so shipping the
    # symlinks here guarantees they exist before any other archive extracts;
    # a later dir member then extracts THROUGH the symlink harmlessly
    # (extraction preserves an existing dir-symlink — verified empirically).
    # Relative targets, same convention as the merged-usr compat symlinks
    # (Arch `filesystem` and Fedora `filesystem` ship exactly these two).
    ln -sfn ../run "${DESTDIR}/var/run"
    ln -sfn ../run/lock "${DESTDIR}/var/lock"

    # ---- Merged-usr compat skeleton — L27 ownership handoff (r7) ----------
    # base-files is now the SOLE package whose archive ships the merged-usr
    # compat skeleton to installed systems. The L27 durable fix (dev 866838c1)
    # makes BOTH builders PRUNE the seed-state skeleton — the bin/lib/sbin ->
    # usr/* compat symlinks + the lib64 dir the builder pre-seeds into every
    # DESTDIR so `make install` follows the live layout — from every OTHER
    # package's staging tree before manifest/archive capture, so evicting a
    # mirror-only package can no longer delete the chroot's compat symlinks
    # (the 908/913-archive over-capture that WAS L27). base-files, the Class-11
    # canonical skeleton owner (build-rules §2.7), is EXEMPT from that prune and
    # must therefore ship the skeleton EXPLICITLY.
    #   - the three symlinks (bin/lib/sbin -> usr/<name>) ride in files/ as
    #     git-native symlinks, preserved by the cp -a above.
    #   - /lib64 is an empty REAL directory created here (git cannot track an
    #     empty dir) — x86_64 only, matching the builder seed
    #     (scripts/pkg-functions.sh) EXACTLY: `mkdir lib64`, a real dir whose
    #     dynamic loader (ld-linux-x86-64.so.2) is glibc's, not ours.
    case "$(uname -m)" in
        x86_64) install -dm0755 "${DESTDIR}/lib64" ;;
    esac

    # /etc/bash.bashrc -> /etc/bashrc symlink. bash looks at bash.bashrc
    # for non-login interactive shells (e.g., GNOME Terminal); symlink
    # so both names resolve to the same content.
    ln -sf bashrc "${DESTDIR}/etc/bash.bashrc"

    # Root shell configs (mirrors /etc/skel pattern).
    cp -a "${DESTDIR}/etc/skel/.bashrc" "${DESTDIR}/root/.bashrc"
    cp -a "${DESTDIR}/etc/skel/.bash_profile" "${DESTDIR}/root/.bash_profile"

    # ---- Fail-closed skeleton assertion (r7 / L27 handoff) ----------------
    # base-files' archive is now the ONLY one shipping the merged-usr skeleton,
    # so a silent files/-tree regression that dropped a compat symlink would
    # ship a skeleton-LESS base-files and leave installed systems with no
    # /bin,/lib,/sbin — the L27 harm INVERTED (absent everywhere, instead of
    # over-captured). HALT the build here instead of shipping that. Verify what
    # the cp -a actually materialized in DESTDIR (readlink-exact per the same
    # `usr/<name>` target the builder seed + prune use).
    local _l _tgt
    for _l in bin lib sbin; do
        if [ ! -L "${DESTDIR}/${_l}" ]; then
            echo "FATAL: base-files skeleton regression — /${_l} is not a symlink in DESTDIR (the files/ tree lost the compat symlink)" >&2
            return 1
        fi
        _tgt="$(readlink "${DESTDIR}/${_l}")"
        if [ "${_tgt}" != "usr/${_l}" ]; then
            echo "FATAL: base-files skeleton regression — /${_l} -> ${_tgt}, expected usr/${_l}" >&2
            return 1
        fi
    done
    case "$(uname -m)" in
        x86_64)
            if [ ! -d "${DESTDIR}/lib64" ] || [ -L "${DESTDIR}/lib64" ]; then
                echo "FATAL: base-files skeleton regression — /lib64 is not a real directory in DESTDIR" >&2
                return 1
            fi
            ;;
    esac
}

post_install() {
    # Stub — no per-package post-install action. PHASE_SERVICES runs
    # the canonical `systemd-tmpfiles --create` + `systemctl preset-all`
    # invocations against the install target (see installer/backend/
    # users.py:enable_services). Per the R5 plan v2 decision — single
    # canonical invocation point rather than per-package duplication.
    return 0
}
