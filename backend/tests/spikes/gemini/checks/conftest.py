"""Tripwires: checks must not construct SDK clients or access network credentials."""

from collections.abc import Iterator
from unittest.mock import patch

import pytest


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError(
        "Real SDK clients, network requests and credentials are forbidden in checks."
    )


@pytest.fixture(autouse=True)
def forbid_real_clients() -> Iterator[None]:
    with (
        patch("google.genai.Client", side_effect=forbidden),
        patch("google.genai.client.AsyncClient", side_effect=forbidden),
        patch("google.genai._api_client.BaseApiClient.__init__", side_effect=forbidden),
        patch("google.auth.default", side_effect=forbidden),
        patch("getpass.getpass", side_effect=forbidden),
        patch("google.genai.live.ws_connect", side_effect=forbidden),
        patch("httpx.AsyncClient.send", side_effect=forbidden),
        patch("httpx.Client.send", side_effect=forbidden),
    ):
        yield
