from starlette.requests import Request

from app.auth.dependencies import client_address


def test_client_address_ignores_client_supplied_forwarded_for() -> None:
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"x-forwarded-for", b"203.0.113.42")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )

    assert client_address(request) == "127.0.0.1"
