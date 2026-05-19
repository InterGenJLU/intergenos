#!/bin/bash
# intergenos-helper-lib 1.0.0 — InterGenOS pkm install-helper API
# https://github.com/InterGenJLU/intergenos
#
# Installs /usr/share/igos/helpers/helper-lib.sh, the sourceable bash
# library install-helpers use to record their install footprint into
# /var/lib/igos/helpers/<name>.manifest. pkm reads the manifest on
# helper success and threads the file list through add_files /
# add_depends so pkm files/verify/remove work as users expect for
# helper-installed packages (chrome, vscode, edge, brave, discord,
# spotify, claude-code).
#
# Closes audit row H-007. See docs/architecture/helper-manifest-spec-v1.md
# for the schema + API contract.
#
# tier=core: the library is install-time infrastructure for the extra-
# tier helpers, so it must be present in any system that supports
# pkm install-helper. Helpers depend on it implicitly by sourcing
# /usr/share/igos/helpers/helper-lib.sh at their first line.

build() {
    set -e
    # No build step — pure-bash library, copied straight from the
    # source tree.
    return 0
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/igos/helpers"
    install -m644 \
        "${IGOS_SOURCE_ROOT:-/mnt/intergenos}/packages/core/intergenos-helper-lib/helper-lib.sh" \
        "${DESTDIR}/usr/share/igos/helpers/helper-lib.sh"

    # Defensive assert: the installed library exports the documented
    # API. If a future edit accidentally renames a function or drops a
    # required helper, this halts the build rather than shipping a
    # broken helper-lib that helpers will fail on at runtime. The API
    # set MUST stay in lock-step with docs/architecture/helper-manifest-
    # spec-v1.md (broken with intent goes via SUPERSEDES per RFC §11,
    # not via silent removal).
    REQUIRED_FUNCTIONS=(
        igos_helper_init
        igos_helper_set_version
        igos_helper_record_file
        igos_helper_record_symlink
        igos_helper_record_dep
        igos_helper_record_post_install_action
        igos_helper_commit
    )
    for fn in "${REQUIRED_FUNCTIONS[@]}"; do
        if ! grep -q "^${fn}()" "${DESTDIR}/usr/share/igos/helpers/helper-lib.sh"; then
            echo "FATAL: intergenos-helper-lib is missing required API function: $fn" >&2
            echo "Edit packages/core/intergenos-helper-lib/helper-lib.sh to restore it, OR" >&2
            echo "land a SUPERSEDES path per RFC §11 if the change is intentional." >&2
            exit 1
        fi
    done

    # Create the runtime state directory pkm reads helper manifests from.
    install -dm755 "${DESTDIR}/var/lib/igos/helpers"
}
