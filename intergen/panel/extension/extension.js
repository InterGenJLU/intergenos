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
    browserItem.connect("activate", () => {
      Gio.AppInfo.create_from_commandline(
        "xdg-open http://localhost:8089", null,
        Gio.AppInfoCreateFlags.NONE
      ).launch([], null);
    });
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

// ── Screenshot D-Bus service ─────────────────────────────────────────────
//
// The InterGen tool surface needs to capture the screen for vision analysis,
// but on a Wayland session no external client may do so: the compositor owns
// every frame. External command-line grabbers (grim needs wlroots protocols
// this compositor does not speak; scrot/ImageMagick-import need X11) and the
// org.gnome.Shell.Screenshot D-Bus method (which now returns AccessDenied to
// out-of-process callers) are all non-viable. The one place a full-frame grab
// is permitted is inside the compositor process itself — where this extension
// runs. So the extension exports its own tiny D-Bus surface with a single
// Screenshot method that drives the in-process Shell.Screenshot API and writes
// the PNG to a caller-supplied absolute path. The tool calls this method.
//
// Contract, fail-loud by construction: Screenshot returns (success, path, error).
// Any failure returns success=false with a real error string and leaves NO file
// behind — never a silent empty PNG the vision model would then be handed.

const SHELL_BUS = "com.intergenos.InterGenShell";
const SHELL_PATH = "/com/intergenos/InterGenShell";

// WHO MAY CALL. "The tool calls this method" was the whole of the access model
// and the code kept no part of it: the handler declared `sender` and never read
// it, nothing asked the bus who the peer was, and the interface carried no
// secret, so every peer on the user's session bus got the full-frame grab that
// org.gnome.Shell.Screenshot answers AccessDenied for out of process — without
// the consent the daemon's own take_screenshot tool carries — and could aim the
// write at any absolute path it named.
//
// The first correction answered whoever the bus reported as OWNING the daemon's
// well-known name. A second, independent read showed that is not an identity: a
// well-known name belongs to whoever asks for it first and is free the moment
// nobody holds it, so while the daemon is not running any peer in the session can
// take that name — and this watch then names that peer, and the surface answers
// it with a full-frame grab. The owner is kept, because it is a cheap first
// refusal and because null must refuse while the bus has not answered, but it is
// no longer what admits a call: _senderIsTheDaemon asks the bus for the things a
// peer cannot claim — the uid and the pid behind the connection — and requires
// the pid to be a process running the daemon's own program.
let _intergenOwner = null;

// The program the daemon runs, as its own D-Bus and systemd units start it
// (intergen/data/com.intergenos.InterGen.service, intergen/data/intergen.service:
// "/usr/bin/intergen daemon"). The pid's /proc entry is compared against this,
// not against an interpreter: the program is a Python script, so its exe link
// reads as the interpreter on every machine and would admit any Python process.
const INTERGEN_DAEMON_PROGRAM = "/usr/bin/intergen";
const INTERGEN_DAEMON_ARGUMENT = "daemon";

// One uint out of org.freedesktop.DBus about a connection, or null if the bus
// will not say. A peer that has already gone is a null, which refuses.
function _busUintAbout(connection, method, name) {
  try {
    const reply = connection.call_sync(
      "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
      method, new GLib.Variant("(s)", [name]), new GLib.VariantType("(u)"),
      Gio.DBusCallFlags.NONE, 1000, null);
    return reply.deepUnpack()[0];
  } catch (e) {
    console.log("[InterGen] the bus would not answer " + method + ": " + e);
    return null;
  }
}

// The argument vector of a pid, as NUL-separated bytes in its own /proc entry,
// or null when there is nothing there to read.
function _argvOfPid(pid) {
  try {
    const [ok, bytes] = GLib.file_get_contents("/proc/" + pid + "/cmdline");
    if (!ok || !bytes || bytes.length === 0) return null;
    return new TextDecoder().decode(bytes).split("\0").filter((a) => a !== "");
  } catch (_e) {
    return null;
  }
}

// WHO THE PEER IS, from what the bus vouches for rather than from what it was
// able to claim. Three conditions, each refusing on its own: the peer belongs to
// this user; the bus can name a pid behind the connection; and that pid is
// running the daemon's program with its daemon argument. A pid is not a permanent
// identity — a peer that exits frees it — so this is the narrowest window the
// surface can be given from inside the compositor, not a proof of identity for
// all time; the call it admits is a single screenshot into the daemon's own temp
// path, and the destination guard stands independently of this one.
function _senderIsTheDaemon(connection, sender) {
  if (!sender) return false;
  const own = connection.get_unique_name();
  const ourUid = own ? _busUintAbout(connection, "GetConnectionUnixUser", own) : null;
  const theirUid = _busUintAbout(connection, "GetConnectionUnixUser", sender);
  if (ourUid === null || theirUid === null || ourUid !== theirUid) {
    console.log("[InterGen] Screenshot refused: the caller is not this user");
    return false;
  }
  const pid = _busUintAbout(connection, "GetConnectionUnixProcessID", sender);
  if (pid === null) {
    console.log("[InterGen] Screenshot refused: the bus names no pid for the caller");
    return false;
  }
  const argv = _argvOfPid(pid);
  if (!argv) {
    console.log("[InterGen] Screenshot refused: pid " + pid + " has no argument vector");
    return false;
  }
  const runsTheProgram = argv.some((a) => a === INTERGEN_DAEMON_PROGRAM
                                   || a.endsWith("/" + GLib.path_get_basename(
                                        INTERGEN_DAEMON_PROGRAM)));
  const carriesTheArgument = argv.includes(INTERGEN_DAEMON_ARGUMENT);
  if (!runsTheProgram || !carriesTheArgument) {
    console.log("[InterGen] Screenshot refused: pid " + pid
                + " is not running " + INTERGEN_DAEMON_PROGRAM + " "
                + INTERGEN_DAEMON_ARGUMENT);
    return false;
  }
  return true;
}

// WHERE THE BYTES MAY LAND. The caller's path is no longer taken on trust: the
// destination must be a file directly inside the temp directory this process
// resolves, named with the prefix and suffix the daemon's tool creates it with
// (tempfile.mkstemp(prefix="intergen-shot-", suffix=".png")), and it must not
// be a symbolic link — Gio.File.replace would follow one out of the directory.
const SHOT_PREFIX = "intergen-shot-";
const SHOT_SUFFIX = ".png";

function _shotDirs() {
  // GLib.get_tmp_dir() honours TMPDIR, as Python's tempfile does; "/tmp" is
  // what both resolve to when it is unset, and is listed so a compositor and a
  // daemon that disagree about TMPDIR still meet on the default.
  const dirs = [GLib.get_tmp_dir(), "/tmp"];
  return dirs.filter((d, i) => !!d && dirs.indexOf(d) === i);
}

// True when the path is a symbolic link; a link at the destination would carry
// Gio.File.replace out of the directory the caller was confined to. A path that
// is not there yet is not a link.
function _isSymlink(filename) {
  try {
    return Gio.File.new_for_path(filename).query_info(
      "standard::is-symlink", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null
    ).get_is_symlink();
  } catch (_e) {
    return false;
  }
}

const SHELL_DBUS_IFACE = `
<node>
  <interface name="com.intergenos.InterGenShell">
    <method name="Screenshot">
      <arg type="s" direction="in" name="filename"/>
      <arg type="b" direction="in" name="include_cursor"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="filename_used"/>
      <arg type="s" direction="out" name="error"/>
    </method>
  </interface>
</node>`;

// Capture the whole screen to `filename` using the compositor-internal
// Shell.Screenshot API (the same API GNOME's own screenshot service uses).
// Resolves with the written path; rejects with a real Error on any failure.
// The callback form is used deliberately so this works whether or not the
// shell has promisified Shell.Screenshot.prototype.screenshot.
function _shellCaptureToFile(filename, includeCursor) {
  return new Promise((resolve, reject) => {
    let file, stream;
    try {
      file = Gio.File.new_for_path(filename);
      // replace() truncates/creates; the compositor process runs as the user,
      // so the caller's temp path is writable here.
      //
      // REPLACE_DESTINATION, not NONE. The handler checks the destination and
      // then this opens it, and those are two operations on a path any peer in
      // the session can change in between: under NONE a symbolic link placed in
      // that window is FOLLOWED, and the capture lands on whatever it points
      // at, outside the directory the caller was confined to, with the link
      // left in place for the next one. Under REPLACE_DESTINATION gio writes a
      // new file and moves it into place, so the link itself is what gets
      // replaced and the file it pointed at is untouched. The check above stays
      // as the loud refusal for a link that is already there; this is what
      // closes the window after it.
      stream = file.replace(
        null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
    } catch (e) {
      reject(new Error(`cannot open output file ${filename}: ${e.message}`));
      return;
    }
    let shooter;
    try {
      shooter = new Shell.Screenshot();
    } catch (e) {
      try { stream.close(null); } catch (_e) {}
      reject(new Error(`Shell.Screenshot unavailable: ${e.message}`));
      return;
    }
    shooter.screenshot(includeCursor, stream, (obj, res) => {
      try {
        const ret = shooter.screenshot_finish(res);
        // screenshot_finish returns (success, area); tolerate a scalar too.
        const success = Array.isArray(ret) ? !!ret[0] : !!ret;
        stream.close(null);
        if (!success) {
          reject(new Error("Shell.Screenshot reported capture failure"));
          return;
        }
        resolve(file.get_path());
      } catch (e) {
        try { stream.close(null); } catch (_e) {}
        reject(new Error(`Shell.Screenshot failed: ${e.message}`));
      }
    });
  });
}

function _screenshotErrorReturn(invocation, message) {
  invocation.return_value(new GLib.Variant("(bss)", [false, "", message]));
}

function _onShellMethodCall(
  connection, sender, path, ifaceName, methodName, params, invocation
) {
  if (methodName !== "Screenshot") {
    invocation.return_dbus_error(
      "com.intergenos.InterGenShell.Error.UnknownMethod",
      `Unknown method: ${methodName}`
    );
    return;
  }

  // The caller, before anything else is read from the call. The name's owner is
  // the cheap first refusal and _senderIsTheDaemon is what admits the call: a
  // peer that claimed the name while the daemon was away passes the first and is
  // refused by the second.
  if (!sender || !_intergenOwner || sender !== _intergenOwner
      || !_senderIsTheDaemon(connection, sender)) {
    console.log(
      "[InterGen] Shell D-Bus service refused Screenshot from " +
      (sender || "an unnamed peer") + " (the InterGen daemon is " +
      (_intergenOwner ? _intergenOwner : "not on this bus") + ")"
    );
    invocation.return_dbus_error(
      "com.intergenos.InterGenShell.Error.AccessDenied",
      "Screenshot is answered only for the InterGen daemon: the peer must hold " +
      "the daemon's name on this bus AND be the daemon's own process, as this " +
      "bus reports the connection's user and pid. This peer is not it."
    );
    return;
  }

  let filename, includeCursor;
  try {
    filename = params.get_child_value(0).get_string()[0];
    includeCursor = params.get_child_value(1).get_boolean();
  } catch (e) {
    _screenshotErrorReturn(invocation, `bad arguments: ${e.message}`);
    return;
  }
  if (!filename || !GLib.path_is_absolute(filename)) {
    _screenshotErrorReturn(
      invocation, "filename must be a non-empty absolute path"
    );
    return;
  }
  // The destination, decided here beside the caller and not in a helper: these
  // are the two things this method authorises, and a reader checking that it
  // authorises them should find both in the one function.
  const dir = GLib.path_get_dirname(filename);
  const base = GLib.path_get_basename(filename);
  const dirs = _shotDirs();
  if (!dirs.includes(dir)) {
    _screenshotErrorReturn(
      invocation,
      `refusing ${filename}: the destination must be a file directly inside ` +
      dirs.join(" or ")
    );
    return;
  }
  if (!base.startsWith(SHOT_PREFIX) || !base.endsWith(SHOT_SUFFIX)) {
    _screenshotErrorReturn(
      invocation,
      `refusing ${filename}: the destination name must start with ` +
      `${SHOT_PREFIX} and end with ${SHOT_SUFFIX}`
    );
    return;
  }
  if (_isSymlink(filename)) {
    _screenshotErrorReturn(
      invocation, `refusing ${filename}: the destination is a symbolic link`
    );
    return;
  }

  _shellCaptureToFile(filename, includeCursor).then((usedPath) => {
    // Prove the capture actually produced bytes — a zero-byte PNG is a
    // silent failure the vision model must never be handed.
    let size = -1;
    try {
      const info = Gio.File.new_for_path(usedPath).query_info(
        "standard::size", Gio.FileQueryInfoFlags.NONE, null
      );
      size = info.get_size();
    } catch (e) {
      _screenshotErrorReturn(
        invocation, `capture wrote no readable file: ${e.message}`
      );
      return;
    }
    if (size <= 0) {
      try { Gio.File.new_for_path(usedPath).delete(null); } catch (_e) {}
      _screenshotErrorReturn(invocation, "capture produced an empty file");
      return;
    }
    invocation.return_value(new GLib.Variant("(bss)", [true, usedPath, ""]));
  }).catch((e) => {
    // Leave no partial/stale PNG behind on failure.
    try { Gio.File.new_for_path(filename).delete(null); } catch (_e) {}
    const msg = e && e.message ? e.message : String(e);
    _screenshotErrorReturn(invocation, msg);
  });
}

// ── Extension class ──────────────────────────────────────────────────────

const INTERGEN_BUS = "com.intergenos.InterGen";
const INTERGEN_PATH = "/com/intergenos/InterGen";

export default class InterGenExtension extends Extension {
  enable() {
    // Screenshot D-Bus surface — registered FIRST and unconditionally while the
    // extension is enabled (the shell is up), independent of the indicator
    // readiness gate and the keybinding/settings setup below: the capture
    // capability lives in this process, not the daemon's, and must not be lost
    // if any later setup step fails.
    this._shellDbusRegId = 0;
    this._shellDbusNameId = 0;
    this._shellCallerWatchId = 0;
    try {
      const nodeInfo = Gio.DBusNodeInfo.new_for_xml(SHELL_DBUS_IFACE);
      this._shellDbusRegId = Gio.DBus.session.register_object(
        SHELL_PATH, nodeInfo.interfaces[0], _onShellMethodCall, null, null
      );
      this._shellDbusNameId = Gio.bus_own_name_on_connection(
        Gio.DBus.session, SHELL_BUS, Gio.BusNameOwnerFlags.NONE, null, null
      );
      // The one peer this surface answers, kept current by the bus itself. It
      // is set up here, beside the registration, because a surface that cannot
      // name its caller must refuse every call rather than guess: _intergenOwner
      // stays null until the bus answers, and null refuses.
      this._shellCallerWatchId = Gio.bus_watch_name_on_connection(
        Gio.DBus.session, INTERGEN_BUS, Gio.BusNameWatcherFlags.NONE,
        (_connection, _name, owner) => { _intergenOwner = owner || null; },
        () => { _intergenOwner = null; }
      );
      console.log("[InterGen] Shell D-Bus service enabled (" + SHELL_BUS + ")");
    } catch (e) {
      console.log("[InterGen] Could not register Shell D-Bus service: " + e);
    }

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
    if (this._shellCallerWatchId) {
      Gio.bus_unwatch_name(this._shellCallerWatchId);
      this._shellCallerWatchId = 0;
    }
    // The surface is going away; so is any memory of who was allowed to use it.
    _intergenOwner = null;
    if (this._shellDbusNameId) {
      Gio.bus_unown_name(this._shellDbusNameId);
      this._shellDbusNameId = 0;
    }
    if (this._shellDbusRegId) {
      Gio.DBus.session.unregister_object(this._shellDbusRegId);
      this._shellDbusRegId = 0;
    }
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
