#!/usr/bin/env python3
"""X-61: Info/media tools smoke harness (--dry mocks network; --live hits real providers).

Never imports WhatsApp, shutdown, mouse, keyboard, or SIM tools.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _bootstrap_env(*, dry: bool) -> None:
    """Keep smoke runs off the live VPS bridge and agent log files."""
    os.environ["UI_BRIDGE_URL"] = "http://127.0.0.1:9"
    log_dir = os.path.join(tempfile.gettempdir(), "jarvis_tests")
    os.makedirs(log_dir, exist_ok=True)
    os.environ.setdefault("LOG_DIR", log_dir)
    os.environ.setdefault("LOG_FILE", os.path.join(log_dir, "jarvis_smoke_info_media.log"))
    if dry:
        os.environ["JARVIS_TEST_MODE"] = "true"
        os.environ.setdefault("OPENWEATHER_API_KEY", "smoke-dry-openweather-not-real")
        os.environ.setdefault("WEATHER_API_KEY", os.environ["OPENWEATHER_API_KEY"])
        os.environ.setdefault("EMAIL_USER", "smoke-dry@example.com")
        os.environ.setdefault("EMAIL_APP_PASSWORD", "smoke-dry-app-password")


def _configure_bridge() -> None:
    try:
        from services.utils.jarvis_config import config

        config.bridge_url = "http://127.0.0.1:9"
        if hasattr(config, "ui_bridge_url"):
            config.ui_bridge_url = "http://127.0.0.1:9"
    except Exception:
        pass
    try:
        from services.utils.jarvis_logger import UIBridgeHandler

        manager = logging.Logger.manager
        names = [""] + [
            n for n in list(manager.loggerDict.keys()) if isinstance(logging.getLogger(n), logging.Logger)
        ]
        for name in names:
            for handler in list(logging.getLogger(name).handlers):
                if isinstance(handler, UIBridgeHandler):
                    logging.getLogger(name).removeHandler(handler)
    except Exception:
        pass


@dataclass
class SmokeRow:
    tool: str
    status: str
    ms: int
    provider: str
    detail: str = ""


def _provider_from_result(result: Any, *, dry: bool, fallback: str = "-") -> str:
    if isinstance(result, bytes):
        return "jarvis_test" if dry or os.environ.get("JARVIS_TEST_MODE") else "gTTS/pyttsx3"
    if isinstance(result, dict):
        prov = result.get("provider")
        if prov:
            return str(prov)
        tried = result.get("providers_tried")
        if isinstance(tried, list) and tried:
            return "+".join(str(x) for x in tried)
        model = result.get("model")
        if model:
            return str(model)
        engine = result.get("engine")
        if engine:
            return str(engine)
    return fallback


def _result_ok(result: Any) -> bool:
    if isinstance(result, bytes):
        return len(result) > 0
    if isinstance(result, str):
        text = result.strip()
        return bool(text) and not text.lower().startswith("maazrat")
    if isinstance(result, dict):
        status = str(result.get("status", "")).lower()
        if status in {"success", "started", "partial"}:
            return True
        if status in {"error", "not_found", "unavailable"}:
            return False
        if status:
            return False
        return bool(result.get("message") or result.get("formatted"))
    return result is not None


def _print_table(rows: list[SmokeRow], *, mode: str) -> None:
    headers = ("tool", "status", "ms", "provider")

    def _cell(row: SmokeRow, key: str) -> str:
        return str(getattr(row, key))

    widths = [max(len(h), *(len(_cell(r, h)) for r in rows)) for h in headers]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(f"info/media smoke ({mode})")
    print(line)
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(
            f"{row.tool.ljust(widths[0])}  "
            f"{row.status.ljust(widths[1])}  "
            f"{str(row.ms).rjust(widths[2])}  "
            f"{row.provider.ljust(widths[3])}"
        )
        if row.detail and row.status == "FAIL":
            print(f"  -> {row.detail[:240]}")


async def _timed(
    name: str,
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    dry: bool,
    provider_hint: str,
) -> SmokeRow:
    t0 = time.perf_counter()
    try:
        result = await coro_factory()
        ms = int((time.perf_counter() - t0) * 1000)
        ok = _result_ok(result)
        prov = _provider_from_result(result, dry=dry, fallback=provider_hint)
        detail = ""
        if isinstance(result, dict) and not ok:
            detail = str(result.get("message") or result.get("error") or result)
        return SmokeRow(name, "PASS" if ok else "FAIL", ms, prov, detail)
    except Exception as exc:  # noqa: BLE001 — smoke harness reports all failures
        ms = int((time.perf_counter() - t0) * 1000)
        return SmokeRow(name, "FAIL", ms, provider_hint, str(exc))


@contextmanager
def _dry_patches(shared_dir: Path) -> Iterator[None]:
    from services.ai_core.llm_text import LLMResult

    def fake_requests_get(url: str, *args: Any, **kwargs: Any) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        url_l = str(url).lower()
        if "openweathermap" in url_l:
            resp.json.return_value = {
                "weather": [{"description": "clear sky"}],
                "main": {"temp": 28.0, "humidity": 45},
                "wind": {"speed": 2.5},
            }
        elif "ipinfo" in url_l:
            resp.json.return_value = {"city": "Lahore", "loc": "31.5,74.3"}
        else:
            resp.json.return_value = {}
        resp.raise_for_status = MagicMock()
        return resp

    def fake_requests_post(url: str, *args: Any, **kwargs: Any) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        if "tavily" in str(url):
            resp.json.return_value = {
                "results": [{"title": "Smoke", "url": "https://example.com", "content": "snippet"}]
            }
        elif "pollinations" in str(url):
            resp.headers = {"Content-Type": "image/jpeg"}
            resp.content = b"\xff\xd8\xff" + b"\0" * 128
        else:
            resp.json.return_value = {}
        resp.raise_for_status = MagicMock()
        return resp

    async def fake_search_ddg(query: str) -> dict[str, Any]:
        return {
            "status": "success",
            "message": f"Dry search for {query}",
            "results": [{"title": "Example", "url": "https://example.com", "snippet": "dry"}],
            "providers_tried": ["duckduckgo"],
        }

    async def fake_get_city() -> str:
        return "Lahore"

    async def dry_generate_image(prompt: str, aspect_ratio: str = "1:1") -> dict[str, Any]:
        _ = prompt, aspect_ratio
        out = shared_dir / "dry_gen.png"
        out.write_bytes(b"\x89PNG\r\n\x1a\n")
        return {
            "status": "success",
            "path": str(out),
            "url": "http://127.0.0.1:9/dry.png",
            "provider": "dry-mock",
            "model": "dry",
            "message": "dry image ok",
        }

    stack = ExitStack()
    stack.enter_context(patch("requests.get", side_effect=fake_requests_get))
    stack.enter_context(patch("requests.post", side_effect=fake_requests_post))
    stack.enter_context(patch("services.info.jarvis_search.search_duckduckgo", new=fake_search_ddg))
    stack.enter_context(patch("services.info.jarvis_search.get_current_city", new=fake_get_city))
    stack.enter_context(patch("services.info.jarvis_get_weather.jarvis_search.get_current_city", new=fake_get_city))
    stack.enter_context(
        patch("services.multimedia.jarvis_image_gen.generate_image", new=dry_generate_image)
    )

    async def _search_urls(query: str, count: int = 5) -> list[dict[str, str]]:
        _ = query, count
        return [{"title": "Dry", "url": "https://dry.example/x", "snippet": "s"}]

    async def _scrape(_url: str, timeout: int = 15) -> str:
        _ = _url, timeout
        return "dry scraped content " * 20

    async def _llm(*_args: Any, **_kw: Any) -> LLMResult:
        return LLMResult(text="Dry Roman Urdu report [1].", provider="gemini", model="dry")

    stack.enter_context(
        patch(
            "services.info.jarvis_researcher.jarvis_search.search_urls",
            new=AsyncMock(side_effect=_search_urls),
        )
    )
    stack.enter_context(
        patch("services.info.jarvis_researcher.scrape_url", new=AsyncMock(side_effect=_scrape))
    )
    stack.enter_context(
        patch(
            "services.info.jarvis_researcher.generate_text",
            new=AsyncMock(side_effect=_llm),
        )
    )

    stack.enter_context(
        patch(
            "services.ai_core.jarvis_vision.vision_system.analyze_content",
            new=AsyncMock(return_value="Dry smoke: screen shows the desktop."),
        )
    )

    stack.enter_context(patch("services.multimedia.jarvis_qr_gen.config.shared_dir", str(shared_dir)))
    stack.enter_context(patch("services.multimedia.jarvis_qr_gen._emit_qr_update", new_callable=AsyncMock))

    async def _wait_ready() -> bool:
        return True

    async def _find_document(_name: str, _filt: Any = None) -> str:
        doc = shared_dir / "smoke_doc.txt"
        doc.write_text("Smoke document body for RAG dry run.", encoding="utf-8")
        return str(doc)

    async def _get_content(_path: str) -> str:
        return Path(_path).read_text(encoding="utf-8")

    def _select_context(full: str, _question: str) -> tuple[str, int]:
        return full[:200], 1

    from services.ai_core import jarvis_rag as rag_mod

    stack.enter_context(patch.object(rag_mod.rag_system, "wait_ready", new=_wait_ready))
    stack.enter_context(patch.object(rag_mod.rag_system, "find_document", new=_find_document))
    stack.enter_context(patch.object(rag_mod.rag_system, "get_document_content", new=_get_content))
    stack.enter_context(patch.object(rag_mod.rag_system, "select_context", side_effect=_select_context))

    stack.enter_context(patch("smtplib.SMTP", MagicMock()))
    stack.enter_context(
        patch(
            "services.ai_core.jarvis_confirm.confirmation_broker.request",
            new=AsyncMock(return_value=True),
        )
    )

    try:
        yield
    finally:
        stack.close()


@contextmanager
def _live_email_confirm_patch() -> Iterator[None]:
    with patch(
        "services.ai_core.jarvis_confirm.confirmation_broker.request",
        new=AsyncMock(return_value=True),
    ):
        yield


async def _run_suite(
    *,
    dry: bool,
    yt_url: str,
    email_to: str,
    shared_dir: Path,
) -> list[SmokeRow]:
    from services.ai_core import jarvis_rag as rag_mod
    from services.ai_core import jarvis_vision as vision_mod
    from services.info import jarvis_get_weather, jarvis_researcher, jarvis_search
    from services.multimedia import jarvis_audio, jarvis_image_gen, jarvis_qr_gen

    rows: list[SmokeRow] = []

    rows.append(
        await _timed(
            "get_formatted_datetime",
            lambda: jarvis_search.get_formatted_datetime(),
            dry=dry,
            provider_hint="local",
        )
    )
    rows.append(
        await _timed(
            "get_weather",
            lambda: jarvis_get_weather.get_weather("Lahore"),
            dry=dry,
            provider_hint="openweather" if not dry else "dry",
        )
    )
    rows.append(
        await _timed(
            "search_internet",
            lambda: jarvis_search.search_internet("JARVIS smoke query"),
            dry=dry,
            provider_hint="search",
        )
    )
    rows.append(
        await _timed(
            "perform_web_research",
            lambda: jarvis_researcher.perform_web_research("JARVIS smoke research"),
            dry=dry,
            provider_hint="research",
        )
    )
    rows.append(
        await _timed(
            "generate_image",
            lambda: jarvis_image_gen.generate_image("minimal red circle icon", "1:1"),
            dry=dry,
            provider_hint="image",
        )
    )
    rows.append(
        await _timed(
            "generate_qr_code",
            lambda: jarvis_qr_gen.generate_qr_code(
                "https://example.com/smoke", filename="smoke_qr.png", open_file=False
            ),
            dry=dry,
            provider_hint="local",
        )
    )
    rows.append(
        await _timed(
            "analyze_screen",
            lambda: vision_mod.analyze_screen("What is visible? One short sentence."),
            dry=dry,
            provider_hint="vision",
        )
    )
    rows.append(
        await _timed(
            "ask_about_document",
            lambda: rag_mod.ask_about_document("smoke_doc", "Summarize in one line."),
            dry=dry,
            provider_hint="rag",
        )
    )

    if hasattr(jarvis_audio, "generate_speech"):
        async def _speech() -> bytes:
            return jarvis_audio.generate_speech("JARVIS info media smoke", lang="en")

        rows.append(
            await _timed("generate_speech", _speech, dry=dry, provider_hint="speech")
        )

    if yt_url:
        from services.multimedia import jarvis_youtube_downloader as yt_mod

        if dry:

            async def _yt_dry() -> dict[str, Any]:
                return {"status": "success", "message": "dry youtube", "provider": "dry"}

            with patch.object(yt_mod.yt_downloader, "download", new=AsyncMock(side_effect=_yt_dry)):
                rows.append(
                    await _timed(
                        "download_youtube_media",
                        lambda: yt_mod.download_youtube_media(yt_url, "audio", auto_open=False),
                        dry=dry,
                        provider_hint="youtube",
                    )
                )
        else:
            rows.append(
                await _timed(
                    "download_youtube_media",
                    lambda: yt_mod.download_youtube_media(yt_url, "audio", auto_open=False),
                    dry=dry,
                    provider_hint="youtube",
                )
            )

    if email_to:
        from services.multimedia import jarvis_advanced_tools as adv

        async def _send() -> dict[str, Any]:
            return await adv.send_email(
                email_to,
                "JARVIS smoke",
                "Info/media smoke harness test message.\nLine two.",
            )

        ctx: Any = _live_email_confirm_patch() if not dry else nullcontext()
        with ctx:
            rows.append(await _timed("send_email", _send, dry=dry, provider_hint="smtp"))

    return rows


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test info/media tools (X-61).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry", action="store_true", help="Mock network; safe for CI.")
    mode.add_argument("--live", action="store_true", help="Call real providers (needs keys/network).")
    parser.add_argument("--yt", metavar="URL", default="", help="Optional YouTube URL to exercise download.")
    parser.add_argument(
        "--email-to",
        metavar="ADDR",
        default="",
        help="Optional recipient for send_email (uses ConfirmDialog in live mode).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dry = bool(args.dry)
    _bootstrap_env(dry=dry)
    _configure_bridge()

    shared_dir = Path(tempfile.mkdtemp(prefix="jarvis_smoke_im_"))
    shared_dir.mkdir(parents=True, exist_ok=True)

    patch_ctx: contextmanager[Any]
    if dry:
        patch_ctx = _dry_patches(shared_dir)
    else:
        patch_ctx = contextmanager(lambda: (yield))()

    with patch_ctx:
        if not dry:
            from services.multimedia import jarvis_qr_gen as qr_mod

            qr_mod.config.shared_dir = str(shared_dir)

        rows = asyncio.run(
            _run_suite(dry=dry, yt_url=args.yt.strip(), email_to=args.email_to.strip(), shared_dir=shared_dir)
        )

    mode = "dry" if dry else "live"
    _print_table(rows, mode=mode)
    failed = [r for r in rows if r.status != "PASS"]
    if failed:
        print(f"\n{len(failed)} tool(s) FAILED.")
        return 1
    print(f"\nAll {len(rows)} tool(s) PASS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
