from dataclasses import dataclass
from collections.abc import Callable

import httpx

from app.config import Settings


@dataclass(slots=True)
class RulePreviewMatch:
    message_id: str
    planned_action: str
    thread_id: str | None = None
    subject: str = ""


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
            params={
                "format": "metadata",
                "metadataHeaders": ["From", "Subject", "List-Unsubscribe"],
            },
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

    async def apply_action(self, message_id: str, action: str) -> None:
        if action == "archive":
            await self.archive_message(message_id)
            return
        if action == "trash":
            await self.trash_message(message_id)
            return
        msg = f"Unsupported Gmail cleanup action: {action}"
        raise ValueError(msg)

    async def preview_matches(self, query: str, *, action: str) -> list[RulePreviewMatch]:
        matches: list[RulePreviewMatch] = []
        for message_id in await self.list_message_ids(query):
            metadata = await self.get_message_metadata(message_id)
            headers = _extract_headers(metadata)
            matches.append(
                RulePreviewMatch(
                    message_id=message_id,
                    thread_id=metadata.get("threadId"),
                    subject=headers.get("Subject", ""),
                    planned_action=action,
                )
            )
        return matches

    async def aclose(self) -> None:
        await self._client.aclose()


def _extract_headers(metadata: dict) -> dict[str, str]:
    payload = metadata.get("payload", {})
    return {
        header["name"]: header["value"]
        for header in payload.get("headers", [])
        if "name" in header and "value" in header
    }
