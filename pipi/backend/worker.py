import base64
import binascii
import math
import re
import shutil
import unicodedata
from copy import deepcopy
from datetime import timedelta

from celery import Celery
from fastapi import HTTPException
from sqlalchemy import delete, func, select, update

from .config import settings
from .conversion import export_deck, template_previews
from .db import Asset, Deck, Job, LoginState, Policy, Step, Template, WebSession, now, session
from .generation import UncertainCall, call_model, search_json, text_json
from .ppt_skill import (
    SKILL_NAME,
    build_outline_prompt,
    build_page_prompt,
    validate_story_outline,
)
from .research import (
    build_research_query,
    citation_notes,
    extract_sources,
    format_research_context,
    should_research,
)
from .schema import Slide
from .security import job_gateway
from .storage import (
    asset_path,
    owned_asset,
    read_document,
    store_asset,
    validate_upload,
)
from .templates import imported_template, layout_for

celery = Celery("pipi", broker=settings().redis_url)
celery.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    task_soft_time_limit=480,
    task_time_limit=510,
    broker_transport_options={"visibility_timeout": 600},
    beat_schedule={
        "dispatch-outbox": {"task": "pipi.dispatch", "schedule": 10.0},
        "cleanup": {"task": "pipi.cleanup", "schedule": 3600.0},
    },
)


def _outline_item_text(item) -> str:
    """Convert the outline shapes commonly returned by chat models to text."""
    if isinstance(item, str):
        return item.strip()[:1000]
    if not isinstance(item, dict):
        return ""

    title = ""
    for key in ("title", "heading", "name", "topic"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            title = value.strip()
            break
    details = ""
    for key in ("key_points", "points", "bullets", "summary", "content", "description"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            details = value.strip()
            break
        if isinstance(value, list):
            parts = [str(part).strip() for part in value if str(part).strip()]
            if parts:
                details = "；".join(parts)
                break
    if title and details and details != title:
        return f"{title} — {details}"[:1000]
    return (title or details)[:1000]


def normalize_outline_response(result: dict, slide_count: int, source: str) -> tuple[list[str], list[dict]]:
    """Keep an otherwise useful model response usable when its JSON shape drifts."""
    raw = result.get("outline") if isinstance(result, dict) else None
    if raw is None and isinstance(result, dict):
        for key in ("slides", "items", "pages", "entries"):
            if isinstance(result.get(key), list):
                raw = result[key]
                break
    if isinstance(raw, dict):
        for key in ("slides", "items", "pages", "entries"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
    if isinstance(raw, str):
        raw = [line.strip(" -*\t") for line in raw.splitlines() if line.strip()]
    if not isinstance(raw, list):
        raw = []

    outline = [text for item in raw if (text := _outline_item_text(item))]
    warnings: list[dict] = []
    if isinstance(result, dict) and isinstance(result.get("_pipi_warnings"), list):
        warnings.extend(
            warning
            for warning in result["_pipi_warnings"]
            if isinstance(warning, dict) and isinstance(warning.get("code"), str)
        )
    if len(outline) > slide_count:
        warnings.append({"code": "outline_truncated", "received": len(outline), "kept": slide_count})
        outline = outline[:slide_count]

    subject = _outline_subject(source)
    while len(outline) < slide_count:
        outline.append(_fallback_outline_item(len(outline), slide_count, subject, source))
    if warnings or len(outline) != len(raw):
        warnings.append({"code": "outline_normalized"})
    return outline, warnings


def _outline_subject(source: str) -> str:
    for line in source.splitlines():
        candidate = line.strip(" -*#\t")
        if not candidate or candidate.startswith("<"):
            continue
        candidate = re.sub(r"^(制作|生成|创建|做)\s*(一个|一份|一套)?\s*", "", candidate)
        return candidate[:80] or "主题内容"
    return "主题内容"


def _fallback_outline_item(index: int, slide_count: int, subject: str, source: str) -> str:
    financial = any(
        keyword in source.casefold()
        for keyword in ("财报", "季度", "营收", "净利润", "现金流", "revenue", "earnings")
    )
    if financial:
        cards = [
            ("先明确阅读框架：本季表现、累计表现与关键变量", "Hook", "建立看财报的共同问题", "收入、利润、现金流和经营变量", "结论卡片"),
            ("本季经营：收入与利润指标需要一起看", "Context", "区分规模变化和盈利变化", "本季核心指标及同比、环比口径", "双轴指标图"),
            ("累计表现：增长来源与质量如何变化", "Tension", "把单季波动放回全年进程", "前三季度累计数据与结构变化", "累计趋势图"),
            ("现金流与资产负债：经营安全边界", "Insight", "判断利润是否转化为现金", "经营现金流、负债和周转线索", "现金流瀑布"),
            ("研发与产品：投入如何转化为竞争力", "Mechanism", "连接投入、产品和长期回报", "研发投入、技术路线和产品动作", "投入产出关系图"),
            ("市场与竞争：外部环境带来的压力", "Proof", "解释业绩变化的外部变量", "行业竞争、价格和需求变化", "竞争对比图"),
            ("效率与成本：利润变化的驱动拆解", "Roadmap", "找到下一步改善利润的抓手", "成本、毛利、费用和效率线索", "驱动树"),
            ("风险与验证：哪些数据仍需跟踪", "Proof", "把不确定性转成观察清单", "风险假设、证据缺口和验证指标", "风险矩阵"),
            ("管理启示：下一阶段的优先事项", "Decision", "把分析落到经营动作", "产品、市场、成本和现金的优先级", "优先级矩阵"),
            ("结论与行动：形成可执行的判断", "Decision", "明确结论、责任和下一步", "结论、行动建议和后续跟踪", "行动清单"),
        ]
    else:
        cards = [
            ("先明确问题与目标", "Hook", "让观众知道这份演示要解决什么", "目标、受众和成功标准", "目标卡片"),
            ("背景变化改变了什么", "Context", "建立共同背景和范围", "现状、趋势和关键约束", "趋势图"),
            ("关键矛盾在哪里", "Tension", "把问题从现象收敛到核心矛盾", "问题、影响和优先级", "问题树"),
            ("证据说明了什么", "Insight", "用证据支持判断", "事实、数据和可验证假设", "数据展板"),
            ("方案如何解决问题", "Mechanism", "解释方案的工作机制", "关键动作、角色和输入输出", "流程图"),
            ("执行路径与里程碑", "Roadmap", "让落地步骤可追踪", "阶段、里程碑和交付物", "时间线"),
            ("资源与预算怎么安排", "Proof", "说明方案具备可执行条件", "人力、预算和依赖", "资源矩阵"),
            ("风险和边界如何控制", "Proof", "提前处理阻力和不确定性", "风险、触发条件和应对动作", "风险矩阵"),
            ("用什么指标验证结果", "Roadmap", "建立可复盘的验收方式", "指标、基线和目标值", "指标看板"),
            ("结论与下一步决策", "Decision", "让观众知道现在需要做什么", "结论、行动建议和责任人", "行动清单"),
        ]
    title, role, purpose, points, visual = cards[index % len(cards)]
    if index == slide_count - 1:
        title = f"{title}：围绕“{subject}”形成判断"
    return f"{title}｜角色：{role}｜目的：{purpose}｜要点：{points}｜视觉：{visual}"


def _safe_chart_data(value) -> tuple[list[str], list[float], bool]:
    if not isinstance(value, dict):
        return [], [], value is not None
    labels, values = value.get("labels", []), value.get("values", [])
    if not isinstance(labels, list) or not isinstance(values, list):
        return [], [], True
    invalid = len(labels) != len(values) or len(labels) > 30 or len(values) > 30
    safe_labels, safe_values = [], []
    for label, raw_value in zip(labels[:30], values[:30], strict=False):
        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            invalid = True
            continue
        if not math.isfinite(number) or abs(number) > 1e12:
            invalid = True
            continue
        safe_labels.append(str(label)[:100])
        safe_values.append(number)
    return safe_labels, safe_values, invalid


def _safe_table_rows(value) -> tuple[list[list[str]], bool]:
    if not isinstance(value, list):
        return [], value is not None
    invalid = len(value) > 25
    rows = []
    for raw_row in value[:25]:
        if not isinstance(raw_row, list):
            invalid = True
            continue
        if len(raw_row) > 12:
            invalid = True
        row = []
        for cell in raw_row[:12]:
            if not isinstance(cell, (str, int, float, bool)):
                invalid = True
            row.append(str(cell)[:300])
        rows.append(row)
    return rows, invalid


def _page_fallback_copy(outline: str) -> tuple[str, str]:
    """Derive short, topic-specific copy when a model omits a text slot."""
    parts = [part.strip() for part in re.split(r"[｜|]", outline) if part.strip()]
    headline = parts[0] if parts else outline.strip()
    if "：" in headline:
        prefix, remainder = headline.split("：", 1)
        if remainder.strip():
            headline = remainder.strip()
    body_parts = []
    for part in parts[1:]:
        if "：" in part:
            label, remainder = part.split("：", 1)
            if label.strip() in {"目的", "要点", "内容", "建议"}:
                part = remainder.strip()
        if part:
            body_parts.append(part)
    body = "；".join(body_parts) or headline
    return headline[:200], body[:1200]


def _field_values(raw) -> dict[str, str]:
    """Accept the common object and list forms used by chat models for fields."""
    values: dict[str, str] = {}
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        items = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("id") or item.get("slot") or item.get("name")
            value = item.get("text", item.get("value", item.get("content")))
            if key is not None:
                items.append((key, value))
    else:
        return values
    for key, value in items:
        if isinstance(value, dict):
            value = value.get("text", value.get("value", value.get("content")))
        if isinstance(value, (str, int, float)) and str(value).strip():
            values[str(key)] = str(value)
    return values


def _save_research(deck_id: str, research: dict):
    with session() as db:
        deck = db.get(Deck, deck_id)
        if deck:
            deck.research = research
            deck.updated = now()
            db.commit()


def _research_for_job(job: Job, topic: str, document_text: str) -> tuple[dict, str]:
    mode = str(job.args.get("research_mode", "auto")).strip().lower()
    if mode not in {"auto", "on", "off"}:
        mode = "auto"
    research = {
        "status": "skipped",
        "mode": mode,
        "query": "",
        "sources": [],
    }
    if not should_research(mode, topic, document_text):
        _save_research(job.deck_id, research)
        return research, ""

    query = build_research_query(topic, document_text)
    research["query"] = query
    with session() as db:
        policy_research_model = str(db.get(Policy, 1).data.get("research_model", ""))
    model = settings().research_model.strip() or policy_research_model.strip() or job.args["text_model"]
    try:
        payload = search_json(job.id, "research-search", model, query)
        sources = extract_sources(payload)
    except UncertainCall:
        research["status"] = "awaiting_confirmation"
        _save_research(job.deck_id, research)
        raise
    except (HTTPException, ValueError) as exc:
        error_code = str(exc) if isinstance(exc, ValueError) else "research_unavailable"
        if error_code not in {"research_model_not_available"}:
            error_code = "research_unavailable"
        research["status"] = "failed"
        research["warning"] = error_code
        _save_research(job.deck_id, research)
        # If the user explicitly requested research, or there is no uploaded
        # evidence to fall back to, do not proceed with unsupported facts.
        if mode == "on" or not document_text.strip():
            raise HTTPException(503, error_code) from exc
        return research, ""

    if not sources:
        research["status"] = "no_results"
        research["warning"] = "research_no_sources"
        _save_research(job.deck_id, research)
        if mode == "on" or not document_text.strip():
            raise HTTPException(503, "research_no_sources")
        return research, ""

    research["status"] = "complete"
    research["sources"] = sources
    _save_research(job.deck_id, research)
    return research, format_research_context(sources)


def submit(db, auth, kind: str, args: dict, key: str, deck_id: str | None = None) -> Job:
    if not key or len(key) > 80:
        raise HTTPException(400, "idempotency_key_required")
    policy = db.scalar(select(Policy).where(Policy.id == 1).with_for_update()).data
    existing = db.scalar(select(Job).where(Job.owner == auth.owner, Job.idempotency_key == key))
    if existing:
        if existing.kind != kind or existing.args != args or existing.deck_id != deck_id:
            raise HTTPException(409, "idempotency_key_reused")
        return existing
    if not policy["enabled"] and kind != "export":
        raise HTTPException(503, "site_paused")
    queued = db.scalar(
        select(func.count()).select_from(Job).where(Job.owner == auth.owner, Job.status == "queued")
    )
    if queued >= policy["user_queued"]:
        raise HTTPException(429, "queue_full")
    if (
        deck_id
        and db.scalar(
            select(Job.id).where(Job.deck_id == deck_id, Job.status.in_(["queued", "running"]))
        )
        and kind != "export"
    ):
        raise HTTPException(409, "deck_busy")
    job = Job(
        owner=auth.owner,
        session_id=auth.id,
        kind=kind,
        args=args,
        idempotency_key=key,
        deck_id=deck_id,
    )
    db.add(job)
    db.commit()  # Durable outbox; beat dispatches even if Redis is temporarily down.
    return job


@celery.task(name="pipi.dispatch")
def dispatch():
    with session() as db:
        # A model call is never automatically replayed after an expired lease.
        stale = db.scalars(
            select(Job).where(Job.status == "running", Job.updated < now() - timedelta(minutes=10))
        ).all()
        for job in stale:
            uncertain = db.scalar(
                select(Step.id).where(
                    Step.job_id == job.id, Step.status.in_(["calling", "uncertain"])
                )
            )
            job.status = "awaiting_confirmation" if uncertain else "queued"
            job.error = "worker_interrupted_model_result_unconfirmed" if uncertain else None
            job.updated = now()
        db.commit()
        jobs = db.scalars(
            select(Job).where(Job.status == "queued").order_by(Job.created).limit(100)
        ).all()
        for job in jobs:
            run_job.apply_async(
                args=[job.id], queue="export" if job.kind == "export" else "generation"
            )


def claim(job_id: str) -> bool:
    with session() as db:
        policy = db.scalar(select(Policy).where(Policy.id == 1).with_for_update()).data
        job = db.get(Job, job_id)
        if not job or job.status != "queued":
            return False
        if job.cancel_requested:
            job.status = "cancelled"
            db.commit()
            return False
        if not policy["enabled"] and job.kind != "export":
            return False
        exporting = job.kind == "export"
        global_running = db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.status == "running",
                (Job.kind == "export") if exporting else (Job.kind != "export"),
            )
        )
        user_running = db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status == "running", Job.owner == job.owner)
        )
        if (
            global_running
            >= policy["export_concurrency" if exporting else "generation_concurrency"]
            or user_running >= policy["user_running"]
        ):
            return False
        result = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(status="running", updated=now())
        )
        db.commit()
        return result.rowcount == 1


@celery.task(name="pipi.run")
def run_job(job_id: str):
    if not claim(job_id):
        return
    try:
        with session() as db:
            job = db.get(Job, job_id)
            deck = db.get(Deck, job.deck_id) if job.deck_id else None
        job_gateway(job_id)  # Validate revocation and live account on each stage.
        if job.kind == "outline":
            topic = job.args["topic"]
            document_text = ""
            with session() as db:
                for asset_id in job.args.get("document_ids", []):
                    document_text += (
                        "\n"
                        + read_document(owned_asset(db, job.owner, asset_id))
                    )
            research, research_context = _research_for_job(job, topic, document_text)
            source = topic
            if document_text.strip():
                source += (
                    "\n<reference>"
                    + document_text[:120000]
                    + "</reference>"
                )
            if research_context:
                source += "\n<web-research>\n" + research_context + "\n</web-research>"
            result = text_json(
                job_id,
                "outline",
                job.args["text_model"],
                build_outline_prompt(source, job.args["slide_count"]),
                reasoning_effort=job.args.get("reasoning_effort", "auto"),
            )
            outline, warnings = normalize_outline_response(
                result, job.args["slide_count"], source
            )
            warnings.extend(validate_story_outline(outline))
            if research.get("warning"):
                warnings.append({"code": research["warning"]})
            with session() as db:
                record = db.get(Deck, deck.id)
                record.outline, record.updated, record.version = (
                    outline,
                    now(),
                    record.version + 1,
                )
                db.commit()
            result_data = {
                "deck_id": deck.id,
                "generation_strategy": SKILL_NAME,
                "research": {
                    "status": research.get("status"),
                    "source_count": len(research.get("sources", [])),
                },
            }
            if warnings:
                result_data["warnings"] = warnings
            complete(job_id, result_data)
        elif job.kind in {"generate", "rewrite"}:
            generate_page(job, deck)
        elif job.kind == "template":
            import_template(job)
        elif job.kind == "export":
            # Snapshot was validated and stored at submit time; later edits cannot
            # change an export already in the queue. No model calls occur here.
            asset = export_deck(
                job.owner, job.args["title"], job.args["slides"], job.args["format"], job.id
            )
            complete(job_id, {"asset_id": asset.id})
        else:
            raise ValueError("unknown_job_kind")
    except UncertainCall:
        fail(job_id, "awaiting_confirmation", "model_result_unconfirmed")
    except HTTPException as exc:
        fail(
            job_id,
            "cancelled" if exc.status_code == 409 else "failed",
            str(exc.detail)[:200],
        )
    except (ValueError, KeyError, IndexError) as exc:
        # Do not leak upstream responses, credentials or arbitrary document text.
        message = (
            str(exc) if str(exc).replace("_", "").isalnum() else "invalid_model_or_document_result"
        )
        fail(job_id, "failed", message[:200])
    except Exception:
        fail(job_id, "failed", "processing_failed")
        raise  # Worker logs a traceback without model/request bodies.


def complete(job_id: str, result: dict):
    with session() as db:
        job = db.get(Job, job_id)
        job.status, job.result, job.updated = "complete", result, now()
        db.execute(update(Step).where(Step.job_id == job_id).values(response=None))
        db.commit()


def fail(job_id: str, status: str, message: str):
    with session() as db:
        job = db.get(Job, job_id)
        job.status, job.error, job.updated = status, message, now()
        db.commit()


def generate_page(job: Job, deck: Deck):
    index = job.args["index"] if job.kind == "rewrite" else job.cursor
    layout = (
        deepcopy(deck.slides[index]) if job.kind == "rewrite" else layout_for(deck.template, index)
    )
    fields = [
        {
            "id": e["id"],
            "max_chars": e["max_chars"],
            "example": e["text"],
            "box": [round(e["x"]), round(e["y"]), round(e["w"]), round(e["h"])],
            "font_size": e["font_size"],
            "align": e["align"],
        }
        for e in layout["elements"]
        if e["type"] == "text" and e["editable"]
    ]
    image_slots = [
        element
        for element in layout["elements"]
        if element["type"] == "image" and element["editable"]
    ]
    data_slots = [
        {"id": e["id"], "type": e["type"]}
        for e in layout["elements"]
        if e["type"] in {"chart", "table"}
    ]
    research_sources = (deck.research or {}).get("sources", [])
    prompt = build_page_prompt(
        index=index,
        outline=deck.outline[index],
        instruction=job.args.get("instruction", ""),
        fields=fields,
        image_slots=image_slots,
        data_slots=data_slots,
        research_context=format_research_context(research_sources),
    )
    response = text_json(
        job.id,
        f"page-{index}",
        job.args["text_model"],
        prompt,
        reasoning_effort=job.args.get("reasoning_effort", "auto"),
    )
    response_warnings = []
    if isinstance(response, dict) and isinstance(response.get("_pipi_warnings"), list):
        response_warnings = [
            warning
            for warning in response["_pipi_warnings"]
            if isinstance(warning, dict) and isinstance(warning.get("code"), str)
        ]
    values = _field_values(response.get("fields"))
    # Keep the page checkpoint usable if an older worker or a provider adapter
    # returns the raw list form after the normalizer. The page must fall back to
    # deterministic copy rather than crash on ``values.get``.
    if not isinstance(values, dict):
        values = _field_values(values)
    editable_ids = {
        element["id"]
        for element in layout["elements"]
        if element["type"] == "text" and element["editable"]
    }
    # Models occasionally copy an example string into the key instead of using
    # the requested slot id. Reassign those values by slot order before using
    # deterministic copy derived from the outline. This keeps template example
    # text from leaking into the user's deck when a response is incomplete.
    unmatched_values = [
        value
        for key, value in values.items()
        if str(key) not in editable_ids
    ]
    missing_slots = []
    data_warnings = []
    fallback_headline, fallback_body = _page_fallback_copy(deck.outline[index])
    charts = response.get("charts")
    tables = response.get("tables")
    if charts is None:
        charts = {}
    elif not isinstance(charts, dict):
        data_warnings.append({"code": "invalid_chart_data"})
        charts = {}
    if tables is None:
        tables = {}
    elif not isinstance(tables, dict):
        data_warnings.append({"code": "invalid_table_data"})
        tables = {}
    for element in layout["elements"]:
        if element["type"] == "text" and element["editable"]:
            value = values.get(element["id"])
            if not isinstance(value, str):
                missing_slots.append(element["id"])
                if unmatched_values:
                    value = unmatched_values.pop(0)
                elif element["max_chars"] <= 4 and element.get("text", "").strip().isdigit():
                    # Numbered timeline markers are part of the layout, not copy.
                    value = element.get("text", "")
                elif element["max_chars"] <= 40 or element["font_size"] >= 38:
                    value = fallback_headline
                else:
                    value = fallback_body
            element["text"] = value[: element["max_chars"]]
            fit_text(element)
        elif element["type"] == "chart" and element["editable"]:
            labels, chart_values, invalid = _safe_chart_data(charts.get(element["id"]))
            element["labels"], element["values"] = labels, chart_values
            if invalid:
                data_warnings.append({"code": "invalid_chart_data", "slot": element["id"]})
        elif element["type"] == "table" and element["editable"]:
            rows, invalid = _safe_table_rows(tables.get(element["id"]))
            element["rows"] = rows
            if invalid:
                data_warnings.append({"code": "invalid_table_data", "slot": element["id"]})
    notes = str(response.get("notes", ""))
    sources_note = citation_notes(research_sources)
    if sources_note:
        notes = f"{notes}\n\n{sources_note}".strip()
    layout["name"], layout["notes"] = deck.outline[index][:200], notes[:10000]
    # Save page text before any image call, so partial results survive failures.
    with session() as db:
        record = db.get(Deck, deck.id)
        slides = deepcopy(record.slides)
        if index < len(slides):
            slides[index] = Slide.model_validate(layout).model_dump()
        else:
            slides.append(Slide.model_validate(layout).model_dump())
        record.slides, record.version, record.updated = (
            slides,
            record.version + 1,
            now(),
        )
        checkpoint = db.get(Job, job.id)
        result = {
            **checkpoint.result,
            "deck_version": record.version,
            "generation_strategy": SKILL_NAME,
        }
        warnings = list(result.get("warnings", []))
        for warning in response_warnings + data_warnings:
            if warning not in warnings:
                warnings.append(warning)
        if missing_slots:
            warning = {"code": "missing_text_slots", "slots": missing_slots}
            if warning not in warnings:
                warnings.append(warning)
            warning = {"code": "text_slot_fallback_applied"}
            if warning not in warnings:
                warnings.append(warning)
        if job.args.get("images") and image_slots and response.get("image_prompt") and not job.args.get("image_model"):
            warning = {"code": "image_model_unavailable"}
            if warning not in warnings:
                warnings.append(warning)
        if warnings:
            result["warnings"] = warnings
        checkpoint.result = result
        db.commit()
    if job.args.get("images") and image_slots and response.get("image_prompt") and job.args.get("image_model"):
        try:
            generated = call_model(
                job.id,
                f"image-{index}",
                job.args["image_model"],
                "image",
                {
                    "prompt": str(response["image_prompt"])[:3000],
                    "n": 1,
                    "size": "1024x1024",
                    "response_format": "b64_json",
                },
            )
            image_data = generated.get("data") if isinstance(generated, dict) else None
            first_image = image_data[0] if isinstance(image_data, list) and image_data else None
            data = first_image.get("b64_json") if isinstance(first_image, dict) else None
            if not isinstance(data, str) or not data:
                raise ValueError("image_model_must_return_base64")
            raw = base64.b64decode(data, validate=True)
            media_type = validate_upload("image.png", raw, "image")
            asset = store_asset(
                job.owner,
                f"slide-{index + 1}.png",
                raw,
                media_type,
                "generated-image",
                idempotency_key=f"{job.id}:image:{index}",
            )
            for element in image_slots:
                element["asset_id"] = asset.id
            with session() as db:
                record = db.get(Deck, deck.id)
                slides = deepcopy(record.slides)
                slides[index] = Slide.model_validate(layout).model_dump()
                record.slides, record.version, record.updated = (
                    slides,
                    record.version + 1,
                    now(),
                )
                checkpoint = db.get(Job, job.id)
                checkpoint.result = {**checkpoint.result, "deck_version": record.version}
                db.commit()
        except UncertainCall:
            raise
        except HTTPException as exc:
            if exc.status_code != 413:
                raise
            warning = {
                "code": "image_generation_failed",
                "slide": index + 1,
                "reason": str(exc.detail)[:80],
            }
            with session() as db:
                checkpoint = db.get(Job, job.id)
                result = {**checkpoint.result}
                warnings = list(result.get("warnings", []))
                if warning not in warnings:
                    warnings.append(warning)
                result["warnings"] = warnings
                checkpoint.result = result
                db.commit()
        except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, binascii.Error):
            warning = {"code": "image_generation_failed", "slide": index + 1}
            with session() as db:
                checkpoint = db.get(Job, job.id)
                result = {**checkpoint.result}
                warnings = list(result.get("warnings", []))
                if warning not in warnings:
                    warnings.append(warning)
                result["warnings"] = warnings
                checkpoint.result = result
                db.commit()
    with session() as db:
        record = db.get(Job, job.id)
        record.cursor += 1
        record.status = (
            "complete" if job.kind == "rewrite" or record.cursor >= len(deck.outline) else "queued"
        )
        if record.cancel_requested:
            record.status = "cancelled"
        if record.status == "complete":
            db.execute(update(Step).where(Step.job_id == job.id).values(response=None))
        record.updated, record.result = (
            now(),
            {**record.result, "deck_id": deck.id, "completed_slides": index + 1},
        )
        db.commit()


def fit_text(element: dict):
    """Keep generated CJK text within its original box using a conservative fit."""
    size = element["font_size"]
    while size > 8:
        lines, occupied = 1, 0.0
        for character in element["text"]:
            if character == "\n":
                lines, occupied = lines + 1, 0.0
                continue
            width = size * (1 if unicodedata.east_asian_width(character) in {"W", "F"} else 0.6)
            if occupied + width > element["w"]:
                lines, occupied = lines + 1, 0.0
            occupied += width
        if lines * size * element.get("line_height", 1.25) <= element["h"]:
            break
        size = max(8, size - 1)
    element["font_size"] = size


def import_template(job: Job):
    with session() as db:
        asset = owned_asset(db, job.owner, job.args["asset_id"])
    # Local parsing/preview checkpoints are also persisted before paid vision.
    data = job.result.get("template")
    if not data:
        data = imported_template(asset, job.id)
        with session() as db:
            record = db.get(Job, job.id)
            record.result = {"template": data}
            db.commit()
    if not data.get("previews"):
        data["previews"] = template_previews(asset)
        with session() as db:
            record = db.get(Job, job.id)
            record.result = {"template": data}
            db.commit()
    with session() as db:
        preview = owned_asset(db, job.owner, data["previews"][0])
    analysis = text_json(
        job.id,
        "template-vision",
        job.args["text_model"],
        'Analyze this presentation style as a visual design reviewer. Return JSON {"description":"brief style description in Chinese","layout_advice":"specific advice for keeping hierarchy, whitespace, image crops and readable text"}. Preserve logos, colors and main layouts. Do not invent brand facts.',
        [asset_path(preview).read_bytes()],
        reasoning_effort=job.args.get("reasoning_effort", "auto"),
    )
    data["analysis"] = analysis
    with session() as db:
        template = Template(owner=job.owner, name=data["name"], data=data, confirmed=False)
        db.add(template)
        db.flush()
        record = db.get(Job, job.id)
        record.status, record.result, record.updated = (
            "complete",
            {"template_id": template.id},
            now(),
        )
        db.execute(update(Step).where(Step.job_id == job.id).values(response=None))
        db.commit()


@celery.task(name="pipi.cleanup")
def cleanup():
    temporary = settings().storage_dir / "tmp"
    cutoff = (now() - timedelta(hours=24)).timestamp()
    if temporary.is_dir():
        for path in temporary.iterdir():
            if path.stat().st_mtime < cutoff:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
    with session() as db:
        known = {asset.id for asset in db.scalars(select(Asset))}
        timestamp = int(now().timestamp())
        db.execute(delete(LoginState).where(LoginState.expires <= timestamp))
        db.execute(delete(WebSession).where(WebSession.expires <= timestamp))
        db.commit()
    for folder in settings().storage_dir.iterdir():
        if folder.is_dir() and folder.name.isdigit():
            for file in folder.iterdir():
                if file.is_file() and file.name not in known and file.stat().st_mtime < cutoff:
                    file.unlink()
