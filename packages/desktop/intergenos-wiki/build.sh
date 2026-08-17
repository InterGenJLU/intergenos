# SPDX-License-Identifier: GPL-3.0-or-later
# intergenos-wiki — install the rendered wiki HTML tree + the operator-signed
# per-page sha256 manifest that InterGen verifies before citing a page.
#
# The generated source tarball (scripts/build-intergenos-source-tarballs.sh ::
# build_intergenos_wiki) is extracted with the top dir stripped, so cwd here holds
# ./book (the rendered mdBook HTML), ./pages-manifest.json, and its detached
# ./pages-manifest.json.asc. All three ship read-only under /usr/share/doc so the
# assistant can never rewrite its own citation source (same AI-immutable posture
# as the howto corpus under /usr/share/intergen).

do_install() {
    set -e
    local docroot="${DESTDIR}/usr/share/doc/intergenos/wiki"
    install -dm755 "${docroot}"
    # The rendered book/ tree (HTML pages + css/fonts/images) preserving layout.
    cp -a ./book/. "${docroot}/"
    # The signed page manifest + detached signature, sibling to the pages they
    # pin. intergen.wiki_citations reads both from this directory.
    install -m644 ./pages-manifest.json "${docroot}/pages-manifest.json"
    install -m644 ./pages-manifest.json.asc "${docroot}/pages-manifest.json.asc"
    chown -R root:root "${docroot}"
}
