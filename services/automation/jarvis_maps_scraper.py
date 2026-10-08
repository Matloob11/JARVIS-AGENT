import asyncio
import glob
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Optional

import pandas as pd
from playwright.async_api import async_playwright

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.system.jarvis_desktop_common import tool_result
from services.utils.jarvis_bridge import notify_thinking, notify_tool_action
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-MAPS")
_bg_tasks: set[asyncio.Task[Any]] = set()

PLAYWRIGHT_INSTALL_HINT = "./.venv_312/Scripts/python.exe -m playwright install chromium"
LEADS_ALLOWED_EXTS = frozenset({".csv", ".json", ".xlsx"})
RESULT_LIST_SELECTOR = 'a.hfpxzc, a[href*="/maps/place/"]'
CONSENT_BUTTONS = (
    'button:has-text("Reject all")',
    'button[aria-label="Reject all"]',
    'button:has-text("Accept all")',
    'button[aria-label="Accept all"]',
)


class BrowserLaunchError(Exception):
    """Playwright could not launch Chrome or bundled Chromium."""


def _outputs_dir() -> str:
    root = getattr(config, "project_root", os.getcwd())
    return str(getattr(config, "outputs_dir", None) or os.path.join(root, "Jarvis_Outputs"))


def _leads_dir() -> str:
    path = os.path.join(_outputs_dir(), "leads")
    os.makedirs(path, exist_ok=True)
    return path


def _clamp_lead_count(count: int) -> int:
    try:
        n = int(count)
    except (TypeError, ValueError):
        n = 10
    return max(1, min(60, n))


def _jobs_module():
    try:
        from services.utils import jarvis_jobs  # pylint: disable=import-outside-toplevel

        return jarvis_jobs
    except ImportError:
        return None


async def _safe_notify(notify_func, *args, **kwargs) -> None:
    try:
        t = asyncio.create_task(notify_func(*args, **kwargs))
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
    except Exception as exc:
        logger.debug("notify skipped: %s", exc)


async def _launch_chromium(playwright, *, headless: bool):
    channel = getattr(config, "playwright_channel", None) or "chrome"
    launch_kw: dict[str, Any] = {"headless": headless, "slow_mo": 50}
    try:
        return await playwright.chromium.launch(channel=channel, **launch_kw)
    except Exception as exc:
        logger.warning("Playwright channel %r launch failed: %s", channel, exc)
    try:
        return await playwright.chromium.launch(**launch_kw)
    except Exception as exc:
        logger.warning("Playwright bundled chromium launch failed: %s", exc)
        raise BrowserLaunchError(PLAYWRIGHT_INSTALL_HINT) from exc


@dataclass
class Place:
    name: str = ""
    address: str = ""
    website: str = ""
    phone: str = ""
    rating: Optional[float] = None
    reviews: Optional[int] = None
    email: str = ""
    socials: str = ""


class MapsScraper:
    def __init__(self) -> None:
        self.output_dir = _leads_dir()

    async def _dismiss_consent(self, page) -> None:
        for selector in CONSENT_BUTTONS:
            try:
                loc = page.locator(selector).first
                if await loc.is_visible(timeout=2000):
                    await loc.click(timeout=3000)
                    return
            except Exception as exc:
                logger.debug("consent button %r: %s", selector, exc)

    async def _extract_first(self, page, selectors: list[str], *, timeout_ms: int = 4000) -> str:
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                await loc.wait_for(state="attached", timeout=timeout_ms)
                if await loc.count() <= 0:
                    continue
                text = (await loc.inner_text()).strip()
                if text:
                    return text
                aria = await loc.get_attribute("aria-label")
                if aria:
                    return aria.strip()
            except Exception as exc:
                logger.debug("selector %r: %s", selector, exc)
        return ""

    async def scrape_leads(
        self,
        category: str,
        location: str,
        total: int,
        headless: bool = False,
        *,
        on_progress: Optional[Callable[[int, int, str], Awaitable[None]]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> list[Place]:
        query = f"{category} in {location}" if location else category
        results: list[Place] = []
        total = _clamp_lead_count(total)

        async with async_playwright() as playwright:
            browser = await _launch_chromium(playwright, headless=headless)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()
            try:
                await page.goto("https://www.google.com/maps", timeout=60000)
                await self._dismiss_consent(page)

                search_box = page.locator('input#searchboxinput, input[role="combobox"]').first
                await search_box.fill(query)
                await page.keyboard.press("Enter")

                try:
                    await page.wait_for_selector(RESULT_LIST_SELECTOR, timeout=15000)
                except Exception as exc:
                    logger.info("No map results for %r: %s", query, exc)
                    return []

                prev_count = 0
                while len(await page.locator(RESULT_LIST_SELECTOR).all()) < total:
                    if is_cancelled and is_cancelled():
                        break
                    await page.locator(RESULT_LIST_SELECTOR).first.hover()
                    await page.mouse.wheel(0, 2000)
                    await asyncio.sleep(1)
                    count = len(await page.locator(RESULT_LIST_SELECTOR).all())
                    if count == prev_count:
                        break
                    prev_count = count

                listings = (await page.locator(RESULT_LIST_SELECTOR).all())[:total]

                for i, listing in enumerate(listings):
                    if is_cancelled and is_cancelled():
                        break
                    try:
                        await listing.scroll_into_view_if_needed()
                        await listing.click()
                        await page.locator("h1.DUwDvf, h1").first.wait_for(
                            state="attached", timeout=10000
                        )

                        place = Place(
                            name=await self._extract_first(
                                page, ["h1.DUwDvf", "h1"], timeout_ms=8000
                            ),
                            address=await self._extract_first(
                                page,
                                [
                                    'button[data-item-id="address"] .Io6YTe',
                                    '[data-item-id="address"]',
                                ],
                            ),
                            website=await self._extract_first(
                                page,
                                [
                                    'a[data-item-id="authority"] .Io6YTe',
                                    'a[data-item-id="authority"]',
                                ],
                            ),
                            phone=await self._extract_first(
                                page,
                                [
                                    'button[data-item-id^="phone:tel:"] .Io6YTe',
                                    '[data-item-id^="phone:tel:"]',
                                ],
                            ),
                        )
                        if place.website and not place.website.startswith("http"):
                            place.website = "https://" + place.website

                        results.append(place)
                        if on_progress is not None:
                            await on_progress(i + 1, total, place.name)
                        await _safe_notify(
                            notify_thinking,
                            f"Sir, {i + 1} leads collect ho chuki hain: {place.name}",
                        )
                    except Exception as exc:
                        logger.warning("Error scraping listing %s: %s", i, exc)
                        continue
            finally:
                await browser.close()

        return results

    def save_results(self, places: list[Place], filename: str, fmt: str = "csv") -> Optional[str]:
        if not places:
            return None

        output_dir = _leads_dir()
        df = pd.DataFrame([asdict(p) for p in places])
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r"[^\w\s-]", "", filename).strip().replace(" ", "_")
        base_path = os.path.join(output_dir, f"{safe_name}_{timestamp}")

        fmt_l = (fmt or "csv").lower()
        if fmt_l == "csv":
            final_file = f"{base_path}.csv"
            df.to_csv(final_file, index=False, encoding="utf-8-sig")
        elif fmt_l == "json":
            final_file = f"{base_path}.json"
            df.to_json(final_file, orient="records", indent=4)
        elif fmt_l in ("excel", "xlsx"):
            final_file = f"{base_path}.xlsx"
            df.to_excel(final_file, index=False)
        else:
            final_file = f"{base_path}.csv"
            df.to_csv(final_file, index=False, encoding="utf-8-sig")

        return final_file


scraper_service = MapsScraper()


async def _open_leads_artifact(file_path: str) -> None:
    if file_path and os.path.isfile(file_path):
        await asyncio.to_thread(os.startfile, file_path)


async def _run_maps_scrape_job(
    ctx,
    *,
    category: str,
    location: str,
    count: int,
    fmt: str,
    headless: bool,
) -> dict[str, Any]:
    total = _clamp_lead_count(count)

    async def on_progress(done: int, goal: int, name: str) -> None:
        pct = (done / goal) * 100.0 if goal else None
        await ctx.progress(pct, f"{done}/{goal} leads: {name}")
        await notify_tool_action("google_maps_scraper", f"Scraped {done}/{goal}: {name}")

    places = await scraper_service.scrape_leads(
        category,
        location,
        total,
        headless=headless,
        on_progress=on_progress,
        is_cancelled=lambda: ctx.cancelled,
    )
    if ctx.cancelled:
        return tool_result("cancelled", "Maps scrape cancel ho gaya.")

    if not places:
        return tool_result("error", "Koi leads nahi mili. Query check karein.")

    file_path = await asyncio.to_thread(
        scraper_service.save_results,
        places,
        f"{category}_{location or 'global'}",
        fmt,
    )
    if not file_path:
        return tool_result("error", "Leads save nahi ho sakin.")

    await _open_leads_artifact(file_path)
    base = os.path.basename(file_path)
    msg = f"{len(places)} leads scrape ho gayi hain aur '{base}' mein save hain."
    return tool_result(
        "success",
        msg,
        data={
            "count": len(places),
            "file_path": file_path,
            "opened_file": base,
            "artifact": {"kind": "file", "path": file_path, "name": base},
        },
    )


@jarvis_tool(execution_timeout=600.0)
async def scrape_google_maps_leads(
    category: str,
    location: str = "",
    count: int = 10,
    format: str = "csv",
    headless: bool = False,
) -> dict[str, Any]:
    """
    Sir, is tool se main Google Maps par kisi bhi category ke leads (name, address, phone, website) scrap karta hoon.
    Main browser open karke aapko dikhaon ga ke kaam kaise ho raha hai.
    """
    count = _clamp_lead_count(count)
    jobs = _jobs_module()
    title = f"Maps leads: {category}" + (f" ({location})" if location else "")

    if jobs is not None and count > 5:

        async def factory(ctx):
            try:
                return await _run_maps_scrape_job(
                    ctx,
                    category=category,
                    location=location,
                    count=count,
                    fmt=format,
                    headless=headless,
                )
            except BrowserLaunchError:
                return tool_result(
                    "error",
                    "Browser launch nahi ho saka.",
                    error_code="BROWSER_MISSING",
                    hint=PLAYWRIGHT_INSTALL_HINT,
                )

        job_id = jobs.start_job(
            "scrape_google_maps_leads",
            title,
            factory,
            timeout=600,
            announce=True,
        )
        await notify_thinking(f"Sir, {count} leads ke liye background scrape shuru ho raha hai...")
        return tool_result(
            "started",
            f"Maps scrape shuru — {count} leads. Job panel par progress dekhein.",
            data={"job_id": job_id},
        )

    try:
        await notify_thinking(f"Sir, main {category} ke liye leads dhondna shuru kar raha hoon...")

        async def on_progress(done: int, goal: int, name: str) -> None:
            await notify_tool_action("google_maps_scraper", f"Scraped {done}/{goal}: {name}")

        places = await asyncio.wait_for(
            scraper_service.scrape_leads(
                category,
                location,
                count,
                headless=headless,
                on_progress=on_progress,
            ),
            timeout=600,
        )
    except BrowserLaunchError:
        return tool_result(
            "error",
            "Browser launch nahi ho saka.",
            error_code="BROWSER_MISSING",
            hint=PLAYWRIGHT_INSTALL_HINT,
        )
    except asyncio.TimeoutError:
        return tool_result(
            "error",
            "Maps scrape time limit (600s) par ruk gaya.",
            error_code="TIMEOUT",
        )
    except Exception as exc:
        logger.exception("Scraper Tool Error: %s", exc)
        return tool_result("error", f"Scraper error: {exc}")

    if not places:
        return tool_result("error", "Koi leads nahi mili. Query check karein.")

    file_path = await asyncio.to_thread(
        scraper_service.save_results,
        places,
        f"{category}_{location or 'global'}",
        format,
    )
    if not file_path:
        return tool_result("error", "Leads save nahi ho sakin.")

    await _open_leads_artifact(file_path)
    base = os.path.basename(file_path)
    msg = f"Success! {len(places)} leads scrape ho gayi hain aur '{base}' mein save kar di gayi hain."
    await notify_tool_action("google_maps_scraper", f"Scraped {len(places)} leads for {category}")
    return tool_result(
        "success",
        msg,
        data={"count": len(places), "file_path": file_path, "opened_file": base},
    )


@jarvis_tool
async def open_leads_file(name: str) -> dict[str, Any]:
    """
    Sir, agar aap ne pehle koi leads scrape ki hain, to unka naam bataein, main file open kar doon ga.
    """
    leads_dir = _leads_dir()
    if not os.path.isdir(leads_dir):
        return tool_result("error", "Abhi tak koi leads folder nahi bana.")

    raw = (name or "").strip()
    basename = os.path.basename(raw)
    if not basename or basename in (".", ".."):
        return tool_result(
            "error",
            "Sirf file ka naam (basename) den.",
            error_code="PATH_NOT_ALLOWED",
        )

    ext = os.path.splitext(basename)[1].lower()
    if ext and ext not in LEADS_ALLOWED_EXTS:
        return tool_result(
            "error",
            "Sirf .csv, .json ya .xlsx leads files khul sakti hain.",
            error_code="PATH_NOT_ALLOWED",
        )

    pattern = os.path.join(leads_dir, f"*{glob.escape(basename)}*")
    files = [
        f
        for f in glob.glob(pattern)
        if os.path.splitext(f)[1].lower() in LEADS_ALLOWED_EXTS
    ]
    if not files:
        return tool_result("error", f"'{basename}' ke naam se koi leads file nahi mili.")

    files.sort(key=os.path.getmtime, reverse=True)
    target = files[0]
    await _open_leads_artifact(target)
    opened = os.path.basename(target)
    return tool_result(
        "success",
        f"File '{opened}' open kar di gayi hai.",
        data={"opened_file": opened},
    )
