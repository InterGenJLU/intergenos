# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The `chronicle` command-line client (spec §11).

argparse with add_subparsers + a cmd_* dispatch dict, a shared -v/-q verbosity
parent, --yes/-y, per-subcommand --json, and an opt-in tiered reporter — the pkm
house pattern. Every verb mirrors the engine API, so every GUI action has an
equivalent CLI command (spec §1: the GUI and CLI are peers).

The client reaches the engine through the IPC socket when the service is
running (the single-writer path); when it is not, it drives an in-process engine
directly (the CLI runs as root via its units / a polkit-authorized launch). Both
paths route through the same api.dispatch verb map, so behaviour is identical.
"""

import argparse
import json
import sys

from . import api as _api
from . import engine as _engine
from . import paths as _paths

LAYER_CHOICES = list(_paths.LAYERS)


# --------------------------------------------------------------------------
# tiered reporter (opt-in transparency, quiet by default at ERROR)
# --------------------------------------------------------------------------

QUIET, NORMAL, VERBOSE = 0, 1, 2


class Reporter:
    def __init__(self, level=NORMAL, stream=None, err=None):
        self.level = level
        self.out = stream or sys.stdout
        self.err = err or sys.stderr

    @classmethod
    def from_args(cls, args):
        if getattr(args, "quiet", False):
            return cls(QUIET)
        if getattr(args, "verbose", False):
            return cls(VERBOSE)
        return cls(NORMAL)

    def info(self, text):
        if self.level >= NORMAL:
            print(text, file=self.out)

    def note(self, text):
        if self.level >= VERBOSE:
            print(text, file=self.out)

    def warn(self, text):
        print(f"WARNING: {text}", file=self.err)

    def error(self, text):
        print(f"ERROR: {text}", file=self.err)


class Backend:
    """Routes verbs to the socket when present, else an in-process engine."""

    def __init__(self, socket_path=None, local_root=None, config_path=None):
        self.client = _api.Client(socket_path)
        self._engine = None
        self._local_root = local_root
        self._config_path = config_path

    def call(self, verb, **args):
        if self.client.available():
            try:
                resp = self.client.call(verb, **args)
            except _api.EngineAccessDenied as e:
                # The engine is running and this account may not open its
                # socket. Falling through to the in-process engine would be
                # worse than useless: it would run a second engine as an
                # unprivileged user against a root-owned store and report
                # whatever partial truth that produced. Say what is wrong.
                raise RuntimeError(str(e)) from e
        else:
            if self._engine is None:
                self._engine = _engine.Engine(
                    local_root=self._local_root, config_path=self._config_path)
            resp = _api.dispatch(self._engine, {"verb": verb, "args": args})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "engine error"))
        return resp.get("result")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def _emit_json(obj):
    print(json.dumps(obj, indent=2, sort_keys=True))


def cmd_status(backend, args, rep):
    st = backend.call("status")
    if args.json:
        return _emit_json(st)
    t = st.get("target")
    rep.info("Chronicle status")
    if t:
        present = "attached" if st.get("target_present") else "NOT attached"
        rep.info(f"  Target: {t.get('mountpoint')} ({t.get('class')}) — {present}")
    else:
        rep.info("  Target: none adopted — only the always-on local history is active")
    free = st.get("target_free_bytes")
    if free is not None:
        rep.info(f"  Target free: {_human(free)}")
    for layer, w in sorted(st.get("last_capture", {}).items()):
        rep.info(f"  Last {layer}: {_ts(w)}")
    q = st.get("queue", {})
    if q.get("summary"):
        rep.info(f"  {q['summary']}")
    if st.get("pins"):
        rep.info(f"  Pinned: {len(st['pins'])} version(s)")
    for ev in st.get("clock_skew_events", []):
        rep.warn(f"system clock moved backward at sequence {ev['at_sequence']}")


def cmd_list(backend, args, rep):
    vs = backend.call("list", layer=args.layer, since=args.since, until=args.until)
    if args.json:
        return _emit_json(vs)
    if not vs:
        rep.info(f"No {args.layer} versions yet.")
        return
    rep.info(f"{args.layer} timeline ({len(vs)} version(s)):")
    for v in vs:
        pin = " [pinned]" if v.get("pinned") else ""
        rep.info(f"  {v['version_id']}  {_ts(v['wall_clock'])}  "
                 f"{v['files']} files  {v.get('reason','')}{pin}")


def cmd_capture(backend, args, rep):
    scope = args.paths or None
    res = backend.call("capture", layer=args.layer, scope=scope,
                       reason=args.reason or f"manual {args.layer} capture",
                       sync=not args.async_)
    if args.json:
        return _emit_json(res)
    if "queued" in res:
        rep.info(f"Queued for the off-peak window: {res['queued']}")
    else:
        rep.info(f"Captured {args.layer} version {res['version_id']}")


def cmd_diff(backend, args, rep):
    d = backend.call("diff", layer=args.layer, version_id=args.version,
                     path=args.path)
    if args.json:
        return _emit_json(d)
    state = "CHANGED" if d["changed"] else "unchanged"
    rep.info(f"{args.path}: {state}")
    rep.info(f"  stored : {d['stored_sha256']}")
    rep.info(f"  live   : {d['live_sha256'] or '(absent)'}")


def cmd_restore(backend, args, rep):
    plan = backend.call("restore-plan", layer=args.layer,
                        version_id=args.version, paths=args.paths, mode=args.mode)
    if args.json and args.dry_run:
        return _emit_json(plan)
    rep.info(f"Restore plan for {args.version} (mode: {args.mode}):")
    for a in plan["actions"]:
        if a["action"] == "skip":
            rep.info(f"  SKIP {a['path']} — {a['reason']}")
        elif a.get("will_overwrite"):
            rep.info(f"  OVERWRITE (with confirmation) {a['path']}")
        else:
            rep.info(f"  restore {a['path']}")
    if args.dry_run:
        rep.info("Dry run — nothing was changed.")
        return
    # Never a silent overwrite (spec §8): confirm unless --yes.
    if not args.yes:
        if not sys.stdin.isatty():
            rep.error("stdin is not a tty; pass --yes to confirm the restore, "
                      "or --dry-run to preview.")
            sys.exit(1)
        ans = input("  Proceed with restore? [y/N] ").strip().lower()
        if ans != "y":
            rep.info("  Aborted.")
            return
    res = backend.call("restore", layer=args.layer, version_id=args.version,
                       paths=args.paths, mode=args.mode)
    if args.json:
        return _emit_json(res)
    for r in res["results"]:
        if r["ok"]:
            rep.info(f"  restored {r['path']} -> {r['written_to']}")
        else:
            rep.error(f"  {r['path']}: {r['reason']}")


def cmd_verify(backend, args, rep):
    if args.scrub:
        res = backend.call("scrub")
        if args.json:
            return _emit_json(res)
        if res["clean"]:
            rep.info("Scrub complete — every stored version verifies.")
        else:
            rep.error(f"Scrub found {len(res['corrupt'])} corrupt item(s):")
            for c in res["corrupt"]:
                where = c.get("sha256") or c.get("path")
                rep.error(f"  {where} — affects versions: "
                          f"{', '.join(c.get('versions', []))}")
        return
    if not (args.layer and args.version):
        rep.error("verify needs <layer> <version>, or --scrub")
        sys.exit(1)
    res = backend.call("verify", layer=args.layer, version_id=args.version)
    if args.json:
        return _emit_json(res)
    if res["ok"]:
        rep.info(f"{args.version} verifies intact.")
    else:
        rep.error(f"{args.version} FAILED verification:")
        for p in res["problems"]:
            rep.error(f"  {p}")


def cmd_target(backend, args, rep):
    if args.target_action == "scan":
        cands = backend.call("target-scan")
        if args.json:
            return _emit_json(cands)
        if not cands:
            rep.info("No candidate volumes found.")
            return
        rep.info("Candidate backup targets (strongest protection first):")
        for c in cands:
            rep.info(f"  {c['device']}  [{c['fstype']}]  {c['protection_text']}")
            if c["disqualified"]:
                rep.info(f"      unavailable as-is: {c['disqualified']}")
            for opt in (c.get("remediation") or {}).get("options", []):
                rep.info(f"      option: {opt['action']} — {opt['description']}")
            for tgt in c.get("supported_targets", []):
                rep.info(f"      target: {tgt['mode']} — {tgt['description']}")
    elif args.target_action == "adopt":
        res = backend.call("target-adopt", mountpoint=args.mountpoint,
                           target_class=args.klass, device=args.device,
                           cap_bytes=args.cap)
        if args.json:
            return _emit_json(res)
        rep.info(f"Adopted target at {res['root']} ({res['class']}).")


def cmd_pin(backend, args, rep):
    res = backend.call("pin", version_id=args.version)
    rep.info(f"Pinned {res['pinned']}.") if not args.json else _emit_json(res)


def cmd_unpin(backend, args, rep):
    res = backend.call("unpin", version_id=args.version)
    rep.info(f"Unpinned {res['unpinned']}.") if not args.json else _emit_json(res)


def cmd_queue(backend, args, rep):
    q = backend.call("queue-status")
    if args.json:
        return _emit_json(q)
    if q["count"] == 0:
        rep.info("The capture queue is empty.")
        return
    rep.info(q["summary"])
    for intent in q["intents"]:
        rep.info(f"  {intent.get('layer')}  {intent.get('reason','')}")


COMMANDS = {
    "status": cmd_status, "list": cmd_list, "capture": cmd_capture,
    "diff": cmd_diff, "restore": cmd_restore, "verify": cmd_verify,
    "target": cmd_target, "pin": cmd_pin, "unpin": cmd_unpin,
    "queue": cmd_queue,
}


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_mutually_exclusive_group()
    # default=SUPPRESS so these parents can be attached to BOTH the top parser
    # and every subparser without the subparser's default clobbering a value
    # the top parser set — `chronicle -q status` and `chronicle status -q` both
    # take effect. The namespace is pre-seeded with the real defaults in main().
    g.add_argument("-v", "--verbose", action="store_true",
                   default=argparse.SUPPRESS, help="show more detail")
    g.add_argument("-q", "--quiet", action="store_true",
                   default=argparse.SUPPRESS, help="errors only")
    common.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS,
                        help="machine-readable JSON output")

    ap = argparse.ArgumentParser(
        prog="chronicle", parents=[common],
        description="Back up and restore system and user data over time.")
    ap.add_argument("--socket", default=None, help="engine socket path")
    ap.add_argument("--local-root", default=None, help="local store root (testing)")
    ap.add_argument("--config", default=None, help="chronicle.conf path (testing)")
    sub = ap.add_subparsers(dest="command", metavar="command")

    sub.add_parser("status", parents=[common], help="target + capture health")

    p = sub.add_parser("list", parents=[common], help="browse a layer's timeline")
    p.add_argument("layer", choices=LAYER_CHOICES)
    p.add_argument("--since", type=int, default=None, help="epoch lower bound")
    p.add_argument("--until", type=int, default=None, help="epoch upper bound")

    p = sub.add_parser("capture", parents=[common], help="take a version now")
    p.add_argument("layer", choices=LAYER_CHOICES)
    p.add_argument("paths", nargs="*", help="config-state scope override")
    p.add_argument("--reason", default="")
    p.add_argument("--async", dest="async_", action="store_true",
                   help="queue for the off-peak window instead of now")

    p = sub.add_parser("diff", parents=[common], help="then-vs-now for a path")
    p.add_argument("layer", choices=LAYER_CHOICES)
    p.add_argument("version")
    p.add_argument("path")

    p = sub.add_parser("restore", parents=[common],
                       help="restore paths from a version (never silent)")
    p.add_argument("layer", choices=LAYER_CHOICES)
    p.add_argument("version")
    p.add_argument("paths", nargs="+")
    p.add_argument("--mode", choices=["replace-confirm", "beside"],
                   default="replace-confirm")
    p.add_argument("--dry-run", action="store_true", help="show the plan only")
    p.add_argument("-y", "--yes", action="store_true", help="skip the prompt")

    p = sub.add_parser("verify", parents=[common],
                       help="verify a version, or --scrub the whole store")
    p.add_argument("layer", nargs="?", choices=LAYER_CHOICES)
    p.add_argument("version", nargs="?")
    p.add_argument("--scrub", action="store_true")

    p = sub.add_parser("target", parents=[common], help="scan / adopt a target")
    tsub = p.add_subparsers(dest="target_action", metavar="action")
    tsub.add_parser("scan", parents=[common], help="enumerate candidate volumes")
    pa = tsub.add_parser("adopt", parents=[common], help="initialize a target")
    pa.add_argument("mountpoint")
    pa.add_argument("--class", dest="klass",
                    choices=["whole-volume", "directory"], default="whole-volume")
    pa.add_argument("--device", default=None)
    pa.add_argument("--cap", type=int, default=None,
                    help="size cap in bytes (directory class)")

    p = sub.add_parser("pin", parents=[common], help="protect a version from pruning")
    p.add_argument("version")
    p = sub.add_parser("unpin", parents=[common], help="release a pin")
    p.add_argument("version")

    sub.add_parser("queue", parents=[common], help="show the capture queue")
    return ap


def main(argv=None):
    ap = build_parser()
    # Pre-seed the verbosity/format flags (the common parents use
    # default=SUPPRESS so a flag given at EITHER position survives — see
    # build_parser). getattr-safe defaults for every command that reads them.
    seed = argparse.Namespace(quiet=False, verbose=False, json=False)
    args = ap.parse_args(argv, namespace=seed)
    if not args.command:
        ap.print_help()
        return 2
    rep = Reporter.from_args(args)
    backend = Backend(socket_path=args.socket, local_root=args.local_root,
                      config_path=args.config)
    try:
        COMMANDS[args.command](backend, args, rep)
        return 0
    except RuntimeError as e:
        rep.error(str(e))
        return 1


def _human(n):
    n = float(n or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}"
        n /= 1024


def _ts(epoch):
    import time
    if not epoch:
        return "(never)"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


if __name__ == "__main__":
    sys.exit(main())
