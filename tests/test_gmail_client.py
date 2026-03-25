from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.gmail.auth import AuthState
from app.gmail.client import GmailClient


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        gmail_token_path=tmp_path / "token.json",
        gmail_base_url="https://gmail.googleapis.com",
    )


def test_auth_state_reports_reconnect_when_token_missing(settings: Settings) -> None:
    state = AuthState.from_disk(settings)

    assert state.connected is False
    assert state.reason == "missing_token"


def test_auth_state_reports_connected_when_token_exists(settings: Settings) -> None:
    settings.gmail_token_path.write_text("{}", encoding="utf-8")

    state = AuthState.from_disk(settings)

    assert state.connected is True
    assert state.reason is None


@pytest.mark.asyncio
async def test_list_candidate_messages_uses_expected_query(settings: Settings) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})

    client = GmailClient(
        settings,
        token_getter=lambda: "token",
        transport=httpx.MockTransport(handler),
    )

    try:
        ids = await client.list_message_ids("category:promotions older_than:2d")
    finally:
        await client.aclose()

    assert ids == ["m1"]
    assert len(requests) == 1
    request = requests[0]
    assert request.headers["Authorization"] == "Bearer token"
    assert str(request.url) == "https://gmail.googleapis.com/gmail/v1/users/me/messages?q=category%3Apromotions+older_than%3A2d&maxResults=100"
    assert request.url.params["q"] == "category:promotions older_than:2d"
    assert request.url.params["maxResults"] == "100"


@pytest.mark.asyncio
async def test_get_message_metadata_requests_metadata_format(settings: Settings) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "m1", "labelIds": ["INBOX"]})

    client = GmailClient(
        settings,
        token_getter=lambda: "token",
        transport=httpx.MockTransport(handler),
    )

    try:
        payload = await client.get_message_metadata("m1")
    finally:
        await client.aclose()

    assert payload == {"id": "m1", "labelIds": ["INBOX"]}
    assert len(requests) == 1
    request = requests[0]
    assert request.url.params["format"] == "metadata"


@pytest.mark.asyncio
async def test_archive_message_removes_inbox_label(settings: Settings) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "m1"})

    client = GmailClient(
        settings,
        token_getter=lambda: "token",
        transport=httpx.MockTransport(handler),
    )

    try:
        await client.archive_message("m1")
    finally:
        await client.aclose()

    assert len(requests) == 1
    assert requests[0].content == b'{"removeLabelIds":["INBOX"]}'


@pytest.mark.asyncio
async def test_trash_message_posts_to_trash_endpoint(settings: Settings) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "m1"})

    client = GmailClient(
        settings,
        token_getter=lambda: "token",
        transport=httpx.MockTransport(handler),
    )

    try:
        await client.trash_message("m1")
    finally:
        await client.aclose()

    assert len(requests) == 1
    assert requests[0].url.path == "/gmail/v1/users/me/messages/m1/trash"
