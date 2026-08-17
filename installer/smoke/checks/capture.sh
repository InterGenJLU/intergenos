#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
# installer/smoke/checks/capture.sh — Category 7: capture-stack capability.
#
# The capture stack's failure mode is SILENT: a machine with no DSP
# firmware, no UCM profiles, a null echo canceller, or no camera pipeline
# library still boots, still plays audio through fallback paths, and only
# fails when a user tries the microphone or camera. These checks assert the
# ARTIFACT contract (what must ship on every install) unconditionally, and
# the hardware-facing legs venue-aware: a box with no capture hardware
# SKIPs those legs explicitly, never silently.

run_capture_checks() {
    # ---- Artifact contract: ships on every install, hardware-independent ----

    # Intel audio-DSP firmware (sof-firmware package). Upstream
    # linux-firmware ships no intel/sof — this tree is the only source.
    if [ -d /usr/lib/firmware/intel/sof ] \
        && [ -n "$(ls -A /usr/lib/firmware/intel/sof 2>/dev/null)" ] \
        && [ -d /usr/lib/firmware/intel/sof-tplg ]; then
        check_pass "capture/sof-firmware" "intel/sof + sof-tplg present"
    else
        check_fail "capture/sof-firmware" "Intel SOF firmware missing/empty under /usr/lib/firmware/intel — every Intel DSP laptop has no mic without it"
    fi

    # ALSA UCM profiles (alsa-ucm-conf package): the parser entry point.
    if [ -f /usr/share/alsa/ucm2/ucm.conf ]; then
        check_pass "capture/ucm-profiles" "ucm2 profile tree present"
    else
        check_fail "capture/ucm-profiles" "/usr/share/alsa/ucm2/ucm.conf missing — internal-mic routing has no profiles"
    fi

    # Real echo-cancellation engine: the webrtc AEC SPA plugin beside the
    # null stub. Only the null shipped before the capture wave — that is
    # exactly the silent degradation this check exists to catch.
    if ls /usr/lib/spa-0.2/aec/libspa-aec-webrtc*.so >/dev/null 2>&1; then
        check_pass "capture/aec-engine" "webrtc AEC SPA plugin present"
    else
        check_fail "capture/aec-engine" "no webrtc AEC plugin in /usr/lib/spa-0.2/aec — echo cancellation is the null stub"
    fi

    # Camera pipeline library + pipewire's libcamera SPA plugin (how
    # non-UVC built-in cameras reach applications).
    if ls /usr/lib/libcamera.so* >/dev/null 2>&1; then
        check_pass "capture/libcamera" "libcamera present"
    else
        check_fail "capture/libcamera" "libcamera absent — MIPI/IPU built-in cameras produce no frames"
    fi
    if ls /usr/lib/spa-0.2/libcamera/libspa-libcamera*.so >/dev/null 2>&1; then
        check_pass "capture/libcamera-spa" "pipewire libcamera SPA plugin present"
    else
        check_fail "capture/libcamera-spa" "pipewire built without its libcamera plugin — camera frames cannot reach the session"
    fi

    # ---- Venue-aware: hardware-facing legs ----

    # Intel SOF DSP boxes: if the SOF driver bound, its firmware load must
    # not have failed (the load error is the exact symptom the firmware
    # package closes).
    if grep -qs . /sys/module/snd_sof/initstate 2>/dev/null \
        || lsmod 2>/dev/null | grep -q '^snd_sof'; then
        if dmesg 2>/dev/null | grep -iE 'sof.*(error|failed).*(firmware|fw)' >/dev/null; then
            check_fail "capture/sof-load" "SOF driver bound but firmware load reported an error (dmesg)"
        else
            check_pass "capture/sof-load" "SOF driver bound, no firmware load errors"
        fi
    else
        check_skip "capture/sof-load" "no SOF audio DSP in this venue"
    fi

    # A capture device present -> the session's audio server must expose a
    # source for it (the works-but-invisible class is handled at the UI
    # layer; this asserts the server half).
    if [ -c /dev/video0 ] || arecord -l 2>/dev/null | grep -q '^card'; then
        if command -v wpctl >/dev/null 2>&1 \
            && wpctl status 2>/dev/null | grep -qi 'Sources:'; then
            check_pass "capture/session-source" "capture hardware present and wireplumber is serving"
        else
            check_warn "capture/session-source" "capture hardware present but no wireplumber session view (headless/system context?)"
        fi
    else
        check_skip "capture/session-source" "no capture hardware in this venue"
    fi
}
