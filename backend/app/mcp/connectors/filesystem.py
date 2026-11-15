"""Filesystem MCP Connector strictly confined to /artifacts/{workspace_id} (Phase 08)."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)


class SecurityTraversalError(Exception):
    """Raised when an operation attempts to access files outside the permitted workspace sandbox."""


class FilesystemConnector:
    """Sandboxed filesystem connector enforcing strict isolation within /artifacts."""

    def __init__(self, base_dir: Path | str, workspace_id: UUID | str) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.workspace_id = str(workspace_id)
        self.root_dir = (self.base_dir / self.workspace_id).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_and_verify_path(self, relative_path: str) -> Path:
        """Resolve path and verify it remains strictly within self.root_dir."""
        # Reject absolute paths or leading slashes attempting root breakout
        if (
            relative_path.startswith("/")
            or relative_path.startswith("\\")
            or Path(relative_path).is_absolute()
        ):
            raise SecurityTraversalError(
                f"Absolute path '{relative_path}' is forbidden in workspace sandbox."
            )

        candidate = (self.root_dir / relative_path).resolve()

        # Check containment
        try:
            if not candidate.is_relative_to(self.root_dir):
                raise SecurityTraversalError(
                    f"Path '{relative_path}' resolves outside workspace sandbox root."
                )
        except ValueError:
            raise SecurityTraversalError(
                f"Path '{relative_path}' resolves outside workspace sandbox root."
            ) from None

        # Check for symlink escapes along all resolved path components
        curr = candidate
        while curr != self.root_dir and curr != curr.parent:
            if curr.is_symlink():
                target = curr.resolve()
                try:
                    if not target.is_relative_to(self.root_dir):
                        raise SecurityTraversalError(
                            f"Symlink '{curr}' escapes workspace sandbox root to '{target}'."
                        )
                except ValueError:
                    raise SecurityTraversalError(
                        f"Symlink '{curr}' escapes workspace sandbox root."
                    ) from None
            curr = curr.parent

        return candidate

    def read_file(self, relative_path: str) -> str:
        """Read text file within workspace sandbox."""
        resolved = self._resolve_and_verify_path(relative_path)
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"File '{relative_path}' not found in workspace.")
        return resolved.read_text(encoding="utf-8")

    def write_file(self, relative_path: str, content: str) -> None:
        """Write text file within workspace sandbox."""
        resolved = self._resolve_and_verify_path(relative_path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

    def list_directory(self, relative_path: str = "") -> list[str]:
        """List contents of directory within workspace sandbox."""
        resolved = self._resolve_and_verify_path(relative_path)
        if not resolved.exists() or not resolved.is_dir():
            raise NotADirectoryError(f"Directory '{relative_path}' not found in workspace.")
        return sorted([p.name for p in resolved.iterdir()])

    def delete_file(self, relative_path: str) -> None:
        """Delete file within workspace sandbox."""
        resolved = self._resolve_and_verify_path(relative_path)
        if resolved.exists():
            resolved.unlink()
