"""X-57: deep web research and research+email background job."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai_core.llm_text import LLMResult, LLMUnavailableError
from services.info import jarvis_researcher as researcher


@pytest.mark.asyncio
@patch("services.info.jarvis_researcher.generate_text", new_callable=AsyncMock)
@patch("services.info.jarvis_researcher.scrape_url", new_callable=AsyncMock)
@patch("services.info.jarvis_researcher.jarvis_search.search_urls", new_callable=AsyncMock)
async def test_perform_web_research_success(
    mock_search_urls: AsyncMock,
    mock_scrape: AsyncMock,
    mock_llm: AsyncMock,
) -> None:
    mock_search_urls.return_value = [
        {"title": "Solar A", "url": "https://a.example/solar", "snippet": "snippet a"},
        {"title": "Solar B", "url": "https://b.example/solar", "snippet": "snippet b"},
    ]
    mock_scrape.side_effect = ["content one " * 20, "content two " * 20]
    mock_llm.return_value = LLMResult(
        text="Yeh solar inverters ki report hai.\n\nKey point [1].",
        provider="gemini",
        model="test",
    )

    result = await researcher.perform_web_research("best solar inverter 2026")

    assert result["status"] == "success"
    assert result["query"] == "best solar inverter 2026"
    assert "solar" in result["report"].lower()
    assert len(result["sources"]) == 2
    assert result["sources"][0]["n"] == 1
    assert "[INSTRUCTION]" not in result["message"]
    assert "Sources:" in result["message"]
    assert "https://a.example/solar" in result["message"]
    mock_scrape.assert_called()
    mock_llm.assert_awaited_once()


@pytest.mark.asyncio
@patch("services.info.jarvis_researcher.generate_text", new_callable=AsyncMock)
@patch("services.info.jarvis_researcher.scrape_url", new_callable=AsyncMock)
@patch("services.info.jarvis_researcher.jarvis_search.search_urls", new_callable=AsyncMock)
async def test_perform_web_research_partial_when_llm_down(
    mock_search_urls: AsyncMock,
    mock_scrape: AsyncMock,
    mock_llm: AsyncMock,
) -> None:
    mock_search_urls.return_value = [
        {"title": "One", "url": "https://one.test", "snippet": "s1"},
    ]
    mock_scrape.return_value = "scraped body text"
    mock_llm.side_effect = LLMUnavailableError(["all providers down"])

    result = await researcher.perform_web_research("topic")

    assert result["status"] == "partial"
    assert "excerpts" in result["report"].lower() or "LLM unavailable" in result["report"]
    assert result["sources"]


@pytest.mark.asyncio
@patch("services.info.jarvis_researcher.send_email", new_callable=AsyncMock)
@patch("services.info.jarvis_researcher._run_research", new_callable=AsyncMock)
async def test_research_email_job_body_has_report_not_instruction(
    mock_run: AsyncMock,
    mock_send: AsyncMock,
) -> None:
    mock_run.return_value = {
        "status": "success",
        "query": "q",
        "report": "Roman Urdu report line one.\nDoosri line.",
        "sources": [{"n": 1, "title": "Src", "url": "https://src.test"}],
        "message": "ignored",
    }
    mock_send.return_value = {"status": "success", "message": "sent"}

    ctx = MagicMock()
    ctx.progress = AsyncMock()

    out = await researcher._research_email_job(ctx, "q", "owner@example.com", "Research Report")

    assert out["status"] == "success"
    mock_send.assert_awaited_once()
    body = mock_send.await_args.args[2]
    assert "Roman Urdu report" in body
    assert "Sources:" in body
    assert "https://src.test" in body
    assert "[INSTRUCTION]" not in body


@pytest.mark.asyncio
@patch("services.info.jarvis_researcher._jobs_module")
async def test_autonomous_research_starts_background_job(mock_jobs_mod: MagicMock) -> None:
    fake_jobs = MagicMock()
    fake_jobs.start_job.return_value = "job-research-1"
    mock_jobs_mod.return_value = fake_jobs

    result = await researcher.autonomous_research_and_email(
        "Pakistan solar",
        "owner@example.com",
        subject="Report",
    )

    assert result["status"] == "started"
    assert result["job_id"] == "job-research-1"
    assert "Research shuru" in result["message"]
    fake_jobs.start_job.assert_called_once()
    assert fake_jobs.start_job.call_args.args[0] == "research_email"
