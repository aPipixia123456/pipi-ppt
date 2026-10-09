import hashlib
import hmac
import time

import httpx
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from sqlalchemy import select

from .config import settings
from .db import Job, WebSession, session


def cipher():
    return Fernet(settings().credential_key.encode())


def digest(value: str) -> str:
    return hmac.new(settings().credential_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def encrypt(value: str) -> str:
    return cipher().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return cipher().decrypt(value.encode()).decode()


class Gateway:
    def __init__(self, credential: str = ""):
        self.credential = credential

    def request(self, method: str, path: str, **kwargs):
        headers = kwargs.pop("headers", {})
        if self.credential:
            headers["Authorization"] = "Bearer " + self.credential
        with httpx.Client(
            timeout=settings().request_timeout, follow_redirects=False, trust_env=False
        ) as client:
            with client.stream(
                method, settings().gateway_url + path, headers=headers, **kwargs
            ) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 24 * 1024 * 1024:
                        raise httpx.ProtocolError("gateway_response_too_large")
                    chunks.append(chunk)
                return httpx.Response(
                    response.status_code,
                    # iter_bytes already decodes gzip/br. Do not decode again.
                    headers={
                        k: v
                        for k, v in response.headers.items()
                        if k.lower() not in {"content-encoding", "content-length"}
                    },
                    content=b"".join(chunks),
                    request=response.request,
                )

    def data(self, path: str):
        response = self.request("GET", path)
        if response.status_code in (401, 403):
            raise HTTPException(401, "authorization_expired")
        if response.status_code != 200:
            raise HTTPException(503, "gateway_unavailable")
        try:
            body = response.json()
        except (TypeError, ValueError):
            raise HTTPException(503, "gateway_unavailable") from None
        if not isinstance(body, dict):
            raise HTTPException(503, "gateway_unavailable")
        if body.get("success") is False:
            raise HTTPException(503, "gateway_unavailable")
        return body.get("data", body)


def current_session(request: Request) -> WebSession:
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and request.headers.get("origin") != settings().public_url
    ):
        raise HTTPException(403, "invalid_origin")
    raw = request.cookies.get("pipi_session", "")
    if not raw:
        raise HTTPException(401, "login_required")
    with session() as db:
        auth = db.get(WebSession, digest(raw))
        if not auth or auth.expires <= time.time():
            raise HTTPException(401, "authorization_expired")
    profile = gateway_for(auth).data("/api/app-auth/me")
    if profile.get("id") != auth.owner:
        raise HTTPException(401, "identity_mismatch")
    request.state.profile = profile
    return auth


def gateway_for(auth: WebSession) -> Gateway:
    return Gateway(decrypt(auth.credential))


def job_gateway(job_id: str) -> Gateway:
    with session() as db:
        job = db.get(Job, job_id)
        if not job or job.cancel_requested:
            raise HTTPException(409, "job_cancelled")
        auth = db.scalar(
            select(WebSession).where(WebSession.id == job.session_id, WebSession.owner == job.owner)
        )
        if not auth or auth.expires <= time.time():
            raise HTTPException(401, "authorization_expired")
        gateway = gateway_for(auth)
    me = gateway.data("/api/app-auth/me")
    if me["id"] != job.owner:
        raise HTTPException(401, "identity_mismatch")
    return gateway
