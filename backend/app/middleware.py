from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """
    Reject request bodies larger than `max_bytes` with 413, before they are
    buffered or spooled to disk.

    A declared Content-Length over the limit is refused without reading the
    body. Chunked bodies are counted as they stream in, and reading stops at
    the limit. FastAPI converts errors raised while parsing a form into a 400,
    so rather than relying on the exception propagating, the app's response
    is replaced with the 413 once the limit has been hit.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _send_413(self, send: Send) -> None:
        body = json.dumps({
            "detail": f"Request body exceeds the {self.max_bytes // (1024 * 1024)} MB limit"
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > self.max_bytes:
                    await self._send_413(send)
                    return

        received = 0
        too_large = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if too_large:
                # swap whatever the app answered (usually FastAPI's 400) for 413
                if message["type"] == "http.response.start" and not response_started:
                    response_started = True
                    await self._send_413(send)
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            if not response_started:
                await self._send_413(send)
