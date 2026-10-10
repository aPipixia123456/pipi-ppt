import json
import os
import re
import time
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


@pytest.fixture
def site(tmp_path, monkeypatch, request):
    monkeypatch.setenv("PIPI_CREDENTIAL_KEY", Fernet.generate_key().decode())
    database_url = os.environ.get("PIPI_TEST_DATABASE_URL")
    control, namespace = None, None
    if database_url:
        # Integration tests own only this randomly named schema, never public.
        assert make_url(database_url).get_backend_name() == "postgresql"
        namespace = "pipi_test_" + uuid4().hex
        control = create_engine(database_url)
        with control.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{namespace}"')

        def cleanup_schema():
            with control.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{namespace}" CASCADE')
            control.dispose()

        request.addfinalizer(cleanup_schema)
        database_url = (
            make_url(database_url)
            .update_query_dict({"options": f"-csearch_path={namespace}"})
            .render_as_string(hide_password=False)
        )
    else:
        database_url = "sqlite:///" + str(tmp_path / "test.db")
    monkeypatch.setenv("PIPI_DATABASE_URL", database_url)
    monkeypatch.setenv("PIPI_STORAGE_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("PIPI_PUBLIC_URL", "http://localhost:3000")
    from pipi.backend.config import settings

    settings.cache_clear()
    from pipi.backend.db import Account, Policy, WebSession, engine, session

    engine.cache_clear()
    from pipi.backend.api import app
    from pipi.backend.security import Gateway, digest, encrypt

    calls = []
    disabled = set()

    def fake_request(self, method, path, **kwargs):
        uid = 1 if self.credential == "test-user-one" else 2
        if uid in disabled:
            return httpx.Response(401)
        if path == "/api/app-auth/me":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {"id": uid, "username": f"user-{uid}", "role": 1, "quota": 10000},
                },
            )
        if path == "/v1/models":
            return httpx.Response(
                200, json={"data": [{"id": "tested-text"}, {"id": "tested-image"}]}
            )
        if method == "POST" and path == "/v1/chat/completions":
            headers = kwargs["headers"]
            calls.append((uid, headers["X-Pipi-Job-ID"], headers["X-Pipi-Step-ID"]))
            prompt = kwargs["json"]["messages"][-1]["content"][0]["text"]
            if "Create exactly" in prompt:
                count = int(re.search(r"Create exactly (\d+)", prompt)[1])
                result = {"outline": [f"第{i + 1}页 — 核心内容" for i in range(count)]}
            else:
                slots = json.loads(
                    prompt.split("Template text slots: ", 1)[1].split(". Return JSON", 1)[0]
                )
                result = {"fields": {slot["id"]: "中文内容" for slot in slots}, "notes": "演讲备注"}
            return httpx.Response(
                200,
                headers={"X-Oneapi-Request-Id": f"request-{len(calls)}"},
                json={"choices": [{"message": {"content": json.dumps(result)}}]},
            )
        if method == "POST" and path == "/v1/alpha/search":
            return httpx.Response(
                200,
                headers={"X-Oneapi-Request-Id": "search-request"},
                json={
                    "results": [
                        {
                            "title": "测试资料来源",
                            "url": "https://example.com/source",
                            "snippet": "这是用于自动研究回归测试的可核对资料摘要。",
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected gateway request: {method} {path}")

    monkeypatch.setattr(Gateway, "request", fake_request)
    with TestClient(app) as client:
        with session() as db:
            db.add_all([Account(id=1, name="One"), Account(id=2, name="Two")])
            db.flush()
            db.add_all(
                [
                    WebSession(
                        id=digest("browser-one"),
                        owner=1,
                        credential=encrypt("test-user-one"),
                        expires=int(time.time()) + 3600,
                    ),
                    WebSession(
                        id=digest("browser-two"),
                        owner=2,
                        credential=encrypt("test-user-two"),
                        expires=int(time.time()) + 3600,
                    ),
                ]
            )
            policy = db.get(Policy, 1)
            policy.data = {
                **policy.data,
                "enabled": True,
                "text_models": ["tested-text"],
                "image_models": ["tested-image"],
            }
            db.commit()
        client.cookies.set("pipi_session", "browser-one")
        client.headers["Origin"] = "http://localhost:3000"
        yield client, calls, disabled
    engine().dispose()
    engine.cache_clear()
    settings.cache_clear()
