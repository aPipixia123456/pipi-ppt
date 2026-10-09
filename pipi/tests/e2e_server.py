"""Local E2E fixture only. Not copied to production images; no paid calls."""

import base64
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
import uvicorn
from cryptography.fernet import Fernet
from fastapi.responses import HTMLResponse, RedirectResponse
from PIL import Image
from sqlalchemy import select

temporary = tempfile.TemporaryDirectory(prefix="pipi-ppt-e2e-")
os.environ["PIPI_DATABASE_URL"] = "sqlite:///" + str(Path(temporary.name) / "test.db")
os.environ["PIPI_STORAGE_DIR"] = str(Path(temporary.name) / "assets")
os.environ["PIPI_CREDENTIAL_KEY"] = Fernet.generate_key().decode()
os.environ["PIPI_PUBLIC_URL"] = "http://localhost:13100"
os.environ["PIPI_GATEWAY_URL"] = "http://localhost:18180"

from pipi.backend.api import app  # noqa: E402
from pipi.backend.db import Job, Policy, initialize, session  # noqa: E402
from pipi.backend.security import Gateway  # noqa: E402
from pipi.backend.worker import run_job  # noqa: E402

authorizations = {}
calls = []
buffer = io.BytesIO()
Image.new("RGB", (400, 300), "#C6B9EC").save(buffer, "PNG")
image_data = base64.b64encode(buffer.getvalue()).decode()


def gateway_request(self, method, path, **kwargs):
    if path == "/api/app-auth/requests":
        data = kwargs["json"]
        authorizations[data["state"]] = data
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"authorization_path": "/app-authorize?request=" + data["state"]},
            },
        )
    if path == "/api/app-auth/token":
        data = kwargs["json"]
        match = authorizations.pop(data["code"], None)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if not match or challenge != match["code_challenge"]:
            return httpx.Response(400)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "access_token": "local-fixture-credential",
                    "expires_at": int(time.time()) + 3600,
                },
            },
        )
    if path == "/api/app-auth/me":
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": 1,
                    "username": "演示账户",
                    "display_name": "Pipi 创作者",
                    "role": 10,
                    "quota": 100000,
                    "quota_per_unit": 500000,
                },
            },
        )
    if path == "/v1/models":
        return httpx.Response(
            200, json={"data": [{"id": "fixture-text-vision"}, {"id": "fixture-image"}]}
        )
    if "/usage" in path:
        job_id = path.split("/")[-2]
        quota = sum(100 for call in calls if call["job"] == job_id)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"quota": quota, "quota_per_unit": 500000, "settling": False, "steps": []},
            },
        )
    if method == "POST" and path in {"/v1/chat/completions", "/v1/images/generations"}:
        calls.append(
            {"job": kwargs["headers"]["X-Pipi-Job-ID"], "step": kwargs["headers"]["X-Pipi-Step-ID"]}
        )
        if path.endswith("generations"):
            return httpx.Response(
                200,
                headers={"X-Oneapi-Request-Id": f"fixture-{len(calls)}"},
                json={"data": [{"b64_json": image_data}]},
            )
        prompt = kwargs["json"]["messages"][-1]["content"][0]["text"]
        if "Create exactly" in prompt:
            count = int(re.search(r"Create exactly (\d+)", prompt)[1])
            headings = [
                "看见新的增长机会",
                "从用户需求出发",
                "明确产品核心价值",
                "让团队形成合力",
                "把规划变成行动",
            ]
            result = {
                "outline": [headings[i % 5] + " — 目标、策略与行动建议" for i in range(count)]
            }
        elif "Template text slots: " in prompt:
            slots = json.loads(
                prompt.split("Template text slots: ", 1)[1].split(". Return JSON", 1)[0]
            )
            result = {
                "fields": {
                    slot["id"]: (
                        "让好想法成为现实"
                        if slot["max_chars"] < 80
                        else "从真实需求出发，把复杂问题拆解成清晰的目标与可执行的步骤。共同创造持续的价值。"
                    )[: slot["max_chars"]]
                    for slot in slots
                },
                "notes": "本页用于展示产品思路与行动计划。",
                "image_prompt": "Soft abstract forms in a lavender and cream palette",
            }
        else:
            result = {
                "description": "柔和配色与清晰的内容分区",
                "layout_advice": "保留 Logo 和主要布局",
            }
        return httpx.Response(
            200,
            headers={"X-Oneapi-Request-Id": f"fixture-{len(calls)}"},
            json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}]},
        )
    raise ValueError("unexpected_fixture_endpoint")


Gateway.request = gateway_request


@app.get("/app-authorize")
def authorize(request: str):
    if request not in authorizations:
        return HTMLResponse("Expired", status_code=400)
    return HTMLResponse(
        '<!doctype html><html lang="zh-CN"><title>测试授权</title><body style="font-family:sans-serif;padding:50px"><h1>pipiapi 测试授权</h1><p>仅用于本地端到端验证，不产生实际费用。</p><a href="/fixture-consent?'
        + urlencode({"request": request})
        + '">同意授权</a></body></html>'
    )


@app.get("/fixture-consent")
def consent(request: str):
    return RedirectResponse(
        os.environ["PIPI_PUBLIC_URL"]
        + "/api/auth/callback?"
        + urlencode({"state": request, "code": request})
    )


def run_queue():
    while True:
        with session() as db:
            pending = list(db.scalars(select(Job.id).where(Job.status == "queued")))
        for job_id in pending:
            run_job(job_id)
        time.sleep(0.5)


if __name__ == "__main__":
    initialize()
    Path(os.environ["PIPI_STORAGE_DIR"]).mkdir()
    with session() as db:
        policy = db.get(Policy, 1)
        policy.data = {
            **policy.data,
            "enabled": True,
            "text_models": ["fixture-text-vision"],
            "image_models": ["fixture-image"],
        }
        db.commit()
    threading.Thread(target=run_queue, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=18180, access_log=False)
