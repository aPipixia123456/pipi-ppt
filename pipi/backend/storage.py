import io
import os
import re
import zipfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from lxml import etree
from PIL import Image
from sqlalchemy import delete, select, update

from .config import settings
from .db import Account, Asset, Policy, identifier, session

Image.MAX_IMAGE_PIXELS = 20_000_000


def asset_path(asset: Asset) -> Path:
    return settings().storage_dir / str(asset.owner) / asset.id


def owned_asset(db, owner: int, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if not asset or asset.owner != owner:
        raise HTTPException(404, "file_not_found")
    return asset


def inspect_archive(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        items = archive.infolist()
        if len(items) > 10000 or sum(item.file_size for item in items) > 150 * 1024 * 1024:
            raise ValueError("archive_limit_exceeded")
        for item in items:
            name = item.filename.lower().replace("\\", "/")
            if ".." in name.split("/") or name.startswith("/") or item.flag_bits & 1:
                raise ValueError("unsafe_archive")
            printer_settings = re.fullmatch(
                r"(?:ppt|word)/printersettings/printersettings\d+\.bin", name
            )
            if (
                "vbaproject" in name
                or "activex" in name
                or name.endswith((".exe", ".dll"))
                or (name.endswith(".bin") and not printer_settings)
            ):
                raise ValueError("embedded_code_unsupported")
            if name.endswith(".rels"):
                parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
                relationships = etree.fromstring(archive.read(item), parser)
                if relationships.getroottree().docinfo.doctype:
                    raise ValueError("document_type_unsupported")
                for relation in relationships:
                    if relation.get("TargetMode", "").lower() == "external":
                        raise ValueError("external_relationship_unsupported")
                    if relation.get("Type", "").rsplit("/", 1)[-1].lower() in {
                        "vbaproject",
                        "oleobject",
                        "control",
                    }:
                        raise ValueError("embedded_code_unsupported")


def validate_upload(name: str, data: bytes, purpose: str) -> str:
    suffix = Path(name).suffix.lower()
    if purpose == "image":
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("image_type_unsupported")
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            return Image.MIME.get(image.format, "image/png")
    if purpose == "template":
        if suffix != ".pptx":
            raise ValueError("pptx_required")
        inspect_archive(data)
        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    if suffix == ".docx":
        inspect_archive(data)
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix == ".pdf" and data.startswith(b"%PDF-"):
        return "application/pdf"
    if suffix in {".md", ".txt"}:
        data.decode("utf-8-sig")
        return "text/plain"
    raise ValueError("file_type_unsupported")


def store_asset(
    owner: int,
    name: str,
    data: bytes,
    media_type: str,
    purpose: str,
    idempotency_key: str | None = None,
) -> Asset:
    with session() as db:
        # Serialize per-owner asset creation, including idempotent imports of
        # the same logo/preview by separate concurrent jobs.
        db.scalar(select(Account).where(Account.id == owner).with_for_update())
        asset_id = (
            str(uuid5(NAMESPACE_URL, f"pipi-ppt:{owner}:{idempotency_key}"))
            if idempotency_key
            else identifier()
        )
        existing = db.get(Asset, asset_id)
        if existing:
            return existing
        policy = db.get(Policy, 1).data
        if len(data) > min(
            settings().upload_limit, policy["upload_mb"] * 1024 * 1024
        ) and purpose not in {"export", "preview"}:
            raise HTTPException(413, "upload_too_large")
        cap = policy["storage_mb"] * 1024 * 1024
        result = db.execute(
            update(Account)
            .where(Account.id == owner, Account.stored_bytes + len(data) <= cap)
            .values(stored_bytes=Account.stored_bytes + len(data))
        )
        if result.rowcount != 1:
            raise HTTPException(413, "storage_quota_exceeded")
        asset = Asset(
            id=asset_id,
            owner=owner,
            name=Path(name).name[:200],
            size=len(data),
            media_type=media_type,
            purpose=purpose,
        )
        path = asset_path(asset)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
            db.add(asset)
            db.commit()
        except Exception:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise
        return asset


def remove_asset(db, asset: Asset):
    # DB is authoritative. Orphan files from an interrupted delete are swept later.
    db.scalar(select(Account).where(Account.id == asset.owner).with_for_update())
    removed = db.execute(delete(Asset).where(Asset.id == asset.id, Asset.owner == asset.owner))
    if removed.rowcount != 1:
        db.rollback()
        return
    db.execute(
        update(Account)
        .where(Account.id == asset.owner)
        .values(stored_bytes=Account.stored_bytes - asset.size)
    )
    db.commit()
    asset_path(asset).unlink(missing_ok=True)


def read_document(asset: Asset) -> str:
    path = asset_path(asset)
    suffix = Path(asset.name).suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if len(reader.pages) > 200:
            raise ValueError("document_page_limit")
        return "\n".join((page.extract_text() or "")[:10000] for page in reader.pages)[:60000]
    if suffix == ".docx":
        from docx import Document

        document = Document(path)
        return "\n".join(p.text for p in document.paragraphs)[:60000]
    return path.read_text(encoding="utf-8-sig")[:60000]
