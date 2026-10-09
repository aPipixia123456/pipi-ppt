import base64
import hashlib
import secrets
import time
from contextlib import asynccontextmanager
from datetime import timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from .body_limit import BodyLimit
from .config import settings
from .db import (
    Account,
    Asset,
    Deck,
    Job,
    LoginState,
    Policy,
    Step,
    Template,
    WebSession,
    initialize,
    now,
    session,
)
from .generation import available_models
from .schema import AdminPolicy, CreateDeck, Generate, Rewrite, UpdateDeck
from .security import Gateway, current_session, decrypt, digest, encrypt, gateway_for
from .storage import asset_path, owned_asset, remove_asset, store_asset, validate_upload
from .templates import BUILTINS, builtin_asset_path, builtin_template
from .worker import submit


@asynccontextmanager
async def lifespan(app):
    initialize()
    settings().storage_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Pipi PPT", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(BodyLimit, max_bytes=22 * 1024 * 1024)


@app.middleware("http")
async def response_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(httpx.HTTPError)
async def gateway_error(request, error):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": "gateway_unavailable"}, status_code=503)


@app.get("/api/health")
def health():
    with session() as db:
        db.execute(select(Policy.id).limit(1))
    return {"status": "ok"}


@app.get("/internal/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics():
    # Internal Compose network only: Next proxies /api/*, never /internal/*.
    lines = ["# TYPE pipi_jobs gauge", "# TYPE pipi_model_steps gauge"]
    with session() as db:
        for kind, status, count in db.execute(
            select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status)
        ):
            lines.append(f'pipi_jobs{{kind="{kind}",status="{status}"}} {count}')
        for status, count in db.execute(select(Step.status, func.count()).group_by(Step.status)):
            lines.append(f'pipi_model_steps{{status="{status}"}} {count}')
        oldest = db.scalar(select(func.min(Job.created)).where(Job.status == "queued"))
        age = max(0, (now() - oldest.replace(tzinfo=timezone.utc)).total_seconds()) if oldest else 0
        lines += [
            "# TYPE pipi_oldest_queued_seconds gauge",
            f"pipi_oldest_queued_seconds {age:.3f}",
        ]
        durations = {}
        for kind, created, updated in db.execute(
            select(Job.kind, Job.created, Job.updated)
            .where(Job.status == "complete")
            .order_by(Job.updated.desc())
            .limit(500)
        ):
            durations.setdefault(kind, []).append(max(0, (updated - created).total_seconds()))
        lines.append("# TYPE pipi_recent_job_duration_seconds gauge")
        for kind, values in durations.items():
            lines.append(
                f'pipi_recent_job_duration_seconds{{kind="{kind}"}} {sum(values) / len(values):.3f}'
            )
    return "\n".join(lines) + "\n"


@app.get("/api/auth/start")
def auth_start():
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    response = Gateway().request(
        "POST",
        "/api/app-auth/requests",
        json={
            "client_id": "pipi-ppt",
            "redirect_uri": settings().public_url + "/api/auth/callback",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    if response.status_code != 200 or not response.json().get("success"):
        raise HTTPException(503, "authorization_unavailable")
    path = response.json()["data"]["authorization_path"]
    if not path.startswith("/app-authorize?request="):
        raise HTTPException(503, "invalid_authorization_url")
    with session() as db:
        db.add(
            LoginState(
                id=digest(state),
                verifier=encrypt(verifier),
                expires=int(time.time()) + 600,
            )
        )
        db.commit()
    redirect = RedirectResponse(settings().gateway_url + path, status_code=303)
    redirect.set_cookie(
        "pipi_login",
        state,
        httponly=True,
        secure=settings().public_url.startswith("https"),
        samesite="lax",
        max_age=600,
        path="/api/auth",
    )
    return redirect


@app.get("/api/auth/callback")
def auth_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    cookie = request.cookies.get("pipi_login", "")
    if not state or not cookie or not secrets.compare_digest(cookie, state):
        raise HTTPException(400, "invalid_login_state")
    with session() as db:
        login = db.scalar(
            select(LoginState).where(LoginState.id == digest(state)).with_for_update()
        )
        if not login or login.expires <= time.time():
            raise HTTPException(400, "login_expired")
        verifier = decrypt(login.verifier)
        db.delete(login)
        db.commit()
    if error:
        response = RedirectResponse(settings().public_url + "/?auth=declined", status_code=303)
        response.delete_cookie("pipi_login", path="/api/auth")
        return response
    response = Gateway().request(
        "POST",
        "/api/app-auth/token",
        json={
            "client_id": "pipi-ppt",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": settings().public_url + "/api/auth/callback",
        },
    )
    if response.status_code != 200 or not response.json().get("success"):
        raise HTTPException(400, "authorization_exchange_failed")
    result = response.json()["data"]
    credential = result["access_token"]
    me = Gateway(credential).data("/api/app-auth/me")
    raw = secrets.token_urlsafe(32)
    with session() as db:
        account = db.get(Account, me["id"])
        if not account:
            db.add(Account(id=me["id"], name=me.get("display_name") or me["username"]))
            db.flush()
        db.add(
            WebSession(
                id=digest(raw),
                owner=me["id"],
                credential=encrypt(credential),
                expires=result["expires_at"],
            )
        )
        db.commit()
    redirect = RedirectResponse(settings().public_url, status_code=303)
    redirect.set_cookie(
        "pipi_session",
        raw,
        httponly=True,
        secure=settings().public_url.startswith("https"),
        samesite="lax",
        max_age=max(0, result["expires_at"] - int(time.time())),
        path="/",
    )
    redirect.delete_cookie("pipi_login", path="/api/auth")
    return redirect


@app.post("/api/auth/logout")
def logout(auth=Depends(current_session)):
    with session() as db:
        db.execute(delete(WebSession).where(WebSession.id == auth.id))
        db.commit()
    response = JSONResponse({"ok": True})
    response.delete_cookie("pipi_session", path="/")
    return response


@app.get("/api/me")
def me(auth=Depends(current_session)):
    gateway = gateway_for(auth)
    profile = gateway.data("/api/app-auth/me")
    with session() as db:
        profile["stored_bytes"] = db.get(Account, auth.owner).stored_bytes
        policy = db.get(Policy, 1).data
    return {
        "profile": profile,
        "pricing_url": settings().gateway_url + "/pricing",
        "models": available_models(gateway, policy),
        "enabled": policy["enabled"],
        "storage_limit": policy["storage_mb"] * 1024 * 1024,
    }


@app.get("/api/pricing")
def pricing(auth=Depends(current_session)):
    return gateway_for(auth).data("/api/app-auth/pricing")


@app.get("/api/templates")
def templates(auth=Depends(current_session)):
    with session() as db:
        hidden = {
            t.id
            for t in db.scalars(
                select(Template).where(Template.owner.is_(None), Template.enabled.is_(False))
            )
        }
        personal = db.scalars(select(Template).where(Template.owner == auth.owner)).all()
    builtins = [
        {"id": key, "name": name, "confirmed": True, "builtin": True}
        for key, name in BUILTINS.items()
        if key not in hidden
    ]
    return builtins + [
        {"id": t.id, "name": t.name, "confirmed": t.confirmed, "builtin": False} for t in personal
    ]


def template_data(db, owner, template_id, confirmed=True):
    if template_id in BUILTINS:
        setting = db.get(Template, template_id)
        if setting and not setting.enabled:
            raise HTTPException(404, "template_unavailable")
        return builtin_template(template_id)
    template = db.get(Template, template_id)
    if not template or template.owner != owner or not template.enabled:
        raise HTTPException(404, "template_unavailable")
    if confirmed and not template.confirmed:
        raise HTTPException(409, "template_preview_confirmation_required")
    return template.data


@app.get("/api/templates/{template_id}")
def get_template(template_id: str, auth=Depends(current_session)):
    with session() as db:
        return template_data(db, auth.owner, template_id, confirmed=False)


@app.get("/api/template-assets/{reference:path}")
def template_asset(reference: str, auth=Depends(current_session)):
    try:
        path = builtin_asset_path(reference)
    except ValueError:
        raise HTTPException(404, "file_not_found") from None
    return FileResponse(path)


@app.post("/api/templates/{template_id}/confirm")
def confirm_template(template_id: str, auth=Depends(current_session)):
    with session() as db:
        template_data(db, auth.owner, template_id, confirmed=False)
        template = db.get(Template, template_id)
        if not template or template.owner != auth.owner:
            raise HTTPException(404, "template_unavailable")
        template.confirmed = True
        db.commit()
    return {"ok": True}


@app.delete("/api/templates/{template_id}")
def delete_template(template_id: str, auth=Depends(current_session)):
    with session() as db:
        template = db.get(Template, template_id)
        if not template or template.owner != auth.owner:
            raise HTTPException(404, "template_unavailable")
        db.delete(template)
        db.commit()
    return {"ok": True}


@app.post("/api/assets")
def upload(file: UploadFile, purpose: str, auth=Depends(current_session)):
    if purpose not in {"document", "template", "image"}:
        raise HTTPException(400, "invalid_file_purpose")
    with session() as db:
        limit = min(settings().upload_limit, db.get(Policy, 1).data["upload_mb"] * 1024 * 1024)
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "upload_too_large")
    try:
        media_type = validate_upload(file.filename or "", data, purpose)
    except Exception:
        raise HTTPException(400, "invalid_or_unsupported_file") from None
    asset = store_asset(auth.owner, file.filename or "upload", data, media_type, purpose)
    return {"id": asset.id, "name": asset.name, "size": asset.size}


@app.get("/api/assets")
def list_assets(auth=Depends(current_session)):
    with session() as db:
        return [
            {"id": a.id, "name": a.name, "size": a.size, "purpose": a.purpose}
            for a in db.scalars(
                select(Asset).where(Asset.owner == auth.owner).order_by(Asset.created.desc())
            )
        ]


@app.get("/api/assets/{asset_id}")
def download(asset_id: str, auth=Depends(current_session)):
    with session() as db:
        asset = owned_asset(db, auth.owner, asset_id)
    return FileResponse(
        asset_path(asset),
        filename=asset.name,
        media_type=asset.media_type,
        content_disposition_type="inline"
        if asset.media_type.startswith("image/")
        else "attachment",
    )


@app.delete("/api/assets/{asset_id}")
def delete_asset(asset_id: str, auth=Depends(current_session)):
    with session() as db:
        db.scalar(select(Policy).where(Policy.id == 1).with_for_update())
        asset = owned_asset(db, auth.owner, asset_id)
        # JSON remains portable and owner-scoped. Deleting referenced artwork is
        # refused; a deleted template's images can still belong to saved decks.
        import json

        refs = [
            json.dumps(d.slides) + json.dumps(d.template)
            for d in db.scalars(select(Deck).where(Deck.owner == auth.owner))
        ]
        refs += [
            json.dumps(t.data)
            for t in db.scalars(select(Template).where(Template.owner == auth.owner))
        ]
        refs += [
            json.dumps(j.args) + json.dumps(j.result)
            for j in db.scalars(
                select(Job).where(Job.owner == auth.owner, Job.status.in_(["queued", "running"]))
            )
        ]
        if any(asset_id in ref for ref in refs):
            raise HTTPException(409, "file_still_in_use")
        remove_asset(db, asset)
    return {"ok": True}


class ImportTemplate(BaseModel):
    asset_id: str = Field(max_length=36)
    text_model: str = Field(min_length=1, max_length=160)


@app.post("/api/templates/import")
def import_template(
    body: ImportTemplate,
    idempotency_key: str = Header(default=""),
    auth=Depends(current_session),
):
    with session() as db:
        if owned_asset(db, auth.owner, body.asset_id).purpose != "template":
            raise HTTPException(400, "pptx_required")
        job = submit(db, auth, "template", body.model_dump(), idempotency_key)
    return job_view(job)


def deck_owned(db, auth, deck_id):
    deck = db.get(Deck, deck_id)
    if not deck or deck.owner != auth.owner:
        raise HTTPException(404, "deck_not_found")
    return deck


def job_view(job):
    return {
        "id": job.id,
        "deck_id": job.deck_id,
        "kind": job.kind,
        "status": job.status,
        "cursor": job.cursor,
        "error": job.error,
        "result": job.result,
        "created": job.created,
        "updated": job.updated,
    }


def deck_view(deck):
    return {
        "id": deck.id,
        "title": deck.title,
        "outline": deck.outline,
        "slides": deck.slides,
        "version": deck.version,
        "updated": deck.updated,
    }


@app.post("/api/decks")
def create_deck(
    body: CreateDeck,
    idempotency_key: str = Header(default=""),
    auth=Depends(current_session),
):
    with session() as db:
        db.scalar(select(Policy).where(Policy.id == 1).with_for_update())
        existing = db.scalar(
            select(Job).where(Job.owner == auth.owner, Job.idempotency_key == idempotency_key)
        )
        if existing:
            if existing.kind != "outline" or existing.args != body.model_dump():
                raise HTTPException(409, "idempotency_key_reused")
            return job_view(existing)
        template = template_data(db, auth.owner, body.template_id)
        for asset_id in body.document_ids:
            if owned_asset(db, auth.owner, asset_id).purpose != "document":
                raise HTTPException(400, "document_required")
        deck = Deck(
            owner=auth.owner,
            title=body.title,
            template_id=body.template_id,
            template=template,
        )
        db.add(deck)
        db.flush()
        job = submit(db, auth, "outline", body.model_dump(), idempotency_key, deck.id)
    return job_view(job)


@app.get("/api/decks")
def list_decks(auth=Depends(current_session)):
    with session() as db:
        return [
            {"id": d.id, "title": d.title, "updated": d.updated, "pages": len(d.slides)}
            for d in db.scalars(
                select(Deck).where(Deck.owner == auth.owner).order_by(Deck.updated.desc())
            )
        ]


@app.get("/api/decks/{deck_id}")
def get_deck(deck_id: str, auth=Depends(current_session)):
    with session() as db:
        return deck_view(deck_owned(db, auth, deck_id))


@app.put("/api/decks/{deck_id}")
def save_deck(deck_id: str, body: UpdateDeck, auth=Depends(current_session)):
    if any(len(item) > 1000 for item in body.outline):
        raise HTTPException(400, "outline_too_long")
    with session() as db:
        db.scalar(select(Policy).where(Policy.id == 1).with_for_update())
        deck_owned(db, auth, deck_id)
        if db.scalar(
            select(Job.id).where(
                Job.deck_id == deck_id,
                Job.status.in_(["queued", "running"]),
                Job.kind != "export",
            )
        ):
            raise HTTPException(409, "deck_busy")
        for slide in body.slides:
            for element in slide.elements:
                if element.builtin_asset:
                    try:
                        builtin_asset_path(element.builtin_asset)
                    except ValueError:
                        raise HTTPException(400, "invalid_template_asset") from None
                if element.asset_id:
                    asset = owned_asset(db, auth.owner, element.asset_id)
                    if not asset.media_type.startswith("image/"):
                        raise HTTPException(400, "image_required")
        result = db.execute(
            update(Deck)
            .where(
                Deck.id == deck_id,
                Deck.owner == auth.owner,
                Deck.version == body.version,
            )
            .values(
                title=body.title,
                outline=body.outline,
                slides=[s.model_dump() for s in body.slides],
                version=body.version + 1,
                updated=now(),
            )
        )
        if result.rowcount != 1:
            raise HTTPException(409, "version_conflict")
        db.commit()
        return deck_view(db.get(Deck, deck_id))


@app.delete("/api/decks/{deck_id}")
def delete_deck(deck_id: str, auth=Depends(current_session)):
    with session() as db:
        db.scalar(select(Policy).where(Policy.id == 1).with_for_update())
        deck = deck_owned(db, auth, deck_id)
        if db.scalar(
            select(Job.id).where(Job.deck_id == deck_id, Job.status.in_(["queued", "running"]))
        ):
            raise HTTPException(409, "deck_busy")
        # Keep billing metadata, but remove document snapshots and model bodies.
        for job in db.scalars(select(Job).where(Job.deck_id == deck_id, Job.owner == auth.owner)):
            db.execute(update(Step).where(Step.job_id == job.id).values(response=None))
            job.args, job.deck_id = {}, None
            job.result = {"asset_id": job.result["asset_id"]} if "asset_id" in job.result else {}
            if job.status in {"failed", "cancelled"}:
                job.error = "presentation_deleted"
        db.delete(deck)
        db.commit()
    return {"ok": True}


@app.post("/api/decks/{deck_id}/generate")
def generate(
    deck_id: str,
    body: Generate,
    idempotency_key: str = Header(default=""),
    auth=Depends(current_session),
):
    with session() as db:
        db.scalar(select(Policy).where(Policy.id == 1).with_for_update())
        deck = deck_owned(db, auth, deck_id)
        existing = db.scalar(
            select(Job).where(Job.owner == auth.owner, Job.idempotency_key == idempotency_key)
        )
        if existing:
            return job_view(
                submit(db, auth, "generate", body.model_dump(), idempotency_key, deck_id)
            )
        if not 5 <= len(deck.outline) <= 30:
            raise HTTPException(400, "confirm_outline_first")
        if deck.slides:
            raise HTTPException(409, "use_page_rewrite_for_existing_slides")
        return job_view(submit(db, auth, "generate", body.model_dump(), idempotency_key, deck_id))


@app.post("/api/decks/{deck_id}/rewrite")
def rewrite(
    deck_id: str,
    body: Rewrite,
    idempotency_key: str = Header(default=""),
    auth=Depends(current_session),
):
    with session() as db:
        db.scalar(select(Policy).where(Policy.id == 1).with_for_update())
        deck = deck_owned(db, auth, deck_id)
        if body.index >= len(deck.slides) or body.index >= len(deck.outline):
            raise HTTPException(400, "invalid_slide")
        return job_view(submit(db, auth, "rewrite", body.model_dump(), idempotency_key, deck_id))


@app.post("/api/decks/{deck_id}/export")
def export(
    deck_id: str,
    format: str = "pptx",
    idempotency_key: str = Header(default=""),
    auth=Depends(current_session),
):
    if format not in {"pptx", "pdf"}:
        raise HTTPException(400, "invalid_format")
    with session() as db:
        db.scalar(select(Policy).where(Policy.id == 1).with_for_update())
        deck = deck_owned(db, auth, deck_id)
        if not deck.slides:
            raise HTTPException(400, "no_slides")
        return job_view(
            submit(
                db,
                auth,
                "export",
                {
                    "title": deck.title,
                    "slides": deck.slides,
                    "format": format,
                    "version": deck.version,
                },
                idempotency_key,
                deck_id,
            )
        )


@app.get("/api/jobs")
def jobs(auth=Depends(current_session)):
    with session() as db:
        return [
            job_view(j)
            for j in db.scalars(
                select(Job).where(Job.owner == auth.owner).order_by(Job.created.desc()).limit(100)
            )
        ]


def job_owned(db, auth, job_id):
    job = db.get(Job, job_id)
    if not job or job.owner != auth.owner:
        raise HTTPException(404, "job_not_found")
    return job


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, auth=Depends(current_session)):
    with session() as db:
        job = job_owned(db, auth, job_id)
        job.cancel_requested = True
        if job.status != "running" and job.status != "complete":
            job.status = "cancelled"
        db.commit()
        return job_view(job)


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str, auth=Depends(current_session)):
    with session() as db:
        policy = db.scalar(select(Policy).where(Policy.id == 1).with_for_update()).data
        job = job_owned(db, auth, job_id)
        if job.status not in {"failed", "cancelled"}:
            raise HTTPException(409, "job_not_resumable")
        if job.kind != "template" and (not job.deck_id or not db.get(Deck, job.deck_id)):
            raise HTTPException(409, "presentation_deleted")
        if not policy["enabled"] and job.kind != "export":
            raise HTTPException(503, "site_paused")
        if db.scalar(select(Step.id).where(Step.job_id == job.id, Step.status != "complete")):
            raise HTTPException(409, "model_result_requires_manual_review")
        if (
            job.deck_id
            and job.result.get("deck_version")
            and db.get(Deck, job.deck_id).version != job.result["deck_version"]
        ):
            raise HTTPException(409, "presentation_changed_after_pause")
        if job.kind == "generate" and job.cursor >= len(db.get(Deck, job.deck_id).outline):
            job.status, job.cancel_requested = "complete", False
            db.commit()
            return job_view(job)
        queued = db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.owner == auth.owner, Job.status == "queued")
        )
        if queued >= policy["user_queued"]:
            raise HTTPException(429, "queue_full")
        if job.deck_id and db.scalar(
            select(Job.id).where(
                Job.deck_id == job.deck_id,
                Job.id != job.id,
                Job.status.in_(["running", "queued"]),
                Job.kind != "export",
            )
        ):
            raise HTTPException(409, "deck_busy")
        job.status, job.error, job.cancel_requested, job.session_id = (
            "queued",
            None,
            False,
            auth.id,
        )
        job.updated = now()
        db.commit()
        return job_view(job)


@app.get("/api/jobs/{job_id}/usage")
def usage(job_id: str, auth=Depends(current_session)):
    with session() as db:
        job = job_owned(db, auth, job_id)
        called = db.scalar(select(Step.id).where(Step.job_id == job.id))
    if not called:
        return {"quota": 0, "settling": False, "steps": []}
    response = gateway_for(auth).request("GET", f"/api/app-auth/jobs/{job.id}/usage")
    if response.status_code == 404:
        return {"quota": 0, "settling": True, "steps": []}
    if response.status_code != 200:
        raise HTTPException(503, "usage_unavailable")
    return response.json()["data"]


def admin(auth):
    if gateway_for(auth).data("/api/app-auth/me").get("role", 0) < 10:
        raise HTTPException(403, "admin_required")


@app.get("/api/admin")
def admin_config(auth=Depends(current_session)):
    admin(auth)
    with session() as db:
        counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
        hidden = {
            t.id
            for t in db.scalars(
                select(Template).where(Template.owner.is_(None), Template.enabled.is_(False))
            )
        }
        oldest = db.scalar(select(func.min(Job.created)).where(Job.status == "queued"))
        return {
            "policy": db.get(Policy, 1).data,
            "jobs": counts,
            "oldest_queued_at": oldest,
            "templates": [
                {"id": key, "name": name, "enabled": key not in hidden}
                for key, name in BUILTINS.items()
            ],
        }


@app.put("/api/admin")
def update_admin(body: AdminPolicy, auth=Depends(current_session)):
    admin(auth)
    with session() as db:
        record = db.scalar(select(Policy).where(Policy.id == 1).with_for_update())
        record.data = body.model_dump()
        db.commit()
    return {"ok": True}


@app.put("/api/admin/templates/{template_id}")
def admin_template(template_id: str, enabled: bool, auth=Depends(current_session)):
    admin(auth)
    if template_id not in BUILTINS:
        raise HTTPException(404, "template_not_found")
    with session() as db:
        record = db.get(Template, template_id)
        if not record:
            record = Template(
                id=template_id,
                owner=None,
                name=BUILTINS[template_id],
                data={},
                confirmed=True,
            )
            db.add(record)
        record.enabled = enabled
        db.commit()
    return {"ok": True}
