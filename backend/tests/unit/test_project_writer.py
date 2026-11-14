"""The project writer: plan -> per-file generation -> validation -> SafePath writes (fake model)."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel

from app.agents.specialized.project_writer import (
    GeneratedFile,
    PlannedFile,
    ProjectPlan,
    ProjectProblem,
    run_project_step,
    validate_files,
    validate_plan,
)
from app.tasks.worker import NonRetryableSubtaskError
from tests.support.files import make_job

INDEX = (
    "<!doctype html><html><head><meta charset='utf-8'><title>Solar</title>"
    "<link rel='stylesheet' href='css/styles.css'></head>"
    "<body><h1>Solar energy</h1><a href='about.html'>About</a>"
    "<script src='js/app.js'></script></body></html>"
)
ABOUT = "<!doctype html><html><body><p>About</p><a href='index.html'>Home</a></body></html>"
GOOD_FILES = {
    "index.html": INDEX,
    "about.html": ABOUT,
    "css/styles.css": "body { color: #123; }",
    "js/app.js": "console.log('ok');",
}


def _plan(paths: list[str]) -> ProjectPlan:
    return ProjectPlan(
        project_name="Solar Energy Site",
        description="A site about solar energy.",
        files=[PlannedFile(path=p, purpose=f"the {p}") for p in paths],
        style_notes="dark blue",
    )


class SiteGateway:
    """Answers the plan call, then one file call per requested path (from ``files``)."""

    def __init__(
        self,
        plan: ProjectPlan,
        files: dict[str, str],
        *,
        override: dict[str, list[str]] | None = None,
        plans: list[ProjectPlan] | None = None,
    ) -> None:
        self.plans = plans or [plan]
        self.files = files
        self.override = override or {}
        self.prompts: list[str] = []

    async def generate_structured(self, **kwargs: Any) -> BaseModel:
        schema = kwargs["response_schema"]
        prompt = str(kwargs["contents"][0])
        self.prompts.append(prompt)
        if schema is ProjectPlan:
            return self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        assert schema is GeneratedFile
        target = prompt.split("Write this file: ")[1].split(" (")[0]
        queued = self.override.get(target)
        if queued:
            first = queued.pop(0)
            return GeneratedFile(path=target, content=first)
        return GeneratedFile(path=target, content=self.files[target])


async def test_builds_a_site_into_the_connected_folder(file_env: Any) -> None:
    services, folder = file_env
    gateway = SiteGateway(_plan(list(GOOD_FILES)), GOOD_FILES)
    output = await run_project_step(
        gateway,
        make_job(),
        {
            "description": "build a website about solar energy",
            "requirements": {"user_request": "dark blue theme, simple", "style": {}},
        },
    )
    project = folder / "solar-energy-site"
    assert (project / "index.html").read_text() == INDEX
    assert (project / "css" / "styles.css").is_file()
    readme = (project / "README.md").read_text()
    assert "index.html" in readme and "browser" in readme
    result = output["result"]
    assert result["saved_in_folder"] is True and result["project"] == "solar-energy-site"
    assert {m["path"] for m in result["manifest"]} == {*GOOD_FILES, "README.md"}
    proof = output["proof"]
    assert Path(proof["storage_uri"]) == project / "index.html"
    assert len(proof["extra_artifacts"]) == len(GOOD_FILES)  # every other file is registered too
    assert all("sha256" in e and e["size_bytes"] > 0 for e in proof["extra_artifacts"])
    # The user's requirements were put in every model prompt.
    assert all("dark blue theme" in p for p in gateway.prompts)
    assert services.audits[-1]["detail"]["files"] == len(GOOD_FILES) + 1


async def test_without_a_folder_the_site_goes_to_the_artifacts_store(
    file_env_no_folder: Any,
) -> None:
    services = file_env_no_folder
    gateway = SiteGateway(_plan(list(GOOD_FILES)), GOOD_FILES)
    job = make_job()
    output = await run_project_step(gateway, job, {"description": "build a website"})
    assert output["result"]["saved_in_folder"] is False
    assert ".zip" in output["result"]["summary"]
    archive = Path(output["proof"]["storage_uri"])
    assert services.artifacts_dir in archive.parents and archive.is_file()  # noqa: ASYNC240
    assert output["proof"]["file_type"] == "zip"
    assert output["result"]["artifact"]["file_type"] == "zip"
    assert not output["proof"]["extra_artifacts"]  # one download, not loose files
    with zipfile.ZipFile(archive) as bundle:  # noqa: ASYNC240
        names = bundle.namelist()
    assert "solar-energy-site/index.html" in names and "solar-energy-site/README.md" in names
    assert all(n.startswith("solar-energy-site/") for n in names)


async def test_two_runs_never_overwrite_each_other(file_env: Any) -> None:
    _services, folder = file_env
    for _ in range(2):
        await run_project_step(
            SiteGateway(_plan(list(GOOD_FILES)), GOOD_FILES), make_job(), {"description": "site"}
        )
    assert (folder / "solar-energy-site" / "index.html").is_file()
    assert (folder / "solar-energy-site-2" / "index.html").is_file()


async def test_missing_folder_falls_back_with_a_warning(file_env: Any) -> None:
    services, folder = file_env
    folder.rmdir()
    output = await run_project_step(
        SiteGateway(_plan(list(GOOD_FILES)), GOOD_FILES), make_job(), {"description": "site"}
    )
    assert output["result"]["saved_in_folder"] is False
    assert "missing" in output["result"]["summary"]
    assert Path(output["proof"]["storage_uri"]).is_file()  # noqa: ASYNC240


@pytest.mark.parametrize(
    "bad_path",
    [
        "../evil.html",
        "..\\evil.html",
        "/abs/evil.html",
        "C:\\evil.html",
        "a/../../x.html",
        "con.html",
    ],
)
async def test_a_bad_path_from_the_model_is_rejected(file_env: Any, bad_path: str) -> None:
    _services, folder = file_env
    bad = _plan(["index.html", bad_path])
    gateway = SiteGateway(bad, GOOD_FILES, plans=[bad, bad])
    with pytest.raises(NonRetryableSubtaskError, match="plan was not usable"):
        await run_project_step(gateway, make_job(), {"description": "site"})
    assert not list(folder.iterdir())  # nothing was written
    assert not list(folder.parent.glob("evil*"))


async def test_a_bad_plan_is_repaired_once(file_env: Any) -> None:
    _services, folder = file_env
    bad = _plan(["../evil.html", "index.html"])
    good = _plan(list(GOOD_FILES))
    gateway = SiteGateway(good, GOOD_FILES, plans=[bad, good])
    await run_project_step(gateway, make_job(), {"description": "site"})
    assert "rejected" in gateway.prompts[1]
    assert (folder / "solar-energy-site" / "index.html").is_file()


async def test_model_returning_another_path_for_a_file_is_rejected(file_env: Any) -> None:
    _services, folder = file_env

    class Sneaky(SiteGateway):
        async def generate_structured(self, **kwargs: Any) -> BaseModel:
            if kwargs["response_schema"] is GeneratedFile:
                return GeneratedFile(path="../../escape.html", content="<p>x</p>")
            return await super().generate_structured(**kwargs)

    with pytest.raises(NonRetryableSubtaskError, match="could not be written"):
        await run_project_step(
            Sneaky(_plan(list(GOOD_FILES)), GOOD_FILES), make_job(), {"description": "site"}
        )
    assert not list(folder.iterdir())
    assert not (folder.parent / "escape.html").exists()


async def test_invalid_file_is_regenerated_once_then_written(file_env: Any) -> None:
    _services, folder = file_env
    broken = INDEX.replace("css/styles.css", "css/missing.css")
    gateway = SiteGateway(_plan(list(GOOD_FILES)), GOOD_FILES, override={"index.html": [broken]})
    await run_project_step(gateway, make_job(), {"description": "site"})
    assert "does not exist in the project" in "\n".join(gateway.prompts)
    assert "css/styles.css" in (folder / "solar-energy-site" / "index.html").read_text()


async def test_a_site_that_never_passes_validation_is_not_written(file_env: Any) -> None:
    _services, folder = file_env
    broken = INDEX.replace("js/app.js", "js/nope.js")
    gateway = SiteGateway(
        _plan(list(GOOD_FILES)), GOOD_FILES, override={"index.html": [broken, broken]}
    )

    class Stubborn(SiteGateway):
        async def generate_structured(self, **kwargs: Any) -> BaseModel:
            result = await super().generate_structured(**kwargs)
            if isinstance(result, GeneratedFile) and result.path == "index.html":
                return GeneratedFile(path="index.html", content=broken)
            return result

    with pytest.raises(NonRetryableSubtaskError, match="did not pass its checks"):
        await run_project_step(
            Stubborn(_plan(list(GOOD_FILES)), GOOD_FILES), make_job(), {"description": "site"}
        )
    del gateway
    assert not list(folder.iterdir())


# ------------------------------------------------------------------------------ validation


def test_validate_plan_rules() -> None:
    assert validate_plan(_plan(["index.html", "css/a.css"])) == ["index.html", "css/a.css"]
    for paths in (
        [],
        ["about.html"],  # no index.html
        ["index.html", "index.html"],
        ["index.html", "INDEX.HTML"],
        ["index.html", "run.exe"],
        ["index.html", "script.sh"],
        ["index.html", "noext"],
        ["index.html"] + [f"p{i}.html" for i in range(20)],
    ):
        with pytest.raises(ProjectProblem):
            validate_plan(_plan(paths))


def test_validate_files_table() -> None:
    ok = dict(GOOD_FILES)
    assert validate_files(ok, allow_external=False) == {}
    cases: dict[str, dict[str, str]] = {
        "empty css": {**ok, "css/styles.css": "  "},
        "empty js": {**ok, "js/app.js": ""},
        "empty html": {**ok, "about.html": ""},
        "unclosed tag": {**ok, "about.html": "<html><body><div><p>x</p></body></html>"},
        "stray end tag": {**ok, "about.html": "<html><body></span></body></html>"},
        "missing script": {**ok, "index.html": INDEX.replace("js/app.js", "js/zzz.js")},
        "missing page link": {**ok, "index.html": INDEX.replace("about.html", "nope.html")},
        "escapes project": {**ok, "index.html": INDEX.replace("css/styles.css", "../x.css")},
        "external script": {
            **ok,
            "index.html": INDEX.replace("js/app.js", "https://cdn.example.com/x.js"),
        },
        "external stylesheet": {
            **ok,
            "index.html": INDEX.replace("css/styles.css", "//cdn.example.com/x.css"),
        },
    }
    for name, files in cases.items():
        assert validate_files(files, allow_external=False), name
    external = cases["external script"]
    assert "index.html" in validate_files(external, allow_external=False)
    assert validate_files(external, allow_external=True) == {}  # the user asked for a CDN stack


def test_external_hyperlinks_and_anchors_are_fine() -> None:
    page = (
        "<html><body><a href='https://example.org'>x</a><a href='#top'>t</a>"
        "<a href='mailto:a@b.c'>m</a><img src='data:image/png;base64,AAAA'></body></html>"
    )
    assert validate_files({"index.html": page}, allow_external=False) == {}


def test_links_resolve_relative_to_the_page() -> None:
    files = {
        "index.html": "<html><body><a href='pages/a.html'>a</a></body></html>",
        "pages/a.html": (
            "<html><head><link rel='stylesheet' href='../css/s.css'></head>"
            "<body><a href='../index.html'>home</a></body></html>"
        ),
        "css/s.css": "p{margin:0}",
    }
    assert validate_files(files, allow_external=False) == {}


async def test_the_unique_task_id_keeps_artifact_dirs_apart(file_env_no_folder: Any) -> None:
    services = file_env_no_folder
    first, second = make_job(uuid4()), make_job(uuid4())
    for job in (first, second):
        await run_project_step(
            SiteGateway(_plan(list(GOOD_FILES)), GOOD_FILES), job, {"description": "site"}
        )
    assert len(list((services.artifacts_dir / str(first.workspace_id)).iterdir())) == 2
