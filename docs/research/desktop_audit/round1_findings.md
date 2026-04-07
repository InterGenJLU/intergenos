# Round 1 Findings — Crypto, Audio, Image (39 packages)

## ERRORS (3)
- **pinentry**: Missing first sed command `sed -i "/FLTK 1/s/3/4/" configure`
- **libsamplerate**: Contains incorrect seds copied from libsndfile (will fail)
- **exiv2**: Missing cmake flags: -DEXIV2_ENABLE_VIDEO=yes, -DEXIV2_ENABLE_WEBREADY=yes, -DEXIV2_ENABLE_CURL=yes, -DEXIV2_BUILD_SAMPLES=no, -G Ninja; missing curl dep

## WARNINGS (6)
- **libgpg-error**: Missing --sysconfdir=/etc
- **npth**: package.yml has --disable-static but BLFS doesn't use it
- **libpwquality**: Python bindings not built (--disable-python-bindings)
- **opus**: Uses autotools but BLFS uses meson/ninja
- **sbc**: Missing --disable-tester flag
- **libwebp**: Missing --enable-libwebpextras and --enable-swap-16bit-csp

## INFO (8)
- libgcrypt, libassuan, gnupg2, cracklib: docs/build dir differences (acceptable)
- mpg123, libtiff, libexif, libavif: dep classification or minor omissions

## CLEAN (22)
- nettle, gnutls, libksba, libogg, libvorbis, flac, speex, lame, fdk-aac, cdparanoia, libsndfile
- nasm, libpng, libjpeg-turbo, giflib, pixman, lcms2, openjpeg2, gexiv2, highway, libjxl

# Round 2 Findings — Video, Misc Libs 1&2 (46 packages)

## ERRORS (2)
- **libva**: Uses --prefix=/usr instead of $XORG_PREFIX; BLFS requires Xorg env + libdrm
- **soundtouch**: Uses CMake but BLFS uses autotools (bootstrap + configure)

## WARNINGS (5)
- **x265**: Missing -D CMAKE_POLICY_VERSION_MINIMUM=3.5 and rm of static lib
- **svt-av1**: Missing -D CMAKE_SKIP_INSTALL_RPATH=ON -D BUILD_SHARED_LIBS=ON -W no-dev -G Ninja
- **libmnl**: Added --disable-static not in BLFS
- **libevdev**: BLFS uses $XORG_PREFIX; meson flags may not be applied from package.yml
- **hicolor-icon-theme**: Uses autotools but BLFS uses meson

## INFO (3)
- ffmpeg: minor test flag skip
- newt: GPM optional
- libfyaml, libFS: minor flag differences

## CLEAN (36)
- dav1d, libvpx, x264, libaom, libass, fribidi, graphite2, icu, libxml2, libxslt
- json-c, inih, brotli, libyaml, iso-codes, libndp, libnl, keyutils, libusb, libseccomp
- mtdev, hwdata, libevent, c-ares, lua, slang, rpcsvc-proto, fuse3, duktape, boost
- libpcap, libaio, cryptsetup, libwacom, libICE, libpcap

# Round 3 Findings — Python, DocBook, Xorg Foundation (46 packages)

## ERRORS (1)
- **lynx**: Missing --datadir flag and install-full post-install step per BLFS

## WARNINGS (4)
- **itstool**: Uses ./configure instead of ./autogen.sh per BLFS
- **doxygen**: Disables build_wizard when BLFS expects it enabled with Qt6
- **vala**: Disables valadoc when Graphviz is available
- **util-macros**: Minor package.yml vs build.sh flag inconsistency (build.sh correct)

## INFO (6)
- docutils: missing optional old-file cleanup
- pycairo, pygobject3: redundant --libdir
- cargo-c: libssh2 classification
- yelp-xsl: extra --libdir
- help2man, bash-completion: not core BLFS packages

## CLEAN (35)
- editables, pluggy, hatchling, pathspec, trove-classifiers, hatch-fancy-pypi-readme
- hatch-vcs, setuptools-scm, pygments, markdown, Mako, cython, lxml, attrs
- sgml-common, docbook-xml, docbook-xsl-nons, xmlto, aspell
- xorgproto, libXau, libXdmcp, xcb-proto, libxcb, xtrans, libX11, libXext
- libSM, libXt, libXmu, libXpm, libXaw, libfontenc, libXfont2

# Round 4 Findings — Xorg Libs 2, Wayland, XCB/Apps (50 packages)

## ERRORS (1)
- **libdmx**: NOT in BLFS 13.0 — cannot verify

## WARNINGS (16)
- **libXrender, libXrandr, libXtst, libXv, libXvMC, libXxf86dga, libXxf86vm, libXScrnSaver**: Missing --sysconfdir=/etc --localstatedir=/var in build.sh
- **xcb-util, xcb-util-image, xcb-util-keysyms, xcb-util-renderutil, xcb-util-wm, xcb-util-cursor, xbitmaps**: Missing --sysconfdir=/etc --localstatedir=/var
- **startup-notification**: Missing --sysconfdir=/etc --localstatedir=/var
- **libinput**: Missing -D udev-dir=/usr/lib/udev

## INFO (3)
- libXpresent, libxcvt, wayland-protocols: minor flag differences

## CLEAN (30)
- libXfixes, libXcomposite, libXcursor, libXdamage, libXft, libXi, libXinerama
- libpciaccess, libxkbfile, libxshmfence, libdrm, libdisplay-info, libepoxy, libxkbcommon
- wayland, xkeyboard-config, mkfontscale, xauth, xkbcomp, xrandr, xrdb, xset
- setxkbmap, xprop, xinput, xcursorgen, xcursor-themes, xdpyinfo, xdriinfo

# Round 5 Findings — Fonts, Rust/LLVM/Mesa, Cairo/GTK prereqs (45 packages)

## ERRORS (6)
- **iceauth**: Syntax error in build.sh (trailing backslash without continuation)
- **sessreg**: Syntax error in build.sh (trailing backslash without continuation)
- **font-util**: Syntax error in build.sh (trailing backslash without continuation)
- **font-alias**: Syntax error in build.sh (trailing backslash without continuation)
- **font-dejavu**: NOT in BLFS 13.0 (supplementary font)
- **xwayland**: Undocumented -Dxkb_dir parameter not in BLFS

## WARNINGS (6)
- **xev, xhost**: Missing --disable-static in configure
- **font-cursor-misc**: NOT in BLFS 13.0
- **glslang**: Duplicate cmake parameters (copy-paste error)
- **mesa**: Uses /usr instead of $XORG_PREFIX (architecture decision)
- **gdk-pixbuf**: Image loaders disabled (BLFS recommends enabled)

## INFO (6)
- xmodmap, xwininfo, smproxy: incomplete $XORG_CONFIG flags
- font-noto: NOT in BLFS (supplementary)
- cairo: optional backends not configured
- pycairo: redundant --libdir

## CLEAN (27)
- encodings, font-misc-misc, rust, llvm, cbindgen, rust-bindgen
- spirv-headers, spirv-tools, spirv-llvm-translator, libclc
- vulkan-headers, vulkan-loader, spidermonkey
- freetype2-pass1, harfbuzz, freetype2, fontconfig, woff2, pango
- json-glib, shared-mime-info, desktop-file-utils, libgudev, libxmlb
- librsvg, graphene, at-spi2-core, gsettings-desktop-schemas

## CIRCULAR DEPENDENCY: freetype2-pass1 → harfbuzz → freetype2 — CORRECTLY HANDLED

# Round 6 Findings — GTK/Audio, GStreamer/Net, Services (56 packages)

## ERRORS (0)

## WARNINGS (4)
- **gtk3**: -D man=false instead of BLFS man=true
- **gtk4**: Unnecessary build-testsuite/build-tests flags
- **libadwaita1**: Unnecessary -Dexamples=false and -Dtests=false
- **polkit**: man and test flags disabled vs BLFS enabled

## INFO (2)
- alsa-lib: configure_flags variant
- pipewire: minor quoting difference

## CLEAN (50)
- adwaita-icon-theme, libnotify, appstream, sassc, alsa-plugins, alsa-utils
- pulseaudio, wireplumber, sound-theme-freedesktop, libcanberra, gsound
- gstreamer, gst-plugins-base, gst-plugins-good, gst-plugins-bad
- glib-networking, libsoup3, iptables, wpa_supplicant, networkmanager
- geoclue2, libmbim, libqmi, modemmanager, cups, poppler, qpdf, mupdf
- libcupsfilters, libppd, cups-filters, accountsservice, upower
- power-profiles-daemon, lvm2, udisks2, libical, bluez, colord, libgusb
- colord-gtk, bubblewrap, xdg-dbus-proxy, xdg-user-dirs, xdg-desktop-portal
- xdg-desktop-portal-gtk, xdg-utils, libportal, perl-parse-yapp, samba

# Round 7 Findings — GNOME Libs, GNOME Core, Overflow (55 packages)

## ERRORS (1)
- **gnome-shell-extensions**: Missing libgtop dependency that BLFS requires

## WARNINGS (2)
- **gnome-terminal**: Uses libadwaita1 instead of BLFS libhandy1
- **gvfs**: Fewer backends disabled than BLFS suggests

## INFO (1)
- nautilus: gst-plugins-base as build dep (BLFS recommends, not requires)

## CLEAN (51)
- dconf, gjs, libsecret, gcr, gcr4, geocode-glib, libgweather
- gnome-online-accounts, gnome-desktop, vte, gnome-menus, gnome-autoar
- librest, totem-pl-parser, libhandy1, libpeas, libcloudproviders
- ruby, enchant, unifdef, gnome-settings-daemon, gnome-keyring
- libei, mutter, gnome-shell, gnome-session, gdm, gnome-tweaks
- gnome-control-center, gnome-bluetooth, gnome-backgrounds
- gnome-user-docs, ibus, libnma, libgtop, blueprint-compiler, tecla
- webkitgtk, webkitgtk-gtk3, evolution-data-server, localsearch
- mitkrb, protobuf, protobuf-c, abseil-cpp, libatasmart, libblockdev
- libbytesize, libnvme, libshumate, tinysparql
