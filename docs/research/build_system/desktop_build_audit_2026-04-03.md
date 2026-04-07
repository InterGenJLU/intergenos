# Desktop Build.sh Audit: BLFS 13.0 Compliance

**Date:** 2026-04-03
**Context:** Pre-build audit comparing desktop package build.sh files against BLFS 13.0 instructions
**Result:** 10 critical, 12 high, 6 medium issues found

## CRITICAL (Will cause build failures or broken functionality)

### 1. polkit — Missing user/group, missing session tracking
- **BLFS says:** Create polkitd user/group (uid/gid 27), configure with `-D session_tracking=logind`
- **We have:** No user/group creation, no session_tracking flag
- **Impact:** polkitd daemon won't start, privilege escalation broken for entire desktop

### 2. gdm — Missing user/group
- **BLFS says:** Create gdm user/group (uid/gid 21), `passwd -ql gdm`
- **We have:** No user/group creation
- **Impact:** No login screen = no graphical desktop

### 3. colord — Missing user/group, missing flags
- **BLFS says:** Create colord user/group (uid/gid 71), `-D vapi=true -D systemd=true -D libcolordcompat=true -D argyllcms_sensor=false -D bash_completion=false`
- **We have:** No user/group, missing flags
- **Impact:** colord daemon won't run, no systemd integration, compatibility breaks

### 4. ibus — Entirely wrong build procedure
- **BLFS says:** Install Unicode Character Database, sed fix for schema path, `SAVE_DIST_FILES=1 NOCONFIGURE=1 ./autogen.sh`, configure with `--disable-python2 --disable-appindicator --disable-gtk2 --disable-emoji-dict`
- **We have:** Bare `./configure --prefix=/usr --sysconfdir=/etc`
- **Impact:** Build will likely fail; input methods won't work without UCD

### 5. libcanberra — Missing Wayland crash patch
- **BLFS says:** Apply wayland-1.patch
- **We have:** Patch in package.yml patches field (should be applied by builder)
- **Status:** VERIFY — patch was added to package.yml, confirm builder applies it

### 6. bluez — Missing adapter initialization fix
- **BLFS says:** `sed -i '4967,4968d' src/adapter.c`, post-install `ln -svf ../libexec/bluetooth/bluetoothd /usr/sbin`
- **We have:** Neither fix
- **Impact:** Bluetooth adapters won't initialize

### 7. evolution-data-server — Missing security patch, wrong cmake flags
- **BLFS says:** Apply security patch, use `-D SYSCONF_INSTALL_DIR=/etc -D ENABLE_VALA_BINDINGS=ON -D WITH_OPENLDAP=OFF -D WITH_KRB5=OFF -D ENABLE_INTROSPECTION=ON -D WITH_LIBDB=OFF -W no-dev -G Ninja`
- **We have:** Missing patch, missing flags, has wrong flag (-DENABLE_OAUTH2_WEBKITGTK4=ON)
- **Status:** Patch was added to package.yml, flags still need fixing

### 8. cups-filters — gcc-15 build fix + security patch
- **BLFS says:** `sed -i '/proc_func)()/s/()/(FILE*, FILE*, void*)/' filter/foomatic-rip/process.h`, apply security patch
- **We have:** Security patch in package.yml, missing gcc-15 sed fix
- **Impact:** Build WILL FAIL with gcc-15

### 9. libsoup3 — Credential leakage patch
- **BLFS says:** Apply upstream_fixes-1.patch, use `--wrap-mode=nofallback`
- **We have:** Patch in package.yml, missing --wrap-mode=nofallback
- **Status:** VERIFY patch application, add flag

### 10. cracklib — Missing dictionary creation
- **BLFS says:** Post-install: download cracklib-words, create dictionary with `create-cracklib-dict`
- **We have:** No dictionary creation
- **Impact:** Password quality checking completely broken

## HIGH (Significant functionality loss)

### 11. webkitgtk — Missing GTK-3 build
- **BLFS says:** Build twice — once with USE_GTK4=OFF, once with USE_GTK4=ON
- **We have:** Only GTK-4 build
- **Impact:** GTK-3 WebKit apps won't work

### 12. networkmanager — Missing python fix, wrong flags
- **BLFS says:** `grep -rl '^#!.*python$' | xargs sed -i '1s/python/&3/'`, `-D modem_manager=false -D nm_cloud_setup=false -D nbft=false`
- **We have:** Missing python fix, has `-Dmodem_manager=true`
- **Impact:** Build may fail, modem_manager dependency may not be ready

### 13. gtk3 — Missing post-install cache generation
- **BLFS says:** `gtk-query-immodules-3.0 --update-cache`, `glib-compile-schemas /usr/share/glib-2.0/schemas`
- **We have:** No post-install
- **Impact:** Input methods broken, GSettings schemas not compiled

### 14. gtk4 — Missing Vulkan flag
- **BLFS says:** `-D vulkan=enabled`
- **We have:** Missing
- **Impact:** No Vulkan rendering, fallback to software

### 15. gdk-pixbuf — Missing loader cache
- **BLFS says:** `gdk-pixbuf-query-loaders --update-cache`
- **We have:** No post-install
- **Impact:** Images won't load in GTK apps

### 16. pulseaudio — Missing flags
- **BLFS says:** `-D database=gdbm -D bluez5=disabled`
- **We have:** Missing both
- **Impact:** Build may fail if BlueZ not ready

### 17. libxml2 — Wrong build system
- **BLFS says:** Uses meson
- **We have:** Uses autotools
- **Impact:** May build but upstream no longer maintains autotools path

### 18. freetype2 — Missing subpixel rendering
- **BLFS says:** Two sed commands to enable GX/AAT validation and subpixel rendering
- **We have:** Uses meson, seds not applied
- **Impact:** Worse font rendering

### 19. ruby — Missing compat workaround
- **BLFS says:** `ac_cv_func_qsort_r=no`, `--disable-rpath --without-valgrind --without-baseruby`
- **We have:** Only `--prefix=/usr --enable-shared`
- **Impact:** Compatibility issues

### 20. wireplumber — Missing PulseAudio conflict resolution
- **BLFS says:** Remove PA autostart files, disable PA autospawn, enable pipewire sockets
- **We have:** None
- **Impact:** Audio broken — PA and PipeWire conflict

### 21. libxslt — Missing --without-python
- **BLFS says:** `--without-python`
- **We have:** Missing
- **Impact:** Build may fail trying to build Python bindings

### 22. cups-filters — gcc-15 sed fix
- (Same as #8 above)

## MEDIUM (Minor problems or suboptimal behavior)

### 23. gstreamer (all 4 packages) — Missing `-D gst_debug=false`
### 24. libpeas — Missing `--wrap-mode=nofallback -D python3=false`
### 25. vte — Missing post-install `rm -v /etc/profile.d/vte.*`
### 26. gcr4 — Has `-Dssh_agent=false` disabling SSH key management
### 27. udisks2 — Missing `--enable-available-modules`
### 28. libxml2 — Should switch to meson (same as #17)

## Also Check

### Packages that may install to bare lib/bin/sbin (keyutils pattern)
- Scan all remaining desktop packages with `make` or `custom` build_style for Makefiles that use bare `/lib`, `/bin`, `/sbin` install paths
- The deploy safety check will catch these, but proactively fixing saves build restarts

## Action Plan

1. Fix all CRITICAL issues (user/group creation, build procedures, missing flags)
2. Fix all HIGH issues (post-install hooks, missing flags, build system changes)
3. Scan for keyutils-pattern bare path installs
4. Commit everything
5. Relaunch desktop build
