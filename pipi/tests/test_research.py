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
