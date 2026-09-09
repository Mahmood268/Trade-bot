"""A fake ``requests.Session`` that replays scripted Telegram responses."""

from __future__ import annotations

import json
from typing import Any


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, text: str | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True, "result": {}}
        self.text = text if text is not None else json.dumps(self._payload)

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Returns queued responses in order; the last one repeats forever."""

    def __init__(self, responses: list[Any] | None = None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None):  # noqa: A002
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, json or {}))
        if not self.responses:
            return FakeResponse(200, {"ok": True, "result": {"message_id": len(self.calls)}})
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]

    def payloads(self, method: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.calls if name == method]


def ok(result: Any) -> FakeResponse:
    return FakeResponse(200, {"ok": True, "result": result})


def message_update(update_id: int, text: str, *, chat_id: int = 42, user_id: int = 7, username: str = "trader"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "text": text,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "username": username},
        },
    }
