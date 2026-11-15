"""Unit tests for MCP stdio and HTTP transports."""

import sys
from typing import Any

import pytest

from app.mcp.transports.base import MCPProcessCrashedError, MCPTransportError
from app.mcp.transports.http import StreamableHttpTransport
from app.mcp.transports.stdio import StdioTransport


@pytest.mark.asyncio
async def test_stdio_transport_lifecycle_and_echo() -> None:
    # A tiny python inline script acting as an MCP JSON-RPC echo server
    echo_server_script = (
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    if not line.strip(): continue\n"
        "    req = json.loads(line)\n"
        "    res = {'jsonrpc': '2.0', 'id': req.get('id'), 'result': {'echo': req.get('params')}}\n"
        "    sys.stdout.write(json.dumps(res) + '\\n')\n"
        "    sys.stdout.flush()\n"
    )

    transport = StdioTransport(
        command=sys.executable,
        args=["-u", "-c", echo_server_script],
    )

    await transport.start()
    assert transport.is_alive() is True

    try:
        response = await transport.send_request("ping", {"test": "data"}, timeout=5.0)
        assert response == {"echo": {"test": "data"}}
    finally:
        await transport.stop()

    assert transport.is_alive() is False


@pytest.mark.asyncio
async def test_stdio_transport_crashes_fail_pending_requests() -> None:
    # Server that crashes immediately upon receiving any message
    crash_server_script = "import sys\nsys.stdin.readline()\nsys.exit(42)\n"

    transport = StdioTransport(
        command=sys.executable,
        args=["-u", "-c", crash_server_script],
    )

    await transport.start()
    assert transport.is_alive() is True

    with pytest.raises((MCPProcessCrashedError, MCPTransportError)):
        await transport.send_request("test_call", {}, timeout=5.0)

    await transport.stop()


@pytest.mark.asyncio
async def test_streamable_http_transport_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    async def mock_post(client: Any, url: str, **kwargs: Any) -> httpx.Response:
        req_json = kwargs.get("json", {})
        return httpx.Response(
            status_code=200,
            json={"jsonrpc": "2.0", "id": req_json.get("id"), "result": {"status": "ok"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    transport = StreamableHttpTransport(url="http://localhost:8080/mcp")
    await transport.start()
    assert transport.is_alive() is True

    response = await transport.send_request("initialize", {"capabilities": {}})
    assert response == {"status": "ok"}

    await transport.stop()
    assert transport.is_alive() is False
