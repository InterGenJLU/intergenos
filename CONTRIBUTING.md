# Contributing to InterGenOS

Thanks for your interest in InterGenOS! This project is in active development.

## The Prime Directive

Every contribution must serve the Prime Directive:

> *InterGenOS exists to put the user in control of their own machine. Every design decision, every default, every included component must serve this purpose: giving people a system they understand, can modify, and can trust.*

If a change adds complexity that doesn't serve the user, it's not welcome - regardless of how conventional it may be.

## Quick Links

- **Want to use InterGenOS, not contribute to it?** Read the [Getting Started Guide](docs/getting-started.md).
- **Want a deeper understanding of the system?** Read the [Contributor Guide](docs/contributor-guide.md).
- **How does the build system work?** See the [Architecture Overview](docs/architecture.md).
- **How does the package manager work?** Read the [PKM internals documentation](docs/components/pkm.md) (coming soon, see the pkm directory for source).

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](https://github.com/InterGenJLU/intergenos/issues) for bug reports and feature requests
- Include the package name, version, and build log output for build failures
- Check existing issues before creating a new one
- For security vulnerabilities, please refer to our [Security Policy](SECURITY.md)

### Code Contributions

InterGenOS is currently maintained by a single developer. If you'd like to contribute:

1. Open an issue first to discuss the change
2. Fork the repo and create a branch
3. Follow the existing code style (no surprises)
4. Test your changes - the build must remain reproducible
5. Submit a pull request with a clear description

### Package Templates

The most impactful contributions are package templates. Each package needs:

- package.yml - metadata, source URL, SHA256, dependencies, build style
- uild.sh - bash functions: configure(), uild(), check(), do_install()

Follow the BLFS 13.0 book for build instructions. Use DESTDIR="" for all installs.

### Security Standards

InterGenOS is built for hostile-network resilience: the project assumes adversaries have superhuman vulnerability-discovery capability and treats every package, configuration, and code change as potentially within their reach. All contributions are reviewed against current best-practice vulnerability-discovery tooling before merging.

Contributions that introduce any of the following will not be accepted:

- **Known vulnerabilities** - code patterns flagged by static analysis, dependency chains with published CVEs, or configurations that weaken the security posture of the system
- **Supply chain risks** - unverified source tarballs, missing or incorrect SHA256 checksums, dependencies fetched from untrusted origins
- **Privilege escalation vectors** - improper setuid usage, world-writable files in privileged paths, or unsafe default permissions
- **Feature regressions disguised as fixes** - disabling functionality or removing dependencies to work around build failures rather than resolving the root cause

If our review surfaces an issue in your contribution, we will work with you to resolve it before merging. Security is not negotiable. For full details on our response times and disclosure timelines, read our [Security Policy](SECURITY.md).

#### Public Content Audit

All pull requests to master are scanned by an automated public-content audit
(.github/workflows/public-content-audit.yml). This check ensures that
internal development artifacts - agent attribution names, developer-host paths,
memory-file references, and credential-like strings - do not enter the public
repository. If the audit fails on your PR, amend the flagged files and push
again. Intentional uses of flagged terms that are legitimate public content can
be added to scripts/check-public-content.allowlist.

### What We Value

- **Research before implementing** - check the LFS/BLFS book, check how other distros handle it
- **Simplicity over cleverness** - if there's a simpler way, use it
- **Transparency** - no hidden behavior, no magic
- **Tested changes** - if you can't verify it works, don't submit it

## Development Setup

See the [README](README.md) for build system overview. The build requires:

- Ubuntu 24.04 (or equivalent) build host
- KVM/QEMU with libvirt for VM-based builds
- Python 3.12+ with PyYAML for the build system
- All LFS 13.0 host requirements (run python3 scripts/host-check.py to verify)

## License and Sign-Off

InterGenOS contributions are licensed under the
[GNU General Public License v3.0 or later](LICENSE) — the project's
outbound license. Contributions are accepted under this same license
(inbound = outbound; no separate Contributor License Agreement).

The contributor authorship and right-to-submit attestation is the
**Developer Certificate of Origin (DCO) version 1.1**. By appending
a `Signed-off-by:` trailer to each commit, you certify the DCO 1.1
statements (see [DCO.md](DCO.md) for the verbatim text and full
context).

```sh
git commit -s
```

(or `--signoff`) adds the trailer automatically using your `user.name`
and `user.email` git configuration. Every commit in a pull request
must carry the trailer; pull requests whose commits are not all
signed off will fail the public-content + DCO audit workflow and
cannot merge until the trailer is added.

To add the trailer to existing commits retroactively:

```sh
git rebase --signoff main      # all commits on this branch
git commit --amend --signoff   # the latest commit only
```

Pre-existing commits in the repository (those made before
**2026-05-18**, when the DCO was adopted) are grandfathered under
the prior implicit-CLA model; the DCO applies from that date
forward.
