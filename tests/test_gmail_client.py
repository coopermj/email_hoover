from pathlib import Path

import httpx
import pytest

from app.config import DEFAULT_GMAIL_TOKEN_PATH, Settings
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
    settings.gmail_token_path.write_text(
        '{"token":"access-token","refresh_token":"refresh-token","token_uri":"https://oauth2.googleapis.com/token","client_id":"client-id","client_secret":"client-secret","scopes":["https://www.googleapis.com/auth/gmail.modify"]}',
        encoding="utf-8",
    )

    state = AuthState.from_disk(settings)

    assert state.connected is True
    assert state.reason is None


def test_settings_default_gmail_token_path_lives_outside_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GMAIL_TOKEN_PATH", raising=False)
    default_path = Settings.from_env().gmail_token_path
    repo_root = Path(__file__).resolve().parents[1]

    assert default_path.is_absolute() is True
    assert default_path == DEFAULT_GMAIL_TOKEN_PATH
    assert repo_root not in default_path.parents


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
    assert request.headers["Authorization"] == "Bearer token"
    assert request.url.params["format"] == "metadata"
    assert request.url.params.get_list("metadataHeaders") == [
        "From",
        "Subject",
        "List-Unsubscribe",
    ]


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
    request = requests[0]
    assert request.headers["Authorization"] == "Bearer token"
    assert request.content == b'{"removeLabelIds":["INBOX"]}'


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
    request = requests[0]
    assert request.headers["Authorization"] == "Bearer token"
    assert request.url.path == "/gmail/v1/users/me/messages/m1/trash"
