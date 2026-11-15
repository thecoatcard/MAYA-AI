"""The project writer: a small static website written as files, with no code execution (ADR-0022).

Request ("build a landing page / portfolio / website ...") -> step 1: one structured PLAN call
(project name, file tree with purposes, static HTML/CSS/vanilla JS unless the user asked for
more) -> step 2: one structured call per file -> the code validates (every local link and asset
exists, HTML is well formed, CSS/JS not empty, no external CDN script unless the user asked) ->
the files are written with SafePath into ``<folder>/<project-slug>/`` (or the artifacts store
without a folder), a README is added by the code, and every file is registered as an artifact.

The model never chooses a location: it returns relative names that pass the SafePath rules and
must equal the planned names. Nothing is executed, installed or fetched.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field

from app.agents.model_policy import agent_call_kwargs
from app.agents.requirements import requirements_block
from app.automation.zipper import ZipSafetyError, build_project_zip, verify_zip
from app.files import services as file_services
from app.files.access import MAX_PROJECT_FILES, MAX_WRITE_BYTES
from app.files.safepath import SafePathError, resolve_inside, split_relative
from app.tasks.queue import SubtaskJob

logger = logging.getLogger(__name__)

MAX_PLANNED_FILES = 16
# Files generated at the same time (each file is one independent model call).
FILE_CONCURRENCY = 4
MAX_FILE_CHARS = 120_000
MAX_REQUEST_CHARS = 4_000
_WEB_EXTENSIONS = frozenset({"html", "css", "js", "mjs", "json", "svg", "txt", "md", "xml"})
# Common source and data files a project may hold. Never included: anything that runs when it is
# opened or double-clicked (exe, bat, cmd, ps1, sh, msi, jar, dll, vbs, scr, lnk, com, php).
_CODE_EXTENSIONS = frozenset(
    "ts tsx jsx py java c h cpp cs go rs rb sql yaml yml toml ini csv".split()
)
_ALLOWED_EXTENSIONS = _WEB_EXTENSIONS | _CODE_EXTENSIONS
_EXECUTABLE_EXTENSIONS = frozenset(
    "exe bat cmd ps1 sh msi jar dll vbs scr lnk com php pif reg app dmg apk bin".split()
)
_VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)
_STACK_WORDS = re.compile(
    r"\b(react|vue|angular|svelte|next\.?js|bootstrap|tailwind|jquery|cdn|typescript|node)\b",
    re.IGNORECASE,
)


class PlannedFile(BaseModel):
    path: str = Field(description="Relative path such as index.html or css/styles.css.")
    purpose: str = Field(default="", description="What this file contains, one sentence.")


class ProjectPlan(BaseModel):
    project_name: str = Field(description="Short human name of the project.")
    description: str = Field(default="", description="One sentence about the project.")
    files: list[PlannedFile] = Field(default_factory=list)
    style_notes: str = Field(default="", description="Colours, fonts and tone to follow.")


class GeneratedFile(BaseModel):
    path: str
    content: str


class ProjectProblem(ValueError):
    """The model's plan or a file is not acceptable (the message says why, in plain words)."""


def _normalise(path: str) -> str:
    text = str(path).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def validate_plan(plan: ProjectPlan) -> list[str]:
    """The planned relative paths (checked), or :class:`ProjectProblem`."""
    paths: list[str] = []
    seen: set[str] = set()
    for item in plan.files:
        path = _normalise(item.path)
        try:
            split_relative(path)
        except SafePathError as err:
            raise ProjectProblem(f"The file name '{path[:60]}' is not allowed: {err}") from None
        extension = path.rpartition(".")[2].lower() if "." in path else ""
        if extension in _EXECUTABLE_EXTENSIONS:
            raise ProjectProblem(
                f"The file '{path[:60]}' is a program or script that runs when opened; "
                "MAYA never writes those."
            )
        if extension not in _ALLOWED_EXTENSIONS:
            raise ProjectProblem(f"The file '{path[:60]}' has a type that is not allowed.")
        if path.lower() in seen:
            raise ProjectProblem(f"The file '{path[:60]}' is listed twice.")
        seen.add(path.lower())
        paths.append(path)
    if not paths:
        raise ProjectProblem("The plan has no files.")
    if len(paths) > min(MAX_PLANNED_FILES, MAX_PROJECT_FILES):
        raise ProjectProblem(f"The plan has too many files (at most {MAX_PLANNED_FILES}).")
    if "index.html" not in {p.lower() for p in paths}:
        raise ProjectProblem("The plan must include an index.html at the top level.")
    return paths


# ------------------------------------------------------------------------------ validation

_REF_ATTRIBUTES = ("src", "href", "poster", "data-src")


class _Scan(HTMLParser):
    """Collects local references and structural problems of one HTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.refs: list[tuple[str, str, str]] = []  # (tag, attribute, value)
        self.problems: list[str] = []

    def _collect(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value and name in _REF_ATTRIBUTES:
                self.refs.append((tag, name, value))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(tag, attrs)
        if tag not in _VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if tag not in self.stack:
            self.problems.append(f"stray </{tag}>")
            return
        while self.stack and self.stack[-1] != tag:
            self.problems.append(f"<{self.stack.pop()}> is not closed")
        self.stack.pop()

    def finish(self) -> list[str]:
        self.close()
        for tag in self.stack:
            if tag not in ("html", "body", "head", "p", "li"):
                self.problems.append(f"<{tag}> is not closed")
        return self.problems


def _is_external(value: str) -> bool:
    return value.strip().lower().startswith(("http://", "https://", "//", "ftp:"))


def _is_skippable(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        not lowered
        or lowered.startswith(("#", "mailto:", "tel:", "data:", "javascript:", "sms:"))
        or _is_external(lowered)
    )


def validate_files(files: dict[str, str], *, allow_external: bool) -> dict[str, list[str]]:
    """Problems per file path (an empty dict means the project is acceptable)."""
    problems: dict[str, list[str]] = {}
    lowered = {path.lower() for path in files}

    def add(path: str, message: str) -> None:
        problems.setdefault(path, []).append(message)

    for path, content in files.items():
        extension = path.rpartition(".")[2].lower()
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            add(path, "the file is larger than the 5 MB limit")
        if extension in ("css", "js") and not content.strip():
            add(path, f"the {extension.upper()} file is empty")
        if extension != "html":
            continue
        if not content.strip():
            add(path, "the HTML file is empty")
            continue
        scan = _Scan()
        try:
            scan.feed(content)
            for message in scan.finish()[:5]:
                add(path, f"HTML is not well formed: {message}")
        except Exception:
            add(path, "HTML could not be parsed")
            continue
        base = path.rpartition("/")[0]
        for tag, attribute, value in scan.refs:
            if (
                _is_external(value)
                and not allow_external
                and (tag == "script" or (tag == "link" and attribute == "href"))
            ):
                add(path, f"it loads an external script or stylesheet ({value[:60]})")
                continue
            if _is_skippable(value):
                continue
            target = unquote(urlparse(value).path)
            if not target:
                continue
            if target.startswith("/"):
                joined = os.path.normpath(target.lstrip("/")).replace("\\", "/")
            else:
                joined = os.path.normpath(f"{base}/{target}" if base else target).replace("\\", "/")
            if joined.startswith(".."):
                add(path, f"'{value[:60]}' points outside the project")
            elif joined.lower() in lowered:
                continue
            elif tag in ("script", "link", "img", "source"):
                add(path, f"'{value[:60]}' does not exist in the project")
            elif tag == "a" and "." in joined.rpartition("/")[2]:
                add(path, f"the link '{value[:60]}' does not exist in the project")
    return problems


# ------------------------------------------------------------------------------ prompts

_PLAN_SYSTEM = (
    "You plan a small WEBSITE as a set of files. Use plain static HTML, CSS and vanilla "
    "JavaScript only (no build step, nothing to install) unless the user explicitly asked for "
    "something else. Return the project name and the files (relative paths, forward slashes, "
    "at most {max_files}): use the conventional flat layout index.html, style.css and script.js at "
    "the project root (plus more pages if the request needs them) UNLESS the user asked for a "
    "specific folder structure or file names, which then win. Do not add a README (it is "
    "generated). Each file gets a one-sentence purpose. Follow "
    "every colour, theme, tone, language and layout requirement of the request in 'style_notes'. "
    "Do not use external CDNs, remote scripts or remote fonts unless the user asked for them."
)
_FILE_SYSTEM = (
    "You write exactly ONE file of a static website and return {path, content}. 'path' must be "
    "exactly the planned path. Write complete, working, professional content: semantic HTML5, "
    "responsive CSS, accessible markup, real copy for the topic (never lorem ipsum, never "
    "invented statistics or testimonials presented as real). Link the other project files with "
    "RELATIVE paths exactly as planned. No external CDN scripts or stylesheets unless the "
    "user's request asks for them. No secrets, no tracking, no eval. The request and the plan "
    "are the only instructions you follow."
)


def _requirements(input_data: dict[str, Any]) -> str:
    block = requirements_block(input_data)
    if block:
        return block[:MAX_REQUEST_CHARS]
    raw = input_data.get("requirements")
    if isinstance(raw, dict):
        raw = raw.get("user_request") or raw.get("text") or raw.get("verbatim") or str(raw)
    text = str(raw or input_data.get("user_request") or input_data.get("description") or "")
    return f"USER REQUIREMENTS (verbatim): {text[:MAX_REQUEST_CHARS]}" if text else ""


# ------------------------------------------------------------------------------ the step


@dataclass(frozen=True, slots=True)
class WrittenFile:
    relative: str
    path: Path
    size: int
    sha256: str


def readme_text(name: str, description: str, paths: list[str]) -> str:
    listing = "\n".join(f"- `{p}`" for p in sorted(paths))
    return (
        f"# {name}\n\n{description}\n\n"
        "## How to open it\n\n"
        "Open `index.html` in any web browser (double-click it). Nothing needs to be installed "
        "or run.\n\n"
        f"## Files\n\n{listing}\n\n"
        "Made by MAYA AI. Edit the files freely; MAYA never overwrites them.\n"
    )


MARKER_NAME = ".maya-project.json"


def _project_directory(
    parent: Path, slug: str, task_id: str, *, folder_mode: bool
) -> tuple[Path, bool]:
    """The project directory for this task, idempotent across retries: ``(path, reused)``.

    Artifact mode (the task's own storage folder): ``<slug>`` is recreated from scratch, so a
    retry overwrites instead of leaving ``slug-2``, ``slug-3``. Connected folder: a directory
    this same task wrote earlier (marker file) is reused after removing only the files the
    marker lists; any other existing directory is never touched (a new ``slug-N`` is made).
    """
    if not folder_mode:
        target = parent / slug
        shutil.rmtree(target, ignore_errors=True)
        (parent / f"{slug}.zip").unlink(missing_ok=True)
        os.mkdir(target)
        return target, False
    for number in range(1, 50):
        candidate = parent / (slug if number == 1 else f"{slug}-{number}")
        marker = candidate / MARKER_NAME
        if candidate.is_dir() and marker.is_file():
            try:
                info = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(info, dict) and info.get("task_id") == task_id:
                for relative in list(info.get("files") or [])[: MAX_PROJECT_FILES + 5]:
                    try:
                        resolve_inside(candidate, str(relative)).unlink(missing_ok=True)
                    except (OSError, ValueError, SafePathError):
                        continue
                marker.unlink(missing_ok=True)
                return candidate, True
    return file_services.unique_subdirectory(parent, slug), False


def _write_marker(directory: Path, task_id: str, written: list[WrittenFile]) -> None:
    payload = {"task_id": task_id, "files": [w.relative for w in written]}
    (directory / MARKER_NAME).write_text(json.dumps(payload), encoding="utf-8")


def _discard_partial(directory: Path | None, zip_path: Path | None, in_folder: bool) -> None:
    """Remove what a failed run created, so no half-written project is left behind."""
    try:
        if zip_path is not None:
            zip_path.unlink(missing_ok=True)
        if directory is None:
            return
        if in_folder:
            # _write_files already removed its own files; drop the directory only if empty.
            with contextlib.suppress(OSError):
                (directory / MARKER_NAME).unlink(missing_ok=True)
                directory.rmdir()
        else:
            shutil.rmtree(directory, ignore_errors=True)
    except OSError:
        logger.warning("Could not clean up a partial project at %s", directory)


def _write_files(directory: Path, files: dict[str, str]) -> list[WrittenFile]:
    """Create every file exclusively under ``directory`` (SafePath again at write time)."""
    written: list[WrittenFile] = []
    created: list[Path] = []
    try:
        for relative, content in files.items():
            target = resolve_inside(directory, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8")
            with open(target, "xb") as handle:  # never overwrites
                created.append(target)
                handle.write(data)
            written.append(
                WrittenFile(relative, target, len(data), hashlib.sha256(data).hexdigest())
            )
    except BaseException:
        for path in created:  # only files this run created
            path.unlink(missing_ok=True)
        raise
    return written


async def _structured(
    gateway: Any, job: SubtaskJob, prompt: str, schema: type[BaseModel], system: str
) -> Any:
    try:
        return await gateway.generate_structured(
            **agent_call_kwargs("document"),
            contents=[prompt],
            response_schema=schema,
            system_instruction=system,
            workspace_id=job.workspace_id,
            task_id=job.task_id,
        )
    except Exception as err:
        raise RuntimeError(f"Model could not produce the project: {str(err)[:300]}") from err


async def _plan(
    gateway: Any, job: SubtaskJob, request: str, requirements: str
) -> tuple[ProjectPlan, list[str]]:
    problem = ""
    for _attempt in range(2):
        prompt = f"{requirements}\nRequest: {request}\n" + (
            f"Your previous plan was rejected: {problem}\nFix it.\n" if problem else ""
        )
        plan = await _structured(
            gateway, job, prompt, ProjectPlan, _PLAN_SYSTEM.format(max_files=MAX_PLANNED_FILES)
        )
        try:
            return plan, validate_plan(plan)
        except ProjectProblem as err:
            problem = str(err)
    from app.tasks.worker import NonRetryableSubtaskError  # local: imports the task layer

    raise NonRetryableSubtaskError(f"The project plan was not usable: {problem}")


async def _generate_file(
    gateway: Any,
    job: SubtaskJob,
    plan: ProjectPlan,
    paths: list[str],
    target: str,
    purpose: str,
    requirements: str,
    problem: str = "",
) -> str:
    prompt = (
        f"{requirements}\nProject: {plan.project_name}. {plan.description}\n"
        f"Style: {plan.style_notes}\n"
        "All files of the project: " + ", ".join(paths) + "\n"
        f"Write this file: {target} ({purpose}).\n"
        + (f"Your previous version was rejected: {problem}\nFix it.\n" if problem else "")
    )
    generated = await _structured(gateway, job, prompt, GeneratedFile, _FILE_SYSTEM)
    if _normalise(generated.path).lower() != target.lower():
        raise ProjectProblem(f"the file path must be exactly '{target}'")
    if len(generated.content) > MAX_FILE_CHARS:
        raise ProjectProblem("the file is too long")
    return str(generated.content)


async def _generate_with_retries(
    gateway: Any,
    job: SubtaskJob,
    plan: ProjectPlan,
    paths: list[str],
    path: str,
    purpose: str,
    requirements: str,
) -> str:
    """One file: up to two attempts; the second carries the first attempt's problem."""
    from app.tasks.worker import NonRetryableSubtaskError  # local: imports the task layer

    problem = ""
    for _attempt in range(2):
        try:
            return await _generate_file(
                gateway, job, plan, paths, path, purpose, requirements, problem
            )
        except ProjectProblem as err:
            problem = str(err)
    raise NonRetryableSubtaskError(f"The file '{path}' could not be written: {problem}")


async def _generate_files(
    gateway: Any,
    job: SubtaskJob,
    plan: ProjectPlan,
    paths: list[str],
    purposes: dict[str, str],
    requirements: str,
) -> dict[str, str]:
    """Every planned file, generated concurrently (at most FILE_CONCURRENCY model calls at a time).

    The result keeps the planned order, so the zip layout does not depend on which call finished
    first. The first file that fails stops the rest and its error is raised.
    """
    semaphore = asyncio.Semaphore(FILE_CONCURRENCY)

    async def one(path: str) -> str:
        async with semaphore:
            return await _generate_with_retries(
                gateway, job, plan, paths, path, purposes.get(path.lower(), ""), requirements
            )

    tasks = [asyncio.create_task(one(path)) for path in paths]
    try:
        texts = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return dict(zip(paths, texts, strict=True))


async def run_project_step(
    gateway: Any, job: SubtaskJob, input_data: dict[str, Any]
) -> dict[str, Any]:
    """The handler output (``result`` + ``proof``) of a ``project`` step."""
    from app.tasks.worker import NonRetryableSubtaskError  # local: imports the task layer

    if gateway is None:
        raise NonRetryableSubtaskError("No model gateway is configured for this step")
    request = str(input_data.get("description") or "")[:MAX_REQUEST_CHARS]
    requirements = _requirements(input_data)
    allow_external = bool(_STACK_WORDS.search(f"{request} {requirements}"))

    plan, paths = await _plan(gateway, job, request, requirements)
    purposes = {_normalise(f.path).lower(): f.purpose for f in plan.files}

    contents = await _generate_files(gateway, job, plan, paths, purposes, requirements)

    # One repair round for the files the validation names.
    for path, found in list(validate_files(contents, allow_external=allow_external).items()):
        try:
            contents[path] = await _generate_file(
                gateway,
                job,
                plan,
                paths,
                path,
                purposes.get(path.lower(), ""),
                requirements,
                "; ".join(found),
            )
        except ProjectProblem:
            continue
    problems = validate_files(contents, allow_external=allow_external)
    if problems:
        detail = "; ".join(f"{p}: {', '.join(m)}" for p, m in list(problems.items())[:4])
        raise NonRetryableSubtaskError(f"The website did not pass its checks ({detail[:400]}).")

    name = plan.project_name.strip()[:80] or "Website"
    if "readme.md" not in {p.lower() for p in contents}:
        contents["README.md"] = readme_text(name, plan.description, [*contents, "README.md"])

    services = file_services.current()
    slug = file_services.project_slug(name, fallback="website")
    in_folder = False
    warning: str | None = None
    created_dir: Path | None = None
    zip_path: Path | None = None
    try:
        if services is None:
            raise OSError("file services are not configured")
        destination = await file_services.OutputTarget(services).resolve(
            job.workspace_id, job.user_id
        )
        warning = destination.warning
        if destination.mode == "folder" and destination.folder is not None:
            directory, reused = await asyncio.to_thread(
                _project_directory, destination.folder, slug, str(job.task_id), folder_mode=True
            )
            in_folder = True
        else:
            parent = services.artifacts_dir / str(job.workspace_id) / str(job.task_id)
            await asyncio.to_thread(parent.mkdir, parents=True, exist_ok=True)
            directory, reused = await asyncio.to_thread(
                _project_directory, parent, slug, str(job.task_id), folder_mode=False
            )
        created_dir = directory
        written = await asyncio.to_thread(_write_files, directory, contents)
        if in_folder:
            await asyncio.to_thread(_write_marker, directory, str(job.task_id), written)
        else:
            # No folder is connected: one downloadable .zip with one top-level folder.
            zip_path = directory.parent / f"{directory.name}.zip"
            await asyncio.to_thread(build_project_zip, contents, zip_path, folder=directory.name)
            bad = await asyncio.to_thread(verify_zip, zip_path)
            if bad:
                raise ZipSafetyError("; ".join(bad[:3]))
            zip_bytes = await asyncio.to_thread(zip_path.read_bytes)
    except ZipSafetyError as err:
        await asyncio.to_thread(_discard_partial, created_dir, zip_path, in_folder)
        raise NonRetryableSubtaskError(f"The project could not be packed: {err}") from None
    except OSError as err:
        await asyncio.to_thread(_discard_partial, created_dir, zip_path, in_folder)
        raise NonRetryableSubtaskError(
            f"The website files could not be saved: {type(err).__name__}."
        ) from None

    assert services is not None  # an OSError above covers the unconfigured case
    await services.audit(
        workspace_id=job.workspace_id,
        user_id=job.user_id,
        task_id=job.task_id,
        tool="file_write_new",
        level="WRITE",
        detail={"project": directory.name, "files": len(written), "in_folder": in_folder},
    )
    manifest = [{"path": w.relative, "size": w.size} for w in written]
    index = next((w for w in written if w.relative.lower() == "index.html"), written[0])
    if not in_folder and zip_path is not None:
        index = WrittenFile(
            zip_path.name, zip_path, len(zip_bytes), hashlib.sha256(zip_bytes).hexdigest()
        )
    where = (
        f"in your folder at '{directory.name}/'"
        if in_folder
        else "as one downloadable .zip (no folder is connected)"
    )
    summary = f"Built the website '{name}' with {len(written)} files {where}. " + (
        f"The zip opens to one top-level folder '{directory.name}/' with index.html inside; "
        "unzip it and open index.html in a browser to view it."
        if not in_folder
        else "Open index.html in a browser to view it."
    )
    if warning:
        summary += f" {warning}"
    extras = [
        {
            "storage_uri": str(w.path),
            "file_name": w.path.name,
            "file_type": w.relative.rpartition(".")[2].lower() or "other",
            "size_bytes": w.size,
            "sha256": w.sha256,
        }
        for w in written
        if w is not index and in_folder
    ]
    return {
        "result": {
            "summary": summary,
            "agent": "file",
            "project": directory.name,
            "manifest": manifest,
            "saved_in_folder": in_folder,
            "artifact": {
                "file_name": index.path.name,
                "file_type": "zip" if not in_folder else "html",
            },
        },
        "proof": {
            "storage_uri": str(index.path),
            "file_name": index.path.name,
            "file_type": "zip" if not in_folder else "html",
            "size_bytes": index.size,
            "sha256": index.sha256,
            "status": "verified",
            "report": {
                "checks_passed": ["links_exist", "html_well_formed", "files_not_empty"],
                "errors": [],
            },
            "extra_artifacts": extras[:MAX_PROJECT_FILES],
        },
    }
