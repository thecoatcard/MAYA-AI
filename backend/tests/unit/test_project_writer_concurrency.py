"""Project writer: files are generated concurrently (bounded), with the same output and errors."""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.agents.specialized.project_writer import (
    FILE_CONCURRENCY,
    GeneratedFile,
    ProjectPlan,
    run_project_step,
)
from app.tasks.worker import NonRetryableSubtaskError
from tests.support.files import make_job
from tests.unit.test_project_writer import SiteGateway, _plan

ABOUT = "<!doctype html><html><body><p>About</p><a href='index.html'>Home</a></body></html>"
CONTACT = "<!doctype html><html><body><p>Contact</p><a href='index.html'>Home</a></body></html>"
INDEX = (
    "<!doctype html><html><head><meta charset='utf-8'><title>Solar</title>"
    "<link rel='stylesheet' href='css/styles.css'><link rel='stylesheet' href='css/print.css'>"
    "</head><body><h1>Solar energy</h1><a href='about.html'>About</a>"
    "<a href='contact.html'>Contact</a><script src='js/app.js'></script>"
    "<script src='js/menu.js'></script><p>Facts in data/facts.txt</p></body></html>"
)
EIGHT_FILES = {
    "index.html": INDEX,
    "about.html": ABOUT,
    "contact.html": CONTACT,
    "css/styles.css": "body { color: #123; }",
    "css/print.css": "@media print { nav { display: none; } }",
    "js/app.js": "console.log('app');",
    "js/menu.js": "console.log('menu');",
    "data/facts.txt": "Solar panels convert light to power.",
}


class TrackingGateway(SiteGateway):
    """Each file call takes a little time, so overlapping calls are visible."""

    def __init__(self, plan: ProjectPlan, files: dict[str, str], **kwargs: Any) -> None:
        super().__init__(plan, files, **kwargs)
        self.in_flight = 0
        self.peak = 0

    async def generate_structured(self, **kwargs: Any) -> Any:
        if kwargs["response_schema"] is ProjectPlan:
            return await super().generate_structured(**kwargs)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            return await super().generate_structured(**kwargs)
        finally:
            self.in_flight -= 1


def test_file_concurrency_is_four() -> None:
    assert FILE_CONCURRENCY == 4


async def test_eight_files_produce_the_same_zip_in_the_planned_order(
    file_env_no_folder: Any,
) -> None:
    gateway = TrackingGateway(_plan(list(EIGHT_FILES)), EIGHT_FILES)
    output = await run_project_step(gateway, make_job(), {"description": "build a website"})

    archive = Path(output["proof"]["storage_uri"])
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        contents = {n: bundle.read(n).decode("utf-8") for n in names}
    expected_names = [f"solar-energy-site/{p}" for p in EIGHT_FILES] + [
        "solar-energy-site/README.md"
    ]
    # The zip writer stores entries sorted by name (unchanged); all 9 members are present.
    assert names == sorted(expected_names)
    for path, text in EIGHT_FILES.items():
        assert contents[f"solar-energy-site/{path}"] == text
    assert output["result"]["manifest"][: len(EIGHT_FILES)] == [
        {"path": p, "size": len(t.encode("utf-8"))} for p, t in EIGHT_FILES.items()
    ]


async def test_file_calls_overlap_but_never_exceed_the_bound(file_env_no_folder: Any) -> None:
    gateway = TrackingGateway(_plan(list(EIGHT_FILES)), EIGHT_FILES)
    await run_project_step(gateway, make_job(), {"description": "build a website"})

    assert gateway.peak == FILE_CONCURRENCY  # 8 files, bound 4: calls did overlap
    assert gateway.in_flight == 0


async def test_a_file_that_keeps_failing_is_reported(file_env_no_folder: Any) -> None:
    class WrongPathForOneFile(TrackingGateway):
        async def generate_structured(self, **kwargs: Any) -> Any:
            prompt = str(kwargs["contents"][0])
            if kwargs["response_schema"] is GeneratedFile and (
                "Write this file: css/print.css (" in prompt
            ):
                self.prompts.append(prompt)
                return GeneratedFile(path="css/other.css", content="body {}")
            return await super().generate_structured(**kwargs)

    gateway = WrongPathForOneFile(_plan(list(EIGHT_FILES)), EIGHT_FILES)
    with pytest.raises(NonRetryableSubtaskError, match="'css/print.css' could not be written"):
        await run_project_step(gateway, make_job(), {"description": "build a website"})
    # The failing file was tried twice; the other files were not left running.
    assert sum("Write this file: css/print.css (" in p for p in gateway.prompts) == 2
    assert gateway.in_flight == 0
