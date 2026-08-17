# SHA-256 Refresh Workflow

When contributing new packages — especially bulk additions of many package
definitions at once — from a Windows host or a network environment that blocks
the source mirror (`[WinError 2]`), `download-sources.py` cannot fetch the
upstream tarballs and so cannot generate real checksums locally.

This document describes a two-host workflow: author the package definitions on
the restricted host using placeholder hashes, then refresh those hashes on an
unrestricted Linux host that can reach the sources. This lets the package
scaffolding pass the local preflight checks without waiting on network access.

## Workflow

### 1. Author on the restricted host (Windows or network-restricted)

- Write the `package.yml` and `build.sh` definitions for each new package.
- Set the `sha256:` field in each `source:` block to a string of sixty-four
  zeros (for example, `sha256: 0000000000000000000000000000000000000000000000000000000000000000`).
- For bulk additions, inject the placeholder programmatically. For example:

  ```python
  with open(yml_path) as f:
      content = f.read()
  content = content.replace("PLACEHOLDER", "0" * 64)
  with open(yml_path, "w") as f:
      f.write(content)
  ```

- Validate the definitions and confirm tier parsing with
  `python3 scripts/validate-package-tiers.py`, then push the branch. The
  preflight checks accept the 64-character placeholder as structurally valid,
  so they pass on the structure of the definition rather than the (not-yet-real)
  hash.

### 2. Refresh on an unrestricted host (Linux)

- Fetch the branch on a host that can reach the source mirror.
- Run the bulk checksum update for the affected tier, for example:

  ```bash
  python3 scripts/download-sources.py --tier extra --update-checksums
  ```

  The script fetches the real tarballs, computes their SHA-256 hashes, and
  rewrites the `package.yml` files in place.
- Commit the refreshed hashes as a separate commit (for example,
  `refresh sha256 placeholders`) and push it back to the branch.

### 3. Rebase on the restricted host

- The original contributor runs `git pull --rebase` to pull in the corrected
  hashes. If the refresh commit was squashed or cherry-picked upstream, the
  rebase drops the original placeholder commit cleanly.

This split workflow keeps local network restrictions from blocking the
authoring of new package definitions, while ensuring every package that lands
carries a real, verified SHA-256 checksum before it is built.
