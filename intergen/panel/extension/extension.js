// InterGen Panel — GNOME Shell Extension (GNOME 46+)
// Panel indicator with ECG pulse icon, Super+I, right-click menu.
//
// The InterGen window is USER-INVOKED ONLY. The extension NEVER auto-opens it:
// it only places the status-area icon. Clicking the icon / Super+I / "Open
// InterGen" launches intergen-panel, which is single-instance — a repeat launch
// re-raises the existing window rather than stacking a new one. (The old code
// auto-spawned the panel at enable(), which presented its window over the
// Welcomer on first boot and stacked a new instance on every login.)

import Clutter from "gi://Clutter";
import Gio from "gi://Gio";
import GObject from "gi://GObject";
import GLib from "gi://GLib";
import Meta from "gi://Meta";
import Shell from "gi://Shell";
import St from "gi://St";

import {Extension} from "resource:///org/gnome/shell/extensions/extension.js";
import * as Main from "resource:///org/gnome/shell/ui/main.js";
import * as PanelMenu from "resource:///org/gnome/shell/ui/panelMenu.js";
import * as PopupMenu from "resource:///org/gnome/shell/ui/popupMenu.js";

const PANEL_APP = "intergen-panel";

// Launch (or re-raise) the InterGen panel window. intergen-panel is a
// single-instance GApplication, so a second invocation just activates and
// re-presents the existing window — no stacking, no second window.
function _launchPanel() {
  try {
    GLib.spawn_command_line_async(PANEL_APP);
  } catch (e) {
    console.log("[InterGen] Could not launch panel: " + e);
  }
}

// Whether this machine has been through onboarding.
//
// The question the icon gate actually needs answered is "has a model ever been
// selected here", because that is what separates a fresh pre-onboarding system
// (where the icon would be a dead end) from a set-up system whose engine is
// failing (where hiding the icon hides the problem). A model name in the status
// payload, or the model manager reporting ready, is that evidence.
function _isOnboarded(status) {
  if (!status) return false;
  if (status.model) return true;
  if (status.components && status.components.model_manager) return true;
  return false;
}

// A short, TRUE sentence about why the engine is not serving.
//
// Read from what the daemon recorded. When it recorded nothing, this says so
// rather than inventing a cause — "not running, and no reason was recorded" is
// a worse-looking message than a confident guess, and it is the honest one.
function _engineFailureReason(status) {
  if (status && status.model_server_integrity_failure) {
    return String(status.model_server_integrity_failure);
  }
  if (status && status.last_error) {
    return String(status.last_error);
  }
  return "it is not running, and no reason was recorded — see " +
         "'journalctl --user -u intergen -n 50'";
}

// Open the panel in the person's browser at the address the daemon serves it
// on, read from the daemon's own status, so the item follows whatever address
// the panel was configured to serve. When the panel is not serving, or the
// daemon does not answer, it says so in words instead of opening a page that
// cannot load.
function _openPanelInBrowser() {
  Gio.DBus.session.call(
    INTERGEN_BUS, INTERGEN_PATH, INTERGEN_BUS, "Status", null,
    new GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 5000, null,
    (conn, res) => {
      let status;
      try {
        const [json] = conn.call_finish(res).deepUnpack();
        status = JSON.parse(json);
      } catch (e) {
        Main.notify(
          "InterGen",
          "InterGen did not answer, so the panel's address is not known: " +
          e.message
        );
        return;
      }
      if (status.panel_down || !status.panel_url) {
        Main.notify(
          "InterGen",
          "The panel is not serving: " +
          (status.panel_down || "the daemon named no address for it")
        );
        return;
      }
      try {
        Gio.AppInfo.launch_default_for_uri(status.panel_url, null);
      } catch (e) {
        Main.notify("InterGen",
                    "Could not open " + status.panel_url + ": " + e.message);
      }
    }
  );
}

// ── Panel indicator ──────────────────────────────────────────────────────

const InterGenIndicator = GObject.registerClass(
class InterGenIndicator extends PanelMenu.Button {
  _init() {
    super._init(0.0, "InterGen");

    // Panel icon — "intergenos-symbolic" (shipped by intergen-mark into
    // hicolor/scalable/apps). Heartbeat-glyph fallback only if genuinely absent.
    const iconName = "intergenos-symbolic";
    this._icon = new St.Icon({
      style_class: "intergen-panel-icon",
      icon_name: iconName,
      icon_size: 22,
      // ECG-blue tint so the mark is discoverable in the panel instead of a
      // faint monochrome glyph nobody notices. The symbolic SVG uses
      // stroke="currentColor", so this `color` drives the rendered hue.
      // #0099FF is the InterGenOS brand blue.
      style: "color: #0099FF;",
    });

    let iconAvailable = true;
    try {
      iconAvailable = St.IconTheme.new().has_icon(iconName);
    } catch (e) {
      iconAvailable = true;
    }

    if (iconAvailable) {
      this.add_child(this._icon);
    } else {
      this._label = new St.Label({
        text: "⚡",
        style: "font-size: 16px; color: #0099FF;",
      });
      this.add_child(this._label);
    }

    this._buildMenu();
  }

  // Show that the assistant is set up but its engine is not serving.
  //
  // The alternative — hiding the icon — is what used to happen, and it told an
  // onboarded user that InterGen "isn't set up yet", which is both false and
  // sends them to redo work that was already done. An engine failure is a state
  // the user needs to SEE, so it gets a visible, differently-coloured icon and
  // a reason, rather than an absence they have to interpret.
  setAttention(reason) {
    this._attentionReason = reason || null;
    const attention = !!reason;
    // #FFA000 (amber) against the normal #0099FF: the panel convention for
    // "needs a look", distinguishable from the healthy state at a glance and
    // without relying on the glyph changing shape.
    const color = attention ? "#FFA000" : "#0099FF";
    if (this._icon) {
      this._icon.style = "color: " + color + ";";
    }
    if (this._label) {
      this._label.style = "font-size: 16px; color: " + color + ";";
    }
    this.accessible_name = attention
      ? "InterGen — engine not serving"
      : "InterGen";
  }

  get attentionReason() {
    return this._attentionReason || null;
  }

  // Split the gestures cleanly: LEFT-click (or tap) launches/re-raises the
  // window and NEVER opens the menu; RIGHT-click opens the menu.
  //
  // PanelMenu.Button's own vfunc_event toggles the menu on ANY button press,
  // so the previous code (a separate button-press-event handler that launched
  // on button 1 and returned PROPAGATE) double-fired: left-click both launched
  // the window AND opened the menu. Overriding vfunc_event lets us own the
  // pointer gesture entirely. Keyboard menu-toggle is handled by the base
  // class's key path, which we leave untouched, so accessibility is preserved.
  vfunc_event(event) {
    const type = event.type();
    if (
      type === Clutter.EventType.BUTTON_PRESS ||
      type === Clutter.EventType.TOUCH_BEGIN
    ) {
      // touch has no button; treat a tap as a left-click.
      const button =
        type === Clutter.EventType.BUTTON_PRESS ? event.get_button() : 1;
      if (button === 1) {
        if (this.menu.isOpen) this.menu.close();
        _launchPanel();
      } else {
        // right-click (or middle) → the dock/float/browser/console menu
        this.menu.toggle();
      }
      return Clutter.EVENT_STOP;
    }
    return Clutter.EVENT_PROPAGATE;
  }

  _buildMenu() {
    let openItem = new PopupMenu.PopupMenuItem("Open InterGen");
    openItem.connect("activate", () => _launchPanel());
    this.menu.addMenuItem(openItem);

    this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    let browserItem = new PopupMenu.PopupMenuItem("Open in Browser");
    browserItem.connect("activate", () => _openPanelInBrowser());
    this.menu.addMenuItem(browserItem);

    let consoleItem = new PopupMenu.PopupMenuItem("Open Terminal Console");
    consoleItem.connect("activate", () => {
      Gio.AppInfo.create_from_commandline(
        "x-terminal-emulator -e intergen console", null,
        Gio.AppInfoCreateFlags.NONE
      ).launch([], null);
    });
    this.menu.addMenuItem(consoleItem);
  }
});

// ── Extension class ──────────────────────────────────────────────────────

const INTERGEN_BUS = "com.intergenos.InterGen";
const INTERGEN_PATH = "/com/intergenos/InterGen";

export default class InterGenExtension extends Extension {
  enable() {
    // The icon is GATED on InterGen actually being installed AND its engine
    // being up — it must NOT appear on a fresh, pre-onboarding system where
    // clicking it would be a dead end (G3-16). It is shown only once the daemon
    // reports components.llama_server === true (model downloaded + llama-server
    // serving), and hidden again if the daemon goes away.
    this._indicator = null;

    // Super+I opens the panel — but only once InterGen is ready; before that it
    // points the user at onboarding instead of launching a dead window.
    this._keybindingId = Main.wm.addKeybinding(
      "toggle-intergen",
      this.getSettings(),
      Meta.KeyBindingFlags.NONE,
      Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW,
      () => {
        if (this._indicator && this._indicator.attentionReason) {
          // Set up, but not serving. Saying "isn't set up yet" here would be
          // false and would send the user to redo onboarding they completed.
          Main.notify(
            "InterGen",
            "InterGen is set up, but its engine is not serving: " +
            this._indicator.attentionReason
          );
        } else if (this._indicator) {
          _launchPanel();
        } else {
          Main.notify(
            "InterGen",
            "InterGen isn't set up yet — open the Welcome app to finish " +
            "installing the assistant."
          );
        }
      }
    );

    // Presence is tracked via the bus name (answered by the dbus-daemon, so it
    // is reliable even while InterGen is busy). On appear/poll we confirm
    // readiness via Status; on vanish we hide definitively.
    this._nameWatchId = Gio.bus_watch_name(
      Gio.BusType.SESSION,
      INTERGEN_BUS,
      Gio.BusNameWatcherFlags.NONE,
      () => this._checkReady(),       // name appeared
      () => this._hideIndicator()     // name vanished — daemon gone
    );

    // The daemon owns the name BEFORE the model is downloaded (engine down);
    // once onboarding finishes, G3-1 restarts it and the engine comes up. The
    // name-owner change fires the appear cb, but poll too so the
    // not-ready -> ready transition is caught promptly either way.
    this._readyTimerId = GLib.timeout_add_seconds(
      GLib.PRIORITY_DEFAULT, 20, () => {
        this._checkReady();
        return GLib.SOURCE_CONTINUE;
      });

    // Probe once now in case the daemon is already up at session start.
    this._checkReady();

    console.log("[InterGen] Panel extension enabled (icon gated on readiness)");
  }

  _checkReady() {
    Gio.DBus.session.call(
      INTERGEN_BUS, INTERGEN_PATH, INTERGEN_BUS, "Status", null,
      new GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 5000, null,
      (conn, res) => {
        try {
          const [json] = conn.call_finish(res).deepUnpack();
          const status = JSON.parse(json);
          const serving = !!(status && status.components &&
                             status.components.llama_server);
          if (serving) {
            this._showIndicator();
            if (this._indicator) this._indicator.setAttention(null);
          } else if (_isOnboarded(status)) {
            // SET UP, BUT THE ENGINE IS NOT SERVING. This used to hide the
            // icon, which made an engine failure indistinguishable from a
            // machine that had never been set up — and Super+I then told an
            // onboarded user to go and install the assistant they had already
            // installed. The icon stays visible and says what is wrong.
            this._showIndicator();
            if (this._indicator) {
              this._indicator.setAttention(_engineFailureReason(status));
            }
          } else {
            // Valid response and genuinely not set up yet — no model has ever
            // been selected — so the icon stays hidden and clicking it would
            // have been a dead end.
            this._hideIndicator();
          }
        } catch (e) {
          // INCONCLUSIVE: the call timed out / errored — most often the daemon
          // is busy doing inference and its single-threaded loop can't answer
          // Status (same class as the G3-6 false negative). Do NOT flip the
          // icon on an inconclusive probe; a genuine "daemon gone" is handled
          // definitively by the name-vanished callback.
        }
      }
    );
  }

  _showIndicator() {
    if (this._indicator) return;  // already shown
    this._indicator = new InterGenIndicator();
    Main.panel.addToStatusArea(
      "intergen-panel",
      this._indicator,
      0,  // leftmost of the RIGHT box — next to the system indicators
      "right"
    );
  }

  _hideIndicator() {
    if (this._indicator) {
      this._indicator.destroy();
      this._indicator = null;
    }
  }

  disable() {
    if (this._nameWatchId) {
      Gio.bus_unwatch_name(this._nameWatchId);
      this._nameWatchId = 0;
    }
    if (this._readyTimerId) {
      GLib.source_remove(this._readyTimerId);
      this._readyTimerId = 0;
    }
    this._hideIndicator();
    if (this._keybindingId) {
      Main.wm.removeKeybinding("toggle-intergen");
      this._keybindingId = null;
    }
    console.log("[InterGen] Panel extension disabled");
  }
}
