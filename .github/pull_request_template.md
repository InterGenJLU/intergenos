## Summary

<!-- What does this change do? -->

## User-Control Check

<!-- How does this change serve a system the user can understand, modify, and trust? Security is not first. It is only. -->

## Testing

<!-- How was this tested? Include build log excerpts if applicable. -->

## Checklist

- [ ] Follows existing code style
- [ ] Package templates have correct SHA256 checksums
- [ ] Build instructions match BLFS 13.0 (or document the upstream source)
- [ ] `DESTDIR="$DESTDIR"` used in all install commands
- [ ] No hardcoded usernames, paths, or package counts
- [ ] New `package.yml` declares `verify_paths:` (or `pending_acquisition:`)

### Gates that run on push

These are enforced automatically — a push that misses one is rejected, so it is
cheaper to check them here:

- [ ] Every commit carries a `Signed-off-by:` trailer (see [DCO.md](../DCO.md))
- [ ] Commit subjects follow Conventional Commits (`feat`, `fix`, `docs`,
      `refactor`, `test`, `chore`, `perf`, `infra`, `build`, `ci`, `revert`)
- [ ] Any file with more than 50 changed lines is named in the commit message
      body — or the commit carries `NO-GATE: <reason>` for a bulk-mechanical change
- [ ] Substantive commits assisted by an AI agent carry a `Co-Authored-By:`
      provenance trailer
- [ ] No internal project vocabulary, developer-host paths, or private-network
      addresses in the changed files **or in the commit messages**
