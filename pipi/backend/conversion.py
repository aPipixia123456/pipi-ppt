import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from .config import settings
from .db import session
from .schema import Slide
from .storage import asset_path, owned_asset, store_asset
from .templates import builtin_asset_path


def run_converter(command: list[str], cwd: Path):
    if os.name != "nt":
        # The timeout process survives a killed Celery parent and stops its group.
        command = ["timeout", "--signal=KILL", f"{settings().converter_timeout}s", *command]
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = process.communicate(timeout=settings().converter_timeout)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.communicate()
        raise ValueError("conversion_timeout") from None
    except BaseException:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.communicate()
        raise
    if process.returncode:
        raise ValueError("conversion_failed")


def convert_pdf(source: Path, directory: Path) -> Path:
    profile = directory / "office-profile"
    profile.mkdir()
    (profile / "user").mkdir()
    (profile / "user/registrymodifications.xcu").write_text(
        """<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item><item oor:path="/org.openoffice.Office.Common/Load"><prop oor:name="UpdateMode" oor:op="fuse"><value>0</value></prop></item></oor:items>""",
        encoding="utf-8",
    )
    output = directory / "pdf"
    output.mkdir()
    run_converter(
        [
            "soffice",
            "-env:UserInstallation=" + profile.as_uri(),
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output),
            str(source),
        ],
        directory,
    )
    result = output / (source.stem + ".pdf")
    if not result.is_file() or result.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("pdf_conversion_failed")
    return result


def export_deck(owner: int, title: str, slides: list, format: str, job_id: str | None = None):
    checked = [Slide.model_validate(slide).model_dump() for slide in slides]
    assets = {}
    with session() as db:
        for page in checked:
            for element in page["elements"]:
                if element["asset_id"]:
                    assets[element["asset_id"]] = str(
                        asset_path(owned_asset(db, owner, element["asset_id"]))
                    )
                elif element["builtin_asset"]:
                    assets[element["builtin_asset"]] = str(
                        builtin_asset_path(element["builtin_asset"])
                    )
    temporary = settings().storage_dir / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temporary) as path:
        folder = Path(path)
        manifest = folder / "deck.json"
        manifest.write_text(
            json.dumps(
                {"title": title, "slides": checked, "assets": assets},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        pptx = folder / "presentation.pptx"
        run_converter(["node", str(settings().exporter), str(manifest), str(pptx)], folder)
        if format == "pdf":
            pdf = convert_pdf(pptx, folder)
            return store_asset(
                owner,
                "presentation.pdf",
                pdf.read_bytes(),
                "application/pdf",
                "export",
                idempotency_key=f"{job_id}:export" if job_id else None,
            )
        return store_asset(
            owner,
            "presentation.pptx",
            pptx.read_bytes(),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "export",
            idempotency_key=f"{job_id}:export" if job_id else None,
        )


def template_previews(asset) -> list[str]:
    temporary = settings().storage_dir / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temporary) as path:
        folder = Path(path)
        source = folder / "template.pptx"
        shutil.copyfile(asset_path(asset), source)
        pdf = convert_pdf(source, folder)
        # Max 30 validated input slides. Bound raster dimensions and page count.
        run_converter(
            [
                "pdftoppm",
                "-png",
                "-scale-to",
                "960",
                "-f",
                "1",
                "-l",
                "30",
                str(pdf),
                str(folder / "page"),
            ],
            folder,
        )
        pages = sorted(folder.glob("page-*.png"))
        if not pages:
            raise ValueError("preview_failed")
        return [
            store_asset(
                asset.owner,
                page.name,
                page.read_bytes(),
                "image/png",
                "preview",
                idempotency_key=f"{asset.id}:preview:{page.name}",
            ).id
            for page in pages
        ]
