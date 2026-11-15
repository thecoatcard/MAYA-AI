"""File-based registry of runtime tools for ONE workspace (no database, no migration).

Layout under ``<workspace storage>/runtime_tools/``::

    <name>/<version>/tool.py         the exact source that was shown to the user
    <name>/<version>/manifest.json   name, description, version, sha256, input_schema,
                                     capabilities, created_at, created_by, approved_reusable,
                                     last_run, status
    <name>/<version>/grant.json      the user's approval, bound to ``binding`` (see below)
    <name>/runs.jsonl                one line per run: no arguments, no outputs, no file content

The BINDING of a version is ``sha256(source_sha256 + "\\n" + canonical(capabilities))``. A grant
is valid only while the source on disk and the capabilities in the manifest still hash to the
grant's binding, so editing ``tool.py`` or ``manifest.json`` after approval voids the approval.
Names are validated before any path is built, and every path is re-checked for escape and for
symlinks, so a name or a planted link cannot point outside the registry root.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from app.files.safepath import is_within
from app.runtime_tools.static_check import MAX_SOURCE_BYTES, check_source

NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
MAX_TOOLS_PER_WORKSPACE: Final = 100
MAX_VERSIONS_PER_TOOL: Final = 30
MAX_DESCRIPTION_CHARS: Final = 500
MAX_SCHEMA_BYTES: Final = 4 * 1024
MAX_RUN_LOG_LINES: Final = 200
STATUS_PROPOSED: Final = "proposed"
STATUS_APPROVED: Final = "approved"
STATUS_RETIRED: Final = "retired"
MODE_ONCE: Final = "once"
MODE_REUSABLE: Final = "reusable"

DEFAULT_CAPABILITIES: Final[dict[str, Any]] = {
    "files_in": False,
    "files_out": False,
    "network": False,
}


class RegistryError(Exception):
    """A registry operation was refused. ``str`` is a plain message for the model and the user."""

    def __init__(self, message: str, code: str = "refused") -> None:
        super().__init__(message)
        self.code = code


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_capabilities(raw: object) -> dict[str, Any]:
    """Only the three known booleans survive; anything else is dropped. Missing means False."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RegistryError("capabilities must be an object", "invalid_capabilities")
    unknown = sorted(str(k) for k in raw if k not in DEFAULT_CAPABILITIES)
    if unknown:
        raise RegistryError(f"unknown capability: {', '.join(unknown)}", "invalid_capabilities")
    out = dict(DEFAULT_CAPABILITIES)
    for key in DEFAULT_CAPABILITIES:
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise RegistryError(f"capability '{key}' must be true or false", "invalid_capabilities")
        out[key] = value
    return out


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def binding_of(source_sha256: str, capabilities: dict[str, Any]) -> str:
    return sha256_text(f"{source_sha256}\n{canonical(capabilities)}")


def validate_name(name: object) -> str:
    if not isinstance(name, str) or NAME_PATTERN.fullmatch(name) is None:
        raise RegistryError(
            "tool names are 3-41 characters: lowercase letters, digits and underscores, "
            "starting with a letter",
            "invalid_name",
        )
    return name


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(frozen=True, slots=True)
class ToolRecord:
    name: str
    version: int
    manifest: dict[str, Any]
    source: str

    @property
    def sha256(self) -> str:
        return str(self.manifest.get("sha256", ""))

    @property
    def capabilities(self) -> dict[str, Any]:
        raw = self.manifest.get("capabilities")
        return dict(raw) if isinstance(raw, dict) else dict(DEFAULT_CAPABILITIES)

    @property
    def binding(self) -> str:
        return binding_of(sha256_text(self.source), normalize_capabilities(self.capabilities))

    @property
    def retired(self) -> bool:
        return self.manifest.get("status") == STATUS_RETIRED


class ToolRegistry:
    """One workspace's tools. ``root`` is the ``runtime_tools`` directory (created on demand)."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------ paths

    def _tool_dir(self, name: str) -> Path:
        validate_name(name)
        return self._checked(self.root / name)

    def _version_dir(self, name: str, version: int) -> Path:
        if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= 9999:
            raise RegistryError("the version must be a positive integer", "invalid_version")
        return self._checked(self.root / validate_name(name) / str(version))

    def _checked(self, path: Path) -> Path:
        """``path`` must stay inside the registry root and no part of it may be a link."""
        root = self.root.resolve()
        candidate = path.resolve(strict=False)
        if not is_within(candidate, root):
            raise RegistryError("that path is outside the tool registry", "unsafe_path")
        probe = path
        while probe != self.root and probe != probe.parent:
            if probe.is_symlink() or _is_junction(probe):
                raise RegistryError("links are not allowed in the tool registry", "unsafe_path")
            probe = probe.parent
        if self.root.is_symlink() or _is_junction(self.root):
            raise RegistryError("links are not allowed in the tool registry", "unsafe_path")
        return path

    # ------------------------------------------------------------------ reads

    def names(self) -> list[str]:
        if not self.root.is_dir():
            return []
        out: list[str] = []
        for entry in sorted(self.root.iterdir()):
            if entry.is_dir() and not entry.is_symlink() and NAME_PATTERN.fullmatch(entry.name):
                out.append(entry.name)
        return out

    def versions(self, name: str) -> list[int]:
        directory = self._tool_dir(name)
        if not directory.is_dir():
            return []
        return sorted(
            int(e.name)
            for e in directory.iterdir()
            if e.is_dir() and not e.is_symlink() and e.name.isdigit()
        )

    def get(self, name: str, version: int | None = None) -> ToolRecord:
        """The tool at ``version`` (the newest when omitted). Raises when it does not exist."""
        found = self.versions(name)
        if not found:
            raise RegistryError(f"there is no tool named '{name}'", "not_found")
        chosen = found[-1] if version is None else version
        if chosen not in found:
            raise RegistryError(f"tool '{name}' has no version {chosen}", "not_found")
        directory = self._version_dir(name, chosen)
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            source = (directory / "tool.py").read_text(encoding="utf-8")
        except (OSError, ValueError):
            raise RegistryError(f"tool '{name}' is damaged and cannot be used", "damaged") from None
        if not isinstance(manifest, dict):
            raise RegistryError(f"tool '{name}' is damaged and cannot be used", "damaged")
        return ToolRecord(name, chosen, manifest, source)

    def search(self, query: str, *, limit: int = 8) -> list[ToolRecord]:
        """Newest non-retired version of each tool, ranked by word overlap with name+description."""
        words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 1}
        scored: list[tuple[int, str, ToolRecord]] = []
        for name in self.names():
            try:
                record = self.get(name)
            except RegistryError:
                continue
            if record.retired:
                continue
            text = f"{name.replace('_', ' ')} {record.manifest.get('description', '')}".lower()
            haystack = set(re.findall(r"[a-z0-9]+", text))
            score = len(words & haystack)
            if score or not words:
                scored.append((score, name, record))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [record for _score, _name, record in scored[:limit]]

    # ------------------------------------------------------------------ create

    def create(
        self,
        *,
        name: str,
        description: str,
        source: str,
        input_schema: dict[str, Any],
        capabilities: object,
        created_by: str,
    ) -> tuple[ToolRecord, bool]:
        """Store a PROPOSAL (not approved, not runnable). Returns ``(record, created)``.

        The same source and capabilities as the newest version return that version (``created``
        False); anything else is a new version, so a change always needs a new approval.
        """
        validate_name(name)
        checked = check_source(source)
        if not checked.ok:
            raise RegistryError(
                "the code was refused before approval: " + "; ".join(checked.problems),
                "static_check",
            )
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise RegistryError("the source is too large", "too_large")
        description = (description or "").strip()
        if not description or len(description) > MAX_DESCRIPTION_CHARS:
            raise RegistryError(
                f"the description is required and at most {MAX_DESCRIPTION_CHARS} characters",
                "invalid_description",
            )
        if not isinstance(input_schema, dict):
            raise RegistryError("input_schema must be a JSON schema object", "invalid_schema")
        if len(canonical(input_schema).encode("utf-8")) > MAX_SCHEMA_BYTES:
            raise RegistryError("input_schema is too large", "invalid_schema")
        caps = normalize_capabilities(capabilities)
        source_sha = sha256_text(source)

        existing = self.versions(name)
        if existing:
            newest = self.get(name, existing[-1])
            if newest.sha256 == source_sha and newest.capabilities == caps:
                return newest, False
            if len(existing) >= MAX_VERSIONS_PER_TOOL:
                raise RegistryError("this tool has too many versions; retire it", "too_many")
        elif len(self.names()) >= MAX_TOOLS_PER_WORKSPACE:
            raise RegistryError(
                f"this workspace already has {MAX_TOOLS_PER_WORKSPACE} tools; retire some first",
                "too_many",
            )
        version = (existing[-1] + 1) if existing else 1
        directory = self._version_dir(name, version)
        directory.mkdir(parents=True, exist_ok=False)
        manifest: dict[str, Any] = {
            "name": name,
            "description": description,
            "version": version,
            "sha256": source_sha,
            "input_schema": input_schema,
            "capabilities": caps,
            "created_at": _now(),
            "created_by": created_by,
            "approved_reusable": False,
            "last_run": None,
            "status": STATUS_PROPOSED,
        }
        (directory / "tool.py").write_text(source, encoding="utf-8", newline="\n")
        self._write_manifest(directory, manifest)
        return self.get(name, version), True

    def _write_manifest(self, directory: Path, manifest: dict[str, Any]) -> None:
        temp = directory / "manifest.json.tmp"
        temp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, directory / "manifest.json")

    # ------------------------------------------------------------------ approval

    def _grant(self, name: str, version: int) -> dict[str, Any] | None:
        path = self._version_dir(name, version) / "grant.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def grant_state(self, record: ToolRecord) -> str:
        """``reusable``, ``once`` (unused approval), or ``none``. Never raises for a bad grant."""
        if record.retired:
            return "none"
        if record.sha256 != sha256_text(record.source):
            return "none"  # tool.py no longer matches the manifest
        grant = self._grant(record.name, record.version)
        if grant is None or grant.get("binding") != record.binding:
            return "none"
        mode = grant.get("mode")
        if mode == MODE_REUSABLE and not record.capabilities.get("network"):
            return MODE_REUSABLE
        if mode in (MODE_ONCE, MODE_REUSABLE) and grant.get("used") is not True:
            return MODE_ONCE  # network tools are never reusable: one run per approval
        return "none"

    def approve(self, name: str, version: int, *, binding: str, reusable: bool) -> str:
        """Record the user's approval of exactly ``binding``. Returns the mode that was stored.

        Called only from the approval-gated step (a verified approval token). Refuses when the
        binding is not the one of the stored files, so a stale or altered proposal cannot be
        approved into a different program.
        """
        record = self.get(name, version)
        if record.retired:
            raise RegistryError("that tool version was retired", "retired")
        if record.sha256 != sha256_text(record.source) or record.binding != binding:
            raise RegistryError(
                "the stored tool no longer matches what was approved; nothing was approved",
                "binding_mismatch",
            )
        network = bool(record.capabilities.get("network"))
        mode = MODE_REUSABLE if reusable and not network else MODE_ONCE
        directory = self._version_dir(name, version)
        grant = {"binding": binding, "mode": mode, "approved_at": _now(), "used": False}
        (directory / "grant.json").write_text(json.dumps(grant), encoding="utf-8")
        manifest = dict(record.manifest)
        manifest["status"] = STATUS_APPROVED
        manifest["approved_reusable"] = mode == MODE_REUSABLE
        self._write_manifest(directory, manifest)
        return mode

    def consume(self, record: ToolRecord) -> str:
        """Authorize ONE run. ``reusable`` stays valid; ``once`` is marked used BEFORE the run."""
        state = self.grant_state(record)
        if state == "none":
            raise RegistryError(
                f"tool '{record.name}' version {record.version} is not approved to run "
                "(a new or changed tool needs the user's approval on the task card)",
                "not_approved",
            )
        if state == MODE_ONCE:
            directory = self._version_dir(record.name, record.version)
            grant = self._grant(record.name, record.version) or {}
            grant["used"] = True
            (directory / "grant.json").write_text(json.dumps(grant), encoding="utf-8")
        return state

    # ------------------------------------------------------------------ runs and retire

    def log_run(
        self,
        record: ToolRecord,
        *,
        exit_code: int,
        duration_seconds: float,
        output_bytes: int,
        files_out: int,
        timed_out: bool,
        user_id: str,
    ) -> None:
        """One metadata line. Arguments, results and file contents are never written."""
        directory = self._tool_dir(record.name)
        entry = {
            "at": _now(),
            "version": record.version,
            "sha256": record.sha256,
            "exit_code": exit_code,
            "duration_seconds": round(float(duration_seconds), 2),
            "output_bytes": int(output_bytes),
            "files_out": int(files_out),
            "timed_out": bool(timed_out),
            "user": user_id,
        }
        log = directory / "runs.jsonl"
        lines: list[str] = []
        try:
            lines = log.read_text(encoding="utf-8").splitlines()
        except OSError:
            pass
        lines = [*lines[-(MAX_RUN_LOG_LINES - 1) :], json.dumps(entry, sort_keys=True)]
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        version_dir = self._version_dir(record.name, record.version)
        manifest = dict(record.manifest)
        manifest["last_run"] = entry["at"]
        self._write_manifest(version_dir, manifest)

    def run_log(self, name: str) -> list[dict[str, Any]]:
        try:
            raw = (self._tool_dir(name) / "runs.jsonl").read_text(encoding="utf-8")
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def retire(self, name: str) -> int:
        """Retire every version (kept on disk for the audit trail, never runnable again)."""
        versions = self.versions(name)
        if not versions:
            raise RegistryError(f"there is no tool named '{name}'", "not_found")
        for number in versions:
            directory = self._version_dir(name, number)
            record = self.get(name, number)
            manifest = dict(record.manifest)
            manifest["status"] = STATUS_RETIRED
            manifest["approved_reusable"] = False
            self._write_manifest(directory, manifest)
            grant = directory / "grant.json"
            if grant.exists():
                grant.unlink()
        return len(versions)

    def purge(self, name: str) -> None:
        """Delete a tool's directory entirely (tests and administration; not model-callable)."""
        directory = self._tool_dir(name)
        if directory.is_dir():
            shutil.rmtree(directory)


def _is_junction(path: Path) -> bool:
    checker = getattr(os.path, "isjunction", None)
    return bool(checker(path)) if checker is not None else False


__all__ = [
    "MAX_TOOLS_PER_WORKSPACE",
    "MODE_ONCE",
    "MODE_REUSABLE",
    "NAME_PATTERN",
    "RegistryError",
    "ToolRecord",
    "ToolRegistry",
    "binding_of",
    "canonical",
    "normalize_capabilities",
    "sha256_text",
    "validate_name",
]
