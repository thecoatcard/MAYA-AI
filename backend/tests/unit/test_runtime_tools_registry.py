"""Tool registry: names, paths, versions, hash-bound approval, once versus reusable, caps."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.runtime_tools.registry import (
    MAX_TOOLS_PER_WORKSPACE,
    RegistryError,
    ToolRecord,
    ToolRegistry,
    sha256_text,
)
from tests.support.runtime_fakes import ECHO_SOURCE

SCHEMA: dict[str, Any] = {"type": "object", "properties": {"text": {"type": "string"}}}


def make(
    registry: ToolRegistry, name: str = "echo_tool", source: str = ECHO_SOURCE, **caps: bool
) -> tuple[ToolRecord, bool]:
    return registry.create(
        name=name,
        description="Echo the text back",
        source=source,
        input_schema=SCHEMA,
        capabilities=caps,
        created_by="user-1",
    )


@pytest.fixture
def registry(tmp_path: Path) -> ToolRegistry:
    return ToolRegistry(tmp_path / "runtime_tools")


def test_create_writes_source_and_manifest_and_binds_the_hash(registry: ToolRegistry) -> None:
    record, created = make(registry)
    assert created and record.version == 1
    base = registry.root / "echo_tool" / "1"
    assert (base / "tool.py").read_text(encoding="utf-8") == ECHO_SOURCE
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == sha256_text(ECHO_SOURCE)
    assert manifest["capabilities"] == {"files_in": False, "files_out": False, "network": False}
    assert manifest["status"] == "proposed" and manifest["approved_reusable"] is False
    expected = {"name", "description", "version", "input_schema", "created_at", "created_by"}
    assert expected <= set(manifest)
    assert registry.grant_state(record) == "none"


def test_same_code_is_the_same_version_changed_code_or_caps_is_a_new_version(
    registry: ToolRegistry,
) -> None:
    first, _ = make(registry)
    again, created = make(registry)
    assert not created and again.version == first.version
    changed, created = make(registry, source=ECHO_SOURCE + "\n# changed\n")
    assert created and changed.version == 2
    wider, created = make(registry, source=ECHO_SOURCE + "\n# changed\n", files_out=True)
    assert created and wider.version == 3


def test_approval_binds_to_source_and_capabilities(registry: ToolRegistry) -> None:
    record, _ = make(registry)
    assert registry.approve("echo_tool", 1, binding=record.binding, reusable=True) == "reusable"
    approved = registry.get("echo_tool", 1)
    assert registry.grant_state(approved) == "reusable"
    assert approved.manifest["approved_reusable"] is True
    changed, _ = make(registry, source=ECHO_SOURCE + "\n# edit\n")
    assert registry.grant_state(changed) == "none"
    with pytest.raises(RegistryError):
        registry.approve("echo_tool", 2, binding=record.binding, reusable=True)


def test_editing_the_stored_code_or_capabilities_voids_the_approval(registry: ToolRegistry) -> None:
    record, _ = make(registry)
    registry.approve("echo_tool", 1, binding=record.binding, reusable=True)
    base = registry.root / "echo_tool" / "1"
    (base / "tool.py").write_text(ECHO_SOURCE + "\nx = 1\n", encoding="utf-8")
    assert registry.grant_state(registry.get("echo_tool", 1)) == "none"
    (base / "tool.py").write_text(ECHO_SOURCE, encoding="utf-8")
    assert registry.grant_state(registry.get("echo_tool", 1)) == "reusable"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    manifest["capabilities"]["network"] = True
    (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert registry.grant_state(registry.get("echo_tool", 1)) == "none"


def test_approve_once_is_spent_by_one_run_reusable_is_not(registry: ToolRegistry) -> None:
    once, _ = make(registry, name="once_tool")
    registry.approve("once_tool", 1, binding=once.binding, reusable=False)
    assert registry.consume(registry.get("once_tool", 1)) == "once"
    with pytest.raises(RegistryError) as refused:
        registry.consume(registry.get("once_tool", 1))
    assert refused.value.code == "not_approved"
    keep, _ = make(registry, name="keep_tool")
    registry.approve("keep_tool", 1, binding=keep.binding, reusable=True)
    for _ in range(3):
        assert registry.consume(registry.get("keep_tool", 1)) == "reusable"


def test_network_capability_is_never_reusable(registry: ToolRegistry) -> None:
    record, _ = make(registry, name="net_tool", network=True)
    assert registry.approve("net_tool", 1, binding=record.binding, reusable=True) == "once"
    assert registry.consume(registry.get("net_tool", 1)) == "once"
    with pytest.raises(RegistryError):
        registry.consume(registry.get("net_tool", 1))


def test_retire_makes_every_version_unrunnable_and_hidden_from_search(
    registry: ToolRegistry,
) -> None:
    record, _ = make(registry)
    registry.approve("echo_tool", 1, binding=record.binding, reusable=True)
    assert [r.name for r in registry.search("echo text")] == ["echo_tool"]
    assert registry.retire("echo_tool") == 1
    assert registry.search("echo text") == []
    with pytest.raises(RegistryError):
        registry.consume(registry.get("echo_tool", 1))


def test_search_ranks_by_name_and_description_words(registry: ToolRegistry) -> None:
    make(registry, name="csv_to_chart")
    registry.create(
        name="pdf_text_dump",
        description="Extract the text of a PDF",
        source=ECHO_SOURCE,
        input_schema=SCHEMA,
        capabilities={},
        created_by="u",
    )
    assert registry.search("extract pdf text")[0].name == "pdf_text_dump"
    assert registry.search("chart")[0].name == "csv_to_chart"
    assert registry.search("zzz unmatched") == []


@pytest.mark.parametrize(
    "name",
    [
        "../evil",
        "..",
        "a/b",
        "a\\b",
        "C:\\x",
        "C:evil",
        "/etc/passwd",
        "Upper",
        "ab",
        "1abc",
        "a" * 42,
        "has space",
        "dot.name",
        "",
        "nul\x00x",
    ],
)
def test_names_that_could_escape_or_are_malformed_are_refused(
    registry: ToolRegistry, name: str
) -> None:
    with pytest.raises(RegistryError) as refused:
        make(registry, name=name)
    assert refused.value.code == "invalid_name"
    assert registry.names() == []


def test_reading_a_tool_by_a_traversal_name_is_refused(registry: ToolRegistry) -> None:
    make(registry)
    for bad in ("../echo_tool", "echo_tool/../echo_tool", "/abs", "C:\\x"):
        with pytest.raises(RegistryError):
            registry.get(bad)


def test_invalid_versions_are_refused(registry: ToolRegistry) -> None:
    make(registry)
    for bad in (0, -1, 99999):
        with pytest.raises(RegistryError):
            registry.get("echo_tool", bad)


def test_a_symlinked_tool_directory_is_refused(registry: ToolRegistry, tmp_path: Path) -> None:
    make(registry)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = registry.root / "linked_tool"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this machine")
    assert "linked_tool" not in registry.names()
    with pytest.raises(RegistryError):
        make(registry, name="linked_tool")
    assert list(outside.iterdir()) == []


def test_static_check_failures_never_reach_the_registry(registry: ToolRegistry) -> None:
    bad = "import os\n\ndef main(args):\n    return {}\n"
    with pytest.raises(RegistryError) as refused:
        make(registry, source=bad)
    assert refused.value.code == "static_check" and registry.names() == []


@pytest.mark.parametrize("caps", [{"root": True}, {"network": "yes"}, ["network"]])
def test_unknown_or_mistyped_capabilities_are_refused(registry: ToolRegistry, caps: Any) -> None:
    with pytest.raises(RegistryError):
        registry.create(
            name="caps_tool",
            description="x",
            source=ECHO_SOURCE,
            input_schema=SCHEMA,
            capabilities=caps,
            created_by="u",
        )


def test_size_and_count_caps(registry: ToolRegistry) -> None:
    with pytest.raises(RegistryError):
        registry.create(
            name="long_desc",
            description="d" * 501,
            source=ECHO_SOURCE,
            input_schema=SCHEMA,
            capabilities={},
            created_by="u",
        )
    with pytest.raises(RegistryError):
        registry.create(
            name="big_schema",
            description="d",
            source=ECHO_SOURCE,
            input_schema={"x": "y" * 5000},
            capabilities={},
            created_by="u",
        )
    for index in range(MAX_TOOLS_PER_WORKSPACE):
        (registry.root / f"filler_{index:03d}" / "1").mkdir(parents=True)
    with pytest.raises(RegistryError) as refused:
        make(registry, name="one_too_many")
    assert refused.value.code == "too_many"


def test_the_run_log_has_no_payload_content(registry: ToolRegistry) -> None:
    record, _ = make(registry)
    registry.log_run(
        record,
        exit_code=0,
        duration_seconds=1.234,
        output_bytes=12,
        files_out=1,
        timed_out=False,
        user_id="user-1",
    )
    (entry,) = registry.run_log("echo_tool")
    assert set(entry) == {
        "at",
        "version",
        "sha256",
        "exit_code",
        "duration_seconds",
        "output_bytes",
        "files_out",
        "timed_out",
        "user",
    }
    assert registry.get("echo_tool", 1).manifest["last_run"] == entry["at"]
