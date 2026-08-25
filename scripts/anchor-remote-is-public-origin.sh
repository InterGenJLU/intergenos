#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
# scripts/anchor-remote-is-public-origin.sh — decide, from a push's remote URL,
# whether that push is a real publication to the project's public repository.
#
# WHY THIS EXISTS (measured 2026-08-24, in this repository)
# --------------------------------------------------------
# The pre-push chain's TRACKER-anchor gate writes OUTWARD: it commits and pushes
# an anchor line into the private repository, recording that public master
# advanced to a given commit. It decided to do that from the ref name alone
# ("am I pushing refs/heads/master?"). A proof harness then made a throwaway
# clone of this repository, gave it a LOCAL BARE REMOTE, and pushed master at it
# to exercise an unrelated gate. The push was local and disposable; the anchor
# gate could not tell, and recorded a promotion that never happened. Undoing it
# took a correcting commit in the private repository.
#
# The remote NAME cannot answer this — any clone may name any remote "origin",
# and the throwaway clone did exactly that. Only the remote URL identifies the
# real publication target, and git hands it to a pre-push hook as $2.
#
# Matching is on the (host, owner, repository) triple after normalisation, never
# on a substring or a prefix. A prefix test would accept the PRIVATE repository
# (its path begins with the public one's), and a substring test would accept any
# host that merely contains the real host's name.
#
# Usage:
#   anchor-remote-is-public-origin.sh <remote-url>
#
# Exit codes — a refusal and an invocation error never share one, so a caller
# can tell "this is not the publication target" from "I was called wrongly":
#   0 — the URL IS the public publication target (normalised form on stdout)
#   1 — the URL is NOT the public publication target (reason on stderr)
#   2 — invocation error (no URL given)

set -uo pipefail

# The one publication target, pinned. Changing this changes what the project
# considers a real promotion, so it is a deliberate edit, not configuration.
readonly PUBLIC_ORIGIN="github.com/InterGenJLU/intergenos"

if [ $# -lt 1 ]; then
    echo "ERROR: usage: $(basename "$0") <remote-url>" >&2
    echo "  No URL was given. This is an invocation error, NOT a decision that" >&2
    echo "  the remote is unrecognised — the caller must not read it as one." >&2
    exit 2
fi

URL="$1"

if [ -z "$URL" ]; then
    echo "not the public origin: the remote URL is empty" >&2
    exit 1
fi

# Normalise every spelling git may hand us to "host/owner/repo".
#   scp-like:  git@github.com:InterGenJLU/intergenos.git
#   https:     https://github.com/InterGenJLU/intergenos.git
#   ssh://:    ssh://git@github.com/InterGenJLU/intergenos.git
#   git://:    git://github.com/InterGenJLU/intergenos.git
# A local path or file:// URL has no host component and normalises to something
# that cannot equal the pinned triple, so it falls through to the refusal.
normalise() {
    local u="$1"

    # A local filesystem path is never a publication target. Reject before any
    # parsing, so a path that happens to contain the origin string cannot be
    # coerced into the right shape.
    case "$u" in
        /*|./*|../*|~*) echo ""; return ;;
        file://*)       echo ""; return ;;
    esac

    # Strip a scheme, if any.
    case "$u" in
        *://*) u="${u#*://}" ;;
    esac

    # Strip userinfo (git@, user:token@ ...).
    u="${u#*@}"

    # scp-like syntax separates host from path with a colon; URL syntax with a
    # slash. Convert the first colon to a slash so one parser handles both.
    case "$u" in
        *:*) u="${u%%:*}/${u#*:}" ;;
    esac

    # Drop a trailing slash and a trailing .git, in that order.
    u="${u%/}"
    u="${u%.git}"
    u="${u%/}"

    echo "$u"
}

NORM="$(normalise "$URL")"

if [ -z "$NORM" ]; then
    echo "not the public origin: '$URL' is a local path or file URL, which is never a publication target" >&2
    exit 1
fi

# Whole-value equality on the normalised triple. Not a prefix, not a substring:
# the private repository's path begins with the public one's, and a host may be
# suffixed to look like the real one.
if [ "$NORM" = "$PUBLIC_ORIGIN" ]; then
    echo "$NORM"
    exit 0
fi

echo "not the public origin: '$URL' normalises to '$NORM', which is not '$PUBLIC_ORIGIN'" >&2
exit 1
