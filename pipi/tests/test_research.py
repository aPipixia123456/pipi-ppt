import pytest


def test_auto_research_requires_missing_or_time_sensitive_evidence():
    from pipi.backend.research import should_research

    assert should_research("auto", "新能源公司介绍", "")
    assert should_research("auto", "2025 年第三季度财报", "已经上传了足够长的资料。" * 500)
    assert not should_research("auto", "内部培训流程", "完整资料。" * 500)
    assert should_research("on", "内部培训流程", "完整资料。" * 500)
    assert not should_research("off", "2025 年财报", "")


def test_extract_sources_accepts_nested_results_and_deduplicates():
    from pipi.backend.research import extract_sources, format_research_context

    sources = extract_sources(
        {
            "data": {
                "results": [
                    {
                        "title": "官方资料",
                        "url": "https://example.com/report",
                        "snippet": "收入与销量数据。",
                    },
                    {
                        "title": "重复资料",
                        "url": "https://example.com/report",
                        "snippet": "不应重复。",
                    },
                ]
            }
        }
    )

    assert len(sources) == 1
    assert sources[0]["id"] == "source-1"
    context = format_research_context(sources)
    assert "官方资料" in context
    assert "https://example.com/report" in context
    assert "不应重复" not in context
    url_only = extract_sources({"results": [{"url": "https://example.com/url-only"}]})
    assert url_only[0]["title"] == "example.com"


def test_minimax_search_request_uses_coding_plan_contract(monkeypatch):
    monkeypatch.setenv("PIPI_CREDENTIAL_KEY", "test-key")
    monkeypatch.setenv("PIPI_MINIMAX_API_KEY", "subscription-key")
    monkeypatch.setenv("PIPI_MINIMAX_API_HOST", "https://api.minimaxi.test")

    from pipi.backend import generation
    from pipi.backend.config import settings

    settings.cache_clear()
    captured = {}

    class Response:
        status_code = 200
        content = b"{}"
        headers = {"Trace-Id": "mini-trace"}

        def json(self):
            return {
                "base_resp": {"status_code": 0, "status_msg": "success"},
                "organic": [
                    {
                        "title": "官方资料",
                        "link": "https://example.com/minimax",
                        "snippet": "可核对摘要",
                    }
                ],
            }

    class Client:
        def __init__(self, **kwargs):
            captured["options"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            captured["url"] = url
            captured["request"] = kwargs
            return Response()

    monkeypatch.setattr(generation.httpx, "Client", Client)
    try:
        payload, request_id = generation._request_minimax_search("MiniMax Web Search")
    finally:
        monkeypatch.delenv("PIPI_MINIMAX_API_KEY", raising=False)
        monkeypatch.delenv("PIPI_MINIMAX_API_HOST", raising=False)
        settings.cache_clear()

    assert request_id == "mini-trace"
    assert payload["base_resp"]["status_code"] == 0
    assert captured["url"] == "https://api.minimaxi.test/v1/coding_plan/search"
    assert captured["request"]["headers"] == {
        "Authorization": "Bearer subscription-key",
        "MM-API-Source": "Minimax-MCP",
    }
    assert captured["request"]["json"] == {"q": "MiniMax Web Search"}


def test_minimax_search_requires_a_server_secret(monkeypatch):
    monkeypatch.setenv("PIPI_CREDENTIAL_KEY", "test-key")
    monkeypatch.delenv("PIPI_MINIMAX_API_KEY", raising=False)
    from pipi.backend import generation
    from pipi.backend.config import settings

    settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="research_provider_not_configured"):
            generation._request_minimax_search("query")
    finally:
        settings.cache_clear()


def test_minimax_research_persists_sources_and_step(site, monkeypatch):
    client, _, _ = site
    from pipi.backend.config import settings
    from pipi.backend.db import Step, session
    from pipi.backend.worker import run_job

    monkeypatch.setenv("PIPI_RESEARCH_PROVIDER", "minimax")
    settings.cache_clear()
    monkeypatch.setattr(
        "pipi.backend.generation._request_minimax_search",
        lambda query: (
            {
                "base_resp": {"status_code": 0},
                "organic": [
                    {
                        "title": "MiniMax 来源",
                        "link": "https://example.com/source",
                        "snippet": "MiniMax 搜索摘要",
                    }
                ],
            },
            "mini-trace",
        ),
    )
    try:
        response = client.post(
            "/api/decks",
            headers={"Idempotency-Key": "minimax-research-outline"},
            json={
                "title": "MiniMax 研究测试",
                "topic": "需要外部资料的行业趋势",
                "template_id": "general",
                "slide_count": 5,
                "text_model": "tested-text",
                "images": False,
                "research_mode": "on",
            },
        )
        assert response.status_code == 200, response.text
        job = response.json()
        run_job(job["id"])
        deck = client.get("/api/decks/" + job["deck_id"]).json()
        with session() as db:
            step = db.query(Step).filter_by(job_id=job["id"], name="research-search").one()
            assert step.status == "complete"
            assert step.request_id == "mini-trace"
        assert deck["research"]["status"] == "complete"
        assert deck["research"]["sources"][0]["url"] == "https://example.com/source"
    finally:
        monkeypatch.delenv("PIPI_RESEARCH_PROVIDER", raising=False)
        settings.cache_clear()


def test_outline_persists_research_sources(site):
    client, _, _ = site
    from pipi.backend.worker import run_job

    response = client.post(
        "/api/decks",
        headers={"Idempotency-Key": "research-outline"},
        json={
            "title": "自动研究测试",
            "topic": "一个需要公开资料的行业趋势",
            "template_id": "general",
            "slide_count": 5,
            "text_model": "tested-text",
            "images": False,
            "research_mode": "on",
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()
    run_job(job["id"])

    deck = client.get("/api/decks/" + job["deck_id"]).json()
    assert deck["research"]["status"] == "complete"
    assert deck["research"]["sources"][0]["url"] == "https://example.com/source"
    result = next(item for item in client.get("/api/jobs").json() if item["id"] == job["id"])
    assert result["result"]["research"]["source_count"] == 1
