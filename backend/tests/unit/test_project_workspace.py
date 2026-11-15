"""Unit tests for Multi-Workspace Foundation and API (ADR-0022)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.workspaces import router as workspaces_router
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.files.folder import FolderError
from app.files.workspace_service import (
    LEGACY_DEFAULT_ID,
    ProjectWorkspaceService,
)
from tests.support.files import USER, WORKSPACE, MemorySettings, RecordingServices, make_services


@pytest.fixture
def workspace_env(
    tmp_path: Path,
) -> tuple[ProjectWorkspaceService, RecordingServices, MemorySettings, Path, Path]:
    services, settings, work1 = make_services(tmp_path)
    work2 = tmp_path / "work2"
    work2.mkdir(exist_ok=True)
    ws_service = ProjectWorkspaceService(services.folders, settings)
    services.workspaces = ws_service
    return ws_service, services, settings, work1, work2


# ---------------------------------------------------------------------- service tests


@pytest.mark.asyncio
async def test_register_and_list_multiple_workspaces(
    workspace_env: tuple[ProjectWorkspaceService, RecordingServices, MemorySettings, Path, Path],
) -> None:
    ws_service, _, _, work1, work2 = workspace_env

    # Initial list is empty
    assert await ws_service.list(WORKSPACE, USER) == []

    # Register first workspace -> becomes default automatically
    ws1 = await ws_service.register(WORKSPACE, USER, str(work1), name="Project Alpha")
    assert ws1.name == "Project Alpha"
    assert ws1.path == str(work1)
    assert ws1.is_default is True
    assert ws1.status == "ok"
    assert ws1.writable is True

    # Register second workspace -> not default
    ws2 = await ws_service.register(WORKSPACE, USER, str(work2), name="Project Beta")
    assert ws2.name == "Project Beta"
    assert ws2.path == str(work2)
    assert ws2.is_default is False

    # List reflects both
    workspaces = await ws_service.list(WORKSPACE, USER)
    assert len(workspaces) == 2
    assert workspaces[0].id == ws1.id
    assert workspaces[0].is_default is True
    assert workspaces[1].id == ws2.id
    assert workspaces[1].is_default is False

    # Get by id
    fetched1 = await ws_service.get(WORKSPACE, USER, ws1.id)
    assert fetched1 is not None
    assert fetched1.id == ws1.id
    assert fetched1.name == "Project Alpha"

    # Re-registering existing path updates name without creating duplicate
    updated1 = await ws_service.register(WORKSPACE, USER, str(work1), name="Alpha Renamed")
    assert updated1.id == ws1.id
    assert updated1.name == "Alpha Renamed"
    assert len(await ws_service.list(WORKSPACE, USER)) == 2


@pytest.mark.asyncio
async def test_validation_checks_reject_invalid_paths(
    workspace_env: tuple[ProjectWorkspaceService, RecordingServices, MemorySettings, Path, Path],
    tmp_path: Path,
) -> None:
    ws_service, _, _, work1, _ = workspace_env

    # Empty path
    with pytest.raises(FolderError) as exc:
        await ws_service.register(WORKSPACE, USER, "")
    assert exc.value.code == "empty"

    # Relative path
    with pytest.raises(FolderError):
        await ws_service.register(WORKSPACE, USER, "some/relative/path")

    # Non-existent directory
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FolderError) as exc:
        await ws_service.register(WORKSPACE, USER, str(missing))
    assert exc.value.code == "missing"

    # File path instead of directory
    file_path = work1 / "sample.txt"
    file_path.write_text("hello")
    with pytest.raises(FolderError) as exc:
        await ws_service.register(WORKSPACE, USER, str(file_path))
    assert exc.value.code == "not_directory"

    # System directory
    sys_dir = tmp_path / "sys"
    with pytest.raises(FolderError) as exc:
        await ws_service.register(WORKSPACE, USER, str(sys_dir))
    assert exc.value.code == "system_dir"

    # Maya protected directory
    maya_dir = tmp_path / "maya"
    with pytest.raises(FolderError) as exc:
        await ws_service.register(WORKSPACE, USER, str(maya_dir))
    assert exc.value.code == "maya_dir"

    # User profile directory
    home_dir = tmp_path / "home"
    with pytest.raises(FolderError) as exc:
        await ws_service.register(WORKSPACE, USER, str(home_dir))
    assert exc.value.code == "user_profile"


@pytest.mark.asyncio
async def test_default_switching_and_disconnect(
    workspace_env: tuple[ProjectWorkspaceService, RecordingServices, MemorySettings, Path, Path],
) -> None:
    ws_service, _, settings, work1, work2 = workspace_env

    ws1 = await ws_service.register(WORKSPACE, USER, str(work1))
    ws2 = await ws_service.register(WORKSPACE, USER, str(work2))
    assert ws1.is_default is True
    assert ws2.is_default is False

    # Switch default to ws2
    new_default = await ws_service.set_default(WORKSPACE, USER, ws2.id)
    assert new_default.id == ws2.id
    assert new_default.is_default is True

    # Legacy folder service reflects new default
    assert await settings.get_workspace_folder(WORKSPACE, USER) == str(work2)

    # Disconnect non-existent workspace returns False
    assert await ws_service.disconnect(WORKSPACE, USER, "unknown-id") is False

    # Disconnecting current default ws2 promotes remaining ws1 to default
    assert await ws_service.disconnect(WORKSPACE, USER, ws2.id) is True
    remaining = await ws_service.list(WORKSPACE, USER)
    assert len(remaining) == 1
    assert remaining[0].id == ws1.id
    assert remaining[0].is_default is True
    assert await settings.get_workspace_folder(WORKSPACE, USER) == str(work1)

    # Disconnecting last workspace clears everything
    assert await ws_service.disconnect(WORKSPACE, USER, ws1.id) is True
    assert await ws_service.list(WORKSPACE, USER) == []
    assert await settings.get_workspace_folder(WORKSPACE, USER) is None


@pytest.mark.asyncio
async def test_safe_usable_root_retrieval(
    workspace_env: tuple[ProjectWorkspaceService, RecordingServices, MemorySettings, Path, Path],
    tmp_path: Path,
) -> None:
    ws_service, _, _, work1, work2 = workspace_env

    # Initially None
    assert await ws_service.usable_root(WORKSPACE, USER) is None

    ws1 = await ws_service.register(WORKSPACE, USER, str(work1))
    ws2 = await ws_service.register(WORKSPACE, USER, str(work2))

    # Default usable root returns ws1 root
    assert await ws_service.usable_root(WORKSPACE, USER) == work1.resolve()
    assert await ws_service.usable_root(WORKSPACE, USER, ws1.id) == work1.resolve()

    # Specific root retrieval by id
    assert await ws_service.usable_root(WORKSPACE, USER, ws2.id) == work2.resolve()
    assert await ws_service.usable_root(WORKSPACE, USER, "non-existent") is None


@pytest.mark.asyncio
async def test_backward_compatibility_with_legacy_folder(
    workspace_env: tuple[ProjectWorkspaceService, RecordingServices, MemorySettings, Path, Path],
) -> None:
    ws_service, _, settings, work1, _ = workspace_env

    # Preset legacy workspace_folder without registering via workspace_service
    await settings.set_workspace_folder(WORKSPACE, USER, str(work1))

    # list() should synthesize a fallback workspace
    items = await ws_service.list(WORKSPACE, USER)
    assert len(items) == 1
    assert items[0].id == LEGACY_DEFAULT_ID
    assert items[0].path == str(work1)
    assert items[0].is_default is True
    assert items[0].status == "ok"

    # usable_root() resolves it
    assert await ws_service.usable_root(WORKSPACE, USER) == work1.resolve()

    # Disconnecting fallback clears legacy folder
    assert await ws_service.disconnect(WORKSPACE, USER, LEGACY_DEFAULT_ID) is True
    assert await settings.get_workspace_folder(WORKSPACE, USER) is None
    assert await ws_service.list(WORKSPACE, USER) == []


# ------------------------------------------------------------------------- api tests


class WorkspaceApiClient:
    def __init__(self, services: RecordingServices) -> None:
        self.app = FastAPI()
        self.app.include_router(workspaces_router, prefix="/api/v1/workspaces")
        self.app.state.file_services = services
        self.workspace = WORKSPACE
        self.app.dependency_overrides[get_principal] = self._principal
        self.http = TestClient(self.app)

    def _principal(self) -> Principal:
        return Principal(
            user_id=USER, workspace_id=self.workspace, role="owner", session_id=uuid4()
        )


@pytest.fixture
def api_client(
    workspace_env: tuple[ProjectWorkspaceService, RecordingServices, MemorySettings, Path, Path],
) -> tuple[WorkspaceApiClient, RecordingServices, Path, Path]:
    _, services, _, work1, work2 = workspace_env
    return WorkspaceApiClient(services), services, work1, work2


def test_api_workspace_lifecycle(
    api_client: tuple[WorkspaceApiClient, RecordingServices, Path, Path],
    tmp_path: Path,
) -> None:
    client, services, work1, work2 = api_client

    # 1. GET /api/v1/workspaces initially empty
    resp = client.http.get("/api/v1/workspaces")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json() == {"workspaces": []}

    # 2. POST /api/v1/workspaces with bad path -> 422
    bad_resp = client.http.post(
        "/api/v1/workspaces",
        json={"path": str(tmp_path / "sys")},
    )
    assert bad_resp.status_code == 422
    assert bad_resp.json()["error"] == "invalid_folder"
    assert bad_resp.json()["reason"] == "system_dir"

    # 3. POST /api/v1/workspaces valid -> 201
    post1 = client.http.post(
        "/api/v1/workspaces",
        json={"path": str(work1), "name": "Work 1"},
    )
    assert post1.status_code == 201
    data1 = post1.json()
    assert data1["name"] == "Work 1"
    assert data1["path"] == str(work1)
    assert data1["is_default"] is True
    ws1_id = data1["id"]
    assert services.audits[-1]["tool"] == "workspace_register"

    # 4. POST /api/v1/workspaces second workspace -> 201
    post2 = client.http.post(
        "/api/v1/workspaces",
        json={"path": str(work2), "name": "Work 2"},
    )
    assert post2.status_code == 201
    data2 = post2.json()
    assert data2["is_default"] is False
    ws2_id = data2["id"]

    # 5. GET /api/v1/workspaces list contains both
    list_resp = client.http.get("/api/v1/workspaces")
    assert list_resp.status_code == 200
    workspaces = list_resp.json()["workspaces"]
    assert len(workspaces) == 2

    # 6. GET /api/v1/workspaces/{id}
    get_resp = client.http.get(f"/api/v1/workspaces/{ws1_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == ws1_id

    # 7. GET /api/v1/workspaces/{id} not found -> 404
    assert client.http.get("/api/v1/workspaces/random-id").status_code == 404

    # 8. POST /api/v1/workspaces/{id}/default
    def_resp = client.http.post(f"/api/v1/workspaces/{ws2_id}/default")
    assert def_resp.status_code == 200
    assert def_resp.json()["is_default"] is True
    assert services.audits[-1]["tool"] == "workspace_set_default"

    # 9. DELETE /api/v1/workspaces/{id}
    del_resp = client.http.delete(f"/api/v1/workspaces/{ws1_id}")
    assert del_resp.status_code == 204
    assert services.audits[-1]["tool"] == "workspace_disconnect"

    # 10. DELETE /api/v1/workspaces/{id} not found -> 404
    assert client.http.delete(f"/api/v1/workspaces/{ws1_id}").status_code == 404
