"""
# jarvis_researcher.py
Autonomous Web Research Module for JARVIS.
Extracts and synthesizes information from multiple websites.
"""

from __future__ import annotations

import asyncio
from typing import Any

import requests
from bs4 import BeautifulSoup

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.ai_core.llm_text import LLMUnavailableError, generate_text
from services.info import jarvis_search
from services.multimedia.jarvis_advanced_tools import send_email
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import ArgPolicyError, VortexGuard

logger = setup_logger("JARVIS-RESEARCHER")

_SCRAPE_TIMEOUT = 8
_SCRAPE_CONCURRENCY = 5
_SOURCE_TEXT_CAP = 3500
_RESEARCH_SYSTEM = (
    "You are JARVIS research analyst. Write in natural Roman Urdu with English technical terms "
    "where needed. Include a short summary, key points, and a comparison table when useful. "
    "Cite sources inline as [n] matching the numbered sources in the prompt."
)

_scrape_sem = asyncio.Semaphore(_SCRAPE_CONCURRENCY)


def _parse_html(html_text: str) -> str:
    """Helper to parse HTML in a separate thread to prevent blocking event loop."""
    soup = BeautifulSoup(html_text, "html.parser")

    for script_or_style in soup(["script", "style"]):
        script_or_style.decompose()

    text = soup.get_text()
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = "\n".join(chunk for chunk in chunks if chunk)
    return text[:4000]


async def scrape_url(url: str, timeout: int = _SCRAPE_TIMEOUT) -> str:
    """Extract clean text content from a URL."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            ),
        }
        response = None
        for attempt in range(2):
            try:
                response = await asyncio.to_thread(
                    requests.get, url, headers=headers, timeout=timeout,
                )
                response.raise_for_status()
                break
            except (TimeoutError, requests.RequestException):
                if attempt == 1:
                    raise
                await asyncio.sleep(1)

        if response is None:
            return ""

        return await asyncio.to_thread(_parse_html, response.text)
    except (requests.RequestException, ValueError, AttributeError,
            KeyError, OSError, RuntimeError) as e:
        logger.error("Error scraping %s: %s", url, e)
        return ""


async def get_search_urls(query: str, count: int = 5) -> list[str]:
    """Get top URLs from the shared search chain (Tavily, Google, DuckDuckGo)."""
    hits = await jarvis_search.search_urls(query, count=count)
    return [str(h.get("url", "")).strip() for h in hits if h.get("url")]


async def _scrape_bounded(url: str) -> str:
    async with _scrape_sem:
        return await scrape_url(url, timeout=_SCRAPE_TIMEOUT)


async def _tavily_extract(urls: list[str]) -> dict[str, str]:
    api_key = jarvis_search.TAVILY_API_KEY
    if not api_key or not urls:
        return {}

    endpoint = "https://api.tavily.com/extract"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"urls": urls[:5], "extract_depth": "basic"}

    try:
        response = await asyncio.to_thread(
            requests.post, endpoint, json=payload, headers=headers, timeout=15,
        )
        if response.status_code in (401, 429, 432, 433):
            jarvis_search._disable_tavily()  # pylint: disable=protected-access
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError, KeyError, RuntimeError) as e:
        logger.warning("Tavily extract failed: %s", e)
        return {}

    out: dict[str, str] = {}
    for row in data.get("results") or []:
        link = str(row.get("url") or "").strip()
        body = str(row.get("raw_content") or row.get("content") or "").strip()
        if link and body:
            out[link] = body[: _SOURCE_TEXT_CAP]
    return out


def _snippet_from_hit(hit: dict[str, Any]) -> str:
    for key in ("content", "snippet"):
        text = str(hit.get(key) or "").strip()
        if text:
            return text[: _SOURCE_TEXT_CAP]
    return ""


def _numbered_sources(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    n = 0
    for hit in hits:
        url = str(hit.get("url") or "").strip()
        if not url:
            continue
        n += 1
        title = str(hit.get("title") or f"Source {n}").strip() or f"Source {n}"
        sources.append({"n": n, "title": title, "url": url})
    return sources


def _hits_for_sources(hits: list[dict[str, Any]], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url = {str(h.get("url") or "").strip(): h for h in hits if h.get("url")}
    return [by_url[s["url"]] for s in sources if s["url"] in by_url]


def _format_sources_block(sources: list[dict[str, Any]]) -> str:
    lines = ["Sources:"]
    for s in sources:
        lines.append(f"[{s['n']}] {s['title']} — {s['url']}")
    return "\n".join(lines)


def _format_message(report: str, sources: list[dict[str, Any]]) -> str:
    report = (report or "").strip()
    if not sources:
        return report
    block = _format_sources_block(sources)
    return f"{report}\n\n{block}" if report else block


def _build_llm_prompt(query: str, sources: list[dict[str, Any]],
                      bodies: list[str]) -> str:
    chunks: list[str] = [f"Research query: {query}", "", "Source material:"]
    for src, body in zip(sources, bodies, strict=False):
        if not body:
            continue
        capped = body[: _SOURCE_TEXT_CAP]
        chunks.append(f"[{src['n']}] {src['title']} ({src['url']})\n{capped}\n")
    chunks.append(
        "Write the final research report for Sir in Roman Urdu (English technical terms OK)."
    )
    return "\n".join(chunks)


def _partial_report(bodies: list[str], sources: list[dict[str, Any]]) -> str:
    excerpts: list[str] = []
    for body, src in zip(bodies, sources, strict=False):
        if not body:
            continue
        excerpts.append(f"[{src['n']}] {src['title']}\n{body[:800]}")
        if len(excerpts) >= 3:
            break
    if not excerpts:
        return "Maaf Sir, research ke liye kaafi content nahi mil saka."
    return "LLM unavailable — top excerpts:\n\n" + "\n\n".join(excerpts)


async def _collect_bodies(
    hits: list[dict[str, Any]],
    scraped: list[str],
) -> list[str]:
    urls = [str(h.get("url") or "").strip() for h in hits if h.get("url")]
    good_pages = sum(1 for text in scraped if text.strip())

    extract_map: dict[str, str] = {}
    if good_pages < 2 and urls:
        extract_map = await _tavily_extract(urls)

    bodies: list[str] = []
    for hit, scraped_text in zip(hits, scraped, strict=False):
        url = str(hit.get("url") or "").strip()
        text = (scraped_text or "").strip()
        if not text and url:
            text = extract_map.get(url, "").strip()
        if not text:
            text = _snippet_from_hit(hit)
        bodies.append(text[: _SOURCE_TEXT_CAP] if text else "")
    return bodies


async def _run_research(query: str) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {
            "status": "error",
            "query": query,
            "report": "",
            "sources": [],
            "message": "Research ke liye query khali hai.",
        }

    hits = await jarvis_search.search_urls(query, count=5)
    sources = _numbered_sources(hits)
    if not sources:
        msg = "Maaf Sir, main research ke liye links nahi dhoond paya."
        return {
            "status": "error",
            "query": query,
            "report": "",
            "sources": [],
            "message": msg,
        }

    ordered_hits = _hits_for_sources(hits, sources)
    urls = [s["url"] for s in sources]
    scraped = await asyncio.gather(*[_scrape_bounded(u) for u in urls])
    bodies = await _collect_bodies(ordered_hits, list(scraped))

    if not any(b.strip() for b in bodies):
        msg = "Maaf Sir, links to mile magar contents extract nahi ho sakay."
        return {
            "status": "error",
            "query": query,
            "report": "",
            "sources": sources,
            "message": msg,
        }

    prompt = _build_llm_prompt(query, sources, bodies)
    try:
        llm = await generate_text(
            prompt,
            system=_RESEARCH_SYSTEM,
            max_output_tokens=1800,
            timeout=60.0,
        )
        report = (llm.text or "").strip()
        status = "success"
    except LLMUnavailableError as e:
        logger.warning("Research synthesis LLM unavailable: %s", e)
        report = _partial_report(bodies, sources)
        status = "partial"

    message = _format_message(report, sources)
    return {
        "status": status,
        "query": query,
        "report": report,
        "sources": sources,
        "message": message,
    }


def _jobs_module() -> Any | None:
    try:
        from services.utils import jarvis_jobs  # pylint: disable=import-outside-toplevel

        if callable(getattr(jarvis_jobs, "start_job", None)):
            return jarvis_jobs
    except ImportError:
        pass
    return None


async def _research_email_job(
    ctx: Any,
    query: str,
    recipient: str,
    subject: str,
) -> dict[str, Any]:
    await ctx.progress(5, "Searching web…")
    result = await _run_research(query)
    if result["status"] == "error":
        return {
            "status": "error",
            "message": result.get("message") or "Research fail ho gayi.",
        }

    n_src = len(result.get("sources") or [])
    await ctx.progress(40, f"Reading {n_src} sources…")
    await ctx.progress(70, "Writing report…")

    try:
        clean_recipient = VortexGuard.sanitize_arg(recipient, "email_list")
    except ArgPolicyError as e:
        return {"status": "error", "message": str(e.message if hasattr(e, "message") else e)}

    body = _format_message(result.get("report") or "", result.get("sources") or [])
    await ctx.progress(85, "Sending email…")
    email_result = await send_email(clean_recipient, subject, body)

    if email_result.get("status") == "success":
        return {
            "status": "success",
            "query": query,
            "recipient": clean_recipient,
            "message": f"Research report '{clean_recipient}' ko bhej di gayi hai.",
        }
    return {
        "status": "error",
        "message": email_result.get("message") or "Email bhejne mein masla aaya.",
    }


@jarvis_tool(execution_timeout=120)
async def perform_web_research(query: str) -> dict[str, Any]:
    """
    Perform deep web research on a query by analyzing multiple websites.
    Returns a synthesized Roman-Urdu report with numbered sources.
    """
    logger.info("Starting deep research for query: %s", query)
    return await _run_research(query)


@jarvis_tool(
    execution_timeout=20,
    arg_policies={"recipient": "email_list", "subject": "plain", "query": "plain"},
    risk="high",
    confirm_summary="Research {query} karke report {recipient} ko email karni hai.",
)
async def autonomous_research_and_email(
    query: str,
    recipient: str,
    subject: str = "Research Report",
) -> dict[str, Any]:
    """
    Performs deep research on a topic, synthesizes it, and sends it via email
    as a background job after dashboard approval.
    """
    logger.info("Starting Agentic Workflow: Research + Email for %s", query)

    jobs = _jobs_module()
    title = f"Research: {(query or '')[:60]}"

    async def factory(ctx: Any) -> dict[str, Any]:
        return await _research_email_job(ctx, query, recipient, subject)

    if jobs is not None:
        job_id = jobs.start_job("research_email", title, factory, timeout=600, announce=True)
        return {
            "status": "started",
            "job_id": job_id,
            "message": "Research shuru... report tayyar hote hi email kar dunga.",
        }

    class _NoProgress:
        async def progress(self, *_a: object, **_k: object) -> None:
            return None

    job_result = await _research_email_job(_NoProgress(), query, recipient, subject)
    if job_result.get("status") == "success":
        return {
            "status": "success",
            "query": query,
            "recipient": recipient,
            "message": job_result.get("message"),
        }
    return {
        "status": "error",
        "message": job_result.get("message") or "Research email workflow fail ho gayi.",
    }
