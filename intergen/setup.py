# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen setup — interactive model download and configuration.

Called by:
  - 'intergen setup' CLI command
  - Forge installer during OS installation
  - Post-install script

Flow:
  1. Detect hardware tier
  2. Show recommended model + download size
  3. Ask user to confirm (or accept --yes flag for Forge)
  4. Download model via model_manager
  5. SHA256 verify
  6. Report success

If the user declines, InterGen still works for basic system queries
(keyword matching + template synthesis) — just no LLM inference.
"""

from __future__ import annotations

import logging
import os
import pwd
import secrets
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _invoking_user() -> tuple[Path, int, int]:
    """Home dir + (uid, gid) of the user the PER-USER setup artifacts belong to.

    `intergen setup` is routinely launched via pkexec/sudo (root) — the Welcomer
    runs ``pkexec intergen setup --yes`` so the model can be written into the
    system model dir. But the web-token, dispatch key and sessions are PER-USER:
    the session daemon (intergen.service) and panel run AS THE USER and read
    ``$HOME/.config/intergen/web-token``. If setup writes them under root's home
    (``/root``), the user-side daemon/panel can never find them — the panel shows
    "No auth token found". So when we are root and pkexec/sudo tells us who
    invoked us, target THAT user's home and hand them ownership.
    """
    if os.geteuid() == 0:
        for env in ("PKEXEC_UID", "SUDO_UID"):
            val = os.environ.get(env)
            if val:
                try:
                    pw = pwd.getpwuid(int(val))
                    if pw.pw_uid != 0:
                        return Path(pw.pw_dir), pw.pw_uid, pw.pw_gid
                except (KeyError, ValueError):
                    pass
        name = os.environ.get("SUDO_USER")
        if name:
            try:
                pw = pwd.getpwnam(name)
                if pw.pw_uid != 0:
                    return Path(pw.pw_dir), pw.pw_uid, pw.pw_gid
            except KeyError:
                pass
    return Path.home(), os.getuid(), os.getgid()


def _chown_user(path: Path, uid: int, gid: int) -> None:
    """chown ``path`` to (uid, gid); no-op on failure or when already owned."""
    try:
        st = path.stat()
        if st.st_uid != uid or st.st_gid != gid:
            os.chown(path, uid, gid)
    except OSError:
        pass


def _user_token_path() -> Path:
    home, _, _ = _invoking_user()
    return home / ".config" / "intergen" / "web-token"


# Default (current-user) locations; the generators below resolve the INVOKING
# user at runtime via _invoking_user() so pkexec/sudo setup lands per-user files
# in the caller's home, not root's.
TOKEN_PATH = Path.home() / ".config" / "intergen" / "web-token"
SESSIONS_DIR = Path.home() / ".local" / "share" / "intergen" / "sessions"


def _generate_auth_token() -> str:
    """Generate a random web auth token and write it to disk.

    Called by 'intergen setup' after model download. The token is used
    by the web server and console for WebSocket authentication.
    """
    home, uid, gid = _invoking_user()
    token_path = home / ".config" / "intergen" / "web-token"
    token = secrets.token_hex(32)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token)
    token_path.chmod(0o600)
    # Hand the per-user file (and the dirs we may have just created as root) to
    # the invoking user so their session daemon/panel can read it.
    _chown_user(token_path, uid, gid)
    _chown_user(token_path.parent, uid, gid)
    _chown_user(token_path.parent.parent, uid, gid)
    logger.info("Web auth token generated at %s", token_path)
    return token


def _generate_dispatch_key() -> None:
    """Ensure the AI-6 per-install dispatch signing key exists (gen-on-first-run).

    Generated alongside the web-token so a freshly set-up install can mint
    privileged-dispatch approval tokens (intergen.dispatch_token). Unlike the
    web-token, this is gen-on-first-run-and-keep (ensure_*, not regenerate): the
    signing key must stay stable across daemon restarts, and an explicit rotation
    is a separate, intentional action. 0o600, ~/.config/intergen/dispatch-key.
    """
    from intergen.dispatch_token import ensure_dispatch_key
    home, uid, gid = _invoking_user()
    key_path = home / ".config" / "intergen" / "dispatch-key"
    ensure_dispatch_key(path=key_path)
    _chown_user(key_path, uid, gid)
    _chown_user(key_path.parent, uid, gid)
    logger.info("Dispatch signing key ready at %s", key_path)


def _ensure_sessions_dir() -> None:
    home, uid, gid = _invoking_user()
    sessions = home / ".local" / "share" / "intergen" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    _chown_user(sessions, uid, gid)
    _chown_user(sessions.parent, uid, gid)
    _chown_user(sessions.parent.parent, uid, gid)


def _restart_user_daemon() -> bool:
    """Restart the invoking user's intergen.service so it loads the just-
    downloaded model — InterGen goes live with NO reboot and NO terminal step.

    G3-1: the daemon detects the model + starts llama-server (and the embedding
    server, G3-5) ONCE at startup. On first boot the model isn't downloaded yet
    (the Welcomer fetches it minutes later), so without this restart the daemon
    stays mute until the user reboots. `intergen setup` is the onboarding driver,
    so it owns making InterGen live the moment the model lands.

    Runs as the INVOKING user: setup is normally `pkexec intergen setup` (root),
    but intergen.service is a `--user` unit, so a bare root `systemctl --user`
    would target root's (non-existent) manager. We drop to the invoking uid/gid
    with their XDG_RUNTIME_DIR so the user's own manager restarts. Best-effort:
    on any failure the caller prints the manual command as a fallback.
    """
    try:
        _, uid, gid = _invoking_user()
    except Exception:
        return False
    cmd = ["systemctl", "--user", "restart", "intergen"]
    user_env = {
        "XDG_RUNTIME_DIR": f"/run/user/{uid}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus",
        "PATH": "/usr/bin:/bin",
    }
    try:
        if os.geteuid() == 0 and uid != 0:
            # Under pkexec/sudo: drop to the invoking user so `systemctl --user`
            # reaches THEIR session manager, not root's.
            result = subprocess.run(cmd, user=uid, group=gid, env=user_env,
                                    check=False, timeout=30)
        else:
            # Already the invoking user (plain `intergen setup` in a terminal).
            result = subprocess.run(cmd, env={**os.environ, **user_env},
                                    check=False, timeout=30)
        return result.returncode == 0
    except Exception as e:
        logger.warning("Could not auto-restart the InterGen daemon: %s", e)
        return False


def _ensure_embedding_model(mm, progress_callback=None) -> None:
    """Provision the embedding model (nomic-embed-text, Apache-2.0, no license
    gate) so the daemon's Layer-2 semantic matcher has a backend (G3-5).

    Onboarding previously downloaded ONLY the main chat model, so the embedding
    GGUF was never fetched → the daemon logged "no embedding backend available"
    for every intent and semantic routing ran permanently degraded. Small
    (~150 MB) and idempotent (no-op if already present+verified). Best-effort:
    a failure only degrades semantic routing to the keyword/LLM fallback, never
    blocks setup.
    """
    try:
        embed = mm.get_embedding_model()
        if embed.downloaded:
            return
        print()
        print(f"Downloading embedding model ({embed.name}, ~{embed.size_gb:.1f} GB)…")
        if mm.provision_model(embed, progress_callback=progress_callback):
            print()
            print("Embedding model ready — semantic intent matching enabled.")
        else:
            print()
            print("Embedding model did not verify — semantic matching will use "
                  "the keyword/LLM fallback.")
    except Exception as e:
        print(f"(embedding model optional — skipped: {e})")


def _model_source_hosts(mm, model) -> list[str]:
    """The hosts the downloader will actually contact, in the order it uses.

    Derived from the live URLs rather than hard-coded, so the preflight can
    never check a different place than the download goes to. Falls back to the
    known source hosts if the model manager's URL builders are not where this
    expects them.
    """
    from urllib.parse import urlparse

    hosts: list[str] = []
    for attr in ("_mirror_url", "_huggingface_url"):
        url_fn = getattr(mm, attr, None)  # don't hard-couple to private methods
        if url_fn is None:
            continue
        try:
            netloc = urlparse(url_fn(model)).netloc
            if netloc:
                hosts.append(netloc.split("@")[-1].split(":")[0])
        except Exception:  # noqa: BLE001 — preflight must not crash setup
            pass
    if not hosts:  # defensive fallback to the known source hosts
        from intergen.net_diagnostics import MODEL_SOURCE_HOSTS
        hosts = list(MODEL_SOURCE_HOSTS)
    return hosts


def _probe_model_sources(mm, model, timeout: float = 4.0):
    """Is a model download source reachable, and if not, WHY?

    The model download fail-closes when the machine cannot reach a source, so
    the friendly thing is to detect that BEFORE recording license acceptance
    or starting a doomed download. What matters as much as detecting it is
    saying the right thing about it.

    This used to return a bare True/False and the caller printed "you may not
    be online yet — join WiFi" for every False. That covered two different
    machines: one with no network at all, and one whose network works fine
    but whose name server does not answer. The second user was being told to
    join a network they were already on. So the check now returns the CAUSE,
    from intergen.net_diagnostics — the same module the Welcomer's page uses,
    so the two surfaces cannot describe the same machine differently.

    Returns a net_diagnostics.ProbeResult.
    """
    from intergen import net_diagnostics

    return net_diagnostics.probe_hosts(
        _model_source_hosts(mm, model), timeout=timeout)


def _is_discrete_for(tier) -> bool:
    """Whether the detected card counts as a discrete accelerator.

    Reuses the hardware detector's own test rather than re-deriving one, so the
    ladder and tier assignment can never disagree about the same card.
    """
    try:
        from intergen.hardware import HardwareDetector
        return HardwareDetector()._is_discrete_capable(tier.gpu_vendor,
                                                       tier.gpu_vram_mb)
    except Exception:
        return False


_TIER_BLURB = {
    1: "2B — runs on any box, fastest replies, smallest download",
    2: "9B — the model InterGen is designed around",
    3: "35B — the largest model, for cards that can hold it",
}


def _choose_tier(offer, *, auto_yes: bool, home):
    """Ask which model to install; return the chosen tier, or None to stop.

    Returns None ONLY when the user chooses to install NVIDIA's drivers first —
    setup then exits without installing anything, and re-running it re-offers
    the ladder against the newly readable card.
    """
    from intergen import model_choice
    from intergen.interfaces.types import HardwareTierLevel

    if offer.advisory:
        print(offer.advisory_text)
        print()

    # Non-interactive (the Welcomer runs `intergen setup --yes`): honour a
    # choice the user already made, else the most capable model that fits. The
    # Welcomer is where the choice gets presented in that flow.
    if auto_yes:
        remembered = model_choice.load_choice(home)
        if remembered is not None and remembered in offer.tiers:
            print(f"  Using your saved choice: tier {remembered.value}.")
            return remembered
        return offer.tiers[0]

    if not offer.is_choice and not offer.advisory:
        return offer.tiers[0]

    print("This box can run:")
    options = []
    for t in offer.tiers:
        options.append(t)
        print(f"  {len(options)}. {_TIER_BLURB.get(int(t.value), t.name)}")
    extra_install_drivers = None
    if offer.advisory:
        extra_install_drivers = len(options) + 1
        print(f"  {extra_install_drivers}. Install NVIDIA's drivers first — "
              f"stop here, install them, reboot, and run setup again")
    print()
    default = 1
    while True:
        raw = input(f"Which would you like? [{default}] ").strip()
        if not raw:
            return options[default - 1]
        try:
            pick = int(raw)
        except ValueError:
            print("Enter the number of one of the choices above.")
            continue
        if extra_install_drivers is not None and pick == extra_install_drivers:
            print()
            print("Nothing was installed. Install NVIDIA's drivers with the "
                  "package manager, reboot, then run 'intergen setup' again.")
            return None
        if 1 <= pick <= len(options):
            return options[pick - 1]
        print("Enter the number of one of the choices above.")


def report_offer() -> int:
    """Print what this box can run as JSON, for the Welcomer's setup flow.

    Read-only and unprivileged — it detects hardware and reads the GPU driver
    binding, nothing more. The Welcomer is a GTK app that deliberately does not
    import the intergen package, so it asks over this thin process boundary.
    """
    import json as _json
    from intergen import model_choice
    try:
        from intergen.hardware import HardwareDetector
        det = HardwareDetector()
        tier = det.detect()
        offer = model_choice.build_offer(
            is_discrete=det._is_discrete_capable(tier.gpu_vendor,
                                                 tier.gpu_vram_mb),
            vram_mb=tier.gpu_vram_mb,
        )
        payload = offer.to_status()
        payload["detected_tier"] = int(tier.tier.value)
        payload["gpu_model"] = tier.gpu_model or ""
        remembered = model_choice.load_choice(_invoking_user()[0])
        payload["saved_choice"] = (int(remembered.value)
                                   if remembered is not None else None)
    except Exception as e:
        print(_json.dumps({"error": str(e)}))
        return 1
    print(_json.dumps(payload, indent=2))
    return 0


def run_setup(*, auto_yes: bool = False, tier_override: int | None = None) -> bool:
    """Run the interactive setup flow.

    Args:
        auto_yes: Skip confirmation prompt (for Forge installer).
        tier_override: Force a specific tier (1, 2, or 3) instead of auto-detect.

    Returns:
        True if setup completed successfully.
    """
    print("InterGen Setup")
    print("=" * 50)
    print()

    # Step 1: Hardware detection
    print("Detecting hardware...")
    try:
        from intergen.hardware import HardwareDetector
        detector = HardwareDetector()
        tier = detector.detect()
    except Exception as e:
        print(f"Hardware detection failed: {e}")
        return False

    print(f"  RAM:   {tier.ram_gb:.1f} GB")
    print(f"  GPU:   {tier.gpu_vendor or 'None detected'}"
          f"{' (' + tier.gpu_model + ')' if tier.gpu_model else ''}")
    print(f"  Tier:  {tier.tier.value}")
    print()

    # Step 1.5: what this box can run, and let the user pick.
    #
    # The machine reports capability; the person decides. An explicit --tier
    # wins outright (it IS the user deciding, from the Welcomer or the command
    # line). Otherwise, when more than one model fits, ask — and when an NVIDIA
    # card is present without NVIDIA's driver, say plainly that capability
    # cannot be read and offer the 2B now or drivers first.
    from intergen import model_choice
    home, uid, gid = _invoking_user()
    offer = model_choice.build_offer(
        is_discrete=_is_discrete_for(tier),
        vram_mb=tier.gpu_vram_mb,
    )
    if not tier_override:
        chosen_tier = _choose_tier(offer, auto_yes=auto_yes, home=home)
        if chosen_tier is None:
            # The user asked to install drivers first rather than proceed.
            return True
        # Record ONLY a choice a person actually made.
        #
        # Under --yes nobody was asked: _choose_tier returns the remembered
        # choice, or the top of the ladder when there is none. Writing that
        # back stamps an automatic pick with a username and a timestamp and
        # gives it the same standing as a decision — and because a later
        # unattended run honours whatever is recorded, one such write can
        # outrank what the person actually chose and quietly install a
        # different model than they asked for. Measured on a real machine
        # (2026-07-31): a record reading tier 1 sat beside a store holding
        # the tier-2 model the operator had picked and which was serving,
        # leaving that machine primed to downgrade itself on its next
        # unattended setup. An automatic pick is not a preference; it reads
        # one, it does not create one.
        if not auto_yes:
            model_choice.record_choice(chosen_tier, home=home,
                                       advisory_shown=offer.advisory)
            _chown_user(model_choice.choice_path(home), uid, gid)
        # When the pick matches what the box detected, resolution stays on the
        # one shared path (model_manager.resolve_for_detected) that the daemon
        # uses at engine start — it carries the detector's within-tier
        # adjustments and the unpinned-model cap, and setup offering a different
        # model than the daemon will load is the engine-never-starts dead-end.
        #
        # Any OTHER pick is honoured LITERALLY. That covers both directions and
        # both matter: a user on a big card asking for the small model, and a
        # ladder that is narrower than detection thought — an NVIDIA card on the
        # open-source driver is told only the 2B is on offer, so the 2B is what
        # must install. Falling back to the detected recommendation there would
        # download a model the user was never offered.
        if chosen_tier != tier.tier:
            tier_override = int(chosen_tier.value)
    else:
        # An explicit --tier IS the user deciding — it is exactly how the
        # Welcomer hands over the model picked on its card. Record it for the
        # same reason the interactive path records its pick: so a later run
        # honours the choice instead of re-deciding for them.
        #
        # Without this the graphical path — the one a new user actually takes —
        # never wrote a preference at all. A user who deliberately picked the
        # smaller model on capable hardware had that wish forgotten, and the
        # next unattended run would resolve to the most capable model on offer
        # instead, which is the opposite of what they asked for. Found by
        # running the graphical flow end to end and looking for the record it
        # was supposed to leave (2026-07-31).
        from intergen.interfaces.types import HardwareTierLevel
        try:
            picked = HardwareTierLevel(int(tier_override))
        except (TypeError, ValueError):
            picked = None
        if picked is not None:
            model_choice.record_choice(picked, home=home,
                                       advisory_shown=offer.advisory)
            _chown_user(model_choice.choice_path(home), uid, gid)

    # Step 2: Determine model
    try:
        from intergen.model_manager import ModelManager
        mm = ModelManager()
        if tier_override:
            from intergen.interfaces.types import HardwareTierLevel
            effective_tier = HardwareTierLevel(tier_override)
            print(f"  Installing the tier-{tier_override} model.")
            model = mm.get_model_for_tier(effective_tier)
        else:
            # The ONE shared model-resolution path
            # (model_manager.resolve_for_detected), used identically by the daemon
            # at engine-start (dbus_daemon.py) — so `intergen setup` offers EXACTLY
            # the model the daemon will run. It honors the detector's within-tier
            # CPU-only adjustment (an integrated-GPU Tier 2 box gets the 2B, not the
            # 9B, for usable latency) and the unpinned->highest-pinned cap, both of
            # which a bare get_model_for_tier(tier) lookup discards. Divergence here
            # is the ge9b-01 engine-never-starts dead-end.
            model = mm.resolve_for_detected(tier)
    except Exception as e:
        print(f"Model selection failed: {e}")
        return False

    if model is None:
        print("No model available for this tier.")
        return False

    # Check if already downloaded
    if model.downloaded:
        print(f"Model already downloaded: {model.name}")
        print(f"  Path: {model.local_path}")
        print()
        _generate_auth_token()
        _generate_dispatch_key()
        print("Web auth token generated.")
        print()
        _ensure_embedding_model(mm)  # G3-5: backfill embedding model if missing
        # Re-running setup with the model already present also (re)loads it:
        # idempotent restart heals a daemon that booted before the model
        # existed and is stuck mute (G3-1). Harmless if it's already serving.
        if _restart_user_daemon():
            print("InterGen restarted — model loaded. InterGen is ready.")
        else:
            print("InterGen is ready.")
        return True

    # Connectivity preflight: the model store is empty and we are about to
    # download. If NO source is reachable — the "set up InterGen before
    # joining WiFi" case — stop NOW with an actionable message instead of
    # recording license acceptance and dead-ending in a fail-closed download.
    # Returns False (NOT a deliberate user skip): setup did not complete, so
    # the exit code is non-zero and the Welcomer correctly shows "didn't
    # finish" rather than a false "ready".
    probe = _probe_model_sources(mm, model)
    if not probe.reachable:
        from intergen import net_diagnostics

        print()
        print("InterGen needs to download its model "
              f"(~{model.size_gb:.1f} GB), and no download source can be "
              "reached right now.")
        print()
        print(net_diagnostics.cause_headline(probe.cause))
        print(net_diagnostics.cause_detail(probe.cause))
        if net_diagnostics.cause_is_name_resolution(probe.cause):
            print()
            print("The Finding Websites page of the InterGenOS Welcomer lets "
                  "you choose a different name server:")
            print("  intergen-welcome --force")
        print()
        print("Then run setup again:")
        print("  intergen setup")
        return False

    # Step 3: Confirm download
    print(f"Recommended model: {model.name}")
    print(f"  Quantization:    {model.quant}")
    print(f"  Download size:   ~{model.size_gb:.1f} GB")
    print(f"  Source:          Hugging Face ({model.repo_id})")
    print()

    if not auto_yes:
        print("InterGen needs this model for AI-powered responses.")
        print("Without it, InterGen can still handle basic system queries")
        print("(hostname, disk space, services) but capability will be limited")
        print("until the model is available.")
        if os.geteuid() != 0:
            print()
            print("The model installs into the system-wide store")
            print("(/var/lib/intergen/models, root-owned read-only).")
            print("You'll be asked to authenticate once to complete the install.")
        print()
        response = input("Download now? [Y/n] ").strip().lower()
        if response in ("n", "no"):
            print()
            print("Setup skipped. You can run 'intergen setup' anytime to download.")
            print("InterGen will still handle basic system requests.")
            return True  # Not a failure — user chose to skip

    # Step 3.5: License acceptance. model_manager refuses to download a
    # license-gated model (Qwen → Tongyi-Qianwen) until acceptance is on record,
    # and delegates the show-license / consent / record flow to this CLI layer.
    # Without this the download dead-ends with "License not accepted" and no way
    # to accept.
    if not mm.check_license_acceptance(model):
        from intergen.model_manager import (
            _model_license_ref, QWEN_LICENSE_REF, QWEN_LICENSE_URL,
        )
        license_ref = _model_license_ref(model)
        license_url = (QWEN_LICENSE_URL if license_ref == QWEN_LICENSE_REF
                       else f"the {model.repo_id} model card")
        print()
        print(f"{model.name} is distributed under the {license_ref} license.")
        print(f"  Review the terms: {license_url}")
        if auto_yes:
            accepted = True  # Forge/--yes: consent is given via the install flow
        else:
            accepted = input(
                f"Do you accept the {license_ref} license terms? [y/N] "
            ).strip().lower() in ("y", "yes")
        if not accepted:
            print()
            print("License not accepted — InterGen cannot install this model.")
            print("InterGen will still handle basic system requests; rerun")
            print("'intergen setup' anytime to accept and download.")
            return True  # user declined acceptance — not a crash
        mm.record_license_acceptance(model, accepted_by=os.environ.get("USER", ""))
        print("License accepted and recorded.")

    # Step 4: Download
    print()
    print(f"Downloading {model.name} ({model.size_gb:.1f} GB)...")

    def progress(downloaded: int, total: int) -> None:
        if total > 0:
            pct = downloaded / total * 100
            bar_width = 40
            filled = int(bar_width * downloaded / total)
            bar = "█" * filled + "░" * (bar_width - filled)
            mb = downloaded / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            print(f"\r  [{bar}] {pct:.0f}% ({mb:.0f}/{total_mb:.0f} MB)", end="", flush=True)

    try:
        success = mm.provision_model(model, progress_callback=progress)
    except Exception as e:
        print(f"\nDownload failed: {e}")
        return False

    print()

    if not success:
        # Say which failure it actually was. This printed "SHA256
        # verification did not pass" for every kind of failure, including the
        # ones where nothing was ever downloaded to verify — a user whose name
        # server stopped answering mid-setup was told their model was
        # corrupt. The model manager records what it saw; this reports it.
        from intergen import net_diagnostics

        reason = getattr(mm, "last_download_failure", None)
        print()
        if reason == "pin-mismatch":
            print("Download failed — the file that arrived did not match its "
                  "published checksum, so it was discarded.")
            print("Please try again: intergen setup")
        elif reason in (net_diagnostics.NAME_RESOLUTION,
                        net_diagnostics.NO_LINK,
                        net_diagnostics.NO_ROUTE):
            print("The download did not finish.")
            print(net_diagnostics.cause_headline(reason))
            print(net_diagnostics.cause_detail(reason))
            if net_diagnostics.cause_is_name_resolution(reason):
                print()
                print("The Finding Websites page of the InterGenOS Welcomer "
                      "lets you choose a different name server:")
                print("  intergen-welcome --force")
            print()
            print("Then run setup again:")
            print("  intergen setup")
        else:
            print("The download did not finish, and the reason was not one "
                  "this setup could identify.")
            print("Please try again: intergen setup")
        return False

    # Step 5: Verify
    print(f"SHA256 verified: {model.sha256[:16]}...")
    print(f"Model saved to: {model.local_path}")
    print()

    _generate_auth_token()
    _generate_dispatch_key()
    print("Web auth token generated.")
    print()
    _ensure_embedding_model(mm, progress)  # G3-5: embedding backend for semantics
    print()

    print("InterGen is ready.")
    print()
    # The daemon loads its model ONCE at startup with no auto-reload, so a
    # freshly-downloaded model needs a RESTART (`start` is a no-op on the
    # already-running unit). Do it for the user — onboarding is one-click /
    # no-terminal, so a printed "go run systemctl" leaves InterGen mute (G3-1).
    if _restart_user_daemon():
        print("InterGen restarted — the new model is live now. No reboot needed.")
    else:
        print("Restart InterGen to load the new model now:")
        print("  systemctl --user restart intergen")

    return True


def main() -> None:
    """CLI entry point for 'intergen setup'."""
    if "--show-offer" in sys.argv:
        sys.exit(report_offer())
    auto_yes = "--yes" in sys.argv or "-y" in sys.argv
    tier_override = None

    for arg in sys.argv:
        if arg.startswith("--tier="):
            try:
                tier_override = int(arg.split("=")[1])
            except ValueError:
                pass

    success = run_setup(auto_yes=auto_yes, tier_override=tier_override)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
