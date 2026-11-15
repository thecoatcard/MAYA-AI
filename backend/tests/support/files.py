"""Fakes for the connected-folder and upload tests (real temp directories, no database)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.files import services as file_services
from app.files.folder import FolderService
from app.files.services import FileServices
from app.files.uploads import UploadStore
from app.tasks.queue import SubtaskJob

WORKSPACE = UUID("11111111-1111-1111-1111-111111111111")
USER = UUID("22222222-2222-2222-2222-222222222222")
OTHER_WORKSPACE = UUID("33333333-3333-3333-3333-333333333333")


class MemorySettings:
    """The folder setting of every user, in memory (``UserSettingsStore`` in production)."""

    def __init__(self) -> None:
        self.paths: dict[tuple[UUID, UUID], str | None] = {}
        self.texts: dict[tuple[UUID, UUID, str], str | None] = {}

    async def get_workspace_folder(self, workspace_id: UUID, user_id: UUID) -> str | None:
        return self.paths.get((workspace_id, user_id))

    async def set_workspace_folder(
        self, workspace_id: UUID, user_id: UUID, path: str | None
    ) -> None:
        self.paths[(workspace_id, user_id)] = path

    async def get_text(self, workspace_id: UUID, user_id: UUID, key: str) -> str | None:
        return self.texts.get((workspace_id, user_id, key))

    async def set_text(
        self, workspace_id: UUID, user_id: UUID, key: str, value: str | None
    ) -> None:
        self.texts[(workspace_id, user_id, key)] = value


class RecordingServices(FileServices):
    """File services that record audit calls instead of writing them to a database."""

    audits: list[dict[str, Any]]

    async def audit(self, **kwargs: Any) -> None:
        self.audits.append(kwargs)


def make_services(tmp_path: Path) -> tuple[RecordingServices, MemorySettings, Path]:
    """Services over ``tmp_path``; returns ``(services, settings, connectable_folder)``."""
    for name in ("sys", "maya", "home", "artifacts", "work"):
        (tmp_path / name).mkdir(exist_ok=True)
    settings = MemorySettings()
    artifacts = (tmp_path / "artifacts").resolve()
    services = RecordingServices(
        folders=FolderService(
            settings,
            system_dirs=[tmp_path / "sys"],
            protected_dirs=[tmp_path / "maya", artifacts],
            home=tmp_path / "home",
            artifacts_dir=artifacts,
        ),
        uploads=UploadStore(artifacts, max_bytes=2 * 1024 * 1024, total_max_bytes=4 * 1024 * 1024),
        artifacts_dir=artifacts,
        engine=None,
    )
    services.audits = []
    return services, settings, (tmp_path / "work").resolve()


@pytest.fixture
def file_env(tmp_path: Path) -> Iterator[tuple[RecordingServices, Path]]:
    """Installed services and a CONNECTED folder for ``WORKSPACE``/``USER``."""
    services, settings, folder = make_services(tmp_path)
    settings.paths[(WORKSPACE, USER)] = str(folder)
    file_services.configure(services)
    yield services, folder
    file_services.reset()


@pytest.fixture
def file_env_no_folder(tmp_path: Path) -> Iterator[RecordingServices]:
    services, _settings, _folder = make_services(tmp_path)
    file_services.configure(services)
    yield services
    file_services.reset()


def make_job(task_id: UUID | None = None) -> SubtaskJob:
    return SubtaskJob(
        workspace_id=WORKSPACE,
        user_id=USER,
        task_id=task_id or uuid4(),
        subtask_id=uuid4(),
        assigned_agent="file",
    )


def make_docx(path: Path, paragraphs: list[str]) -> Path:
    from docx import Document

    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(str(path))
    return path


def make_xlsx(path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.worksheet.worksheet import Worksheet

    workbook = Workbook()
    sheet = workbook.active
    assert isinstance(sheet, Worksheet)
    sheet.title = "Data"
    sheet["A1"] = "Item"
    sheet["B1"] = 1
    sheet["B2"] = 2
    workbook.save(str(path))
    return path


def make_pptx(path: Path) -> Path:
    from pptx import Presentation

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Old title"
    slide.placeholders[1].text = "Old body"
    deck.save(str(path))
    return path
