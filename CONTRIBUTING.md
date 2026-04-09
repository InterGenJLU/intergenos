# Contributing to InterGenOS

Thanks for your interest in InterGenOS! This project is in active early development.

## The Prime Directive

Every contribution must serve the Prime Directive:

> *InterGenOS exists to put the user in control of their own machine. Every design decision, every default, every included component must serve this purpose: giving people a system they understand, can modify, and can trust.*

If a change adds complexity that doesn't serve the user, it's not welcome — regardless of how conventional it may be.

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](https://github.com/InterGenJLU/intergenos/issues) for bug reports and feature requests
- Include the package name, version, and build log output for build failures
- Check existing issues before creating a new one

### Code Contributions

InterGenOS is currently maintained by a single developer. If you'd like to contribute:

1. Open an issue first to discuss the change
2. Fork the repo and create a branch
3. Follow the existing code style (no surprises)
4. Test your changes — the build must remain reproducible
5. Submit a pull request with a clear description

### Package Templates

The most impactful contributions are package templates. Each package needs:

- `package.yml` — metadata, source URL, SHA256, dependencies, build style
- `build.sh` — bash functions: `configure()`, `build()`, `check()`, `do_install()`

Follow the BLFS 13.0 book for build instructions. Use `DESTDIR="$DESTDIR"` for all installs.

### Security Standards

InterGenOS is built to meet the security standards required by Anthropic's [Project Glasswing](https://anthropic.com/glasswing) AI-driven vulnerability discovery program. All contributions are subject to security review consistent with those standards.

Contributions that introduce any of the following will not be accepted:

- **Known vulnerabilities** — code patterns flagged by static analysis, dependency chains with published CVEs, or configurations that weaken the security posture of the system
- **Supply chain risks** — unverified source tarballs, missing or incorrect SHA256 checksums, dependencies fetched from untrusted origins
- **Privilege escalation vectors** — improper setuid usage, world-writable files in privileged paths, or unsafe default permissions
- **Feature regressions disguised as fixes** — disabling functionality or removing dependencies to work around build failures rather than resolving the root cause

If Glasswing flags an issue in your contribution, we will work with you to resolve it before merging. Security is not negotiable.

### What We Value

- **Research before implementing** — check the LFS/BLFS book, check how other distros handle it
- **Simplicity over cleverness** — if there's a simpler way, use it
- **Transparency** — no hidden behavior, no magic
- **Tested changes** — if you can't verify it works, don't submit it

## Development Setup

See the [README](README.md) for build system overview. The build requires:

- Ubuntu 24.04 (or equivalent) build host
- KVM/QEMU with libvirt for VM-based builds
- Python 3.12+ with PyYAML for the build system
- All LFS 13.0 host requirements (run `python3 scripts/host-check.py` to verify)

## License

By contributing, you agree that your contributions will be licensed under the
[GNU General Public License v3.0 or later](LICENSE).
