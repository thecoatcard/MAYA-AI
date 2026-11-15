"""The /api/v1/files API: folder, tree, uploads (types, sizes, sniffing, isolation, download)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.files import router
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from tests.support.files import (
    OTHER_WORKSPACE,
    USER,
    WORKSPACE,
    RecordingServices,
    make_docx,
    make_services,
)


class Client:
    def __init__(self, services: RecordingServices) -> None:
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.state.file_services = services
        self.workspace = WORKSPACE
        self.app.dependency_overrides[get_principal] = self._principal
        self.http = TestClient(self.app)

    def _principal(self) -> Principal:
        return Principal(
            user_id=USER, workspace_id=self.workspace, role="owner", session_id=uuid4()
        )


@pytest.fixture
def api(tmp_path: Path) -> Iterator[tuple[Client, RecordingServices, Path]]:
    services, _settings, work = make_services(tmp_path)
    yield Client(services), services, work


def _upload(client: Client, files: list[tuple[str, bytes]]) -> object:
    return client.http.post(
        "/api/v1/files/uploads",
        files=[("files", (name, BytesIO(data))) for name, data in files],
    )


def test_folder_roundtrip(api: tuple[Client, RecordingServices, Path]) -> None:
    client, services, work = api
    assert client.http.get("/api/v1/files/folder").json() == {
        "path": None,
        "status": "none",
        "writable": False,
        "name": None,
    }
    put = client.http.put("/api/v1/files/folder", json={"path": str(work)})
    assert put.status_code == 200
    assert put.json()["status"] == "ok"
    assert put.headers["cache-control"] == "no-store"
    assert client.http.get("/api/v1/files/folder").json()["name"] == "work"
    assert services.audits[-1]["tool"] == "folder_connect"
    assert client.http.delete("/api/v1/files/folder").status_code == 204
    assert client.http.get("/api/v1/files/folder").json()["status"] == "none"


@pytest.mark.parametrize("bad", ["", "relative", "/", "does/not/exist"])
def test_folder_rejects_invalid_paths_with_a_plain_reason(
    api: tuple[Client, RecordingServices, Path], bad: str
) -> None:
    client, _services, _work = api
    response = client.http.put("/api/v1/files/folder", json={"path": bad})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_folder" and body["message"]


def test_folder_rejects_system_and_maya_directories(
    api: tuple[Client, RecordingServices, Path], tmp_path: Path
) -> None:
    client, _services, _work = api
    for forbidden in (tmp_path / "sys", tmp_path / "maya", tmp_path / "home"):
        response = client.http.put("/api/v1/files/folder", json={"path": str(forbidden)})
        assert response.status_code == 422, forbidden


def test_tree_lists_inside_the_folder_only(api: tuple[Client, RecordingServices, Path]) -> None:
    client, _services, work = api
    assert client.http.get("/api/v1/files/folder/tree").status_code == 409
    client.http.put("/api/v1/files/folder", json={"path": str(work)})
    (work / "a.txt").write_text("hello")
    (work / "sub").mkdir()
    (work / "sub" / "b.md").write_text("x")
    tree = client.http.get("/api/v1/files/folder/tree").json()
    assert [(e["name"], e["type"]) for e in tree["entries"]] == [("sub", "dir"), ("a.txt", "file")]
    assert tree["truncated"] is False
    inner = client.http.get("/api/v1/files/folder/tree", params={"path": "sub"}).json()
    assert inner["path"] == "sub" and inner["entries"][0]["name"] == "b.md"
    for bad in ("..", "../x", "/etc", "C:\\Windows", "a\x00b"):
        response = client.http.get("/api/v1/files/folder/tree", params={"path": bad})
        assert response.status_code == 400, bad
    assert client.http.get("/api/v1/files/folder/tree", params={"path": "nope"}).status_code == 404
    limited = client.http.get("/api/v1/files/folder/tree", params={"limit": 1}).json()
    assert len(limited["entries"]) == 1 and limited["truncated"] is True


def test_tree_hides_links_that_leave_the_folder(
    api: tuple[Client, RecordingServices, Path], tmp_path: Path
) -> None:
    client, _services, work = api
    client.http.put("/api/v1/files/folder", json={"path": str(work)})
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, work / "escape", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this OS refused to create a symlink")
    names = [e["name"] for e in client.http.get("/api/v1/files/folder/tree").json()["entries"]]
    assert "escape" not in names
    assert (
        client.http.get("/api/v1/files/folder/tree", params={"path": "escape"}).status_code == 400
    )


def test_upload_list_get_download_delete(api: tuple[Client, RecordingServices, Path]) -> None:
    client, _services, _work = api
    response = _upload(client, [("notes.md", b"# Title\nbody"), ("data.csv", b"a,b\n1,2\n")])
    assert response.status_code == 201  # type: ignore[attr-defined]
    created = response.json()  # type: ignore[attr-defined]
    assert [(c["name"], c["kind"]) for c in created] == [
        ("notes.md", "markdown"),
        ("data.csv", "csv"),
    ]
    assert response.headers["cache-control"] == "no-store"  # type: ignore[attr-defined]
    first = created[0]["id"]
    listing = client.http.get("/api/v1/files/uploads").json()
    assert {item["id"] for item in listing["items"]} == {c["id"] for c in created}
    assert client.http.get(f"/api/v1/files/uploads/{first}").json()["size"] == 12
    download = client.http.get(f"/api/v1/files/uploads/{first}/download")
    assert download.content == b"# Title\nbody"
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["content-disposition"].startswith("attachment")
    assert client.http.delete(f"/api/v1/files/uploads/{first}").status_code == 204
    assert client.http.get(f"/api/v1/files/uploads/{first}").status_code == 404
    assert client.http.delete(f"/api/v1/files/uploads/{first}").status_code == 404


@pytest.mark.parametrize(
    ("name", "data", "status", "code"),
    [
        ("tool.exe", b"MZ\x90\x00", 415, "blocked_type"),
        ("a.zip", b"PK\x03\x04", 415, "blocked_type"),
        ("run.sh", b"#!/bin/sh", 415, "blocked_type"),
        ("x.bat", b"@echo off", 415, "blocked_type"),
        ("weird.xyz", b"hello", 415, "unsupported_type"),
        ("noext", b"hello", 415, "unsupported_type"),
        ("fake.txt", b"MZ\x90\x00\x03", 415, "executable_content"),
        ("fake.png", b"not a png", 415, "type_mismatch"),
        ("fake.pdf", b"hello", 415, "type_mismatch"),
        ("fake.docx", b"plain text", 415, "type_mismatch"),
        ("fake.docx", b"PK\x03\x04junk", 415, "type_mismatch"),
        ("bin.txt", b"abc\x00def", 415, "type_mismatch"),
        ("empty.txt", b"", 400, "empty_file"),
        ("big.txt", b"x" * (2 * 1024 * 1024 + 1), 413, "file_too_large"),
    ],
)
def test_upload_rejections(
    api: tuple[Client, RecordingServices, Path], name: str, data: bytes, status: int, code: str
) -> None:
    client, services, _work = api
    response = _upload(client, [("ok.txt", b"fine"), (name, data)])
    assert response.status_code == status  # type: ignore[attr-defined]
    assert response.json()["error"] == code  # type: ignore[attr-defined]
    # Validation runs before saving: the good file of the same request is not kept either.
    assert services.uploads.list(WORKSPACE, 10, None)[0] == []


def test_upload_count_and_total_limits(api: tuple[Client, RecordingServices, Path]) -> None:
    client, _services, _work = api
    assert _upload(client, []).status_code == 400  # type: ignore[attr-defined]
    eleven = [(f"f{i}.txt", b"x") for i in range(11)]
    assert _upload(client, eleven).status_code == 400  # type: ignore[attr-defined]
    chunk = b"y" * (3 * 512 * 1024)
    three = [(f"big{i}.txt", chunk) for i in range(3)]
    response = _upload(client, three)
    assert response.status_code == 413  # type: ignore[attr-defined]
    assert response.json()["error"] == "total_too_large"  # type: ignore[attr-defined]
    huge = _upload(client, [("h.txt", b"z" * (6 * 1024 * 1024))])
    assert huge.status_code == 413  # type: ignore[attr-defined]
    assert huge.json()["error"] == "payload_too_large"  # type: ignore[attr-defined]


def test_upload_names_are_sanitised(api: tuple[Client, RecordingServices, Path]) -> None:
    client, services, _work = api
    response = _upload(client, [("..\\..\\evil/../note?.txt", b"hi")])
    assert response.status_code == 201  # type: ignore[attr-defined]
    record = response.json()[0]  # type: ignore[attr-defined]
    assert "/" not in record["name"] and "\\" not in record["name"] and ".." not in record["name"]
    stored = services.uploads.get(WORKSPACE, record["id"])
    assert stored is not None
    path = services.uploads.path_of(stored)
    assert path is not None
    assert services.artifacts_dir in path.parents


def test_uploads_are_isolated_between_workspaces(
    api: tuple[Client, RecordingServices, Path],
) -> None:
    client, _services, _work = api
    first = _upload(client, [("mine.txt", b"secret")]).json()[0]["id"]  # type: ignore[attr-defined]
    client.workspace = OTHER_WORKSPACE
    assert client.http.get(f"/api/v1/files/uploads/{first}").status_code == 404
    assert client.http.get(f"/api/v1/files/uploads/{first}/download").status_code == 404
    assert client.http.delete(f"/api/v1/files/uploads/{first}").status_code == 404
    assert client.http.get("/api/v1/files/uploads").json()["items"] == []
    client.workspace = WORKSPACE
    assert client.http.get(f"/api/v1/files/uploads/{first}").status_code == 200


@pytest.mark.parametrize("bad_id", ["x", "../../etc/passwd", "..%2f..", str(UUID(int=0))[:-1]])
def test_upload_id_is_never_used_as_a_path(
    api: tuple[Client, RecordingServices, Path], bad_id: str
) -> None:
    client, _services, _work = api
    assert client.http.get(f"/api/v1/files/uploads/{bad_id}").status_code == 404


def test_office_upload_is_accepted_and_kinded(
    api: tuple[Client, RecordingServices, Path], tmp_path: Path
) -> None:
    client, _services, _work = api
    docx = make_docx(tmp_path / "in.docx", ["Hello"])
    response = _upload(client, [("in.docx", docx.read_bytes())])
    assert response.status_code == 201  # type: ignore[attr-defined]
    assert response.json()[0]["kind"] == "docx"  # type: ignore[attr-defined]
