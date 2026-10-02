"""
# jarvis_search.py
Jarvis Search Module

Provides internet search functionality using Tavily, Google Custom Search,
and DuckDuckGo, plus shared URL/image search helpers.
"""

import asyncio
import os
import time
from datetime import datetime
from urllib.parse import quote

import requests  # type: ignore
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils import jarvis_location
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import redact_secrets

# Setup logging
logger = setup_logger("JARVIS-SEARCH")

GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
TAVILY_SEARCH_DEPTH = os.getenv("TAVILY_SEARCH_DEPTH", "advanced")

_TAVILY_DISABLE_SECONDS = 600
_tavily_disabled_until: float = 0.0
_GOOGLE_DISABLED_REASON: str | None = None
_GOOGLE_DISABLE_MSG = (
    "Custom Search JSON API not enabled; closed to new customers, ends 2027-01-01"
)

SEARCH_MESSAGE_MAX_CHARS = 6000
_HTTP_TIMEOUT = 15


def _tavily_available() -> bool:
    return bool(TAVILY_API_KEY) and time.monotonic() >= _tavily_disabled_until


def _disable_tavily() -> None:
    global _tavily_disabled_until
    _tavily_disabled_until = time.monotonic() + _TAVILY_DISABLE_SECONDS
    logger.warning("Tavily disabled for %s seconds after auth/rate-limit response.", _TAVILY_DISABLE_SECONDS)


def _google_available() -> bool:
    return bool(GOOGLE_SEARCH_API_KEY and SEARCH_ENGINE_ID) and _GOOGLE_DISABLED_REASON is None


def _disable_google(reason: str | None = None) -> None:
    global _GOOGLE_DISABLED_REASON
    _GOOGLE_DISABLED_REASON = reason or _GOOGLE_DISABLE_MSG
    logger.warning("Google Custom Search disabled for this process: %s", _GOOGLE_DISABLED_REASON)


def _cap_message(message: str) -> str:
    if len(message) <= SEARCH_MESSAGE_MAX_CHARS:
        return message
    return message[:SEARCH_MESSAGE_MAX_CHARS] + "...[truncated]"


def _safe_error_text(exc: BaseException) -> str:
    return redact_secrets(str(exc))


def _log_http_failure(provider: str, exc: BaseException) -> None:
    status = None
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
    reason = _safe_error_text(exc)
    if status is not None:
        logger.warning("%s search failed: HTTP %s %s", provider, status, reason)
    else:
        logger.warning("%s search failed: %s", provider, reason)


def _default_city_data() -> dict[str, float | str]:
    city = (os.getenv("USER_CITY") or "").strip() or jarvis_location.DEFAULT_CITY
    return {"city": city, "lat": jarvis_location.DEFAULT_LAT, "lng": jarvis_location.DEFAULT_LNG}


def _desktop_mode_remote() -> bool:
    return (os.getenv("JARVIS_DESKTOP_MODE") or "").strip().lower() == "remote"


async def get_current_city_data() -> dict:
    """
    Device location from the Desktop Node cache when fresh; else ipinfo on local mode only.
    Returns: {"city": str, "lat": float, "lng": float}
    """
    default_data = _default_city_data()
    device = jarvis_location.get_device_location()
    if device is not None:
        return {"city": device["city"], "lat": device["lat"], "lng": device["lng"]}
    if _desktop_mode_remote():
        return default_data
    try:
        response = await asyncio.to_thread(requests.get, "https://ipinfo.io/json", timeout=5)
        data = response.json()

        loc = data.get("loc", "").split(",")
        lat = float(loc[0]) if len(loc) == 2 else default_data["lat"]
        lng = float(loc[1]) if len(loc) == 2 else default_data["lng"]

        detected_city = data.get("city", default_data["city"])

        if detected_city.lower() in ["unknown", "", "none"]:
            detected_city = str(default_data["city"])

        return {
            "city": detected_city,
            "lat": lat,
            "lng": lng,
        }
    except (requests.RequestException, ValueError, KeyError, OSError, RuntimeError) as e:
        logger.warning("Error getting current location: %s", _safe_error_text(e))
        return default_data


async def get_current_city() -> str:
    """Wrapper for legacy code expecting only string."""
    data = await get_current_city_data()
    return data["city"]


def _success_payload(
    provider: str,
    results: list,
    message: str,
    *,
    providers_tried: list[str] | None = None,
    query: str | None = None,
) -> dict:
    payload: dict = {
        "status": "success",
        "provider": provider,
        "results": results,
        "message": _cap_message(message),
    }
    if providers_tried is not None:
        payload["providers_tried"] = providers_tried
    if query is not None:
        payload["query"] = query
    return payload


@jarvis_tool(execution_timeout=40)
async def search_internet(query: str) -> dict:
    """
    Perform a high-quality internet search.
    Cascades through: Tavily -> Google Custom Search -> DuckDuckGo.
    """
    error_messages: list[str] = []
    providers_tried: list[str] = []

    if _tavily_available():
        providers_tried.append("tavily")
        try:
            tavily_result = await search_tavily(query)
            if tavily_result["status"] == "success":
                tavily_result["providers_tried"] = providers_tried
                tavily_result["message"] = _cap_message(tavily_result.get("message", ""))
                return tavily_result
            error_messages.append(f"Tavily: {redact_secrets(tavily_result.get('message', ''))}")
        except Exception as e:
            error_messages.append(f"Tavily Exception: {_safe_error_text(e)}")

    if _google_available():
        providers_tried.append("google")
        try:
            google_result = await search_google(query)
            if google_result["status"] == "success":
                google_result["providers_tried"] = providers_tried
                google_result["message"] = _cap_message(google_result.get("message", ""))
                return google_result
            error_messages.append(f"Google: {redact_secrets(google_result.get('message', ''))}")
        except Exception as e:
            error_messages.append(f"Google Exception: {_safe_error_text(e)}")

    providers_tried.append("duckduckgo")
    ddg_result = await search_duckduckgo(query)
    if ddg_result["status"] == "success":
        ddg_result["providers_tried"] = providers_tried
        ddg_result["message"] = _cap_message(ddg_result.get("message", ""))
        return ddg_result

    error_messages.append(f"DuckDuckGo: {redact_secrets(ddg_result.get('message', ''))}")

    return {
        "status": "error",
        "message": _cap_message(
            "❌ Sir, tamam search engines (Tavily, Google, DDG) failed ho gaye hain. "
            "Shayad internet connection ya API limits ka masla hai."
        ),
        "details": "; ".join(error_messages),
        "providers_tried": providers_tried,
    }


async def search_tavily(query: str) -> dict:
    """Uses Tavily API for smart research."""
    logger.info("Searching Tavily for: '%s'", query)
    url = "https://api.tavily.com/search"
    headers = {"Authorization": f"Bearer {TAVILY_API_KEY}"}
    payload = {
        "query": query,
        "search_depth": TAVILY_SEARCH_DEPTH,
        "max_results": 5,
    }

    try:
        response = await asyncio.to_thread(
            requests.post, url, json=payload, headers=headers, timeout=_HTTP_TIMEOUT
        )
        if response.status_code in (401, 429, 432, 433):
            _disable_tavily()
        response.raise_for_status()
        data = response.json()

        results = data.get("results", [])
        if not results:
            return {"status": "not_found", "message": "No results found on Tavily."}

        summary = "\n\n".join(
            [f"[{i+1}] {r['title']}\n{r['content']}\nSource: {r['url']}" for i, r in enumerate(results)]
        )
        return _success_payload(
            "tavily",
            results,
            f"[TAVILY INTELLIGENCE]\n{summary}",
        )
    except Exception as e:
        if isinstance(e, requests.HTTPError) and e.response is not None:
            if e.response.status_code in (401, 429, 432, 433):
                _disable_tavily()
        _log_http_failure("Tavily", e)
        return {"status": "error", "message": _safe_error_text(e)}


async def search_google(query: str) -> dict:
    """Perform a Google Custom Search."""
    if not _google_available():
        reason = _GOOGLE_DISABLED_REASON or "Google Custom Search unavailable."
        return {"status": "error", "message": reason}

    logger.info("Searching Google for: '%s'", query)
    url = (
        f"https://www.googleapis.com/customsearch/v1"
        f"?cx={SEARCH_ENGINE_ID}&q={quote(query)}"
    )
    headers = {"x-goog-api-key": GOOGLE_SEARCH_API_KEY or ""}

    try:
        response = await asyncio.to_thread(
            requests.get, url, headers=headers, timeout=_HTTP_TIMEOUT
        )
        if response.status_code in (400, 403):
            _disable_google()
        response.raise_for_status()
        data = response.json()

        if "items" not in data:
            return {"status": "not_found", "message": "No Google results found."}

        results = []
        for item in data["items"][:5]:
            results.append({
                "title": item.get("title", "No title"),
                "snippet": item.get("snippet", ""),
                "link": item.get("link", ""),
            })

        summary = "\n\n".join(
            [f"{r['title']}\n{r['snippet']}\n{r['link']}" for r in results]
        )
        return _success_payload(
            "google",
            results,
            f"[GOOGLE SEARCH]\n{summary}",
        )
    except Exception as e:
        if isinstance(e, requests.HTTPError) and e.response is not None:
            if e.response.status_code in (400, 403):
                _disable_google()
        _log_http_failure("Google", e)
        return {"status": "error", "message": _safe_error_text(e)}


async def search_duckduckgo(query: str) -> dict:
    """Fallback search using DuckDuckGo."""
    logger.info("Attempting DuckDuckGo fallback for: '%s'", query)
    try:
        def _ddgs_sync():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=5))

        results = await asyncio.to_thread(_ddgs_sync)
        if not results:
            return {"status": "not_found", "message": "DuckDuckGo returned no results."}

        formatted_results = []
        for r in results:
            formatted_results.append({
                "title": r.get("title", "No Title"),
                "snippet": r.get("body", ""),
                "link": r.get("href", ""),
            })

        summary = "\n\n".join(
            [f"{r['title']}\n{r['snippet']}\n{r['link']}" for r in formatted_results]
        )
        return _success_payload(
            "duckduckgo",
            formatted_results,
            f"[DUCKDUCKGO BACKUP]\n{summary}",
            query=query,
        )
    except Exception as e:
        _log_http_failure("DuckDuckGo", e)
        return {"status": "error", "message": _safe_error_text(e)[:200]}


def _urls_from_tavily_results(results: list, count: int) -> list[dict]:
    out: list[dict] = []
    for r in results[:count]:
        out.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
            "content": r.get("content"),
        })
    return out


def _urls_from_google_results(results: list, count: int) -> list[dict]:
    out: list[dict] = []
    for r in results[:count]:
        out.append({
            "title": r.get("title", ""),
            "url": r.get("link", ""),
            "snippet": r.get("snippet", ""),
        })
    return out


def _urls_from_ddg_results(results: list, count: int) -> list[dict]:
    out: list[dict] = []
    for r in results[:count]:
        out.append({
            "title": r.get("title", ""),
            "url": r.get("href", ""),
            "snippet": r.get("body", ""),
        })
    return out


async def search_urls(query: str, count: int = 5) -> list[dict]:
    """Return normalized URL hits from Tavily, Google, then DuckDuckGo."""
    count = max(1, min(int(count), 30))

    if _tavily_available():
        tavily = await search_tavily(query)
        if tavily.get("status") == "success":
            urls = _urls_from_tavily_results(tavily.get("results") or [], count)
            if urls:
                return urls

    if _google_available():
        google = await search_google(query)
        if google.get("status") == "success":
            urls = _urls_from_google_results(google.get("results") or [], count)
            if urls:
                return urls

    def _ddgs_sync():
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=count))

    try:
        ddg_raw = await asyncio.to_thread(_ddgs_sync)
        urls = _urls_from_ddg_results(ddg_raw, count)
        if urls:
            return urls
    except Exception as e:
        _log_http_failure("DuckDuckGo", e)

    return []


async def _search_images_ddg(query: str, count: int) -> list[str]:
    def _sync():
        urls: list[str] = []
        with DDGS() as ddgs:
            for row in ddgs.images(query, max_results=count):
                img = row.get("image") or row.get("url")
                if img:
                    urls.append(img)
        return urls

    try:
        return await asyncio.to_thread(_sync)
    except Exception as e:
        _log_http_failure("DuckDuckGo images", e)
        return []


async def _search_images_tavily(query: str, count: int) -> list[str]:
    if not _tavily_available():
        return []
    url = "https://api.tavily.com/search"
    headers = {"Authorization": f"Bearer {TAVILY_API_KEY}"}
    payload = {
        "query": query,
        "search_depth": TAVILY_SEARCH_DEPTH,
        "max_results": min(count, 5),
        "include_images": True,
    }
    try:
        response = await asyncio.to_thread(
            requests.post, url, json=payload, headers=headers, timeout=_HTTP_TIMEOUT
        )
        if response.status_code in (401, 429, 432, 433):
            _disable_tavily()
        response.raise_for_status()
        data = response.json()
        images: list[str] = []
        for img in data.get("images") or []:
            if isinstance(img, str):
                images.append(img)
            elif isinstance(img, dict):
                u = img.get("url") or img.get("image")
                if u:
                    images.append(u)
        if not images:
            for row in data.get("results") or []:
                for key in ("image", "image_url"):
                    if row.get(key):
                        images.append(row[key])
        return images[:count]
    except Exception as e:
        if isinstance(e, requests.HTTPError) and e.response is not None:
            if e.response.status_code in (401, 429, 432, 433):
                _disable_tavily()
        _log_http_failure("Tavily images", e)
        return []


async def _search_images_google(query: str, count: int) -> list[str]:
    if not _google_available():
        return []
    url = (
        f"https://www.googleapis.com/customsearch/v1"
        f"?cx={SEARCH_ENGINE_ID}&q={quote(query)}&searchType=image&num={min(count, 10)}"
    )
    headers = {"x-goog-api-key": GOOGLE_SEARCH_API_KEY or ""}
    try:
        response = await asyncio.to_thread(
            requests.get, url, headers=headers, timeout=_HTTP_TIMEOUT
        )
        if response.status_code in (400, 403):
            _disable_google()
        response.raise_for_status()
        data = response.json()
        return [item["link"] for item in data.get("items", []) if item.get("link")][:count]
    except Exception as e:
        if isinstance(e, requests.HTTPError) and e.response is not None:
            if e.response.status_code in (400, 403):
                _disable_google()
        _log_http_failure("Google images", e)
        return []


async def search_images(query: str, count: int) -> list[str]:
    """Image URLs: DuckDuckGo, then Tavily (include_images), then Google CSE."""
    count = max(1, min(int(count), 30))
    for fetcher in (_search_images_ddg, _search_images_tavily, _search_images_google):
        urls = await fetcher(query, count)
        if urls:
            return urls[:count]
    return []


@jarvis_tool
async def get_formatted_datetime() -> dict:
    """
    Get the current date and time in a human-readable formatted string.
    Example: "Thursday, November 13, 2025 - 07:25 PM"
    """
    now = datetime.now()
    return {
        "formatted": now.strftime("%A, %B %d, %Y - %I:%M %p"),
        "day": now.strftime("%A"),
        "date": now.strftime("%B %d, %Y"),
        "time": now.strftime("%I:%M %p"),
    }
