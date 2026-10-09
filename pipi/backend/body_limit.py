from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class UploadLimitExceeded(HTTPException):
    def __init__(self):
        super().__init__(status_code=413, detail="upload_too_large")


class BodyLimit:
    """Bound the actual incoming stream, including requests without Content-Length."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    raise UploadLimitExceeded
            return message

        try:
            await self.app(scope, limited_receive, send)
        except UploadLimitExceeded:
            await JSONResponse({"detail": "upload_too_large"}, status_code=413)(
                scope, receive, send
            )
