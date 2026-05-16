# `tier: extra` ISO/MIRROR Classification — v1.0 candidate

**Authority.** Owner-direct via internal dispatch (Item 2 of the 2026-05-16 four-item brief):
ISO ships basic-necessities + a few creature-comforts; **everything else is
`pkm install <name>` post-install.** Server-shaped packages are HELL NO on
the ISO by default. When in doubt, MIRROR.

**Inputs.** Every `packages/extra/<name>/package.yml` in tree at master
`51bcdfac` (102 packages). Current tier on each is `extra` (this doc does
not propose any `tier:` field changes — `tier:` is a build-graph concept
per `docs/package-tiers.md`; the proposed bucket here is a *shipping*
concept consumed by an upcoming `iso_include:` field).

**Output buckets.**
- **ISO** — package ships in the live/installed squashfs. Available
  immediately on first boot, no network required.
- **MIRROR** — package is built and signed in the build pipeline, published
  to the InterGenOS mirror (Item 1 of the same brief), available via `pkm install <name>`
  on demand from any installed system with network.

**Summary.** 11 ISO / 91 MIRROR.

The 11 ISO additions are: 1 web browser (`firefox`), 1 media player
(`mpv`) + its sole extra-tier runtime dep (`uchardet`), and 8 small
Rust-static CLI creature-comforts (`bat`, `bottom`, `dust`, `eza`, `fd`,
`ripgrep`, `tealdeer`, `zoxide`). The Rust CLI tools each link statically
via cargo and bring zero extra-tier libraries with them; `firefox` and
`mpv` only consume core/desktop deps besides `uchardet`.

The 91 MIRROR allocations are dominated by:
- 13 server daemons and DB engines (HELL NO category)
- 10 container-runtime / VM-networking infrastructure
- 6 third-party download helpers (Brave/Chrome/Discord/Edge/Spotify/VS Code/Claude Code)
- 6 desktop applications most users don't want by default (LibreOffice, GIMP, Inkscape, Audacity, Thunderbird, Rhythmbox)
- 4 less-universal CLI specialty tools (grex/just/lazygit/starship/hyperfine/tokei/sd/xh)
- ~50 libraries whose only consumers are MIRROR apps (per
  `docs/package-tiers.md`'s "library lives in tier of its consumer" rule,
  re-applied to the ISO/MIRROR axis: a library follows its earliest-shipped
  consumer, so when no consumer is ISO, the library is MIRROR)

---

## Classification table

Sorted alphabetically. `current_tier` is always `extra` (the input scope of
this audit). `proposed_bucket` is ISO or MIRROR.

| name | current_tier | proposed_bucket | rationale |
|---|---|---|---|
| aardvark-dns | extra | MIRROR | Authoritative DNS daemon (server). HELL NO default. |
| apache-httpd | extra | MIRROR | HTTP/HTTPS server daemon. HELL NO default. |
| appstream-glib | extra | MIRROR | AppStream metadata lib; consumers (software-center-shaped tools) all MIRROR. |
| apr | extra | MIRROR | Apache Portable Runtime; consumed only by apache-httpd (MIRROR). |
| apr-util | extra | MIRROR | APR utilities; consumed only by apache-httpd (MIRROR). |
| audacity | extra | MIRROR | Multi-track audio editor; specialty creative app, opt-in. |
| bat | extra | ISO | Rust-static CLI `cat` replacement; widely-expected creature-comfort. |
| bottom | extra | ISO | Rust-static graphical process monitor (`btm`); pairs with gnome-system-monitor for power users. |
| brave-helper | extra | MIRROR | Third-party download wrapper for Brave Browser; opt-in. |
| caddy | extra | MIRROR | HTTP/HTTPS server (single-binary). HELL NO default. |
| cairomm1 | extra | MIRROR | C++ binding for Cairo (GTK3 API); consumers all MIRROR. |
| catatonit | extra | MIRROR | Container init helper; consumed by podman stack (MIRROR). |
| celluloid | extra | MIRROR | GTK4 mpv frontend; mpv is ISO but GUI shell is opt-in. |
| chrome-helper | extra | MIRROR | Third-party download wrapper for Google Chrome; opt-in. |
| claude-code-helper | extra | MIRROR | Third-party download wrapper for Anthropic Claude Code CLI; opt-in. |
| clucene | extra | MIRROR | C++ Lucene port; consumed by libreoffice (MIRROR). |
| conmon | extra | MIRROR | OCI runtime monitor; consumed by podman stack (MIRROR). |
| containers-common | extra | MIRROR | Common config for Podman/Buildah/Skopeo (MIRROR). |
| cppunit | extra | MIRROR | C++ unit-test framework; developer tool. |
| crun | extra | MIRROR | OCI runtime; consumed by podman stack (MIRROR). |
| discord-helper | extra | MIRROR | Third-party download wrapper for Discord; opt-in. |
| dust | extra | ISO | Rust-static `du` replacement; widely-expected creature-comfort. |
| edge-helper | extra | MIRROR | Third-party download wrapper for Microsoft Edge; opt-in. |
| etcd | extra | MIRROR | Distributed KV-store daemon (cluster coordination). HELL NO default. |
| eza | extra | ISO | Rust-static `ls` replacement; widely-expected creature-comfort. |
| fd | extra | ISO | Rust-static `find` replacement; widely-expected creature-comfort. |
| firefox | extra | ISO | Web browser; modern desktop requires a default browser, and no other browser is in tree. |
| fuse-overlayfs | extra | MIRROR | FUSE overlay; consumed by rootless podman (MIRROR). |
| gc | extra | MIRROR | Boehm GC library; consumed only by MIRROR build-tools. |
| gflags | extra | MIRROR | Google C++ command-line-flags library; consumed only by MIRROR consumers. |
| gimp | extra | MIRROR | Image editor; specialty creative app, opt-in. |
| go-md2man | extra | MIRROR | Markdown→roff converter; developer tool. |
| gopls | extra | MIRROR | Go language server; developer tool. |
| grex | extra | MIRROR | Regex-generator CLI; specialty CLI. |
| gsl | extra | MIRROR | GNU Scientific Library; consumed by sci/extra apps. |
| gtkmm3 | extra | MIRROR | C++ binding for GTK3; consumed by MIRROR apps (audacity etc.). |
| haproxy | extra | MIRROR | TCP/HTTP load-balancer daemon. HELL NO default. |
| hugo | extra | MIRROR | Static-site generator; developer/specialty tool. |
| hyperfine | extra | MIRROR | Command-line benchmarking; specialty CLI. |
| influxdb | extra | MIRROR | Time-series DB daemon. HELL NO default. |
| inkscape | extra | MIRROR | Vector graphics editor; specialty creative app, opt-in. |
| jemalloc | extra | MIRROR | Alternate malloc; consumed by mariadb/rocksdb (MIRROR). |
| just | extra | MIRROR | Just (command runner); specialty/developer CLI. |
| lazygit | extra | MIRROR | Terminal git UI; specialty/developer CLI (git CLI already in core). |
| lego | extra | MIRROR | ACME client; companion to MIRROR web servers. |
| leveldb | extra | MIRROR | Embedded KV store; consumed by MIRROR consumers. |
| libatomic_ops | extra | MIRROR | Boehm-GC atomic-ops lib; consumed by MIRROR consumers. |
| libcdr | extra | MIRROR | CorelDRAW import filter; consumed by libreoffice (MIRROR). |
| libdeflate | extra | MIRROR | General DEFLATE library; no ISO consumer in extra/. |
| libdht | extra | MIRROR | BitTorrent DHT lib; consumed by transmission (MIRROR). |
| libid3tag | extra | MIRROR | ID3 tag manipulation; consumed by media-stack MIRROR apps (rhythmbox etc.). |
| libmypaint | extra | MIRROR | Brush engine; consumed by gimp/MyPaint (MIRROR). |
| libnatpmp | extra | MIRROR | NAT-PMP client; consumed by transmission (MIRROR). |
| libpeas | extra | MIRROR | GObject plugin system; consumed by MIRROR consumers (rhythmbox etc.). |
| libreoffice | extra | MIRROR | Office productivity suite; specialty app, opt-in. |
| librevenge | extra | MIRROR | Document-import base lib; consumed by libreoffice (MIRROR). |
| libslirp | extra | MIRROR | User-mode VM networking; consumed by qemu/podman (MIRROR). |
| liburing | extra | MIRROR | io_uring wrapper; consumed by mariadb/rocksdb (MIRROR). |
| libvisio | extra | MIRROR | Visio import filter; consumed by libreoffice (MIRROR). |
| libwpd | extra | MIRROR | WordPerfect import filter; consumed by libreoffice (MIRROR). |
| libwpg | extra | MIRROR | WordPerfect Graphics filter; consumed by libreoffice (MIRROR). |
| libxcrypt-compat | extra | MIRROR | libcrypt.so.1 LSB-compat ABI; legacy-binary install opt-in. |
| lighttpd | extra | MIRROR | Lightweight HTTP server daemon. HELL NO default. |
| mariadb | extra | MIRROR | Relational DB server. HELL NO default. |
| memcached | extra | MIRROR | In-memory KV cache daemon. HELL NO default. |
| miniupnpc | extra | MIRROR | UPnP IGD client; consumed by transmission (MIRROR). |
| mpv | extra | ISO | Media player CLI/desktop; basic creature-comfort, expected default. |
| mypaint-brushes | extra | MIRROR | Brush data files for libmypaint; consumed by gimp (MIRROR). |
| ncurses-compat | extra | MIRROR | libncurses.so.5 LSB-compat ABI; legacy-binary install opt-in. |
| netavark | extra | MIRROR | Container network plugin; consumed by podman stack (MIRROR). |
| nginx | extra | MIRROR | HTTP server / reverse proxy / load balancer. HELL NO default. |
| numpy | extra | MIRROR | Python scientific computing; specialty/developer dep. |
| opusfile | extra | MIRROR | Opus-in-Ogg decoder; mpv links libopus directly (core), no ISO consumer for opusfile. |
| pangomm1 | extra | MIRROR | C++ binding for Pango (GTK3); consumed by MIRROR consumers. |
| passt | extra | MIRROR | User-mode VM networking; consumed by podman stack (MIRROR). |
| perl-archive-zip | extra | MIRROR | Perl Zip module; no ISO consumer. |
| podman | extra | MIRROR | Container/pod management tool. HELL NO default. |
| portaudio | extra | MIRROR | Portable audio I/O; consumed by audacity (MIRROR). |
| portmidi | extra | MIRROR | Portable MIDI I/O; consumed by audacity (MIRROR). |
| postgresql | extra | MIRROR | Relational DB server. HELL NO default. |
| potrace | extra | MIRROR | Bitmap→vector tool; consumed by inkscape (MIRROR). |
| rapidjson | extra | MIRROR | Header-only C++ JSON; consumed by MIRROR consumers. |
| rhythmbox | extra | MIRROR | GNOME music player; gnome-music (desktop tier) covers the minimal case. |
| ripgrep | extra | ISO | Rust-static `grep` replacement; widely-expected creature-comfort. |
| rocksdb | extra | MIRROR | Embedded LSM-tree KV; consumed by MIRROR consumers. |
| scons | extra | MIRROR | Python build tool; developer tool. |
| sd | extra | MIRROR | sed-replacement CLI; less universal than ripgrep/fd, opt-in. |
| snappy | extra | MIRROR | Fast compression lib; consumed by leveldb/rocksdb/mariadb (MIRROR). |
| spotify-helper | extra | MIRROR | Third-party download wrapper for Spotify; opt-in. |
| starship | extra | MIRROR | Shell prompt; default shell prompt is a global UX choice, opt-in. |
| tealdeer | extra | ISO | Rust-static tldr client; widely-used creature-comfort man-page alternative. |
| thunderbird | extra | MIRROR | Email/news client; specialty/communication app, opt-in. |
| tokei | extra | MIRROR | LoC counter; specialty/developer CLI. |
| transmission | extra | MIRROR | BitTorrent client (daemon + GUI). HELL NO default for the daemon component. |
| uchardet | extra | ISO | Charset-detection lib; **runtime dep of mpv (ISO)**. Multi-consumer → earliest tier (also consumed by libreoffice MIRROR). |
| unixodbc | extra | MIRROR | ODBC implementation; consumed by libreoffice/mariadb (MIRROR). |
| valkey | extra | MIRROR | Redis-wire-compatible in-memory KV daemon. HELL NO default. |
| vscode-helper | extra | MIRROR | Third-party download wrapper for Microsoft Visual Studio Code; opt-in. |
| wxwidgets | extra | MIRROR | Cross-platform C++ GUI toolkit; consumed by audacity (MIRROR). |
| xh | extra | MIRROR | HTTP-request CLI; specialty CLI, curl already in core. |
| yajl | extra | MIRROR | C JSON library; consumed by podman stack (MIRROR). |
| zoxide | extra | ISO | Rust-static `cd` replacement; widely-expected creature-comfort. |

---

## Counts by bucket

```
ISO:    11
MIRROR: 91
TOTAL:  102
```

## ISO list at a glance

```
bat
bottom
dust
eza
fd
firefox
mpv
ripgrep
tealdeer
uchardet
zoxide
```

---

## Borderline decisions worth flagging for owner review

1. **`firefox` → ISO.** The only browser in tree. GNOME's Epiphany /
   GNOME-Web is not packaged. If owner wants the live ISO to ship a
   browser at all, `firefox` is the only candidate. Owner can ratify or
   redirect (e.g., "ship no browser by default; add Epiphany to
   `desktop/` later").

2. **`mpv` → ISO.** Media-playback is a basic creature-comfort on a
   modern desktop. The alternative is "no audio/video playback on the
   live ISO," which would feel broken for users testing the live mode.
   `gnome-music` (in `desktop/`) covers the minimum for the music case
   but not for video.

3. **`uchardet` → ISO** (pulled in by `mpv`). Library, not a user-facing
   app, but ISO consumers force ISO placement per the package-tiers doc
   convention.

4. **`bat`/`bottom`/`dust`/`eza`/`fd`/`ripgrep`/`tealdeer`/`zoxide` → ISO.**
   All Rust-static, ~5-10 MB each, no extra/ libraries pulled along.
   These are widely-expected on modern Linux desktops in 2026 and pair
   well with GNOME's text editor / terminal already shipping in
   `desktop/`. The grouping is the most-defensible subset of "modern
   CLI creature-comforts" — every one is a drop-in user-friendly
   replacement for a coreutils binary that's already on the system, not
   a specialty/developer tool.

5. **`starship` → MIRROR.** Plausible counter-position: ship as ISO
   creature-comfort. Held back because the default shell prompt is a
   global UX decision owner should ratify explicitly before defaulting,
   and `starship` ships disabled-by-default unless `/etc/skel/.bashrc`
   activates it. If owner ratifies "yes, ship starship enabled by
   default," promote to ISO and update default skel.

6. **`lazygit` → MIRROR.** Plausible counter-position: ship as ISO for
   developer audience. Held back because the ISO target is a desktop
   live image for general users; developers will `pkm install lazygit`.

7. **`transmission` → MIRROR (whole package).** The package bundles a
   daemon, CLI utils, AND a GTK4 GUI. The daemon component triggers the
   "HELL NO default" rule; rather than partial-ship (GUI without daemon)
   the simpler call is MIRROR for the whole package. Owner can split
   into `transmission-gtk` (ISO) + `transmission` (MIRROR daemon)
   downstream if desired.

---

## Downstream consumption

Build-pipeline work that follows from this list (per the Item 2 brief):

1. Adds an `iso_include: true|false` field to each `package.yml` in
   `packages/extra/`. Default `false` for any package not on the ISO
   list above.
2. Updates `scripts/chroot-build-extra.sh` (or the squashfs-assembly
   path) to filter on `iso_include: true` when populating the
   `tier: extra` portion of the live squashfs.
3. The mirror-publish script (Item 1 of the same brief) signs and uploads ALL 102 packages
   (ISO + MIRROR alike) — the ISO bucket means "also pre-installed,"
   not "exclusive of the mirror." A user who removed `firefox` from
   their installed system can `pkm install firefox` from the mirror,
   same as any other extra package.

---

## Open questions for owner ratification

- Browser default: ratify `firefox`, or redirect (no browser / Epiphany
  in desktop / Chromium added later)?
- Default shell prompt: ratify GNU bash default (starship MIRROR), or
  ship `starship` configured-on-by-default (starship ISO)?
- Transmission shape: leave bundled (MIRROR whole), or split daemon
  vs. GUI (GTK ISO + daemon MIRROR)?

---

Last updated: 2026-05-16 against master `51bcdfac`.
