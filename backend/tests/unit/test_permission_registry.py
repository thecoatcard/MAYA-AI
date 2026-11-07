"""Unit tests for the Four-Tier Permission Registry and Deny-by-Default Classifier (Decision D4)."""

import pytest

from app.mcp.permissions import (
    PermissionDeniedError,
    ToolPermissionRegistry,
    ToolTier,
)


def test_tool_tier_ranking_order() -> None:
    assert ToolTier.READ < ToolTier.WRITE
    assert ToolTier.WRITE < ToolTier.APPROVAL_REQUIRED
    assert ToolTier.APPROVAL_REQUIRED < ToolTier.RESTRICTED
    assert ToolTier.READ.rank == 1
    assert ToolTier.WRITE.rank == 2
    assert ToolTier.APPROVAL_REQUIRED.rank == 3
    assert ToolTier.RESTRICTED.rank == 4


def test_default_tool_classifications() -> None:
    registry = ToolPermissionRegistry()
    assert registry.classify("file_read") == ToolTier.READ
    assert registry.classify("web_search") == ToolTier.READ
    assert registry.classify("file_write_artifact") == ToolTier.WRITE
    assert registry.classify("draft_email") == ToolTier.WRITE
    assert registry.classify("gmail_send") == ToolTier.APPROVAL_REQUIRED
    assert registry.classify("git_push") == ToolTier.APPROVAL_REQUIRED
    assert registry.classify("database_ddl") == ToolTier.RESTRICTED
    assert registry.classify("bulk_delete") == ToolTier.RESTRICTED


def test_unregistered_tool_denied_by_default() -> None:
    registry = ToolPermissionRegistry()
    with pytest.raises(PermissionDeniedError) as exc_info:
        registry.classify("unknown_malicious_tool")
    assert "not registered" in str(exc_info.value)
    assert exc_info.value.tool_name == "unknown_malicious_tool"
    assert not registry.is_allowed_autonomous("unknown_malicious_tool")
    assert registry.requires_human_approval("unknown_malicious_tool")


def test_custom_tool_registration() -> None:
    registry = ToolPermissionRegistry()
    assert not registry.is_registered("custom_tool")
    registry.register_tool("custom_tool", ToolTier.APPROVAL_REQUIRED)
    assert registry.is_registered("custom_tool")
    assert registry.classify("custom_tool") == ToolTier.APPROVAL_REQUIRED

    registry.unregister_tool("custom_tool")
    assert not registry.is_registered("custom_tool")
    with pytest.raises(PermissionDeniedError):
        registry.classify("custom_tool")


def test_server_hints_can_elevate_tier() -> None:
    registry = ToolPermissionRegistry()
    # A read tool marked destructive elevates to APPROVAL_REQUIRED
    elevated = registry.classify("file_read", server_hints={"destructiveHint": True})
    assert elevated == ToolTier.APPROVAL_REQUIRED

    # A restricted tool remains RESTRICTED
    restricted = registry.classify("database_ddl", server_hints={"destructiveHint": True})
    assert restricted == ToolTier.RESTRICTED


def test_server_hints_can_never_lower_tier() -> None:
    registry = ToolPermissionRegistry()
    # Attempting to lower an APPROVAL_REQUIRED tool with readOnlyHint must NOT lower it
    result = registry.classify("gmail_send", server_hints={"readOnlyHint": True})
    assert result == ToolTier.APPROVAL_REQUIRED

    # Attempting to lower a RESTRICTED tool must NOT lower it
    result_restricted = registry.classify("database_ddl", server_hints={"readOnlyHint": True})
    assert result_restricted == ToolTier.RESTRICTED


def test_autonomous_and_approval_predicates() -> None:
    registry = ToolPermissionRegistry()

    assert registry.is_allowed_autonomous("file_read") is True
    assert registry.is_allowed_autonomous("file_write_artifact") is True
    assert registry.is_allowed_autonomous("gmail_send") is False
    assert registry.is_allowed_autonomous("database_ddl") is False

    assert registry.requires_human_approval("file_read") is False
    assert registry.requires_human_approval("file_write_artifact") is False
    assert registry.requires_human_approval("gmail_send") is True
    assert registry.requires_human_approval("database_ddl") is True

    assert registry.requires_step_up_auth("file_read") is False
    assert registry.requires_step_up_auth("gmail_send") is False
    assert registry.requires_step_up_auth("database_ddl") is True


def test_registry_freezing_and_immutability() -> None:
    registry = ToolPermissionRegistry()
    registry.freeze()
    assert registry.is_frozen is True

    # Registering new tools when frozen must raise PermissionDeniedError
    with pytest.raises(PermissionDeniedError, match="frozen and immutable"):
        registry.register_tool("escalated_tool", ToolTier.READ)

    # Unregistering tools when frozen must raise PermissionDeniedError
    with pytest.raises(PermissionDeniedError, match="frozen and immutable"):
        registry.unregister_tool("gmail_send")


def test_argument_schema_validation_and_pre_execution_authorization() -> None:
    registry = ToolPermissionRegistry()
    registry.register_tool("api_deploy", ToolTier.APPROVAL_REQUIRED, required_args={"image", "tag"})

    # Missing required argument fails validation
    with pytest.raises(PermissionDeniedError, match="missing required arguments"):
        registry.authorize_tool_call("api_deploy", {"image": "myapp"})

    # Valid arguments but missing approval token fails
    with pytest.raises(PermissionDeniedError, match="forbidden without valid approval token"):
        registry.authorize_tool_call(
            "api_deploy",
            {"image": "myapp", "tag": "v1.0"},
            has_valid_approval_token=False,
        )

    # Valid arguments with approval token succeeds
    tier = registry.authorize_tool_call(
        "api_deploy",
        {"image": "myapp", "tag": "v1.0"},
        has_valid_approval_token=True,
    )
    assert tier == ToolTier.APPROVAL_REQUIRED
