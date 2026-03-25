from collections.abc import Callable

import httpx

from app.config import Settings


class GmailClient:
    def __init__(
        self,
        settings: Settings,
        token_getter: Callable[[], str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token_getter = token_getter
        self._client = httpx.AsyncClient(base_url=settings.gmail_base_url, transport=transport)

    async def list_message_ids(self, query: str) -> list[str]:
        response = await self._client.get(
            "/gmail/v1/users/me/messages",
            params={"q": query, "maxResults": 100},
            headers={"Authorization": f"Bearer {self._token_getter()}"},
        )
        response.raise_for_status()
        return [item["id"] for item in response.json().get("messages", [])]

    async def get_message_metadata(self, message_id: str) -> dict:
        response = await self._client.get(
            f"/gmail/v1/users/me/messages/{message_id}",
            params={"format": "metadata"},
            headers={"Authorization": f"Bearer {self._token_getter()}"},
        )
        response.raise_for_status()
        return response.json()

    async def archive_message(self, message_id: str) -> None:
        response = await self._client.post(
            f"/gmail/v1/users/me/messages/{message_id}/modify",
            json={"removeLabelIds": ["INBOX"]},
            headers={"Authorization": f"Bearer {self._token_getter()}"},
        )
        response.raise_for_status()

    async def trash_message(self, message_id: str) -> None:
        response = await self._client.post(
            f"/gmail/v1/users/me/messages/{message_id}/trash",
            headers={"Authorization": f"Bearer {self._token_getter()}"},
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()
