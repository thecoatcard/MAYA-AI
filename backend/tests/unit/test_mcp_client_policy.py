"""Tiers for third-party tools and the refusal of private browser targets (SSRF)."""

import pytest

from app.mcp.permissions import ToolTier
from app.mcp_client.definitions import BUILTIN_DEFINITIONS, ServerDefinition
from app.mcp_client.policy import (
    base_tier,
    build_registry,
    check_arguments,
    effective_tier,
    host_named_by_user,
    url_problem,
)

PLAYWRIGHT = BUILTIN_DEFINITIONS["playwright"]
GITHUB = BUILTIN_DEFINITIONS["github"]
REGISTRY = build_registry(dict(BUILTIN_DEFINITIONS))


async def resolver(host: str) -> list[str]:
    return {"public.example": ["93.184.216.34"], "rebind.example": ["10.1.2.3"]}.get(host, [])


# ------------------------------------------------------------------------------------ tiers

PLAYWRIGHT_READ = [
    "browser_snapshot",
    "browser_take_screenshot",
    "browser_console_messages",
    "browser_network_requests",
]
PLAYWRIGHT_APPROVAL = [
    "browser_click",
    "browser_type",
    "browser_fill_form",
    "browser_evaluate",
    "browser_file_upload",
    "browser_run_code",
    "browser_press_key",
    "browser_select_option",
    "browser_navigate",
    "browser_some_new_tool",  # unknown: approval
]
GITHUB_READ = ["get_file_contents", "list_issues", "search_code", "pull_request_read", "issue_read"]
GITHUB_APPROVAL = [
    "create_issue",
    "issue_write",
    "add_issue_comment",
    "create_pull_request",
    "merge_pull_request",
    "create_branch",
    "create_or_update_file",
    "push_files",
    "delete_file",
    "fork_repository",
    "brand_new_tool",  # unknown: approval
]


@pytest.mark.parametrize("tool", PLAYWRIGHT_READ)
def test_playwright_read_tools_are_automatic(tool: str) -> None:
    assert base_tier(REGISTRY, "playwright", tool) is ToolTier.READ


@pytest.mark.parametrize("tool", PLAYWRIGHT_APPROVAL)
def test_playwright_other_tools_need_approval(tool: str) -> None:
    assert base_tier(REGISTRY, "playwright", tool) is ToolTier.APPROVAL_REQUIRED


@pytest.mark.parametrize("tool", GITHUB_READ)
def test_github_read_tools_are_automatic(tool: str) -> None:
    assert base_tier(REGISTRY, "github", tool) is ToolTier.READ


@pytest.mark.parametrize("tool", GITHUB_APPROVAL)
def test_github_writes_and_unknown_tools_need_approval(tool: str) -> None:
    assert base_tier(REGISTRY, "github", tool) is ToolTier.APPROVAL_REQUIRED


def test_a_destructive_hint_raises_a_read_tool_and_never_lowers() -> None:
    raised = base_tier(REGISTRY, "github", "list_issues", {"destructiveHint": True})
    assert raised is ToolTier.APPROVAL_REQUIRED
    kept = base_tier(REGISTRY, "github", "create_issue", {"readOnlyHint": True})
    assert kept is ToolTier.APPROVAL_REQUIRED


def test_a_tool_is_not_read_under_another_servers_name() -> None:
    assert base_tier(REGISTRY, "unknown", "list_issues") is ToolTier.APPROVAL_REQUIRED


def test_opening_a_page_is_automatic_only_for_an_address_the_user_wrote() -> None:
    def tier(url: str, text: str) -> ToolTier:
        return effective_tier(
            PLAYWRIGHT, REGISTRY, "browser_navigate", {"url": url}, hints=None, user_text=text
        )

    assert tier("https://example.com/a", "please open example.com and summarise") is ToolTier.READ
    assert tier("https://evil.test/x", "please open example.com") is ToolTier.APPROVAL_REQUIRED
    assert tier("https://example.com/a", "") is ToolTier.APPROVAL_REQUIRED
    click = effective_tier(
        PLAYWRIGHT,
        REGISTRY,
        "browser_click",
        {"url": "example.com"},
        hints=None,
        user_text="example.com",
    )
    assert click is ToolTier.APPROVAL_REQUIRED


def test_host_named_by_user_compares_the_whole_host() -> None:
    assert host_named_by_user("https://example.com/path", "go to example.com now")
    assert not host_named_by_user("https://other.test/", "go to example.com now")
    assert not host_named_by_user("https://example.com/", "")


# ------------------------------------------------------------------------------------ SSRF

REFUSED = [
    "http://localhost/",
    "http://LOCALHOST:8000/admin",
    "http://localhost./",
    "http://app.localhost/",
    "http://127.0.0.1/",
    "http://127.1/",
    "http://2130706433/",  # decimal 127.0.0.1
    "http://0x7f000001/",  # hex
    "http://0177.0.0.1/",  # octal
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://[::ffff:10.0.0.1]/",
    "http://0.0.0.0/",
    "http://10.0.0.5/",
    "http://172.16.4.4/",
    "http://192.168.1.1/router",
    "http://169.254.169.254/latest/meta-data/",
    "http://[fe80::1]/",
    "http://[fd00::1]/",
    "http://100.64.0.1/",  # carrier-grade NAT
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://metadata/",
    "http://printer.local/",
    "http://nas.lan/",
    "http://intranet/",  # a name without a domain
    "http://%6c%6f%63%61%6c%68%6f%73%74/",
    "http://localhost\u3002/",
    "file:///C:/Windows/win.ini",
    "javascript:alert(1)",
    "data:text/html,<script>1</script>",
    "chrome://settings",
    "about:blank",
    "ftp://example.com/",
    "http://rebind.example/",  # resolves to a private address
    "localhost:3000",
    "",
]
ALLOWED = [
    "https://example.com/",
    "http://public.example/x?y=1",
    "example.com",
    "http://93.184.216.34/",
]


@pytest.mark.parametrize("url", REFUSED)
async def test_private_and_non_http_targets_are_refused(url: str) -> None:
    assert await url_problem(url, resolver) is not None


@pytest.mark.parametrize("url", ALLOWED)
async def test_public_targets_are_allowed(url: str) -> None:
    assert await url_problem(url, resolver) is None


async def test_navigate_arguments_are_inspected_and_other_servers_are_not() -> None:
    assert await check_arguments(
        PLAYWRIGHT, "browser_navigate", {"url": "http://169.254.169.254/"}, resolver
    )
    assert await check_arguments(PLAYWRIGHT, "browser_navigate", {}, resolver)
    ok = {"url": "https://example.com"}
    assert await check_arguments(PLAYWRIGHT, "browser_navigate", ok, resolver) is None
    # A URL hidden in another argument or a nested value of ANY tool of a browser server.
    tabs = {"action": "new", "url": "http://10.0.0.1"}
    assert await check_arguments(PLAYWRIGHT, "browser_tabs", tabs, resolver)
    nested = {"opts": {"x": ["http://127.0.0.1/"]}}
    assert await check_arguments(PLAYWRIGHT, "browser_click", nested, resolver)
    assert await check_arguments(PLAYWRIGHT, "browser_tabs", {"action": "list"}, resolver) is None
    other = {"url": "http://127.0.0.1"}
    assert await check_arguments(GITHUB, "get_file_contents", other, resolver) is None


def test_a_custom_definition_has_no_url_guard_unless_asked() -> None:
    assert ServerDefinition(name="x", command="x", version="1.0.0").url_guard is False
