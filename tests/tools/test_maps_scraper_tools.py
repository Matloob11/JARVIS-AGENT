"""Unit tests for jarvis_maps_scraper (fake async_playwright — no live browser)."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.automation import jarvis_maps_scraper as maps_mod


def _fake_place(name: str = "Test Cafe") -> maps_mod.Place:
    return maps_mod.Place(
        name=name,
        address="123 Main St",
        website="https://example.com",
        phone="+1 555",
    )


class _FakeLocator:
    def __init__(self, *, count: int = 1, text: str = "Place") -> None:
        self._count = count
        self._text = text

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def count(self) -> int:
        return self._count

    async def inner_text(self) -> str:
        return self._text

    async def get_attribute(self, _name: str) -> str | None:
        return None

    async def wait_for(self, **_kw: object) -> None:
        return None

    async def is_visible(self, **_kw: object) -> bool:
        return False

    async def click(self, **_kw: object) -> None:
        return None

    async def fill(self, _text: str) -> None:
        return None

    async def hover(self) -> None:
        return None

    async def scroll_into_view_if_needed(self) -> None:
        return None

    async def all(self) -> list[MagicMock]:
        return [MagicMock()]


class _FakePage:
    def __init__(self) -> None:
        self._listings = [MagicMock()]

    def locator(self, _sel: str) -> _FakeLocator:
        return _FakeLocator()

    async def goto(self, *_a: object, **_kw: object) -> None:
        return None

    async def wait_for_selector(self, *_a: object, **_kw: object) -> None:
        return None

    @property
    def keyboard(self) -> SimpleNamespace:
        return SimpleNamespace(press=AsyncMock())


class _FakeBrowser:
    async def new_context(self, **_kw: object) -> SimpleNamespace:
        page = _FakePage()
        return SimpleNamespace(new_page=AsyncMock(return_value=page))

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_launch_uses_playwright_channel() -> None:
    launch = AsyncMock(return_value=_FakeBrowser())
    chromium = SimpleNamespace(launch=launch)
    playwright = SimpleNamespace(chromium=chromium)

    class _CM:
        async def __aenter__(self):
            return playwright

        async def __aexit__(self, *_a: object) -> None:
            return None

    with (
        patch.object(maps_mod.config, "playwright_channel", "msedge"),
        patch.object(maps_mod, "async_playwright", return_value=_CM()),
        patch.object(maps_mod.scraper_service, "_dismiss_consent", AsyncMock()),
        patch.object(
            maps_mod.scraper_service,
            "_extract_first",
            AsyncMock(side_effect=["Cafe One", "addr", "web", "phone"]),
        ),
    ):
        places = await maps_mod.scraper_service.scrape_leads("cafe", "Lahore", 1, headless=True)

    launch.assert_awaited()
    assert launch.await_args.kwargs.get("channel") == "msedge"
    assert launch.await_args.kwargs.get("slow_mo") == 50
    assert len(places) >= 0


@pytest.mark.asyncio
async def test_launch_falls_back_without_channel() -> None:
    browser = _FakeBrowser()

    async def _launch(**kw: object):
        if "channel" in kw:
            raise RuntimeError("no channel browser")
        return browser

    launch = AsyncMock(side_effect=_launch)
    chromium = SimpleNamespace(launch=launch)
    playwright = SimpleNamespace(chromium=chromium)

    class _CM:
        async def __aenter__(self):
            return playwright

        async def __aexit__(self, *_a: object) -> None:
            return None

    with (
        patch.object(maps_mod, "async_playwright", return_value=_CM()),
        patch.object(maps_mod.scraper_service, "_dismiss_consent", AsyncMock()),
        patch.object(
            maps_mod.scraper_service,
            "_extract_first",
            AsyncMock(return_value="x"),
        ),
    ):
        await maps_mod.scraper_service.scrape_leads("cafe", "", 1, headless=True)

    assert launch.await_count == 2
    assert "channel" in launch.await_args_list[0].kwargs
    assert "channel" not in launch.await_args_list[1].kwargs


@pytest.mark.asyncio
async def test_launch_both_fail_raises_browser_error() -> None:
    launch = AsyncMock(side_effect=RuntimeError("missing"))
    chromium = SimpleNamespace(launch=launch)
    playwright = SimpleNamespace(chromium=chromium)

    class _CM:
        async def __aenter__(self):
            return playwright

        async def __aexit__(self, *_a: object) -> None:
            return None

    with patch.object(maps_mod, "async_playwright", return_value=_CM()):
        with pytest.raises(maps_mod.BrowserLaunchError):
            await maps_mod.scraper_service.scrape_leads("cafe", "", 1)


def test_clamp_lead_count() -> None:
    assert maps_mod._clamp_lead_count(0) == 1
    assert maps_mod._clamp_lead_count(100) == 60
    assert maps_mod._clamp_lead_count(10) == 10
    assert maps_mod._clamp_lead_count("bad") == 10


@pytest.mark.asyncio
async def test_scrape_tool_starts_background_job_when_count_gt_5() -> None:
    fake_jobs = MagicMock()
    fake_jobs.start_job.return_value = "job-abc123"

    with (
        patch.object(maps_mod, "_jobs_module", return_value=fake_jobs),
        patch.object(maps_mod, "notify_thinking", AsyncMock()),
    ):
        result = await maps_mod.scrape_google_maps_leads("restaurants", "Lahore", count=20)

    assert result["status"] == "started"
    assert result["data"]["job_id"] == "job-abc123"
    fake_jobs.start_job.assert_called_once()
    assert fake_jobs.start_job.call_args.args[0] == "scrape_google_maps_leads"


@pytest.mark.asyncio
async def test_scrape_tool_sync_path_saves_and_opens(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(maps_mod.config, "outputs_dir", str(tmp_path))
    leads = [_fake_place("A"), _fake_place("B")]

    with (
        patch.object(maps_mod, "_jobs_module", return_value=None),
        patch.object(maps_mod.scraper_service, "scrape_leads", AsyncMock(return_value=leads)),
        patch.object(maps_mod, "notify_thinking", AsyncMock()),
        patch.object(maps_mod, "notify_tool_action", AsyncMock()),
        patch.object(maps_mod.asyncio, "to_thread", side_effect=lambda fn, *a, **k: fn(*a, **k)),
        patch.object(maps_mod.os, "startfile") as startfile,
    ):
        result = await maps_mod.scrape_google_maps_leads("restaurants", "Lahore", count=3)

    assert result["status"] == "success"
    assert result["data"]["count"] == 2
    file_path = result["data"]["file_path"]
    assert file_path.endswith(".csv")
    assert os.path.isfile(file_path)
    assert "restaurants_Lahore" in os.path.basename(file_path)
    startfile.assert_called_once_with(file_path)


def test_save_results_under_outputs_leads(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(maps_mod.config, "outputs_dir", str(tmp_path))
    scraper = maps_mod.MapsScraper()
    path = scraper.save_results([_fake_place()], "my leads", "csv")
    assert path is not None
    assert path.startswith(os.path.join(str(tmp_path), "leads"))
    assert path.endswith(".csv")
    assert "my_leads_" in os.path.basename(path)


@pytest.mark.asyncio
async def test_open_leads_file_glob_escape_and_ext_filter(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(maps_mod.config, "outputs_dir", str(tmp_path))
    leads_dir = os.path.join(str(tmp_path), "leads")
    os.makedirs(leads_dir, exist_ok=True)
    good = os.path.join(leads_dir, "restaurants_Lahore_20260101_120000.csv")
    with open(good, "w", encoding="utf-8") as fh:
        fh.write("name\nx\n")
    bad = os.path.join(leads_dir, "restaurants_Lahore_secret.txt")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("nope")

    with patch.object(maps_mod.asyncio, "to_thread", side_effect=lambda fn, *a, **k: fn(*a, **k)), patch.object(
        maps_mod.os, "startfile"
    ) as startfile:
        result = await maps_mod.open_leads_file("Lahore")

    assert result["status"] == "success"
    assert result["data"]["opened_file"].endswith(".csv")
    startfile.assert_called_once_with(good)

    with patch.object(maps_mod.asyncio, "to_thread", side_effect=lambda fn, *a, **k: fn(*a, **k)), patch.object(
        maps_mod.os, "startfile"
    ):
        wildcard = await maps_mod.open_leads_file("*")

    assert wildcard["status"] == "error"


@pytest.mark.asyncio
async def test_scrape_tool_browser_missing_error_code() -> None:
    with (
        patch.object(maps_mod, "_jobs_module", return_value=None),
        patch.object(
            maps_mod.scraper_service,
            "scrape_leads",
            AsyncMock(side_effect=maps_mod.BrowserLaunchError("hint")),
        ),
        patch.object(maps_mod, "notify_thinking", AsyncMock()),
    ):
        result = await maps_mod.scrape_google_maps_leads("cafe", count=2)

    assert result["status"] == "error"
    assert result["error_code"] == "BROWSER_MISSING"
    assert maps_mod.PLAYWRIGHT_INSTALL_HINT in result["hint"]
