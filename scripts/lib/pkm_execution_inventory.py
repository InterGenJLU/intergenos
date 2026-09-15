# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Derive and verify the package-manager execution-surface contract."""

from __future__ import annotations

import ast
import csv
import hashlib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


INVENTORY_COLUMNS = (
    "key",
    "legacy_id",
    "kind",
    "owner",
    "source",
    "selector",
    "target",
    "fingerprint",
    "program",
    "interpreter",
    "launched_by",
    "runs_as",
    "reads",
    "writes",
    "executes",
    "network_hosts",
    "verification",
    "capabilities_needed",
    "child_profile_recommendation",
    "shape",
    "launch_risk",
)

PROCESS_COLUMNS = (
    "call_key",
    "module",
    "qualname",
    "canonical_api",
    "variant",
    "dispatch_key",
    "command_builder",
    "fingerprint",
)

SHAPE_COLUMNS = (
    "shape",
    "row_count",
    "members",
    "reads",
    "writes",
    "executes",
    "network_hosts",
    "capabilities_needed",
    "profile_recommendation",
    "description",
)

SAFE_LAUNCH_COLUMNS = (
    "key",
    "owner",
    "target",
    "source",
    "form",
    "expected_prefix",
    "fingerprint",
)

_HEX64 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HELPER_TARGET = re.compile(r"/usr/bin/(igos-install-[A-Za-z0-9_-]+)")
_HEREDOC = re.compile(r"<<\s*'?([A-Za-z0-9_]+)'?")
_STABLE_DEST = re.compile(
    r"var/lib/pkm/hooks/(?P<owner>[A-Za-z0-9_.+-]+)/"
    r"(?P<event>post-install|pre-remove|post-remove)"
)
_PRETXN_DEST = re.compile(
    r"(?:pre-transaction\.d/|\$\{?pretxndir\}?/)"
    r"(?P<name>[A-Za-z0-9_.+-]+)"
)
_EULA_DECL = re.compile(
    r"^eula_helper:\s*['\"]?(?P<name>[A-Za-z0-9_.+-]+)['\"]?\s*$",
    re.MULTILINE,
)
_PYTHON_WRAPPER = re.compile(
    r"^\s*exec\s+(?P<command>/usr/bin/python3(?:\s+-P)?\s+-m\s+pkm)"
    r"\s+[\"']?\$@[\"']?\s*$",
    re.MULTILINE,
)
_HOOK_OPENER = re.compile(
    r"^(?:function\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*(?:\(\s*\))?\s*\{\s*$"
)
LIFECYCLE_EVENTS = (
    "pre_install", "post_install", "pre_upgrade", "post_upgrade",
    "pre_remove", "post_remove",
)
EXPECTED_HOOKSEAL_AST_SHA256 = "sha256:8e932b37ba6837ae42953c92d33aae3c159681c9a379130b813195a8436871fc"

_PROCESS_APIS = frozenset({
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.call",
    "_trace.traced_run",
    "os.execv",
    "os.execve",
    "os.execl",
    "os.execlp",
    "os.execvp",
    "os.system",
    "os.popen",
    "os.posix_spawn",
    "os.posix_spawnp",
    "os.spawnl",
    "os.spawnle",
    "os.spawnlp",
    "os.spawnlpe",
    "os.spawnv",
    "os.spawnve",
    "os.spawnvp",
    "os.spawnvpe",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
})


class InventoryError(RuntimeError):
    """The tree or a contract file could not be inventoried completely."""


@dataclass(frozen=True)
class Surface:
    key: str
    kind: str
    owner: str
    source: str
    selector: str
    target: str
    fingerprint: str


@dataclass(frozen=True)
class ProcessCall:
    call_key: str
    module: str
    qualname: str
    canonical_api: str
    variant: str
    command_builder: str
    fingerprint: str


@dataclass(frozen=True)
class SafeLauncher:
    key: str
    owner: str
    target: str
    source: str
    form: str
    expected_prefix: str
    fingerprint: str


def _sha_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha_text(value: str) -> str:
    return _sha_bytes(value.encode("utf-8", "surrogateescape"))


def _ast_sha(*nodes: ast.AST) -> str:
    rendered = "\n".join(
        ast.dump(node, annotate_fields=True, include_attributes=False)
        for node in nodes
    )
    return _sha_text(rendered)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise InventoryError(f"cannot read {path}: {error}") from error


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise InventoryError(f"source escapes repository root: {path}") from error


def _add_surface(found: dict[str, Surface], surface: Surface) -> None:
    if surface.key in found:
        raise InventoryError(f"duplicate live execution key: {surface.key}")
    found[surface.key] = surface


def _hookseal_contract(root: Path) -> str:
    path = root / "igos-build/hookseal.py"
    if not path.is_file():
        raise InventoryError(f"hook sealer missing: {_relative(root, path)}")
    try:
        tree = ast.parse(_read(path), filename=str(path))
    except SyntaxError as error:
        raise InventoryError(f"cannot parse hook sealer: {error}") from error
    selected: list[ast.AST] = []
    events = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in {"LIFECYCLE_EVENTS", "_OPENER"}
            for target in node.targets
        ):
            selected.append(node)
            if any(
                isinstance(target, ast.Name) and target.id == "LIFECYCLE_EVENTS"
                for target in node.targets
            ):
                try:
                    events = ast.literal_eval(node.value)
                except (ValueError, TypeError) as error:
                    raise InventoryError("hook sealer event list is not literal") from error
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "_bash_available", "_syntax_ok", "extract_function", "locate_function",
        }:
            selected.append(node)
    if tuple(events or ()) != LIFECYCLE_EVENTS:
        raise InventoryError(
            f"hook sealer lifecycle events changed: {events!r}"
        )
    digest = _ast_sha(*selected)
    if digest != EXPECTED_HOOKSEAL_AST_SHA256:
        raise InventoryError(
            "hook sealer extraction contract changed: "
            f"expected {EXPECTED_HOOKSEAL_AST_SHA256}, tree {digest}"
        )
    return digest


def _locate_lifecycle(text: str, event: str) -> tuple[str, int] | None:
    if event not in LIFECYCLE_EVENTS:
        raise InventoryError(f"unknown lifecycle event: {event}")
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        match = _HOOK_OPENER.match(line)
        if match and match.group("name") == event:
            start = index
            break
    if start is None:
        return None
    for end in range(start + 1, len(lines)):
        if lines[end].startswith("}") and lines[end].rstrip() == "}":
            return "\n".join(lines[start + 1:end]), start + 2
    raise InventoryError(
        f"{event} opens at line {start + 1} without a column-zero closer"
    )


def _active_lines(body: str) -> list[str]:
    active: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"\s+#.*$", "", line).strip()
        if line:
            active.append(line)
    return active


def is_semantic_noop(body: str) -> bool:
    """Conservatively classify a sealed lifecycle body with no external effect."""
    for line in _active_lines(body):
        if re.fullmatch(r"set(?:\s+-[^\s]+)+", line):
            continue
        if line in {":", "true", "return", "return 0"}:
            continue
        if (
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", line)
            and "$(" not in line
            and "`" not in line
        ):
            continue
        return False
    return True


def _scan_lifecycle(root: Path, found: dict[str, Surface]) -> None:
    _hookseal_contract(root)
    packages = root / "packages"
    if not packages.is_dir():
        raise InventoryError("packages tree is missing")
    for build_sh in sorted(packages.glob("*/*/build.sh")):
        owner = build_sh.parent.name
        text = _read(build_sh)
        for event in LIFECYCLE_EVENTS:
            try:
                located = _locate_lifecycle(text, event)
            except Exception as error:
                raise InventoryError(
                    f"cannot extract {event} from {_relative(root, build_sh)}: {error}"
                ) from error
            if located is None:
                continue
            body, _first_line = located
            kind = "archive_noop" if is_semantic_noop(body) else "archive_lifecycle"
            _add_surface(found, Surface(
                key=f"archive:{owner}:{event}",
                kind=kind,
                owner=owner,
                source=_relative(root, build_sh),
                selector=event,
                target=f"archive:.scripts/{event}.sh",
                fingerprint=_sha_text(body),
            ))


def _heredoc_helpers(path: Path) -> list[tuple[str, str]]:
    lines = _read(path).splitlines()
    helpers: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        target = _HELPER_TARGET.search(line)
        marker = _HEREDOC.search(line)
        if target is None or marker is None:
            continue
        terminator = marker.group(1)
        for end in range(index + 1, len(lines)):
            if lines[end].strip() == terminator:
                helpers.append((target.group(1), "\n".join(lines[index + 1:end]) + "\n"))
                break
        else:
            raise InventoryError(
                f"unterminated helper heredoc in {path} at line {index + 1}"
            )
    return helpers


def _scan_download_helpers(root: Path, found: dict[str, Surface]) -> None:
    packages = root / "packages"
    declared_targets: set[tuple[str, str]] = set()
    for build_sh in sorted(packages.glob("*/*/build.sh")):
        owner = build_sh.parent.name
        for line in _logical_shell_lines(_read(build_sh)):
            for match in _HELPER_TARGET.finditer(line):
                declared_targets.add((owner, match.group(1)))
        for name, body in _heredoc_helpers(build_sh):
            _add_surface(found, Surface(
                key=f"download-helper:{owner}:{name}",
                kind="download_helper",
                owner=owner,
                source=_relative(root, build_sh),
                selector="download-helper",
                target=f"/usr/bin/{name}",
                fingerprint=_sha_text(body),
            ))

    for helper in sorted(packages.glob("*/*/helper/igos-install-*")):
        owner = helper.parents[1].name
        name = helper.name
        _add_surface(found, Surface(
            key=f"download-helper:{owner}:{name}",
            kind="download_helper",
            owner=owner,
            source=_relative(root, helper),
            selector="download-helper",
            target=f"/usr/bin/{name}",
            fingerprint=_sha_bytes(helper.read_bytes()),
        ))
    inventoried = {
        (surface.owner, Path(surface.target).name)
        for surface in found.values()
        if surface.kind == "download_helper"
    }
    if declared_targets != inventoried:
        raise InventoryError(
            "download-helper producers disagree with installed targets: "
            f"unparsed={sorted(declared_targets - inventoried)!r} "
            f"uninstalled={sorted(inventoried - declared_targets)!r}"
        )


def _scan_eula_helpers(root: Path, found: dict[str, Surface]) -> None:
    for manifest in sorted((root / "packages").glob("*/*/package.yml")):
        match = _EULA_DECL.search(_read(manifest))
        if match is None:
            continue
        owner = manifest.parent.name
        name = match.group("name")
        candidates = [
            path for path in (
                manifest.parent / "eula-helper" / name,
                manifest.parent / "eula-helper" / f"{name}.py",
                manifest.parent / "helper" / name,
                manifest.parent / "helper" / f"{name}.py",
            )
            if path.is_file()
        ]
        if len(candidates) != 1:
            raise InventoryError(
                f"eula_helper {owner}:{name} resolves to {len(candidates)} source files"
            )
        source = candidates[0]
        build_text = _read(manifest.parent / "build.sh")
        target = f"/usr/lib/intergen/eula-helpers/{name}"
        if target not in build_text:
            raise InventoryError(
                f"eula_helper {owner}:{name} has no installed target {target}"
            )
        _add_surface(found, Surface(
            key=f"eula-helper:{owner}:{name}",
            kind="eula_helper",
            owner=owner,
            source=_relative(root, source),
            selector="eula-helper",
            target=target,
            fingerprint=_sha_bytes(source.read_bytes()),
        ))


def _logical_shell_lines(text: str) -> list[str]:
    logical: list[str] = []
    pending = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if raw.rstrip().endswith("\\"):
            pending += raw.rstrip()[:-1] + " "
            continue
        line = (pending + raw).strip()
        pending = ""
        if line and not line.startswith("#"):
            logical.append(line)
    if pending:
        raise InventoryError("shell source ends in an unterminated continuation")
    return logical


def _source_candidates(root: Path, package_dir: Path, token: str) -> list[Path]:
    token = token.replace("${BUILD_DIR}", str(package_dir)).replace(
        "$BUILD_DIR", str(package_dir)
    )
    token = token.replace("/mnt/intergenos/", str(root) + "/")
    path = Path(token)
    direct = path if path.is_absolute() else package_dir / path
    if direct.is_file():
        return [direct]
    candidates = [
        item for item in root.glob(f"assets/**/{path.name}") if item.is_file()
    ]
    return sorted(candidates)


def _install_source(root: Path, package_dir: Path, line: str) -> Path:
    try:
        words = shlex.split(line)
    except ValueError as error:
        raise InventoryError(f"cannot parse install command {line!r}: {error}") from error
    if len(words) < 3 or Path(words[0]).name != "install":
        raise InventoryError(f"unsupported hook install command: {line}")
    source_token = words[-2]
    candidates = _source_candidates(root, package_dir, source_token)
    if len(candidates) != 1:
        raise InventoryError(
            f"hook source {source_token!r} resolves to {len(candidates)} files"
        )
    return candidates[0]


def _scan_stable_and_pretransaction(root: Path, found: dict[str, Surface]) -> None:
    for build_sh in sorted((root / "packages").glob("*/*/build.sh")):
        owner = build_sh.parent.name
        text = _read(build_sh)
        saw_pretxn_dir = "pre-transaction.d" in text
        parsed_pretxn = 0
        for line in _logical_shell_lines(text):
            stable = _STABLE_DEST.search(line)
            if stable is not None and not re.match(r"^(?:/usr/bin/)?install\b", line):
                raise InventoryError(
                    f"unsupported stable-hook producer in {_relative(root, build_sh)}: {line}"
                )
            if stable is not None:
                source = _install_source(root, build_sh.parent, line)
                event = stable.group("event")
                installed_owner = stable.group("owner")
                if installed_owner != owner:
                    raise InventoryError(
                        f"{_relative(root, build_sh)} installs a hook for "
                        f"{installed_owner}, not its owning package {owner}"
                    )
                _add_surface(found, Surface(
                    key=f"stable-hook:{owner}:{event}",
                    kind="stable_hook",
                    owner=owner,
                    source=_relative(root, source),
                    selector=event,
                    target=f"/var/lib/pkm/hooks/{owner}/{event}",
                    fingerprint=_sha_bytes(source.read_bytes()),
                ))

            pretxn = _PRETXN_DEST.search(line)
            if pretxn is not None and not re.match(r"^(?:/usr/bin/)?install\b", line):
                raise InventoryError(
                    f"unsupported pre-transaction producer in {_relative(root, build_sh)}: {line}"
                )
            if pretxn is not None:
                source = _install_source(root, build_sh.parent, line)
                name = pretxn.group("name")
                parsed_pretxn += 1
                _add_surface(found, Surface(
                    key=f"pre-transaction:{owner}:{name}",
                    kind="pre_transaction",
                    owner=owner,
                    source=_relative(root, source),
                    selector="pre-transaction",
                    target=f"/usr/lib/pkm/pre-transaction.d/{name}",
                    fingerprint=_sha_bytes(source.read_bytes()),
                ))
        if saw_pretxn_dir and parsed_pretxn == 0:
            raise InventoryError(
                f"{_relative(root, build_sh)} mentions pre-transaction.d but "
                "no installed handler could be derived"
            )


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self):
        self.stack: list[str] = []
        self.functions: dict[str, ast.AST] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node.name)
        self.functions[".".join(self.stack)] = node
        self.generic_visit(node)
        self.stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)


def _parse_python(path: Path) -> tuple[ast.Module, dict[str, ast.AST], dict[str, str]]:
    text = _read(path)
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as error:
        raise InventoryError(f"cannot parse {path}: {error}") from error
    collector = _FunctionCollector()
    collector.visit(tree)
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return tree, collector.functions, constants


def _required_function(functions: dict[str, ast.AST], name: str, source: str) -> ast.AST:
    node = functions.get(name)
    if node is None:
        raise InventoryError(f"required execution builder {source}:{name} is missing")
    return node


def _resolve_string(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _list_head(node: ast.AST | None, constants: dict[str, str]) -> str | None:
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        return _resolve_string(node.elts[0], constants)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _list_head(node.left, constants)
    return None


def _argv_heads(function: ast.AST, constants: dict[str, str]) -> set[str]:
    heads: set[str] = set()
    for node in ast.walk(function):
        value = None
        if isinstance(node, ast.Return):
            value = node.value
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "cmd"
            for target in node.targets
        ):
            value = node.value
        head = _list_head(value, constants)
        if head is not None:
            heads.add(head)
    return heads


def _canonical_definitions(tree: ast.Module) -> list[tuple[str, str, ast.Call]]:
    definitions: list[tuple[str, str, ast.Call]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name)
            and target.id in {"CANONICAL_HOOKS_PRE", "CANONICAL_HOOKS"}
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            raise InventoryError("canonical hook table is not a literal sequence")
        for item in node.value.elts:
            if not isinstance(item, ast.Call):
                raise InventoryError("canonical hook table contains a non-call entry")
            keywords = {kw.arg: kw.value for kw in item.keywords if kw.arg}
            hook_id = _resolve_string(keywords.get("id"), {}) if "id" in keywords else None
            cmd_node = keywords.get("cmd_fn")
            if not hook_id or not isinstance(cmd_node, ast.Name):
                raise InventoryError("canonical hook entry lacks a literal id/cmd_fn")
            definitions.append((hook_id, cmd_node.id, item))
    if not definitions:
        raise InventoryError("canonical hook tables contain zero entries")
    return definitions


def _scan_canonical(root: Path, found: dict[str, Surface]) -> None:
    path = root / "pkm/hooks.py"
    tree, functions, constants = _parse_python(path)
    for hook_id, builder_name, definition in _canonical_definitions(tree):
        function = _required_function(functions, builder_name, "pkm/hooks.py")
        heads = _argv_heads(function, constants)
        if len(heads) != 1:
            raise InventoryError(
                f"canonical hook {hook_id} resolves to {len(heads)} argv[0] values: "
                f"{sorted(heads)}"
            )
        target = next(iter(heads))
        if not target.startswith("/"):
            raise InventoryError(
                f"canonical hook {hook_id} uses non-absolute argv[0]: {target}"
            )
        extra = b""
        if hook_id == "account-skel-seed":
            seed = root / (
                "packages/core/intergenos-base-files/files/usr/lib/intergenos/"
                "seed-account-skel.sh"
            )
            extra = seed.read_bytes()
        fingerprint = _sha_bytes(
            (ast.dump(definition, include_attributes=False) + "\n" +
             ast.dump(function, include_attributes=False)).encode() + extra
        )
        _add_surface(found, Surface(
            key=f"canonical:{hook_id}",
            kind="canonical",
            owner="",
            source="pkm/hooks.py",
            selector=hook_id,
            target=target,
            fingerprint=fingerprint,
        ))


def _named_heredoc(text: str, marker: str, source: str) -> str:
    lines = text.splitlines()
    opener = re.compile(rf"<<\s*'?{re.escape(marker)}'?\s*$")
    starts = [index for index, line in enumerate(lines) if opener.search(line)]
    if len(starts) != 1:
        raise InventoryError(
            f"{source} contains {len(starts)} {marker} heredoc openers"
        )
    start = starts[0]
    for end in range(start + 1, len(lines)):
        if lines[end].strip() == marker:
            return "\n".join(lines[start + 1:end]) + "\n"
    raise InventoryError(f"{source} has no closing {marker} heredoc marker")


def scan_safe_launchers(root: Path) -> dict[str, SafeLauncher]:
    root = Path(root).resolve()
    found: dict[str, SafeLauncher] = {}
    wrappers = (
        (
            "safe-launch:pkm:package", "pkm", "/usr/bin/pkm",
            "packages/core/pkm/build.sh", "SHIM",
            'exec /usr/bin/python3 -P -m pkm "$@"',
        ),
        (
            "safe-launch:pkm:development-image", "pkm", "/usr/bin/pkm",
            "scripts/create-image.sh", "PKMEOF",
            'exec /usr/bin/python3 -P -m pkm "$@"',
        ),
        (
            "safe-launch:forge:package", "forge", "/usr/bin/forge",
            "packages/desktop/forge/build.sh", "FORGE",
            'exec /usr/bin/python3 -P -m installer "$@"',
        ),
    )
    for key, owner, target, source, marker, expected in wrappers:
        body = _named_heredoc(_read(root / source), marker, source)
        exec_lines = [line.strip() for line in body.splitlines() if line.strip().startswith("exec ")]
        if len(exec_lines) != 1:
            raise InventoryError(f"{source}:{marker} contains {len(exec_lines)} exec lines")
        record = SafeLauncher(
            key=key,
            owner=owner,
            target=target,
            source=source,
            form=f"heredoc:{marker}",
            expected_prefix=exec_lines[0],
            fingerprint=_sha_text(body),
        )
        found[key] = record

    sources = (
        (
            "safe-launch:intergenos-backup:chronicle-pretxn",
            "intergenos-backup",
            "/usr/lib/pkm/pre-transaction.d/chronicle-restore-point;"
            "/usr/libexec/chronicle/chronicle-pretxn-handler",
            "assets/intergenos-backup/chronicle-pretxn-handler",
        ),
        (
            "safe-launch:nvidia:eula",
            "nvidia",
            "/usr/lib/intergen/eula-helpers/nvidia-eula",
            "packages/extra/nvidia/eula-helper/nvidia-eula.py",
        ),
    )
    for key, owner, target, source in sources:
        raw = (root / source).read_bytes()
        try:
            first = raw.decode("utf-8").splitlines()[0]
        except (UnicodeError, IndexError) as error:
            raise InventoryError(f"cannot read launcher shebang from {source}") from error
        found[key] = SafeLauncher(
            key=key,
            owner=owner,
            target=target,
            source=source,
            form="shebang",
            expected_prefix=first,
            fingerprint=_sha_bytes(raw),
        )
    if len(found) != 5:
        raise InventoryError(f"safe-launch census produced {len(found)} rows, expected 5")
    return found


def _wrapper_surface(root: Path) -> Surface:
    entries: list[tuple[str, str]] = []
    for base in (root / "packages", root / "scripts"):
        for path in sorted(base.rglob("*.sh")):
            for match in _PYTHON_WRAPPER.finditer(_read(path)):
                entries.append((_relative(root, path), " ".join(match.group("command").split())))
    if not entries:
        raise InventoryError("no shipped or image-built pkm launcher was found")
    commands = {command for _source, command in entries}
    if len(commands) != 1:
        raise InventoryError(f"pkm launchers disagree: {sorted(commands)}")
    return Surface(
        key="pkm-edge:python-launcher",
        kind="pkm_edge",
        owner="python",
        source=";".join(source for source, _command in entries),
        selector="python-launcher",
        target=next(iter(commands)),
        fingerprint=_sha_text("\n".join(f"{s}\t{c}" for s, c in entries)),
    )


def _module_details(root: Path, relative: str):
    return _parse_python(root / relative)


def _scan_direct_edges(root: Path, found: dict[str, Surface]) -> None:
    _add_surface(found, _wrapper_surface(root))

    repo_tree, repo_functions, repo_constants = _module_details(root, "pkm/repo.py")
    services_tree, service_functions, service_constants = _module_details(root, "pkm/services.py")
    _installer_tree, installer_functions, installer_constants = _module_details(root, "pkm/installer.py")
    _remover_tree, remover_functions, remover_constants = _module_details(root, "pkm/remover.py")
    _hooks_tree, hook_functions, hook_constants = _module_details(root, "pkm/hooks.py")

    direct = (
        (
            "pkm-edge:gpgv-verify", "gpgv-verify", "pkm/repo.py", "/usr/bin/gpgv",
            (repo_constants.get("GPGV"),),
            (_required_function(repo_functions, "_gpgv_command", "pkm/repo.py"),
             _required_function(repo_functions, "RepoManager._verify_signature", "pkm/repo.py")),
        ),
        (
            "pkm-edge:service-query", "service-query", "pkm/services.py",
            "/usr/bin/systemctl", (service_constants.get("SYSTEMCTL"),),
            (_required_function(service_functions, "query_active_services", "pkm/services.py"),),
        ),
        (
            "pkm-edge:service-restart", "service-restart", "pkm/services.py",
            "/usr/bin/systemctl", (service_constants.get("SYSTEMCTL"),),
            (_required_function(service_functions, "run_restart_services", "pkm/services.py"),),
        ),
        (
            "pkm-edge:chroot-mediation", "chroot-mediation",
            "pkm/installer.py;pkm/remover.py", "/usr/sbin/chroot",
            (installer_constants.get("CHROOT"), remover_constants.get("CHROOT")),
            (_required_function(installer_functions, "_post_install_hook_cmd", "pkm/installer.py"),
             _required_function(remover_functions, "_remove_hook_cmd", "pkm/remover.py")),
        ),
        (
            "pkm-edge:archive-bash-mediator", "archive-bash-mediator",
            "pkm/hooks.py", "/usr/bin/bash", (hook_constants.get("BASH"),),
            (_required_function(hook_functions, "_archive_lifecycle_command", "pkm/hooks.py"),
             _required_function(hook_functions, "run_archive_lifecycle_hook", "pkm/hooks.py")),
        ),
    )
    for key, selector, source, target, resolved, functions in direct:
        if any(value is None or not value.startswith("/") for value in resolved):
            raise InventoryError(f"{key} has an unresolved or relative program: {resolved}")
        target_program = target.split()[0]
        if any(value != target_program for value in resolved):
            raise InventoryError(f"{key} program disagrees with {target}: {resolved}")
        _add_surface(found, Surface(
            key=key,
            kind="pkm_edge",
            owner="",
            source=source,
            selector=selector,
            target=target,
            fingerprint=_ast_sha(*functions),
        ))

    sign = _required_function(repo_functions, "sign_index", "pkm/repo.py")
    sign_heads = _argv_heads(sign, repo_constants)
    if sign_heads != {"/usr/bin/gpg"}:
        raise InventoryError(f"build-only index signer changed program: {sorted(sign_heads)}")
    _add_surface(found, Surface(
        key="build-only:pkm.repo.sign_index:gpg",
        kind="build_only",
        owner="gnupg2",
        source="pkm/repo.py",
        selector="index-signing",
        target="/usr/bin/gpg",
        fingerprint=_ast_sha(sign),
    ))


def census_tree(root: Path) -> dict[str, Surface]:
    root = Path(root).resolve()
    found: dict[str, Surface] = {}
    _scan_canonical(root, found)
    _scan_stable_and_pretransaction(root, found)
    _scan_download_helpers(root, found)
    _scan_eula_helpers(root, found)
    _scan_direct_edges(root, found)
    _scan_lifecycle(root, found)
    if not found:
        raise InventoryError("live census returned zero execution surfaces")
    return found


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _module_scope_statements(statements):
    for node in statements:
        yield node
        if isinstance(node, ast.Try):
            yield from _module_scope_statements(node.body)
            for handler in node.handlers:
                yield from _module_scope_statements(handler.body)
            yield from _module_scope_statements(node.orelse)
            yield from _module_scope_statements(node.finalbody)
        elif isinstance(node, ast.If):
            yield from _module_scope_statements(node.body)
            yield from _module_scope_statements(node.orelse)


def _register_alias(table: dict[str, str], alias: str, canonical: str) -> None:
    prior = table.get(alias)
    if prior is not None and prior != canonical:
        raise InventoryError(
            f"process-module alias {alias!r} is ambiguous: {prior!r} and {canonical!r}"
        )
    table[alias] = canonical


def _import_aliases(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    modules: dict[str, str] = {}
    direct: dict[str, str] = {}
    for node in _module_scope_statements(tree.body):
        if isinstance(node, ast.Import):
            for name in node.names:
                if name.name.split(".", 1)[0] not in {"asyncio", "os", "subprocess"}:
                    continue
                _register_alias(
                    modules, name.asname or name.name.split(".")[0], name.name
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for name in node.names:
                if name.name == "*":
                    raise InventoryError(
                        f"wildcard import from {module or '<relative>'} hides process APIs"
                    )
                alias = name.asname or name.name
                canonical = f"{module}.{name.name}" if module else name.name
                if canonical == "_trace":
                    _register_alias(modules, alias, "_trace")
                elif canonical in _PROCESS_APIS:
                    _register_alias(direct, alias, canonical)
    return modules, direct


def _canonical_api(raw: str, modules: dict[str, str], direct: dict[str, str]) -> str:
    if raw in direct:
        return direct[raw]
    first, dot, rest = raw.partition(".")
    if first in modules:
        raw = modules[first] + (dot + rest if dot else "")
    if raw.endswith(".traced_run"):
        return "_trace.traced_run"
    return raw


class _CallCollector(ast.NodeVisitor):
    def __init__(self, modules: dict[str, str], direct: dict[str, str]):
        self.modules = modules
        self.direct = direct
        self.stack: list[str] = []
        self.calls: list[tuple[int, str, str, ast.Call]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Call(self, node: ast.Call) -> None:
        raw = _dotted(node.func)
        api = _canonical_api(raw, self.modules, self.direct)
        if api in _PROCESS_APIS:
            if any(
                keyword.arg == "shell"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            ):
                raise InventoryError(
                    f"shell=True process call at line {node.lineno} is not permitted"
                )
            qualname = ".".join(self.stack) or "<module>"
            self.calls.append((node.lineno, qualname, api, node))
        self.generic_visit(node)


def scan_python_process_calls(pkm_dir: Path) -> dict[str, ProcessCall]:
    pkm_dir = Path(pkm_dir)
    if not pkm_dir.is_dir():
        raise InventoryError(f"pkm source directory is missing: {pkm_dir}")
    provisional: list[tuple[str, int, str, str, ast.Call]] = []
    for path in sorted(pkm_dir.rglob("*.py")):
        tree, _functions, _constants = _parse_python(path)
        modules, direct = _import_aliases(tree)
        visitor = _CallCollector(modules, direct)
        visitor.visit(tree)
        relative = "pkm/" + path.relative_to(pkm_dir).as_posix()
        provisional.extend(
            (relative, line, qualname, api, call)
            for line, qualname, api, call in visitor.calls
        )

    counts: dict[tuple[str, str, str], int] = {}
    found: dict[str, ProcessCall] = {}
    for module, _line, qualname, api, call in sorted(provisional):
        group = (module, qualname, api)
        counts[group] = counts.get(group, 0) + 1
        ordinal = counts[group]
        call_key = f"{module}:{qualname}:{api}:{ordinal}"
        if not call.args:
            raise InventoryError(f"process call has no argv at {call_key}")
        try:
            command_builder = ast.unparse(call.args[0])
        except Exception as error:
            raise InventoryError(f"cannot render argv for {call_key}: {error}") from error
        variant = (
            "trace" if api == "_trace.traced_run"
            else "exec" if api.startswith("os.exec")
            else "subprocess"
        )
        record = ProcessCall(
            call_key=call_key,
            module=module,
            qualname=qualname,
            canonical_api=api,
            variant=variant,
            command_builder=" ".join(command_builder.split()),
            fingerprint=_ast_sha(call),
        )
        if call_key in found:
            raise InventoryError(f"duplicate process call key: {call_key}")
        found[call_key] = record
    if not found:
        raise InventoryError("process-call census returned zero calls")
    return found


def _read_tsv(path: Path, columns: tuple[str, ...], key_name: str) -> list[dict[str, str]]:
    try:
        handle = path.open(encoding="utf-8", newline="")
    except OSError as error:
        raise InventoryError(f"cannot open {path}: {error}") from error
    with handle:
        reader = csv.DictReader(
            (line for line in handle if not line.startswith("#")), delimiter="\t"
        )
        if tuple(reader.fieldnames or ()) != columns:
            raise InventoryError(
                f"{path} columns are {reader.fieldnames!r}; expected {list(columns)!r}"
            )
        rows = list(reader)
    if not rows:
        raise InventoryError(f"{path} contains zero contract rows")
    seen: set[str] = set()
    ordered: list[str] = []
    for index, row in enumerate(rows, 2):
        if None in row:
            raise InventoryError(f"{path}:{index} has extra tab-separated fields")
        if any(
            value is None
            or value == ""
            or value != value.strip()
            or any(ord(char) < 32 for char in value)
            for value in row.values()
        ):
            raise InventoryError(f"{path}:{index} has an empty or non-canonical field")
        key = row[key_name]
        if not re.fullmatch(r"[A-Za-z0-9_.:+*/-]+", key):
            raise InventoryError(f"{path}:{index} has invalid {key_name} {key!r}")
        if key in seen:
            raise InventoryError(f"{path}:{index} duplicates {key_name} {key!r}")
        seen.add(key)
        ordered.append(key)
        fingerprint = row.get("fingerprint")
        if fingerprint is not None and not _HEX64.fullmatch(fingerprint):
            raise InventoryError(f"{path}:{index} has an invalid fingerprint")
    if ordered != sorted(ordered):
        raise InventoryError(f"{path} rows are not in lexical {key_name} order")
    return rows


def load_inventory(path: Path) -> dict[str, dict[str, str]]:
    rows = _read_tsv(Path(path), INVENTORY_COLUMNS, "key")
    allowed_kinds = {
        "archive_lifecycle", "archive_noop", "build_only", "canonical",
        "download_helper", "eula_helper", "pkm_edge", "pre_transaction",
        "stable_hook",
    }
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        if row["kind"] not in allowed_kinds:
            raise InventoryError(f"unknown inventory kind {row['kind']!r}")
        for source in row["source"].split(";"):
            if source.startswith("/") or ".." in Path(source).parts:
                raise InventoryError(f"non-repository source in {row['key']}: {source}")
        out[row["key"]] = row
    return out


def load_process_contract(path: Path) -> dict[str, dict[str, str]]:
    return {
        row["call_key"]: row
        for row in _read_tsv(Path(path), PROCESS_COLUMNS, "call_key")
    }


def load_safe_launch_contract(path: Path) -> dict[str, dict[str, str]]:
    return {
        row["key"]: row
        for row in _read_tsv(Path(path), SAFE_LAUNCH_COLUMNS, "key")
    }


def load_shapes(path: Path) -> dict[str, dict[str, str]]:
    rows = _read_tsv(Path(path), SHAPE_COLUMNS, "shape")
    shapes: dict[str, dict[str, str]] = {}
    seen_members: dict[str, str] = {}
    for row in rows:
        members = row["members"].split(";")
        try:
            declared_count = int(row["row_count"])
        except ValueError as error:
            raise InventoryError(
                f"archive shape {row['shape']} has a non-integer row_count"
            ) from error
        if declared_count != len(members):
            raise InventoryError(
                f"archive shape {row['shape']} declares {declared_count} rows "
                f"but names {len(members)} members"
            )
        for member in members:
            if member in seen_members:
                raise InventoryError(
                    f"archive member {member} appears in both {seen_members[member]} "
                    f"and {row['shape']}"
                )
            seen_members[member] = row["shape"]
        shapes[row["shape"]] = row
    return shapes


def compare_surfaces(
    expected: dict[str, dict[str, str]],
    actual: dict[str, Surface],
    shapes: dict[str, dict[str, str]],
) -> list[str]:
    issues: list[str] = []
    for key in sorted(set(expected) - set(actual)):
        issues.append(f"configured surface missing from tree: {key}")
    for key in sorted(set(actual) - set(expected)):
        row = actual[key]
        issues.append(
            f"unlisted tree surface: {key} ({row.source}; {row.target})"
        )
    for key in sorted(set(expected) & set(actual)):
        configured = expected[key]
        live = actual[key]
        values = {
            "kind": live.kind,
            "source": live.source,
            "selector": live.selector,
            "target": live.target,
            "fingerprint": live.fingerprint,
        }
        if live.owner:
            values["owner"] = live.owner
        for field, value in values.items():
            if configured[field] != value:
                issues.append(
                    f"surface {key} {field} changed: configured={configured[field]!r} "
                    f"tree={value!r}"
                )
        if configured["kind"] in {"archive_lifecycle", "archive_noop"}:
            if configured["shape"] not in shapes:
                issues.append(
                    f"surface {key} names unknown archive shape {configured['shape']!r}"
                )
            elif configured["owner"] not in shapes[configured["shape"]]["members"].split(";"):
                issues.append(
                    f"surface {key} owner {configured['owner']!r} is not a member "
                    f"of archive shape {configured['shape']!r}"
                )
    archive_members = {
        row["owner"]: row["shape"]
        for row in expected.values()
        if row["kind"] in {"archive_lifecycle", "archive_noop"}
    }
    for shape, row in sorted(shapes.items()):
        for member in row["members"].split(";"):
            if archive_members.get(member) != shape:
                issues.append(
                    f"archive shape {shape} member {member!r} has no matching "
                    "inventory row"
                )
    return issues


def compare_safe_launchers(
    expected: dict[str, dict[str, str]],
    actual: dict[str, SafeLauncher],
) -> list[str]:
    issues: list[str] = []
    for key in sorted(set(expected) - set(actual)):
        issues.append(f"configured safe launcher missing from tree: {key}")
    for key in sorted(set(actual) - set(expected)):
        issues.append(f"unlisted safe launcher: {key} ({actual[key].source})")
    for key in sorted(set(expected) & set(actual)):
        configured = expected[key]
        live = actual[key]
        for field in (
            "owner", "target", "source", "form", "expected_prefix", "fingerprint",
        ):
            value = getattr(live, field)
            if configured[field] != value:
                issues.append(
                    f"safe launcher {key} {field} changed: "
                    f"configured={configured[field]!r} tree={value!r}"
                )
        if not live.expected_prefix.startswith("#!/usr/bin/python3 -P") and not (
            live.expected_prefix.startswith("exec /usr/bin/python3 -P -m ")
        ):
            issues.append(
                f"safe launcher {key} does not enable Python safe-path mode: "
                f"{live.expected_prefix!r}"
            )
    return issues


def _dispatch_is_listed(dispatch: str, inventory_keys: set[str]) -> bool:
    for item in dispatch.split(";"):
        if item.endswith("*"):
            if any(key.startswith(item[:-1]) for key in inventory_keys):
                continue
            return False
        if item not in inventory_keys:
            return False
    return True


def compare_process_calls(
    expected: dict[str, dict[str, str]],
    actual: dict[str, ProcessCall],
    inventory_keys: set[str],
) -> list[str]:
    issues: list[str] = []
    for key in sorted(set(expected) - set(actual)):
        issues.append(f"configured process call missing from tree: {key}")
    for key in sorted(set(actual) - set(expected)):
        call = actual[key]
        issues.append(
            f"unlisted process call: {call.module}:{call.qualname} "
            f"via {call.canonical_api} ({call.command_builder})"
        )
    for key in sorted(set(expected) & set(actual)):
        configured = expected[key]
        live = actual[key]
        for field in (
            "module", "qualname", "canonical_api", "variant",
            "command_builder", "fingerprint",
        ):
            value = getattr(live, field)
            if configured[field] != value:
                issues.append(
                    f"process call {key} {field} changed: "
                    f"configured={configured[field]!r} tree={value!r}"
                )
        if not _dispatch_is_listed(configured["dispatch_key"], inventory_keys):
            issues.append(
                f"process call {key} maps to absent inventory key(s): "
                f"{configured['dispatch_key']}"
            )
    return issues


def check_tree(
    root: Path,
    inventory_path: Path,
    process_path: Path,
    shapes_path: Path,
    safe_launch_path: Path,
) -> tuple[list[str], int, int, int, int]:
    inventory = load_inventory(inventory_path)
    process_contract = load_process_contract(process_path)
    shapes = load_shapes(shapes_path)
    safe_contract = load_safe_launch_contract(safe_launch_path)
    surfaces = census_tree(root)
    calls = scan_python_process_calls(Path(root) / "pkm")
    safe_launchers = scan_safe_launchers(root)
    issues = compare_surfaces(inventory, surfaces, shapes)
    issues.extend(compare_process_calls(process_contract, calls, set(inventory)))
    issues.extend(compare_safe_launchers(safe_contract, safe_launchers))
    return issues, len(surfaces), len(calls), len(shapes), len(safe_launchers)
