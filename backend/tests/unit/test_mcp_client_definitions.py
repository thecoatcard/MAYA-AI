"""Server definitions: exact version pins, built-ins disabled, env allowlist rules, the file."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.mcp_client.definitions import (
    BUILTIN_DEFINITIONS,
    ServerDefinition,
    load_definitions,
    parse_definitions,
    pin_problem,
)


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("npx", ("-y", "@playwright/mcp@latest")),
        ("npx", ("-y", "@playwright/mcp")),
        ("npx", ("@playwright/mcp@^1.2.3",)),
        ("npx", ("@playwright/mcp@1",)),
        ("npx", ("@playwright/mcp@next",)),
        ("npx.cmd", ("-y", "pkg@1.x")),
        ("npm", ("exec", "pkg")),
        ("pnpm", ("dlx", "pkg@latest")),
        ("uvx", ("some-server",)),
        ("uvx", ("some-server>=1.0",)),
        ("npx", ("-y", "@playwright/mcp@{version}")),
    ],
)
def test_floating_or_unpinned_launches_are_refused(command: str, args: tuple[str, ...]) -> None:
    assert pin_problem(command, args, None) is not None


@pytest.mark.parametrize(
    ("command", "args", "version"),
    [
        ("npx", ("-y", "@playwright/mcp@1.2.3", "--headless"), None),
        ("npx.cmd", ("pkg@0.0.71",), None),
        ("pnpm", ("dlx", "pkg@2.0.0-beta.1"), None),
        ("uvx", ("some-server==1.2.3",), None),
        ("github-mcp-server", ("stdio",), "1.4.0"),
        ("C:\\tools\\github-mcp-server.exe", ("stdio",), "v1.4.0"),
    ],
)
def test_exact_pins_are_accepted(command: str, args: tuple[str, ...], version: str | None) -> None:
    assert pin_problem(command, args, version) is None


@pytest.mark.parametrize("version", [None, "latest", "1", "1.2", "^1.2.3", ""])
def test_a_plain_program_needs_an_exact_declared_version(version: str | None) -> None:
    assert pin_problem("github-mcp-server", ("stdio",), version) is not None


def test_the_builtin_servers_exist_and_are_disabled_and_unpinned() -> None:
    assert set(BUILTIN_DEFINITIONS) == {"playwright", "github"}
    for definition in BUILTIN_DEFINITIONS.values():
        assert definition.enabled is False
        assert definition.pin_problem() is not None  # no version shipped: the owner sets one
    assert BUILTIN_DEFINITIONS["github"].credential_env == {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "github"
    }


def test_enabling_a_builtin_with_an_exact_version_works() -> None:
    found = parse_definitions(
        {"servers": [{"name": "playwright", "enabled": True, "version": "0.0.5"}]}
    )
    assert found.problems == []
    assert set(found.enabled()) == {"playwright"}
    expected = ("-y", "@playwright/mcp@0.0.5", "--headless")
    assert found.enabled()["playwright"].resolved_args() == expected


def test_enabling_a_builtin_without_a_version_is_refused_and_it_stays_off() -> None:
    found = parse_definitions([{"name": "playwright", "enabled": True}])
    assert found.enabled() == {}
    assert any("playwright" in p and "version" in p for p in found.problems)
    assert found.servers["playwright"].enabled is False


def test_a_latest_version_in_the_file_is_refused() -> None:
    found = parse_definitions([{"name": "playwright", "enabled": True, "version": "latest"}])
    assert found.enabled() == {} and found.problems


def test_a_builtin_cannot_have_its_tiers_or_env_changed_by_the_file() -> None:
    found = parse_definitions(
        [{"name": "github", "enabled": True, "version": "1.0.0", "read_tools": ["create_issue"]}]
    )
    assert found.enabled() == {} and any("read_tools" in p for p in found.problems)
    assert "create_issue" not in BUILTIN_DEFINITIONS["github"].read_tools


def test_a_custom_server_starts_with_no_read_tools() -> None:
    found = parse_definitions(
        [{"name": "docs", "command": "docs-mcp", "version": "1.0.0", "enabled": True}]
    )
    assert found.enabled()["docs"].read_tools == frozenset()


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "Bad Name", "command": "x"},
        {"name": "a__b", "command": "x"},
        {"name": "ok", "command": "x && y"},
        {"name": "ok", "command": "x;rm"},
        {"name": "ok", "command": "x", "env_allow": ["MAYA_JWT_SIGNING_KEY"]},
        {"name": "ok", "command": "x", "env_allow": ["lower"]},
        {"name": "ok", "command": "x", "credential_env": {"TOKEN": "aws"}},
        {"name": "ok", "command": "x", "transport": "http"},
    ],
)
def test_bad_entries_are_skipped_with_a_reason(entry: dict[str, Any]) -> None:
    found = parse_definitions([entry])
    assert "ok" not in found.servers and "Bad Name" not in found.servers
    assert len(found.problems) == 1


def test_env_allow_rejects_the_apps_own_variables() -> None:
    with pytest.raises(ValidationError):
        ServerDefinition(name="x", command="x", env_allow=("MAYA_DATABASE_URL",))


def test_file_loading_is_tolerant(tmp_path: Path) -> None:
    good = tmp_path / "servers.json"
    good.write_text(
        json.dumps({"servers": [{"name": "github", "enabled": True, "version": "1.0.0"}]}),
        encoding="utf-8",
    )
    assert set(load_definitions(good).enabled()) == {"github"}
    assert load_definitions("").enabled() == {} and load_definitions(None).enabled() == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    result = load_definitions(bad)
    assert result.enabled() == {} and result.problems
    assert "not json" not in " ".join(result.problems)
    assert load_definitions(tmp_path / "missing.json").problems
    big = tmp_path / "big.json"
    big.write_text(" " * (70 * 1024), encoding="utf-8")
    assert load_definitions(big).problems
