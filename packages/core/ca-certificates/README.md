# ca-certificates — Mozilla NSS root cert bundle

## Why the source is `file:///`

This package ships the Mozilla NSS root certificate bundle extracted into
PEM form. Upstream (curl.se) serves a single file — `cacert.pem` — not a
tarball. Our build pipeline expects a tarball that extracts into a working
directory with the canonical inner layout (`<name>-<version>/<content>`).

To bridge that gap, we wrap `cacert.pem` in a tarball ourselves and pin
the wrapped artifact in `package.yml` via a `file:///` URL. The wrapped
tarball lives in tree at `build/sources/ca-certificates-<version>.tar.gz`
(selectively un-gitignored — see top-level `.gitignore`).

## Source-of-truth

- Upstream PEM: <https://curl.se/ca/cacert.pem> (always-latest, no dated
  snapshots; curl.se re-extracts from Mozilla NSS on every Mozilla release)
- Upstream sha256: <https://curl.se/ca/cacert.pem.sha256> (curl.se publishes
  this alongside the PEM for verification)
- License: MPL-2.0 (same as Mozilla NSS source)

## Refresh procedure (maintainer-paced)

Refresh is a version bump, same cadence as any other package. Mozilla
typically rotates roots every ~3 months. The pinned blob stays valid until
the maintainer decides to bump:

```sh
# 1. Set the new version (typically today's UTC date in YYYY.MM.DD form).
NEW_VER=$(date -u +%Y.%m.%d)
INNER_VER="${NEW_VER//./-}"

# 2. Stage the upstream PEM (verify against curl.se's published sha256).
STAGE=$(mktemp -d) && cd "$STAGE"
curl -fSL --proto '=https' -o cacert.pem    https://curl.se/ca/cacert.pem
curl -fSL --proto '=https' -o cacert.pem.sha256 https://curl.se/ca/cacert.pem.sha256
# curl.se's .sha256 file is "<hash>  cacert.pem"; verify in place.
sha256sum -c <(awk '{print $1 "  cacert.pem"}' cacert.pem.sha256)

# 3. Wrap in the canonical inner layout. (Inner dir uses HYPHENS in place
#    of the version's DOTS — convention preserved from the original capture.)
mkdir "ca-certificates-${INNER_VER}"
mv cacert.pem "ca-certificates-${INNER_VER}/"

# 4. Tar with deterministic flags so byte-identical regen is achievable.
SDE=$(git -C /mnt/intergenos log -1 --format=%ct)
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime="@${SDE}" \
    -cf - "ca-certificates-${INNER_VER}" \
    | gzip --no-name -9 \
    > /mnt/intergenos/build/sources/ca-certificates-${NEW_VER}.tar.gz

# 5. Remove the stale tarball (different filename for different version).
rm -f /mnt/intergenos/build/sources/ca-certificates-<OLD_VER>.tar.gz

# 6. Update package.yml: bump version + paste new sha256.
sha256sum /mnt/intergenos/build/sources/ca-certificates-${NEW_VER}.tar.gz

# 7. git diff + commit.
```

## User-facing refresh (installed system)

End users refresh their root cert bundle through the normal pkm package
flow — `pkm update ca-certificates` after the maintainer publishes the new
version. Same model as every other package.

## Provenance audit trail

The inner `cacert.pem` of any committed tarball is bit-identical to what
curl.se served at capture time. To independently verify a committed
tarball:

```sh
tar -xzf build/sources/ca-certificates-<version>.tar.gz -O \
    ca-certificates-<inner-version>/cacert.pem \
    | sha256sum
```

Compare to the sha256 noted in the commit message that landed the version
bump (and against `curl.se/ca/cacert.pem.sha256` if curl.se still serves
the matching snapshot — they only ship "current," so older snapshots are
only verifiable against the historical commit message).
