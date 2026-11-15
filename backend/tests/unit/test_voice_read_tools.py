"""Voice and the read-only connector tools: declarations gated on the connection, dispatch, and the
long-text route to the chat transcript (the spoken reply stays short).

No real Gemini, Notion, GitHub, database or WebSocket: the socket is a mock, persistence is a
recording fake and all HTTP is faked with ``httpx.MockTransport``.
"""

import base64
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx

from app.auth.models import Principal
from app.files.access import FileAccessError
from app.mcp.connectors.github import GitHubConnector
from app.mcp.connectors.notion import NotionConnector
from app.mcp.limiter import VendorRateLimiter
from app.mcp.notion_tools import NOTION_TOOL_NAMES, NotionToolkit
from app.mcp.show_code import SHOW_CODE_NAME, ShowCodeToolkit
from app.voice.connection import (
    VOICE_CHAT_MIN_CHARS,
    VOICE_EXCERPT_CHARS,
    VoiceConnection,
    VoiceDeps,
)
from app.voice.persistence import VoicePersistenceError
from app.voice.prompts import build_voice_instruction
from app.voice.tools import VoiceExecutionContext, VoiceToolRegistry

WS = uuid4()
USER = uuid4()
SESSION = uuid4()
DB = "0123456789abcdef0123456789abcdef"
CONTENTS = "/repos/octo/demo/contents/"


class RecordingPersistence:
    """``save_turn`` recorder. ``mode`` picks the outcome: saved, no database, or a failure."""

    def __init__(self, mode: str = "saved") -> None:
        self.mode = mode
        self.calls: list[dict[str, Any]] = []
        self.engine = None

    async def save_turn(
        self,
        workspace_id: UUID,
        user_id: UUID,
        session_id: UUID,
        *,
        role: str,
        content: str,
        modality: str,
        metadata: dict[str, Any] | None = None,
    ) -> UUID | None:
        if self.mode == "fail":
            raise VoicePersistenceError("turn save failed")
        self.calls.append(
            {
                "workspace_id": workspace_id,
                "session_id": session_id,
                "role": role,
                "content": content,
                "modality": modality,
                "metadata": metadata,
            }
        )
        return None if self.mode == "no_database" else uuid4()


def _limiter() -> VendorRateLimiter:
    limiter = VendorRateLimiter()
    limiter.configure_vendor("notion", 1000.0, 1000.0)
    return limiter


def _github_transport(files: dict[str, bytes]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in files:
            data = files[path]
            body = {
                "type": "file",
                "path": path.removeprefix(CONTENTS),
                "size": len(data),
                "encoding": "base64",
                "content": base64.b64encode(data).decode(),
            }
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handle)


class FakeRepoTools:
    """Stands in for ``ChatRepoTools``: the same three factories and the Notion gate."""

    def __init__(self, notion_connected: bool, github_files: dict[str, bytes] | None = None):
        self.notion_connected = notion_connected
        self.github_files = github_files or {}
        self.notion_calls: list[dict[str, Any]] = []

    async def notion_available(self, text: str) -> bool:
        return self.notion_connected

    def new_toolkit(self, **limits: int) -> Any:
        from app.mcp.github_repo_tools import RepoToolkit

        gh = GitHubConnector("ghp_" + "w" * 30, transport=_github_transport(self.github_files))

        async def github() -> GitHubConnector:
            return gh

        return RepoToolkit(github, **limits)

    def new_notion_toolkit(self, **limits: int) -> NotionToolkit:
        connector = NotionConnector(
            "ntn_" + "v" * 40,
            rate_limiter=_limiter(),
            transport=httpx.MockTransport(self._notion),
        )

        async def provider() -> tuple[NotionConnector, str | None]:
            return connector, DB

        return NotionToolkit(provider, **limits)

    def _notion(self, request: httpx.Request) -> httpx.Response:
        self.notion_calls.append({"method": request.method, "path": request.url.path})
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"object": "database", "properties": {}})

    def new_show_code_toolkit(self) -> ShowCodeToolkit:
        gh = GitHubConnector("ghp_" + "w" * 30, transport=_github_transport(self.github_files))

        async def github() -> GitHubConnector:
            return gh

        async def files() -> Any:
            raise FileAccessError("No usable folder is connected.")

        return ShowCodeToolkit(github, files)


def make_connection(repo: Any, persistence: Any) -> VoiceConnection:
    principal = Principal(user_id=USER, workspace_id=WS, role="user", session_id=uuid4())
    deps = VoiceDeps(
        persistence=persistence,
        session_manager=AsyncMock(),
        redis=None,
        model_gateway=None,
        queue=None,
        api_key="",
        models=[],
        repo_tools=repo,
    )
    connection = VoiceConnection(AsyncMock(), principal, deps)
    connection.session_id = SESSION
    return connection


def _context() -> VoiceExecutionContext:
    return VoiceExecutionContext(workspace_id=WS, user_id=USER, session_id=str(SESSION))


def _names(registry: VoiceToolRegistry) -> set[str]:
    return {d["name"] for d in registry.get_tool_declarations()}


# ----------------------------------------------------------------------------- declarations


async def test_notion_tools_are_declared_only_when_the_caller_has_notion() -> None:
    off = make_connection(FakeRepoTools(notion_connected=False), RecordingPersistence())
    registry_off = VoiceToolRegistry()
    await off._enable_connector_tools(registry_off)
    assert SHOW_CODE_NAME in _names(registry_off)
    assert not set(NOTION_TOOL_NAMES) & _names(registry_off)
    assert not set(NOTION_TOOL_NAMES) & set(registry_off.allowed_tool_names)

    on = make_connection(FakeRepoTools(notion_connected=True), RecordingPersistence())
    registry_on = VoiceToolRegistry()
    await on._enable_connector_tools(registry_on)
    assert set(NOTION_TOOL_NAMES) | {SHOW_CODE_NAME} <= _names(registry_on)
    assert set(NOTION_TOOL_NAMES) | {SHOW_CODE_NAME} <= set(registry_on.allowed_tool_names)
    for decl in registry_on.get_tool_declarations():
        assert decl["parameters"]["type"] == "OBJECT"


async def test_no_repo_tools_wired_means_no_connector_tools() -> None:
    connection = make_connection(None, RecordingPersistence())
    registry = VoiceToolRegistry()
    await connection._enable_connector_tools(registry)
    assert not registry.connector_enabled
    assert SHOW_CODE_NAME not in _names(registry)


def test_voice_instruction_mentions_the_new_rules_only_when_on() -> None:
    plain = build_voice_instruction(memory_on=False)
    assert "show_code" not in plain and "Notion tools" not in plain
    text = build_voice_instruction(memory_on=False, notion_on=True, show_code_on=True)
    assert "Notion tools (read-only)" in text
    assert "I put the code in the chat" in text


# ------------------------------------------------------------------------------ dispatch


async def test_show_code_runs_through_the_registry_and_goes_to_the_chat() -> None:
    source = 'def hello():\n    return "hi"\n'
    repo = FakeRepoTools(
        notion_connected=False,
        github_files={f"{CONTENTS}src/app.py": source.encode()},
    )
    persistence = RecordingPersistence()
    connection = make_connection(repo, persistence)
    registry = VoiceToolRegistry()
    await connection._enable_connector_tools(registry)

    result = await registry.execute_tool_async(
        SHOW_CODE_NAME, {"source": "octo/demo", "path": "src/app.py"}, _context()
    )

    # the model hears only the short sentence, never the code
    assert result["posted_to_chat"] is True
    assert "I put the code in the chat" in result["message"]
    assert "def hello" not in str(result)
    # the code is the chat transcript: one assistant text message in this conversation
    assert len(persistence.calls) == 1
    saved = persistence.calls[0]
    assert saved["session_id"] == SESSION and saved["role"] == "assistant"
    assert saved["modality"] == "text"
    assert "def hello" in saved["content"]
    assert saved["metadata"] == {"source": "voice_tool", "tool": SHOW_CODE_NAME}


async def test_show_code_refusal_reaches_the_model_as_a_plain_error() -> None:
    connection = make_connection(FakeRepoTools(notion_connected=False), RecordingPersistence())
    registry = VoiceToolRegistry()
    await connection._enable_connector_tools(registry)
    result = await registry.execute_tool_async(
        SHOW_CODE_NAME, {"source": "folder", "path": "x.py"}, _context()
    )
    assert result["ok"] is False and "No usable folder" in result["error"]


# ------------------------------------------------------------------- long text to the chat


async def test_long_text_goes_to_the_chat_and_the_model_gets_an_excerpt() -> None:
    persistence = RecordingPersistence()
    connection = make_connection(FakeRepoTools(notion_connected=True), persistence)
    long_text = "Task line. " * 400  # well over the threshold
    out = await connection._for_voice("notion_list_tasks", {"ok": True, "result": long_text})
    assert out["ok"] is True
    assert len(out["result"]) == VOICE_EXCERPT_CHARS
    assert out["truncated_for_voice"] is True
    assert "in the chat" in out["message"]
    assert persistence.calls[0]["content"] == long_text  # the full text, once, in the chat


async def test_short_text_stays_with_the_model() -> None:
    persistence = RecordingPersistence()
    connection = make_connection(FakeRepoTools(notion_connected=True), persistence)
    short: dict[str, Any] = {"ok": True, "result": "Two tasks are due today."}
    assert len(short["result"]) <= VOICE_CHAT_MIN_CHARS
    # The result is unchanged, plus an explicit "nothing was posted to the chat" so the model never
    # claims it put the list there (intentional: replaces the old exact-equality assertion).
    assert await connection._for_voice("notion_search", short) == {**short, "posted_to_chat": False}
    assert persistence.calls == []


async def test_without_a_database_the_model_is_told_nothing_was_saved() -> None:
    persistence = RecordingPersistence(mode="no_database")
    connection = make_connection(FakeRepoTools(notion_connected=True), persistence)
    out = await connection._for_voice("notion_list_tasks", {"ok": True, "result": "x" * 900})
    assert "could not be saved to the chat" in out["message"]


async def test_a_failed_chat_save_never_fails_the_voice_turn() -> None:
    persistence = RecordingPersistence(mode="fail")
    connection = make_connection(FakeRepoTools(notion_connected=True), persistence)
    out = await connection._for_voice("notion_list_tasks", {"ok": True, "result": "x" * 900})
    assert out["ok"] is True
    assert "could not be saved to the chat" in out["message"]


async def test_no_conversation_yet_means_no_chat_post() -> None:
    persistence = RecordingPersistence()
    connection = make_connection(FakeRepoTools(notion_connected=True), persistence)
    connection.session_id = None
    out = await connection._for_voice("notion_list_tasks", {"ok": True, "result": "x" * 900})
    assert persistence.calls == [] and "could not be saved" in out["message"]


async def test_tool_errors_pass_through_unchanged() -> None:
    connection = make_connection(FakeRepoTools(notion_connected=True), RecordingPersistence())
    failed = {"ok": False, "error": "Notion is not connected. Connect Notion in Settings."}
    assert await connection._for_voice("notion_search", failed) == failed


async def test_github_read_runs_through_the_voice_registry_and_long_files_go_to_chat() -> None:
    small = b"# Demo\nA tiny demo.\n"
    big = ("def f():\n    return 1\n" * 60).encode()  # longer than the voice threshold
    repo = FakeRepoTools(
        notion_connected=False,
        github_files={f"{CONTENTS}README.md": small, f"{CONTENTS}src/big.py": big},
    )
    persistence = RecordingPersistence()
    connection = make_connection(repo, persistence)
    registry = VoiceToolRegistry()
    connection._enable_repo_tools(registry)

    short = await registry.execute_tool_async(
        "github_read_file", {"repo": "octo/demo", "path": "README.md"}, _context()
    )
    assert short["ok"] is True and "A tiny demo." in short["result"]
    assert persistence.calls == []  # short text is spoken about, not posted

    long = await registry.execute_tool_async(
        "github_read_file", {"repo": "octo/demo", "path": "src/big.py"}, _context()
    )
    assert long["ok"] is True
    assert len(long["result"]) == VOICE_EXCERPT_CHARS and long["truncated_for_voice"] is True
    posted = persistence.calls[0]["content"]
    assert "def f():" in posted and "<<<REPO_DATA>>>" in posted  # the whole file is in the chat


async def test_notion_runs_through_the_voice_registry() -> None:
    repo = FakeRepoTools(notion_connected=True)
    persistence = RecordingPersistence()
    connection = make_connection(repo, persistence)
    registry = VoiceToolRegistry()
    await connection._enable_connector_tools(registry)

    out = await registry.execute_tool_async("notion_list_tasks", {}, _context())

    assert out["ok"] is True and "no pages found" in out["result"]
    assert repo.notion_calls  # the database was asked for: the schema, then the query
    assert persistence.calls == []  # a short answer is spoken, not posted


async def test_redacted_page_text_is_what_reaches_the_voice_and_the_chat() -> None:
    token = "ntn_" + "s" * 40
    page_text = "Plan notes. " * 60 + "\n" + f"API_KEY = {token}" + "\n"
    persistence = RecordingPersistence()
    connection = make_connection(FakeRepoTools(notion_connected=True), persistence)
    outcome = await NotionToolkit(_one_page_provider(page_text)).call(
        "notion_get_page", {"page_id": "fedcba9876543210fedcba9876543210"}
    )
    out = await connection._for_voice("notion_get_page", outcome)
    assert token not in str(out)
    assert token not in persistence.calls[0]["content"]
    assert "[REDACTED]" in persistence.calls[0]["content"]


def _one_page_provider(page_text: str) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/children"):
            block = {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": page_text}]}}
            return httpx.Response(200, json={"results": [block], "has_more": False})
        return httpx.Response(
            200, json={"object": "page", "properties": {"Name": {"type": "title", "title": []}}}
        )

    connector = NotionConnector(
        "ntn_" + "v" * 40, rate_limiter=_limiter(), transport=httpx.MockTransport(handler)
    )

    async def provider() -> tuple[NotionConnector, str | None]:
        return connector, DB

    return provider
