import asyncio
import gzip
import io
import zipfile

import httpx
import pytest


def test_gateway_accepts_gzip_without_double_decoding(monkeypatch):
    from cryptography.fernet import Fernet
    from pipi.backend.config import settings
    from pipi.backend.security import Gateway

    monkeypatch.setenv("PIPI_CREDENTIAL_KEY", Fernet.generate_key().decode())
    settings.cache_clear()
    original = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=gzip.compress(b'{"data":{"id":12}}'),
            headers={"Content-Encoding": "gzip"},
            request=request,
        )
    )
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    try:
        assert Gateway().data("/api/app-auth/me") == {"id": 12}
    finally:
        settings.cache_clear()


def test_gateway_rejects_non_object_json_response(monkeypatch):
    from fastapi import HTTPException
    from pipi.backend.security import Gateway

    monkeypatch.setattr(
        Gateway, "request", lambda *args, **kwargs: httpx.Response(200, json=["unexpected"])
    )
    with pytest.raises(HTTPException) as error:
        Gateway().data("/v1/models")
    assert error.value.status_code == 503


def test_chunked_upload_is_rejected_before_reading_entire_body():
    from pipi.backend.body_limit import BodyLimit

    received, sent = [], []

    async def app(scope, receive, send):
        while True:
            message = await receive()
            received.append(message)
            if not message.get("more_body"):
                break

    async def receive():
        return {"type": "http.request", "body": b"x" * 8, "more_body": True}

    async def send(message):
        sent.append(message)

    asyncio.run(BodyLimit(app, max_bytes=12)({"type": "http", "method": "POST"}, receive, send))
    assert len(received) == 1
    assert sent[0]["status"] == 413


def test_multipart_parser_preserves_upload_limit_status(site):
    client, _, _ = site

    def chunks():
        yield b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="large.txt"\r\nContent-Type: text/plain\r\n\r\n'
        for _ in range(23):
            yield b"x" * 1024 * 1024
        yield b"\r\n--boundary--\r\n"

    response = client.post(
        "/api/assets?purpose=document",
        content=chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=boundary"},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "upload_too_large"


def test_encoded_external_relationships_are_rejected():
    from pipi.backend.storage import inspect_archive

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            (
                '<?xml version="1.0" encoding="UTF-16"?>'
                '<Relationships><Relationship TargetMode="External" Target="https://invalid.example/image"/></Relationships>'
            ).encode("utf-16"),
        )
    with pytest.raises(ValueError, match="external_relationship_unsupported"):
        inspect_archive(output.getvalue())


def test_admin_template_configuration_and_access(site, monkeypatch):
    from pipi.backend.security import Gateway

    client, _, _ = site
    assert client.get("/api/admin").status_code == 403
    original = Gateway.request

    def administrator(self, method, path, **kwargs):
        response = original(self, method, path, **kwargs)
        if path == "/api/app-auth/me":
            body = response.json()
            body["data"]["role"] = 10
            return httpx.Response(200, json=body)
        return response

    monkeypatch.setattr(Gateway, "request", administrator)
    from pipi.backend.templates import BUILTINS

    config = client.get("/api/admin").json()
    assert len(config["templates"]) == len(BUILTINS)
    assert client.put("/api/admin/templates/executive?enabled=false").status_code == 200
    assert all(t["id"] != "executive" for t in client.get("/api/templates").json())
    assert client.get("/api/templates/executive").status_code == 404
    assert client.put("/api/admin/templates/executive?enabled=true").status_code == 200


def test_deleting_work_removes_cached_content_but_retains_usage_metadata(site):
    from pipi.backend.db import Job, Step, session
    from pipi.backend.worker import run_job
    from pipi.tests.test_workflow import create
    from sqlalchemy import select

    client, _, _ = site
    job = create(client).json()
    run_job(job["id"])
    assert client.delete("/api/decks/" + job["deck_id"]).status_code == 200
    with session() as db:
        saved = db.get(Job, job["id"])
        step = db.scalar(select(Step).where(Step.job_id == job["id"]))
        assert saved.deck_id is None and saved.args == {}
        assert step.response is None
        assert step.request_id


def test_repeated_generate_after_first_page_reuses_existing_job(site):
    from pipi.backend.worker import run_job
    from pipi.tests.test_workflow import create

    client, calls, _ = site
    outline = create(client).json()
    run_job(outline["id"])
    path = "/api/decks/" + outline["deck_id"] + "/generate"
    options = {
        "headers": {"Idempotency-Key": "generate-retry"},
        "json": {"text_model": "tested-text", "images": False},
    }
    first = client.post(path, **options)
    assert first.status_code == 200
    run_job(first.json()["id"])
    repeated = client.post(path, **options)
    assert repeated.status_code == 200
    assert repeated.json()["id"] == first.json()["id"]
    assert len(calls) == 2


def test_internal_metrics_exposes_counts_without_user_content(site):
    from pipi.tests.test_workflow import create

    client, _, _ = site
    job = create(client).json()
    response = client.get("/internal/metrics")
    assert response.status_code == 200
    assert 'pipi_jobs{kind="outline",status="queued"} 1' in response.text
    assert "pipi_oldest_queued_seconds" in response.text
    assert job["id"] not in response.text
    assert "test-user-one" not in response.text


def test_resuming_old_job_never_overwrites_manual_edits(site):
    from pipi.backend.worker import run_job
    from pipi.tests.test_workflow import create

    client, calls, _ = site
    outline = create(client).json()
    run_job(outline["id"])
    path = "/api/decks/" + outline["deck_id"]
    job = client.post(
        path + "/generate",
        headers={"Idempotency-Key": "resume-edits"},
        json={"text_model": "tested-text", "images": False},
    ).json()
    run_job(job["id"])
    assert client.post("/api/jobs/" + job["id"] + "/cancel").status_code == 200
    deck = client.get(path).json()
    edited = {key: deck[key] for key in ["version", "title", "outline", "slides"]}
    edited["title"] = "暂停后手动修订"
    assert client.put(path, json=edited).status_code == 200
    response = client.post("/api/jobs/" + job["id"] + "/resume")
    assert response.status_code == 409
    assert response.json()["detail"] == "presentation_changed_after_pause"
    assert client.get(path).json()["title"] == edited["title"]
    assert len(calls) == 2
