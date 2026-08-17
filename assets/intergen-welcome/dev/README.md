# Welcomer design-iteration harness (dev only — not shipped)

Headless render + live-preview tools used for the 2026-06-08 visual-language
redesign. Reuse them when iterating the welcomer (or porting the same pass to
the Forge installer screens).

## render_page.py — headless render to PNG (no display, GTK4-native rasterization)
Renders one welcomer page in a 760x720 window and saves a PNG. No screen capture,
no compositor — works on a headless build host.

    sudo apt-get install -y xvfb imagemagick   # one-time
    GSK_RENDERER=cairo xvfb-run -a -s "-screen 0 1366x768x24" \
      python3 assets/intergen-welcome/dev/render_page.py <page> /tmp/out.png
    # <page> = welcome|appearance|extensions|prompt|shortcuts|intergen|community|done

GSK_RENDERER=cairo is REQUIRED under Xvfb (the default GL/Vulkan renderer has no
GPU and renders nothing). The harness snapshots the widget tree to a render node
and rasterizes via the window renderer, then save_to_png — it does NOT screenshot
the screen (that captures a black root pixmap under Xvfb).

## preview_live.py — pop the full welcomer on a live Wayland/X session
Runs the real WelcomeApp (all pages + Next/Back nav) with the system-mutating
handlers (apply_theme / apply_prompt / set_enabled_extensions) STUBBED, so
clicking options previews without touching the live desktop / ~/.bashrc.

    WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 GDK_BACKEND=wayland \
      python3 assets/intergen-welcome/dev/preview_live.py
