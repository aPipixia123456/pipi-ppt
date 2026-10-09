import os
import shutil
import time
from uuid import uuid4

import pytest


@pytest.mark.skipif(
    not shutil.which("soffice") or not shutil.which("pdftoppm"),
    reason="LibreOffice and Poppler required",
)
def test_real_pdf_export_and_original_template_preview(site):
    from pipi.backend.conversion import export_deck, template_previews
    from pipi.backend.storage import asset_path
    from pipi.backend.templates import builtin_template
    from pypdf import PdfReader

    layout = builtin_template("executive")["layouts"][:1]
    text = next(e for e in layout[0]["elements"] if e["type"] == "text" and e["editable"])
    text["text"] = "中文导出与字体测试"
    pptx = export_deck(1, "验收", layout, "pptx")
    pdf = export_deck(1, "验收", layout, "pdf")
    assert len(PdfReader(asset_path(pdf)).pages) == 1
    previews = template_previews(pptx)
    assert len(previews) == 1


@pytest.mark.skipif(
    not os.environ.get("PIPI_TEST_REDIS_URL") or not os.environ.get("PIPI_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL and Redis integration services required",
)
def test_real_queue_delivers_durable_jobs_across_worker_restart(site):
    from celery.contrib.testing.worker import start_worker
    from pipi.backend.db import Job, session
    from pipi.backend.worker import celery, dispatch, run_job
    from pipi.tests.test_workflow import create

    client, calls, _ = site
    previous_broker = celery.conf.broker_url
    previous_options = celery.conf.broker_transport_options
    celery.conf.broker_url = os.environ["PIPI_TEST_REDIS_URL"]
    celery.conf.broker_transport_options = {
        **previous_options,
        "global_keyprefix": "pipi-test-" + uuid4().hex + ":",
    }
    try:
        for index in range(2):
            # Commit while no worker exists, then start a real Celery consumer.
            job = create(client, f"worker-restart-{index}").json()
            dispatch()
            with start_worker(
                celery,
                pool="solo",
                queues=["generation"],
                perform_ping_check=False,
                shutdown_timeout=15,
            ):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    with session() as db:
                        status = db.get(Job, job["id"]).status
                    if status in {"complete", "failed", "awaiting_confirmation"}:
                        break
                    time.sleep(0.05)
                assert status == "complete"
                run_job.apply_async(args=[job["id"]], queue="generation")
        assert len(calls) == 2
        assert len({call[1] for call in calls}) == 2
    finally:
        celery.close()
        celery.conf.broker_url = previous_broker
        celery.conf.broker_transport_options = previous_options
