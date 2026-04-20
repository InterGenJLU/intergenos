Dependency Audit (tier: desktop)
======================================================================
  Packages audited:          382
  Packages with gaps:        171
  Unmatched (no BLFS entry): 88

  Missing deps (to add):
    Intra-tier required:     61
    Intra-tier recommended:  119
    Intra-tier optional:     287
    Cross-tier (all types):  129
    TOTAL:                   596

  Skipped (docs/tests only): 86

  Mako (desktop):
    [required   ] [INTRA] pygobject3
    [recommended] [INTRA] at-spi2-core
    [optional   ] [INTRA] docbook-xsl
    [optional   ] [INTRA] libxslt
    [optional   ] [INTRA] lynx

  accountsservice (desktop):
    [recommended] [cross] glib2  (with GObject Introspection)
    [recommended] [cross] systemd  (runtime)
    [optional   ] [SKIP ] xmlto-0.0.29 (docs/tests only)

  adwaita-icon-theme (desktop):
    [required   ] [INTRA] gtk4

  alsa-plugins (desktop):
    [optional   ] [INTRA] ffmpeg
    [optional   ] [INTRA] libsamplerate
    [optional   ] [INTRA] pulseaudio
    [optional   ] [INTRA] speex

  alsa-utils (desktop):
    [optional   ] [INTRA] docutils
    [optional   ] [INTRA] libsamplerate
    [optional   ] [SKIP ] xmlto-0.0.29 (docs/tests only)

  appstream (desktop):
    [recommended] [INTRA] docbook-xsl

  at-spi2-core (desktop):
    [required   ] [INTRA] gsettings-desktop-schemas  (Runtime)
    [required   ] [cross] dbus
    [required   ] [cross] glib2  (GObject Introspection
                required for GNOME)

  avahi (desktop):
    [optional   ] [INTRA] libevent
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)

  blueprint-compiler (desktop):
    [recommended] [INTRA] at-spi2-core
    [optional   ] [INTRA] docbook-xsl
    [optional   ] [INTRA] libxslt
    [optional   ] [INTRA] lynx

  bluez (desktop):
    [recommended] [INTRA] docutils  (to generate man pages)
    [required   ] [cross] dbus
    [required   ] [cross] glib2

  boost (desktop):
    [optional   ] [INTRA] icu
    [recommended] [cross] which

  bubblewrap (desktop):
    [optional   ] [INTRA] libxslt  (to generate manual
                pages)
    [optional   ] [SKIP ] libseccomp-2.6.0 (docs/tests only)

  cairo (desktop):
    [optional   ] [INTRA] gs
    [optional   ] [INTRA] libdrm
    [optional   ] [INTRA] librsvg
    [optional   ] [INTRA] libxml2
    [optional   ] [INTRA] poppler
    [recommended] [cross] glib2  (required
                for most GUIs)

  colord (desktop):
    [optional   ] [INTRA] colord-gtk  (to build the example
                tools)
    [optional   ] [INTRA] gnome-desktop
    [required   ] [cross] dbus
    [required   ] [cross] glib2  (GObject Introspection
                recommended)
    [optional   ] [SKIP ] libxslt-1.1.45 (docs/tests only)

  colord-gtk (desktop):
    [optional   ] [INTRA] libxslt  (to build the man page)
    [recommended] [cross] glib2  (with GObject Introspection)

  cryptsetup (desktop):
    [optional   ] [INTRA] libpwquality

  cups (desktop):
    [recommended] [INTRA] colord
    [recommended] [INTRA] xdg-utils
    [optional   ] [INTRA] avahi

  cyrus-sasl (desktop):
    [optional   ] [INTRA] mitkrb
    [optional   ] [INTRA] openldap
    [optional   ] [cross] linux-pam

  cython (desktop):
    [required   ] [INTRA] pygobject3
    [recommended] [INTRA] at-spi2-core
    [optional   ] [INTRA] docbook-xsl
    [optional   ] [INTRA] libxslt
    [optional   ] [INTRA] lynx

  docbook-xsl-nons (desktop):
    [optional   ] [INTRA] libxslt  (or
                any other XSLT processor)
    [optional   ] [INTRA] ruby

  docutils (desktop):
    [required   ] [INTRA] pygobject3
    [recommended] [INTRA] at-spi2-core
    [optional   ] [INTRA] docbook-xsl
    [optional   ] [INTRA] libxslt
    [optional   ] [INTRA] lynx

  doxygen (desktop):
    [optional   ] [INTRA] gs
    [optional   ] [INTRA] llvm  (with clang)
    [optional   ] [SKIP ] Graphviz-14.1.2 (docs/tests only)
    [optional   ] [SKIP ] libxml2-2.15.1 (docs/tests only)

  editables (desktop):
    [required   ] [INTRA] hatch-fancy-pypi-readme
    [required   ] [INTRA] hatch-vcs

  efibootmgr (desktop):
    [required   ] [cross] popt

  evolution-data-server (desktop):
    [recommended] [INTRA] icu
    [recommended] [INTRA] libcanberra
    [optional   ] [INTRA] mitkrb
    [optional   ] [INTRA] openldap
    [recommended] [cross] glib2  (with GObject Introspection)

  exiv2 (desktop):
    [required   ] [cross] cmake

  ffmpeg (desktop):
    [optional   ] [INTRA] fontconfig
    [optional   ] [INTRA] fribidi
    [optional   ] [INTRA] gnutls
    [optional   ] [INTRA] libcdio  (to
                identify and play CDs)
    [optional   ] [INTRA] libdrm
    [optional   ] [INTRA] libjxl
    [optional   ] [INTRA] libwebp
    [optional   ] [INTRA] openjpeg2
    [optional   ] [INTRA] pulseaudio
    [optional   ] [INTRA] samba
    [optional   ] [INTRA] speex
    [optional   ] [INTRA] vulkan-loader
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)

  fontconfig (desktop):
    [optional   ] [INTRA] json-c
    [optional   ] [INTRA] libxml2
    [optional   ] [cross] curl
    [optional   ] [SKIP ] bubblewrap-0.11.0 (docs/tests only)
    [optional   ] [SKIP ] libarchive-3.8.5 (docs/tests only)

  freetype2 (desktop):
    [optional   ] [INTRA] librsvg
    [recommended] [cross] which

  gcr (desktop):
    [recommended] [INTRA] gnupg2
    [recommended] [INTRA] libxslt
    [required   ] [cross] glib2  (GObject Introspection
                recommended)

  gcr4 (desktop):
    [recommended] [INTRA] gnupg2
    [recommended] [INTRA] libxslt
    [optional   ] [INTRA] gnutls
    [required   ] [cross] glib2  (GObject Introspection
                recommended)
    [optional   ] [cross] openssh

  gdk-pixbuf (desktop):
    [recommended] [INTRA] docutils
    [recommended] [INTRA] glycin  (circular: build gdk-pixbuf without glycin first, then build
                glycin with all its recommended dependencies, and rebuild
                gdk-pixbuf again)
    [optional   ] [INTRA] libavif  (runtime, deprecated)
    [optional   ] [INTRA] libjpeg  (deprecated)
    [optional   ] [INTRA] libjxl  (runtime, deprecated)
    [optional   ] [INTRA] librsvg  (runtime, deprecated)
    [required   ] [cross] glib2  (GObject Introspection required
                for GNOME)

  gdm (desktop):
    [optional   ] [INTRA] keyutils

  geoclue2 (desktop):
    [recommended] [INTRA] ModemManager
    [optional   ] [INTRA] avahi

  geocode-glib (desktop):
    [recommended] [cross] glib2  (with GObject Introspection)

  ghostscript (desktop):
    [recommended] [INTRA] libjpeg
    [optional   ] [INTRA] cairo
    [optional   ] [INTRA] gtk3
    [optional   ] [INTRA] libwebp

  gjs (desktop):
    [required   ] [cross] dbus
    [required   ] [cross] glib2  (with GObject
                Introspection)

  glib-networking (desktop):
    [required   ] [cross] glib2
    [recommended] [cross] make-ca

  glslang (desktop):
    [required   ] [cross] cmake

  glycin (desktop):
    [recommended] [INTRA] libjxl
    [optional   ] [INTRA] gtk4
    [required   ] [cross] glib2  (GObject
                Introspection recommended)

  gnome-autoar (desktop):
    [required   ] [cross] libarchive

  gnome-backgrounds (desktop):
    [required   ] [INTRA] libjxl

  gnome-bluetooth (desktop):
    [required   ] [INTRA] libnotify
    [recommended] [cross] glib2  (with GObject Introspection)

  gnome-control-center (desktop):
    [required   ] [INTRA] ModemManager
    [recommended] [INTRA] ibus
    [optional   ] [INTRA] xwayland

  gnome-desktop (desktop):
    [recommended] [INTRA] bubblewrap  (needed for thumbnailers in Nautilus)
    [optional   ] [INTRA] itstool
    [optional   ] [INTRA] libxkbcommon
    [recommended] [cross] glib2  (with
                GObject Introspection)

  gnome-keyring (desktop):
    [recommended] [INTRA] libxslt
    [required   ] [cross] dbus
    [recommended] [cross] openssh

  gnome-online-accounts (desktop):
    [optional   ] [INTRA] keyutils
    [optional   ] [INTRA] mitkrb
    [recommended] [cross] glib2  (with GObject Introspection)

  gnome-session (desktop):
    [optional   ] [INTRA] DocBook
    [optional   ] [INTRA] libxslt
    [required   ] [cross] systemd  (runtime)
    [optional   ] [SKIP ] docbook-xsl-nons-1.79.2 (docs/tests only)
    [optional   ] [SKIP ] xmlto-0.0.29 (docs/tests only)

  gnome-settings-daemon (desktop):
    [recommended] [INTRA] ModemManager
    [recommended] [INTRA] NetworkManager
    [recommended] [INTRA] cups
    [optional   ] [INTRA] gnome-session
    [optional   ] [INTRA] mutter
    [optional   ] [INTRA] xwayland
    [recommended] [cross] nss

  gnome-shell (desktop):
    [recommended] [INTRA] NetworkManager
    [recommended] [INTRA] desktop-file-utils
    [recommended] [INTRA] gnome-autoar
    [recommended] [INTRA] gnome-bluetooth
    [recommended] [INTRA] gst10-plugins-base
    [recommended] [INTRA] power-profiles-daemon

  gnome-terminal (desktop):
    [required   ] [INTRA] gnome-shell
    [recommended] [INTRA] nautilus
    [optional   ] [INTRA] desktop-file-utils

  gnupg2 (desktop):
    [required   ] [INTRA] openldap
    [optional   ] [INTRA] fuse3
    [optional   ] [INTRA] libusb
    [optional   ] [cross] curl

  gnutls (desktop):
    [optional   ] [INTRA] brotli
    [optional   ] [INTRA] libseccomp
    [optional   ] [cross] libidn2
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)

  graphite2 (desktop):
    [optional   ] [INTRA] freetype2
    [optional   ] [INTRA] harfbuzz

  graphviz (desktop):
    [optional   ] [INTRA] cups  (for formatting graphs for
                printing)
    [optional   ] [INTRA] webkitgtk

  gsound (desktop):
    [recommended] [cross] glib2  (with GObject Introspection)

  gst-plugins-bad (desktop):
    [required   ] [INTRA] gst10-plugins-base
    [recommended] [INTRA] libaom  (for chroma subsampling
                outside of YUV420)
    [recommended] [INTRA] libva
    [recommended] [INTRA] svt-av1  (only supports YUV420)
    [optional   ] [INTRA] bluez
    [optional   ] [INTRA] fdk-aac
    [optional   ] [INTRA] glslc  (for Vulkan plugin)
    [optional   ] [INTRA] gst10-plugins-good  (for
                one test)
    [optional   ] [INTRA] gtk3  (for examples)
    [optional   ] [INTRA] json-glib
    [optional   ] [INTRA] lcms2
    [optional   ] [INTRA] libass
    [optional   ] [INTRA] libexif  (for one test)
    [optional   ] [INTRA] libgcrypt  (for SSL support in
                the hls plugin, if both are not installed OpenSSL will be
                used instead)
    [optional   ] [INTRA] librsvg
    [optional   ] [INTRA] libsndfile
    [optional   ] [INTRA] libusb
    [optional   ] [INTRA] libwebp
    [optional   ] [INTRA] libxkbcommon
    [optional   ] [INTRA] libxml2
    [optional   ] [INTRA] nettle
    [optional   ] [INTRA] openjpeg2
    [optional   ] [INTRA] opus
    [optional   ] [INTRA] pango
    [optional   ] [INTRA] sbc
    [optional   ] [INTRA] vulkan-loader
    [optional   ] [INTRA] wayland
    [optional   ] [INTRA] x265
    [optional   ] [cross] curl
    [optional   ] [cross] libssh2

  gst-plugins-base (desktop):
    [required   ] [INTRA] gstreamer10
    [recommended] [INTRA] cdparanoia  (for building
                the CDDA plugin)
    [recommended] [INTRA] iso-codes
    [recommended] [INTRA] libgudev
    [recommended] [INTRA] libjpeg
    [recommended] [INTRA] libpng
    [recommended] [INTRA] mesa
    [recommended] [INTRA] wayland-protocols
    [optional   ] [INTRA] graphene
    [optional   ] [INTRA] gtk3  (for examples)
    [optional   ] [INTRA] opus
    [recommended] [cross] glib2  (with GObject Introspection)

  gst-plugins-good (desktop):
    [required   ] [INTRA] gst10-plugins-base
    [recommended] [INTRA] gdk-pixbuf
    [recommended] [INTRA] libsoup3
    [recommended] [INTRA] libvpx
    [recommended] [INTRA] mpg123
    [recommended] [INTRA] nasm
    [optional   ] [INTRA] gtk3  (for examples)
    [optional   ] [INTRA] speex
    [optional   ] [INTRA] wayland

  gstreamer (desktop):
    [optional   ] [INTRA] gtk3  (for examples)
    [optional   ] [INTRA] rust  (for IEEE 1588:2008 PTP clock
                support)
    [optional   ] [cross] libnsl

  gtk3 (desktop):
    [recommended] [INTRA] adwaita-icon-theme  (at
                runtime; default for some gtk3 settings keys)
    [recommended] [INTRA] docbook-xsl  (for
                generating manual pages)
    [recommended] [INTRA] hicolor-icon-theme  (needed
                for tests)
    [recommended] [INTRA] libxslt  (for generating manual
                pages)
    [optional   ] [INTRA] colord
    [optional   ] [INTRA] cups
    [optional   ] [INTRA] libcloudproviders
    [optional   ] [INTRA] sassc
    [optional   ] [INTRA] tinysparql

  gtk4 (desktop):
    [recommended] [INTRA] adwaita-icon-theme  (runtime, default for some gtk4 settings keys)
    [recommended] [INTRA] glslc
    [recommended] [INTRA] gst10-plugins-bad
    [recommended] [INTRA] gst10-plugins-good
    [recommended] [INTRA] hicolor-icon-theme  (needed
                for tests and for defaults)
    [recommended] [INTRA] libvpx
    [optional   ] [INTRA] colord
    [optional   ] [INTRA] cups
    [optional   ] [INTRA] docutils
    [optional   ] [INTRA] libcloudproviders
    [optional   ] [INTRA] sassc
    [optional   ] [INTRA] tinysparql
    [optional   ] [SKIP ] Avahi-0.8 (docs/tests only)

  gtksourceview5 (desktop):
    [recommended] [INTRA] libxml2
    [optional   ] [INTRA] vala
    [optional   ] [INTRA] vulkan-loader
    [recommended] [cross] glib2  (with GObject Introspection)

  gvfs (desktop):
    [recommended] [INTRA] libcdio
    [recommended] [INTRA] libgudev
    [recommended] [INTRA] libsoup3
    [optional   ] [INTRA] bluez
    [optional   ] [INTRA] fuse3
    [optional   ] [INTRA] gnome-online-accounts
    [optional   ] [INTRA] libgcrypt
    [optional   ] [INTRA] libxml2
    [optional   ] [INTRA] libxslt
    [optional   ] [INTRA] samba
    [required   ] [cross] dbus
    [required   ] [cross] glib2
    [recommended] [cross] systemd  (runtime)
    [optional   ] [cross] libarchive
    [optional   ] [cross] openssh

  harfbuzz (desktop):
    [recommended] [INTRA] freetype2
    [optional   ] [INTRA] cairo  (circular: build cairo and
                all its recommended dependencies, including harfbuzz, first,
                then rebuild harfbuzz if the cairo backend is needed)
    [recommended] [cross] glib2  (required for Pango; GObject
                Introspection required for building GNOME)
    [optional   ] [cross] git

  hatch-fancy-pypi-readme (desktop):
    [required   ] [INTRA] hatch-fancy-pypi-readme
    [required   ] [INTRA] hatch-vcs

  hatch-vcs (desktop):
    [required   ] [INTRA] hatch-fancy-pypi-readme
    [required   ] [INTRA] hatch-vcs

  hatchling (desktop):
    [required   ] [INTRA] hatch-fancy-pypi-readme
    [required   ] [INTRA] hatch-vcs

  ibus (desktop):
    [recommended] [INTRA] libnotify
    [optional   ] [INTRA] gnome-desktop  (for one test)
    [optional   ] [INTRA] libxkbcommon
    [optional   ] [INTRA] pygobject3
    [optional   ] [INTRA] wayland  (to build the Wayland
                support programs)
    [recommended] [cross] glib2  (with GObject Introspection)

  iptables (desktop):
    [optional   ] [INTRA] libpcap  (required for BPF
                compiler or nfsynproxy support)

  itstool (desktop):
    [required   ] [INTRA] DocBook

  json-glib (desktop):
    [optional   ] [INTRA] docutils

  lame (desktop):
    [optional   ] [INTRA] libsndfile
    [optional   ] [INTRA] nasm

  lcms2 (desktop):
    [optional   ] [INTRA] libjpeg
    [optional   ] [INTRA] libtiff

  libXdmcp (desktop):
    [optional   ] [INTRA] libxslt
    [optional   ] [SKIP ] xmlto-0.0.29 (docs/tests only)

  libass (desktop):
    [optional   ] [INTRA] harfbuzz

  libavif (desktop):
    [recommended] [INTRA] libaom  (for chroma subsampling
                outside YUV420)
    [optional   ] [INTRA] gdk-pixbuf

  libblockdev (desktop):
    [optional   ] [INTRA] json-glib
    [required   ] [cross] glib2  (GObject Introspection required
                for GNOME)

  libcanberra (desktop):
    [recommended] [INTRA] gstreamer10
    [optional   ] [INTRA] pulseaudio

  libcupsfilters (desktop):
    [required   ] [INTRA] gs
    [recommended] [INTRA] libexif
    [recommended] [INTRA] libjpeg
    [recommended] [INTRA] libpng
    [recommended] [INTRA] libtiff
    [required   ] [cross] glib2

  libdaemon (desktop):
    [optional   ] [INTRA] lynx
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)

  libdrm (desktop):
    [optional   ] [INTRA] DocBook
    [optional   ] [INTRA] docbook-xsl
    [optional   ] [INTRA] docutils
    [optional   ] [INTRA] libxslt  (to
                build manual pages)
    [optional   ] [cross] cmake  (could be
                used to find dependencies without pkgconfig files)
    [optional   ] [SKIP ] Cairo-1.18.4 (docs/tests only)

  libei (desktop):
    [optional   ] [INTRA] libevdev
    [optional   ] [INTRA] libxkbcommon
    [optional   ] [INTRA] libxml2

  libevdev (desktop):
    [required   ] [INTRA] libevdev
    [required   ] [INTRA] mtdev
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)

  libfyaml (desktop):
    [recommended] [INTRA] libyaml  (for YAML 0.1 support)
    [optional   ] [cross] git

  libgusb (desktop):
    [recommended] [INTRA] hwdata
    [recommended] [cross] glib2  (with GObject Introspection)

  libgweather (desktop):
    [recommended] [INTRA] libxml2
    [recommended] [INTRA] vala
    [optional   ] [INTRA] llvm  (for clang-format)
    [recommended] [cross] glib2  (with GObject Introspection)

  libheif (desktop):
    [optional   ] [INTRA] brotli
    [optional   ] [INTRA] ffmpeg
    [optional   ] [INTRA] gdk-pixbuf
    [optional   ] [INTRA] libjpeg
    [optional   ] [INTRA] libpng
    [optional   ] [INTRA] libtiff
    [optional   ] [INTRA] libwebp
    [optional   ] [INTRA] openjpeg2
    [optional   ] [INTRA] svt-av1
    [optional   ] [INTRA] x264
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)

  libical (desktop):
    [optional   ] [INTRA] icu
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)
    [optional   ] [SKIP ] Graphviz-14.1.2 (docs/tests only)
    [optional   ] [SKIP ] PyGObject-3.54.5 (docs/tests only)

  libjpeg-turbo (desktop):
    [required   ] [cross] cmake

  libjxl (desktop):
    [required   ] [INTRA] libjpeg
    [optional   ] [INTRA] gdk-pixbuf  (for the plugin)
    [optional   ] [INTRA] libavif
    [optional   ] [INTRA] libwebp
    [required   ] [cross] cmake
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)
    [optional   ] [SKIP ] Graphviz-14.1.2 (docs/tests only)

  libnma (desktop):
    [required   ] [INTRA] NetworkManager

  libnotify (desktop):
    [optional   ] [cross] glib2  (with GObject Introspection)

  libpcap (desktop):
    [optional   ] [INTRA] bluez
    [optional   ] [INTRA] libnl
    [optional   ] [INTRA] libusb

  libpeas (desktop):
    [recommended] [INTRA] libxml2
    [required   ] [cross] glib2  (with GObject Introspection)

  libportal (desktop):
    [optional   ] [INTRA] vala
    [required   ] [cross] glib2  (with GObject Introspection)

  librest (desktop):
    [optional   ] [INTRA] vala
    [recommended] [cross] glib2  (with GObject Introspection)

  librsvg (desktop):
    [optional   ] [INTRA] dav1d  (to support embedded AVIF in
                SVG)
    [recommended] [cross] glib2  (with
                GObject Introspection)
    [optional   ] [SKIP ] docutils-0.22.4 (docs/tests only)

  libsamplerate (desktop):
    [optional   ] [INTRA] alsa-lib
    [optional   ] [INTRA] libsndfile

  libseccomp (desktop):
    [optional   ] [INTRA] cython  (for python
                bindings)
    [optional   ] [SKIP ] Which-2.23 (docs/tests only)

  libsecret (desktop):
    [recommended] [INTRA] gnutls
    [optional   ] [INTRA] DocBook
    [optional   ] [INTRA] docbook-xsl

  libshumate (desktop):
    [recommended] [cross] glib2  (with GObject Introspection)

  libsndfile (desktop):
    [optional   ] [INTRA] alsa-lib
    [optional   ] [INTRA] lame
    [optional   ] [INTRA] mpg123
    [optional   ] [INTRA] speex

  libsoup3 (desktop):
    [optional   ] [INTRA] brotli
    [recommended] [cross] glib2  (with GObject Introspection)
    [optional   ] [cross] curl  (required to run the test
                suite)
    [optional   ] [SKIP ] MIT Kerberos
                V5-1.22.2 (docs/tests only)
    [optional   ] [SKIP ] Samba-4.23.5 (docs/tests only)

  libtiff (desktop):
    [optional   ] [INTRA] libjpeg
    [optional   ] [INTRA] libwebp
    [recommended] [cross] cmake

  libvpx (desktop):
    [recommended] [cross] which
    [optional   ] [cross] curl  (to download test files)
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)

  libwacom (desktop):
    [optional   ] [INTRA] librsvg
    [optional   ] [cross] git
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)

  libwebp (desktop):
    [recommended] [INTRA] libjpeg
    [optional   ] [INTRA] giflib

  libxcb (desktop):
    [recommended] [INTRA] mesa
    [optional   ] [INTRA] libxslt
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)

  libxkbcommon (desktop):
    [optional   ] [INTRA] xwayland
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)

  libxml2 (desktop):
    [optional   ] [INTRA] libxslt

  libxslt (desktop):
    [recommended] [INTRA] DocBook
    [recommended] [INTRA] docbook-xsl
    [optional   ] [INTRA] libgcrypt  (only needed for the
                deprecated EXSLT crypto extension, see Command Explanations)

  llvm (desktop):
    [optional   ] [INTRA] libxml2
    [optional   ] [INTRA] pygments
    [optional   ] [cross] git
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)
    [optional   ] [SKIP ] Graphviz-14.1.2 (docs/tests only)
    [optional   ] [SKIP ] Systemd-259.1 (docs/tests only)
    [optional   ] [SKIP ] rsync-3.4.1 (docs/tests only)

  localsearch (desktop):
    [required   ] [INTRA] gst10-plugins-base
    [recommended] [INTRA] ffmpeg
    [recommended] [INTRA] giflib
    [recommended] [INTRA] icu
    [recommended] [INTRA] libexif
    [recommended] [INTRA] libseccomp
    [recommended] [INTRA] libwebp
    [recommended] [INTRA] poppler
    [recommended] [INTRA] upower
    [optional   ] [INTRA] gst10-plugins-good  (for
                one test)
    [optional   ] [INTRA] totem-pl-parser
    [optional   ] [cross] cmake

  lvm2 (desktop):
    [optional   ] [INTRA] dosfstools
    [optional   ] [cross] which

  lxml (desktop):
    [required   ] [INTRA] pygobject3
    [recommended] [INTRA] at-spi2-core
    [optional   ] [INTRA] docbook-xsl
    [optional   ] [INTRA] lynx

  lynx (desktop):
    [recommended] [INTRA] brotli
    [optional   ] [INTRA] gnutls  (experimental, to replace
                openssl)
    [optional   ] [cross] libarchive
    [optional   ] [cross] libidn2

  markdown (desktop):
    [required   ] [INTRA] hatch-fancy-pypi-readme
    [required   ] [INTRA] hatch-vcs

  mesa (desktop):
    [optional   ] [INTRA] libdisplay-info

  mitkrb (desktop):
    [optional   ] [INTRA] cracklib
    [optional   ] [INTRA] openldap

  modemmanager (desktop):
    [recommended] [cross] glib2  (with GObject Introspection)

  mpg123 (desktop):
    [optional   ] [INTRA] pulseaudio

  mupdf (desktop):
    [recommended] [INTRA] libjpeg
    [optional   ] [INTRA] xdg-utils  (runtime)
    [recommended] [cross] curl

  mutter (desktop):
    [required   ] [INTRA] docutils
    [required   ] [INTRA] gnome-settings-daemon
    [recommended] [INTRA] desktop-file-utils
    [optional   ] [INTRA] libadwaita1
    [optional   ] [INTRA] xwayland
    [recommended] [cross] glib2  (with GObject Introspection)
    [optional   ] [SKIP ] GTK-3.24.51 (docs/tests only)

  nautilus (desktop):
    [recommended] [INTRA] desktop-file-utils
    [recommended] [INTRA] gst10-plugins-base
    [recommended] [INTRA] libcloudproviders
    [recommended] [INTRA] localsearch  (required at
                runtime)
    [recommended] [cross] glib2  (with GObject Introspection)

  networkmanager (desktop):
    [optional   ] [INTRA] ModemManager
    [optional   ] [INTRA] bluez
    [optional   ] [INTRA] gnutls
    [optional   ] [INTRA] jansson
    [optional   ] [INTRA] libnvme
    [optional   ] [INTRA] upower
    [recommended] [cross] curl
    [recommended] [cross] glib2  (with GObject
                Introspection)
    [recommended] [cross] libpsl
    [recommended] [cross] nss
    [recommended] [cross] systemd

  openjpeg2 (desktop):
    [optional   ] [INTRA] lcms2
    [optional   ] [INTRA] libpng
    [optional   ] [INTRA] libtiff
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)
    [optional   ] [SKIP ] git-2.53.0 (docs/tests only)

  openldap (desktop):
    [optional   ] [INTRA] gnutls

  pango (desktop):
    [optional   ] [INTRA] docutils  (to generate manual
                pages)

  pathspec (desktop):
    [required   ] [INTRA] hatch-fancy-pypi-readme
    [required   ] [INTRA] hatch-vcs

  perl-parse-yapp (desktop):
    [required   ] [cross] libarchive

  pinentry (desktop):
    [optional   ] [INTRA] gcr
    [optional   ] [INTRA] gcr4
    [optional   ] [INTRA] libsecret

  pipewire (desktop):
    [recommended] [INTRA] bluez
    [recommended] [INTRA] gst10-plugins-base
    [recommended] [INTRA] gstreamer10
    [recommended] [INTRA] pulseaudio
    [recommended] [INTRA] sbc
    [recommended] [INTRA] wireplumber  (runtime)
    [optional   ] [INTRA] avahi
    [optional   ] [INTRA] fdk-aac
    [optional   ] [INTRA] ffmpeg
    [optional   ] [INTRA] libcanberra
    [optional   ] [INTRA] libdrm  (for
                one example and libcamera support)
    [optional   ] [INTRA] libsndfile
    [optional   ] [INTRA] libusb
    [optional   ] [INTRA] libxcb
    [optional   ] [INTRA] opus
    [optional   ] [INTRA] vulkan-loader
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)
    [optional   ] [SKIP ] Graphviz-14.1.2 (docs/tests only)

  pixman (desktop):
    [optional   ] [INTRA] libpng
    [optional   ] [SKIP ] GTK-3.24.51 (docs/tests only)

  pluggy (desktop):
    [required   ] [INTRA] hatch-fancy-pypi-readme
    [required   ] [INTRA] hatch-vcs

  poppler (desktop):
    [recommended] [INTRA] boost
    [recommended] [INTRA] lcms2
    [recommended] [INTRA] libjpeg
    [recommended] [INTRA] libtiff
    [optional   ] [INTRA] gdk-pixbuf
    [optional   ] [INTRA] gtk3
    [required   ] [cross] cmake
    [required   ] [cross] glib2  (with GObject Introspection)
    [optional   ] [cross] curl
    [optional   ] [cross] git  (for
                downloading test files)

  protobuf (desktop):
    [required   ] [cross] cmake

  pulseaudio (desktop):
    [optional   ] [INTRA] avahi
    [optional   ] [INTRA] bluez
    [optional   ] [INTRA] gst10-plugins-base
    [optional   ] [INTRA] gtk3
    [optional   ] [INTRA] libsamplerate
    [optional   ] [INTRA] sbc  (Bluetooth
                support)
    [recommended] [cross] dbus
    [recommended] [cross] glib2
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)

  pycairo (desktop):
    [required   ] [INTRA] pygobject3
    [recommended] [INTRA] at-spi2-core
    [optional   ] [INTRA] docbook-xsl
    [optional   ] [INTRA] libxslt
    [optional   ] [INTRA] lynx

  pygments (desktop):
    [required   ] [INTRA] pygobject3
    [recommended] [INTRA] at-spi2-core
    [optional   ] [INTRA] docbook-xsl
    [optional   ] [INTRA] libxslt
    [optional   ] [INTRA] lynx

  pygobject3 (desktop):
    [required   ] [INTRA] pygobject3
    [recommended] [INTRA] at-spi2-core
    [optional   ] [INTRA] docbook-xsl
    [optional   ] [INTRA] libxslt
    [optional   ] [INTRA] lynx

  qpdf (desktop):
    [required   ] [INTRA] libjpeg
    [optional   ] [INTRA] gnutls
    [optional   ] [INTRA] gs
    [optional   ] [INTRA] libtiff

  ruby (desktop):
    [optional   ] [INTRA] rust
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)
    [optional   ] [SKIP ] Graphviz-14.1.2 (docs/tests only)

  rust (desktop):
    [recommended] [cross] libssh2
    [optional   ] [SKIP ] git-2.53.0 (docs/tests only)

  rust-bindgen (desktop):
    [required   ] [INTRA] llvm  (with Clang,
                runtime)

  samba (desktop):
    [recommended] [INTRA] icu
    [recommended] [INTRA] libxslt  (for
                documentation)
    [recommended] [INTRA] lmdb
    [optional   ] [INTRA] avahi
    [optional   ] [INTRA] cups
    [optional   ] [INTRA] cyrus-sasl
    [optional   ] [INTRA] libaio
    [optional   ] [INTRA] libgcrypt
    [optional   ] [INTRA] markdown
    [optional   ] [INTRA] vala
    [recommended] [cross] libtasn1
    [optional   ] [cross] git
    [optional   ] [cross] libarchive  (for tar in smbclient)
    [optional   ] [cross] libnsl
    [optional   ] [cross] nss
    [optional   ] [cross] popt
    [optional   ] [SKIP ] GnuPG-2.5.17 (docs/tests only)

  slang (desktop):
    [optional   ] [INTRA] libpng

  spirv-tools (desktop):
    [required   ] [cross] cmake

  svt-av1 (desktop):
    [required   ] [cross] cmake

  tinysparql (desktop):
    [recommended] [INTRA] localsearch  (runtime)
    [optional   ] [INTRA] avahi
    [recommended] [cross] glib2  (with GObject Introspection)
    [optional   ] [SKIP ] Graphviz-14.1.2 (docs/tests only)

  totem-pl-parser (desktop):
    [recommended] [cross] glib2  (with GObject Introspection)
    [optional   ] [cross] cmake
    [optional   ] [SKIP ] Gvfs-1.58.2 (docs/tests only)

  trove-classifiers (desktop):
    [required   ] [INTRA] hatch-fancy-pypi-readme
    [required   ] [INTRA] hatch-vcs

  udisks2 (desktop):
    [recommended] [cross] systemd  (runtime)
    [optional   ] [cross] glib2  (with GObject Introspection)

  upower (desktop):
    [optional   ] [cross] glib2  (with GObject Introspection)

  vte (desktop):
    [recommended] [cross] glib2  (with GObject
                Introspection)
    [optional   ] [cross] git
    [optional   ] [cross] make-ca  (for downloading copies
                of fast_float, fmt, and simdutf if these recommended
                dependencies are not installed)

  vulkan-loader (desktop):
    [required   ] [cross] cmake
    [optional   ] [cross] git

  wayland (desktop):
    [optional   ] [INTRA] DocBook
    [optional   ] [INTRA] docbook-xsl
    [optional   ] [INTRA] libxslt  (to build the manual
                pages)
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)
    [optional   ] [SKIP ] Graphviz-14.1.2 (docs/tests only)
    [optional   ] [SKIP ] xmlto-0.0.29 (docs/tests only)

  webkitgtk (desktop):
    [required   ] [INTRA] gst10-plugins-bad
    [required   ] [INTRA] gst10-plugins-base
    [required   ] [INTRA] lcms2
    [required   ] [INTRA] libsecret
    [required   ] [INTRA] mesa
    [recommended] [INTRA] hicolor-icon-theme
    [recommended] [INTRA] libavif
    [recommended] [INTRA] libjxl
    [optional   ] [INTRA] harfbuzz
    [optional   ] [INTRA] woff2
    [required   ] [cross] cmake
    [required   ] [cross] libtasn1
    [required   ] [cross] which
    [recommended] [cross] glib2  (with
                GObject Introspection)

  webkitgtk-gtk3 (desktop):
    [required   ] [INTRA] gst10-plugins-bad
    [required   ] [INTRA] gst10-plugins-base
    [required   ] [INTRA] gtk4
    [required   ] [INTRA] lcms2
    [required   ] [INTRA] libsecret
    [required   ] [INTRA] mesa
    [recommended] [INTRA] hicolor-icon-theme
    [recommended] [INTRA] libavif
    [recommended] [INTRA] libjxl
    [optional   ] [INTRA] harfbuzz
    [optional   ] [INTRA] woff2
    [required   ] [cross] cmake
    [required   ] [cross] libtasn1
    [required   ] [cross] which
    [recommended] [cross] glib2  (with
                GObject Introspection)

  wireplumber (desktop):
    [optional   ] [INTRA] lxml
    [required   ] [cross] linux-pam
    [required   ] [cross] systemd
    [optional   ] [SKIP ] Doxygen-1.16.1 (docs/tests only)

  woff2 (desktop):
    [required   ] [cross] cmake

  wpa_supplicant (desktop):
    [optional   ] [INTRA] libxml2

  x265 (desktop):
    [required   ] [cross] cmake

  xdg-desktop-portal (desktop):
    [required   ] [INTRA] xdg-desktop-portal-gnome
    [required   ] [INTRA] xdg-desktop-portal-gtk
    [recommended] [INTRA] docutils  (for building the manual pages)
    [optional   ] [INTRA] geoclue2
    [optional   ] [INTRA] libportal
    [required   ] [cross] dbus  (at runtime)

  xdg-desktop-portal-gnome (desktop):
    [required   ] [INTRA] xdg-desktop-portal-gtk  (at runtime)

  xdg-user-dirs (desktop):
    [optional   ] [INTRA] DocBook
    [optional   ] [INTRA] docbook-xsl

  xdg-utils (desktop):
    [required   ] [INTRA] Links
    [optional   ] [cross] dbus

  xkeyboard-config (desktop):
    [optional   ] [INTRA] libxkbcommon

  xmlto (desktop):
    [required   ] [INTRA] DocBook
    [required   ] [INTRA] docbook-xsl

  xorgproto (desktop):
    [optional   ] [INTRA] libxslt
    [optional   ] [SKIP ] xmlto-0.0.29 (docs/tests only)

  xwayland (desktop):
    [optional   ] [INTRA] libei
    [optional   ] [INTRA] libgcrypt
    [optional   ] [INTRA] nettle
    [recommended] [cross] libtirpc
    [optional   ] [SKIP ] git-2.53.0 (docs/tests only)
    [optional   ] [SKIP ] xmlto-0.0.29 (docs/tests only)

  NOTE: BLFS groups Python modules on shared pages. Pure Python
  packages (Mako, docutils, cython, etc.) may show false deps
  from the page header (PyGObject3, at-spi2-core). Review these
  manually — they are likely false positives.

  Unmatched packages (no BLFS anchor or alias):
    argcomplete
    bash-completion
    bdftopcf
    dconf
    encodings
    font-alias
    font-cursor-misc
    font-dejavu
    font-misc-misc
    font-noto
    font-util
    help2man
    iceauth
    libFS
    libICE
    libSM
    libX11
    libXScrnSaver
    libXaw
    libXcomposite
    libXcursor
    libXdamage
    libXext
    libXfixes
    libXfont2
    libXft
    libXi
    libXinerama
    libXmu
    libXpm
    libXpresent
    libXrandr
    libXrender
    libXt
    libXtst
    libXv
    libXvMC
    libXxf86dga
    libXxf86vm
    libbluray
    libdmx
    libfontenc
    libgphoto2
    libimobiledevice
    libimobiledevice-glue
    libmsgraph
    libmtp
    libnfs
    libpciaccess
    libplist
    libtatsu
    libusbmuxd
    libxkbfile
    libxshmfence
    linux-firmware
    mandoc
    mkfontscale
    nss-mdns
    orc
    parallel
    rdfind
    rtkit
    sessreg
    setuptools-scm
    setxkbmap
    smproxy
    spidermonkey
    wireless-regdb
    xauth
    xcb-util-cursor
    xcb-util-image
    xcb-util-keysyms
    xcb-util-renderutil
    xcb-util-wm
    xcursorgen
    xdpyinfo
    xdriinfo
    xev
    xhost
    xinput
    xkbcomp
    xmodmap
    xprop
    xrandr
    xrdb
    xset
    xtrans
    xwininfo

  These may need alias entries in the database, or they may be
  InterGenOS-specific packages not in BLFS.
Dependency Audit (tier: base)
======================================================================
  Packages audited:          20
  Packages with gaps:        4
  Unmatched (no BLFS entry): 6

  Missing deps (to add):
    Intra-tier required:     0
    Intra-tier recommended:  0
    Intra-tier optional:     0
    Cross-tier (all types):  7
    TOTAL:                   7

  Skipped (docs/tests only): 2

  exim (base):
    [optional   ] [cross] cyrus-sasl
    [optional   ] [cross] gnutls
    [optional   ] [cross] openldap

  fcron (base):
    [optional   ] [cross] linux-pam
    [optional   ] [cross] vim

  libtirpc (base):
    [optional   ] [cross] mitkrb

  perl-file-fcntllock (base):
    [required   ] [cross] libarchive

  NOTE: BLFS groups Python modules on shared pages. Pure Python
  packages (Mako, docutils, cython, etc.) may show false deps
  from the page header (PyGObject3, at-spi2-core). Review these
  manually — they are likely false positives.

  Unmatched packages (no BLFS anchor or alias):
    atop
    btop
    htop
    iotop
    strace
    which

  These may need alias entries in the database, or they may be
  InterGenOS-specific packages not in BLFS.
