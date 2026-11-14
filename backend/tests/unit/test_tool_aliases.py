"""Tool alias table: synonyms resolve to the classified tool, tier never lowered."""

from __future__ import annotations

import pytest

from app.mcp.permissions import (
    DEFAULT_TOOL_PERMISSIONS,
    TOOL_ALIASES,
    PermissionDeniedError,
    ToolPermissionRegistry,
    ToolTier,
    resolve_tool_alias,
)


@pytest.mark.parametrize("alias", ["save_file", "write_file", "export_file", "store_file"])
def test_synonyms_map_to_the_artifact_write_tool_with_its_own_tier(alias: str) -> None:
    registry = ToolPermissionRegistry()
    assert resolve_tool_alias(alias) == "file_write_artifact"
    assert registry.canonical_name(alias) == "file_write_artifact"
    assert registry.classify(alias) == registry.classify("file_write_artifact") == ToolTier.WRITE
    assert not registry.is_registered(alias)  # it is a synonym, not a registered tool


def test_unknown_tools_remain_denied_by_default() -> None:
    registry = ToolPermissionRegistry()
    with pytest.raises(PermissionDeniedError):
        registry.classify("mystery_shell")
    assert registry.canonical_name("mystery_shell") == "mystery_shell"
    assert registry.requires_human_approval("mystery_shell") is True


def test_every_alias_targets_a_registered_tool_at_read_or_write_tier_only() -> None:
    for alias, target in TOOL_ALIASES.items():
        assert target in DEFAULT_TOOL_PERMISSIONS, alias
        assert DEFAULT_TOOL_PERMISSIONS[target] in (ToolTier.READ, ToolTier.WRITE), alias


def test_an_alias_never_follows_a_target_above_write() -> None:
    registry = ToolPermissionRegistry({"file_write_artifact": ToolTier.APPROVAL_REQUIRED})
    assert registry.canonical_name("save_file") == "save_file"  # not resolved
    with pytest.raises(PermissionDeniedError):
        registry.classify("save_file")


def test_a_registered_name_wins_over_an_alias_and_keeps_its_tier() -> None:
    registry = ToolPermissionRegistry({"save_file": ToolTier.RESTRICTED})
    assert registry.canonical_name("save_file") == "save_file"
    assert registry.classify("save_file") == ToolTier.RESTRICTED


def test_the_authorisation_gate_is_unchanged_for_aliases_of_write_tools() -> None:
    registry = ToolPermissionRegistry()
    assert registry.authorize_tool_call("save_file", {}) == ToolTier.WRITE
    assert all(registry.classify(alias) < ToolTier.APPROVAL_REQUIRED for alias in TOOL_ALIASES)
