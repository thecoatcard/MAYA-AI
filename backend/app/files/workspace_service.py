"""Multi-Workspace Foundation: manage connected project workspaces per user.

Provides ProjectWorkspace dataclass and ProjectWorkspaceService to manage multiple
project workspaces per user with full backward compatibility with FolderService and
legacy user_settings['workspace_folder'].
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.files.folder import FolderError, FolderInfo, FolderService, FolderSettings, _norm

logger = logging.getLogger("maya")

PROJECT_WORKSPACES_KEY = "project_workspaces"
LEGACY_DEFAULT_ID = "default"


@dataclass
class ProjectWorkspace:
    """A connected project workspace directory."""

    id: str
    name: str
    path: str
    is_default: bool = False
    status: str = "ok"
    writable: bool = True
    created_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "is_default": self.is_default,
            "status": self.status,
            "writable": self.writable,
            "created_at": self.created_at,
        }


class ProjectWorkspaceService:
    """Manages multiple connected project workspaces per tenant user."""

    def __init__(
        self,
        folder_service: FolderService | None = None,
        settings: FolderSettings | None = None,
        *,
        system_dirs: Sequence[Path | str] | None = None,
        protected_dirs: Sequence[Path | str] | None = None,
        home: Path | str | None = None,
        artifacts_dir: Path | str | None = None,
    ) -> None:
        self._settings: Any
        if isinstance(folder_service, FolderService):
            self._folder_service = folder_service
            self._settings = settings or getattr(folder_service, "_settings", None)
        elif folder_service is not None and settings is None:
            self._settings = folder_service
            self._folder_service = FolderService(
                self._settings,
                system_dirs=system_dirs,
                protected_dirs=protected_dirs,
                home=home,
                artifacts_dir=artifacts_dir,
            )
        else:
            if settings is None:
                raise ValueError("folder_service or settings must be provided")
            self._settings = settings
            self._folder_service = FolderService(
                settings,
                system_dirs=system_dirs,
                protected_dirs=protected_dirs,
                home=home,
                artifacts_dir=artifacts_dir,
            )
        self._memory_store: dict[tuple[UUID, UUID], list[dict[str, Any]]] = {}

    @property
    def folder_service(self) -> FolderService:
        return self._folder_service

    async def _load_raw(self, workspace_id: UUID, user_id: UUID) -> list[dict[str, Any]]:
        if self._settings is not None and hasattr(self._settings, "get_text"):
            raw = await self._settings.get_text(workspace_id, user_id, PROJECT_WORKSPACES_KEY)
            if raw:
                try:
                    data = json.loads(raw)
                    if isinstance(data, list):
                        return data
                except (json.JSONDecodeError, TypeError):
                    pass
        elif hasattr(self._settings, "texts"):
            raw = getattr(self._settings, "texts", {}).get(
                (workspace_id, user_id, PROJECT_WORKSPACES_KEY)
            )
            if raw:
                try:
                    data = json.loads(raw)
                    if isinstance(data, list):
                        return data
                except (json.JSONDecodeError, TypeError):
                    pass
        elif (workspace_id, user_id) in self._memory_store:
            return self._memory_store[(workspace_id, user_id)]
        return []

    async def _save_raw(
        self, workspace_id: UUID, user_id: UUID, items: list[dict[str, Any]]
    ) -> None:
        serialized = json.dumps(items)
        if self._settings is not None and hasattr(self._settings, "set_text"):
            await self._settings.set_text(workspace_id, user_id, PROJECT_WORKSPACES_KEY, serialized)
        else:
            texts = getattr(self._settings, "texts", None)
            if isinstance(texts, dict):
                texts[(workspace_id, user_id, PROJECT_WORKSPACES_KEY)] = serialized
        self._memory_store[(workspace_id, user_id)] = items

    async def _get_legacy_folder(self, workspace_id: UUID, user_id: UUID) -> FolderInfo:
        return await self._folder_service.get(workspace_id, user_id)

    async def list(self, workspace_id: UUID, user_id: UUID) -> list[ProjectWorkspace]:
        """List all project workspaces for this user, recomputing status dynamically."""
        raw_list = await self._load_raw(workspace_id, user_id)
        workspaces: list[ProjectWorkspace] = []
        for raw in raw_list:
            info = self._folder_service.info_for(raw.get("path"))
            workspaces.append(
                ProjectWorkspace(
                    id=str(raw.get("id")),
                    name=str(raw.get("name") or info.name or raw.get("path")),
                    path=str(raw.get("path")),
                    is_default=bool(raw.get("is_default", False)),
                    status=info.status,
                    writable=info.writable,
                    created_at=raw.get("created_at"),
                )
            )

        # Backward compatibility with FolderService / legacy workspace_folder
        legacy = await self._get_legacy_folder(workspace_id, user_id)
        if legacy.path and legacy.status != "none":
            legacy_norm = str(_norm(Path(legacy.path)))
            already_included = any(str(_norm(Path(ws.path))) == legacy_norm for ws in workspaces)
            if not already_included:
                has_default = any(ws.is_default for ws in workspaces)
                fallback = ProjectWorkspace(
                    id=LEGACY_DEFAULT_ID,
                    name=legacy.name or Path(legacy.path).name or "Default",
                    path=legacy.path,
                    is_default=not has_default,
                    status=legacy.status,
                    writable=legacy.writable,
                    created_at=None,
                )
                workspaces.append(fallback)

        if workspaces and not any(ws.is_default for ws in workspaces):
            workspaces[0].is_default = True

        return workspaces

    async def get(
        self, workspace_id: UUID, user_id: UUID, workspace_id_or_id: str
    ) -> ProjectWorkspace | None:
        """Get details and dynamic status for a project workspace."""
        workspaces = await self.list(workspace_id, user_id)
        for ws in workspaces:
            if ws.id == workspace_id_or_id:
                return ws
        return None

    async def register(
        self,
        workspace_id: UUID,
        user_id: UUID,
        path: str,
        name: str | None = None,
    ) -> ProjectWorkspace:
        """Register a new project workspace directory after full validation."""
        resolved = self._folder_service.validate(path)
        resolved_str = str(resolved)
        raw_list = await self._load_raw(workspace_id, user_id)

        ws_name = name.strip() if name and name.strip() else (resolved.name or resolved_str)
        existing_index = -1
        for i, item in enumerate(raw_list):
            if str(_norm(Path(item.get("path", "")))) == resolved_str:
                existing_index = i
                break

        if existing_index >= 0:
            if name and name.strip():
                raw_list[existing_index]["name"] = ws_name
                await self._save_raw(workspace_id, user_id, raw_list)
            target = raw_list[existing_index]
            info = self._folder_service.info_for(target["path"])
            return ProjectWorkspace(
                id=target["id"],
                name=target["name"],
                path=target["path"],
                is_default=target.get("is_default", False),
                status=info.status,
                writable=info.writable,
                created_at=target.get("created_at"),
            )

        is_first = len(raw_list) == 0
        new_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        new_entry = {
            "id": new_id,
            "name": ws_name,
            "path": resolved_str,
            "is_default": is_first,
            "created_at": now,
        }
        raw_list.append(new_entry)
        await self._save_raw(workspace_id, user_id, raw_list)

        if is_first:
            try:
                await self._folder_service.set(workspace_id, user_id, resolved_str)
            except Exception:
                pass

        info = self._folder_service.info_for(resolved_str)
        return ProjectWorkspace(
            id=new_id,
            name=ws_name,
            path=resolved_str,
            is_default=is_first,
            status=info.status,
            writable=info.writable,
            created_at=now,
        )

    async def disconnect(self, workspace_id: UUID, user_id: UUID, workspace_id_or_id: str) -> bool:
        """Disconnect a project workspace."""
        raw_list = await self._load_raw(workspace_id, user_id)
        target_idx = -1
        for i, item in enumerate(raw_list):
            if item.get("id") == workspace_id_or_id:
                target_idx = i
                break

        if target_idx >= 0:
            removed = raw_list.pop(target_idx)
            was_default = removed.get("is_default", False)
            if was_default and raw_list:
                raw_list[0]["is_default"] = True
                new_default_path = raw_list[0]["path"]
                try:
                    await self._folder_service.set(workspace_id, user_id, new_default_path)
                except Exception:
                    pass
            elif was_default and not raw_list:
                await self._folder_service.clear(workspace_id, user_id)
            await self._save_raw(workspace_id, user_id, raw_list)
            return True

        if workspace_id_or_id == LEGACY_DEFAULT_ID:
            legacy = await self._folder_service.get(workspace_id, user_id)
            if legacy.path:
                await self._folder_service.clear(workspace_id, user_id)
                return True

        return False

    async def set_default(
        self, workspace_id: UUID, user_id: UUID, workspace_id_or_id: str
    ) -> ProjectWorkspace:
        """Set a project workspace as the active/default workspace."""
        raw_list = await self._load_raw(workspace_id, user_id)
        target_idx = -1
        for i, item in enumerate(raw_list):
            if item.get("id") == workspace_id_or_id:
                target_idx = i
                break

        if target_idx < 0:
            if workspace_id_or_id == LEGACY_DEFAULT_ID:
                legacy = await self._folder_service.get(workspace_id, user_id)
                if legacy.path:
                    return await self.register(workspace_id, user_id, legacy.path, name=legacy.name)
            raise FolderError("Workspace not found.", "not_found")

        for i, item in enumerate(raw_list):
            item["is_default"] = i == target_idx

        await self._save_raw(workspace_id, user_id, raw_list)
        target = raw_list[target_idx]
        try:
            await self._folder_service.set(workspace_id, user_id, target["path"])
        except Exception:
            pass
        info = self._folder_service.info_for(target["path"])
        return ProjectWorkspace(
            id=target["id"],
            name=target["name"],
            path=target["path"],
            is_default=True,
            status=info.status,
            writable=info.writable,
            created_at=target.get("created_at"),
        )

    async def usable_root(
        self,
        workspace_id: UUID,
        user_id: UUID,
        workspace_id_or_id: str | None = None,
    ) -> Path | None:
        """The root directory when connected, present and writable now; else None."""
        if workspace_id_or_id:
            ws = await self.get(workspace_id, user_id, workspace_id_or_id)
            if not ws or ws.status != "ok":
                return None
            try:
                return self._folder_service.validate(ws.path)
            except FolderError:
                return None

        workspaces = await self.list(workspace_id, user_id)
        default_ws = next((w for w in workspaces if w.is_default), None)
        if default_ws and default_ws.status == "ok":
            try:
                return self._folder_service.validate(default_ws.path)
            except FolderError:
                pass

        return await self._folder_service.usable_root(workspace_id, user_id)

    def resolve(
        self,
        root: Path,
        relative: str,
        *,
        allow_root: bool = False,
        resolver: Any = None,
    ) -> Path:
        """Resolve a relative path under root safely."""
        return self._folder_service.resolve(
            root, relative, allow_root=allow_root, resolver=resolver
        )
