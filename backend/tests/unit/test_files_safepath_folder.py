"""SafePath rules and the connected-folder validation matrix (ADR-0022)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.files import services as file_services
from app.files.folder import FolderError
from app.files.safepath import SafePathError, is_within, resolve_inside, split_relative
from tests.support.files import USER, WORKSPACE, make_services

BAD_PATHS = [
    "..",
    "../x",
    "a/../../x",
    "a/..",
    "/etc/passwd",
    "\\windows\\system32",
    "C:\\Windows\\x",
    "c:/x",
    "C:x",
    "\\\\server\\share\\x",
    "//server/share/x",
    "a\x00b",
    "con",
    "CON.txt",
    "nul",
    "aux.md",
    "COM1",
    "lpt9.txt",
    "name.",
    "name ",
    "a/b./c",
    "a/ b/c",
    "bad<name",
    "bad|name",
    'bad"name',
    "q?.txt",
    "x*y",
    "a/" + "x" * 300,
    "\uff0e\uff0e/x",  # fullwidth dots normalise to ..
    "..\\x",
    "a\\..\\..\\x",
]


@pytest.mark.parametrize("relative", BAD_PATHS)
def test_resolve_inside_rejects_bad_paths(tmp_path: Path, relative: str) -> None:
    with pytest.raises(SafePathError):
        resolve_inside(tmp_path, relative)


@pytest.mark.parametrize(
    "relative", ["a.txt", "dir/a.txt", "dir\\a.txt", "Ünï/ç.md", "a b/c d.txt", "a/b/c/d.html"]
)
def test_resolve_inside_accepts_plain_paths(tmp_path: Path, relative: str) -> None:
    result = resolve_inside(tmp_path, relative)
    assert is_within(result, tmp_path)


def test_root_only_with_allow_root(tmp_path: Path) -> None:
    assert resolve_inside(tmp_path, "", allow_root=True) == tmp_path.resolve()
    with pytest.raises(SafePathError):
        resolve_inside(tmp_path, "")


def test_relative_root_is_rejected() -> None:
    with pytest.raises(SafePathError):
        resolve_inside("relative/root", "a.txt")


def test_symlink_escape_rejected_with_a_fake_resolver(tmp_path: Path) -> None:
    """A link inside the folder that points outside is caught (resolver injected: no OS links)."""
    root = tmp_path.resolve()
    outside = (tmp_path.parent / "outside").resolve()

    def resolver(path: Path) -> Path:
        return outside / "secret" if path.name == "link" else path

    with pytest.raises(SafePathError) as raised:
        resolve_inside(root, "link/file.txt", resolver=resolver)
    assert raised.value.code == "escape"
    with pytest.raises(SafePathError):
        resolve_inside(root, "ok/../link", resolver=resolver)


def test_real_symlink_escape_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this OS refused to create a symlink")
    with pytest.raises(SafePathError):
        resolve_inside(root, "link/file.txt")


def test_case_insensitive_containment() -> None:
    assert is_within("C:/Work/Sub/file.txt", "c:/work", case_insensitive=True)
    assert not is_within("C:/Work2/file.txt", "c:/work", case_insensitive=True)
    assert not is_within("C:/Work/Sub", "c:/work/sub/deeper", case_insensitive=True)


def test_forbidden_directories_are_refused(tmp_path: Path) -> None:
    (tmp_path / "private").mkdir()
    with pytest.raises(SafePathError) as raised:
        resolve_inside(tmp_path, "private/x.txt", forbidden=[tmp_path / "private"])
    assert raised.value.code == "protected"


def test_path_length_limit(tmp_path: Path) -> None:
    with pytest.raises(SafePathError) as raised:
        resolve_inside(tmp_path, "a/b.txt", max_chars=len(str(tmp_path)) + 2)
    assert raised.value.code == "too_long"


def test_split_relative_normalises_separators() -> None:
    assert split_relative("a\\b/c.txt") == ["a", "b", "c.txt"]
    assert split_relative("", allow_root=True) == []


# ------------------------------------------------------------------------------ folder


async def test_folder_validation_matrix(tmp_path: Path) -> None:
    services, _settings, work = make_services(tmp_path)
    folders = services.folders
    a_file = tmp_path / "work" / "file.txt"
    a_file.write_text("x")
    cases: list[tuple[object, str]] = [
        ("", "empty"),
        (None, "empty"),
        (42, "empty"),
        ("relative/path", "not_absolute"),
        ("\\\\server\\share", "unc"),
        ("//server/share", "unc"),
        ("bad\x00path", "bad_path"),
        (str(tmp_path / "does-not-exist"), "missing"),
        (str(a_file), "not_directory"),
        (str(tmp_path / "home"), "user_profile"),
        (str(tmp_path / "sys"), "system_dir"),
        (str(tmp_path / "sys" / "inner"), "missing"),
        (str(tmp_path / "maya"), "maya_dir"),
        (str(tmp_path / "artifacts"), "maya_dir"),
        (Path(tmp_path.anchor).as_posix(), "root"),
    ]
    for raw, _code in cases:
        with pytest.raises(FolderError):
            folders.validate(raw)
    # The reasons are specific where it matters.
    for raw, code in cases:
        if code in {"user_profile", "system_dir", "maya_dir", "root", "missing", "unc"}:
            with pytest.raises(FolderError) as raised:
                folders.validate(raw)
            assert raised.value.code == code, raw
    assert folders.validate(str(work)) == work


async def test_folder_inside_a_system_dir_is_refused(tmp_path: Path) -> None:
    services, _s, _w = make_services(tmp_path)
    inner = tmp_path / "sys" / "inner"
    inner.mkdir()
    with pytest.raises(FolderError) as raised:
        services.folders.validate(str(inner))
    assert raised.value.code == "system_dir"


def test_real_default_system_dirs_are_refused() -> None:
    from app.files.folder import FolderService

    service = FolderService(_Null())
    for candidate in ("C:\\Windows", "C:\\Program Files", "/etc", "/usr", "/System"):
        path = Path(candidate)
        if path.is_dir():
            with pytest.raises(FolderError):
                service.validate(candidate)


class _Null:
    async def get_workspace_folder(self, *_: object) -> str | None:
        return None

    async def set_workspace_folder(self, *_: object) -> None:
        return None


async def test_set_get_clear_and_missing_status(tmp_path: Path) -> None:
    services, settings, work = make_services(tmp_path)
    folders = services.folders
    assert (await folders.get(WORKSPACE, USER)).status == "none"
    info = await folders.set(WORKSPACE, USER, str(work))
    assert (info.status, info.writable, info.name) == ("ok", True, "work")
    assert settings.paths[(WORKSPACE, USER)] == str(work)
    # No probe file is left behind by the writability test.
    assert [p.name for p in work.iterdir()] == []
    work.rmdir()
    assert (await folders.get(WORKSPACE, USER)).status == "missing"
    assert await folders.usable_root(WORKSPACE, USER) is None
    work.mkdir()
    assert (await folders.get(WORKSPACE, USER)).status == "ok"
    await folders.clear(WORKSPACE, USER)
    assert (await folders.get(WORKSPACE, USER)).status == "none"


async def test_a_stored_path_that_is_now_forbidden_is_not_usable(tmp_path: Path) -> None:
    services, settings, _work = make_services(tmp_path)
    settings.paths[(WORKSPACE, USER)] = str(tmp_path / "maya")
    assert await services.folders.usable_root(WORKSPACE, USER) is None


# ------------------------------------------------------------------------------ routing


async def test_output_target_without_folder_uses_artifacts(
    tmp_path: Path,
) -> None:
    services, _settings, _work = make_services(tmp_path)
    destination = await file_services.OutputTarget(services).resolve(WORKSPACE, USER)
    assert destination.mode == "artifacts"
    assert destination.warning is None


async def test_output_target_with_folder(tmp_path: Path) -> None:
    services, settings, work = make_services(tmp_path)
    settings.paths[(WORKSPACE, USER)] = str(work)
    destination = await file_services.OutputTarget(services).resolve(WORKSPACE, USER)
    assert destination.mode == "folder"
    assert destination.folder == work


async def test_output_target_missing_folder_falls_back_with_warning(tmp_path: Path) -> None:
    services, settings, work = make_services(tmp_path)
    settings.paths[(WORKSPACE, USER)] = str(work)
    work.rmdir()
    destination = await file_services.OutputTarget(services).resolve(WORKSPACE, USER)
    assert destination.mode == "artifacts"
    assert destination.warning and "missing" in destination.warning


async def test_deliver_output_copies_without_overwriting(tmp_path: Path) -> None:
    services, settings, work = make_services(tmp_path)
    settings.paths[(WORKSPACE, USER)] = str(work)
    file_services.configure(services)
    try:
        source = services.artifacts_dir / "report.pdf"
        source.write_bytes(b"%PDF-new")
        (work / "report.pdf").write_bytes(b"precious")
        final, warning = await file_services.deliver_output(WORKSPACE, USER, source)
        assert warning is None
        assert final == work / "report (2).pdf"
        assert (work / "report.pdf").read_bytes() == b"precious"
        assert final.read_bytes() == b"%PDF-new"
        assert source.exists()  # the artifacts copy stays: downloads keep working
        assert services.audits[-1]["tool"] == "file_write_new"
    finally:
        file_services.reset()


async def test_deliver_output_without_folder_keeps_the_artifact(tmp_path: Path) -> None:
    services, _settings, _work = make_services(tmp_path)
    file_services.configure(services)
    try:
        source = services.artifacts_dir / "a.pdf"
        source.write_bytes(b"x")
        final, warning = await file_services.deliver_output(WORKSPACE, USER, source)
        assert final == source and warning is None
    finally:
        file_services.reset()


async def test_deliver_output_missing_folder_warns_and_keeps_the_file(tmp_path: Path) -> None:
    services, settings, work = make_services(tmp_path)
    settings.paths[(WORKSPACE, USER)] = str(work)
    work.rmdir()
    file_services.configure(services)
    try:
        source = services.artifacts_dir / "a.pdf"
        source.write_bytes(b"x")
        final, warning = await file_services.deliver_output(WORKSPACE, USER, source)
        assert final == source
        assert warning and "storage" in warning
    finally:
        file_services.reset()


def test_helpers_never_overwrite_and_names_are_plain(tmp_path: Path) -> None:
    first = file_services.collision_safe_write(tmp_path, "a.txt", b"1")
    second = file_services.collision_safe_write(tmp_path, "a.txt", b"2")
    assert (first.name, second.name) == ("a.txt", "a (2).txt")
    assert first.read_bytes() == b"1"
    assert file_services.unique_subdirectory(tmp_path, "site").name == "site"
    assert file_services.unique_subdirectory(tmp_path, "site").name == "site-2"
    assert file_services.safe_output_name("../../evil.exe.pdf") == "evil.exe.pdf"
    assert file_services.safe_output_name("con.txt") != "con.txt"
    assert file_services.project_slug("Solar Energy Site!") == "solar-energy-site"
    assert file_services.project_slug("../..") == "project"


def test_an_unwritable_folder_says_why_in_plain_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal keeps its first sentence and adds the likely cause (access denied on Windows)."""
    import builtins

    real_open = builtins.open

    def _deny(path: object, *args: object, **kwargs: object) -> object:
        if ".maya-write-test-" in str(path):
            raise PermissionError(13, "Access is denied")
        return real_open(path, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(builtins, "open", _deny)
    services, _settings, work = make_services(tmp_path)
    with pytest.raises(FolderError) as raised:
        services.folders.validate(str(work))
    assert raised.value.code == "readonly"
    message = str(raised.value)
    assert message.startswith("MAYA cannot write to that folder. Pick one you can edit.")
    assert "access denied" in message
    assert "Controlled folder access" in message
    assert str(work) not in message
