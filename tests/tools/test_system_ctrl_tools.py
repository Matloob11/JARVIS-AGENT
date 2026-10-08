"""X-29: System control tools — risk meta, cancel mapping, sleep paths, recycle summary."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import services.system.jarvis_system_ctrl as sc
from services.ai_core.jarvis_plugin_manager import plugin_manager


def test_meta_risk_and_timeouts() -> None:
    shut = plugin_manager.get_tool_meta("shutdown_system")
    rest = plugin_manager.get_tool_meta("restart_system")
    sleep = plugin_manager.get_tool_meta("sleep_system")
    recycle = plugin_manager.get_tool_meta("empty_recycle_bin")
    cancel = plugin_manager.get_tool_meta("cancel_shutdown")

    assert shut is not None and shut["risk"] == "high" and shut["timeout"] == 20.0
    assert rest is not None and rest["risk"] == "high" and rest["timeout"] == 20.0
    assert sleep is not None and sleep["risk"] == "medium"
    assert recycle is not None and recycle["risk"] == "high"
    assert cancel is not None and cancel["risk"] == "low" and cancel["timeout"] == 10.0
    assert "SHUTDOWN" in (shut.get("confirm_summary") or "")
    assert callable(recycle.get("confirm_summary"))


@pytest.mark.asyncio
async def test_cancel_shutdown_exit_1116_not_found() -> None:
    fake = SimpleNamespace(returncode=1116, stdout="", stderr="")
    with patch.object(sc, "sys") as fake_sys, patch.object(
        sc, "_run_shutdown", return_value=fake
    ) as run:
        fake_sys.platform = "win32"
        res = await sc.cancel_shutdown()
    assert res["status"] == "not_found"
    assert "pending nahi" in res["message"]
    run.assert_called_once_with(["shutdown", "/a"])


@pytest.mark.asyncio
async def test_cancel_shutdown_success() -> None:
    fake = SimpleNamespace(returncode=0, stdout="", stderr="")
    with patch.object(sc, "sys") as fake_sys, patch.object(
        sc, "_run_shutdown", return_value=fake
    ):
        fake_sys.platform = "win32"
        res = await sc.cancel_shutdown()
    assert res["status"] == "success"


@pytest.mark.asyncio
async def test_shutdown_uses_to_thread_and_delay() -> None:
    fake = SimpleNamespace(returncode=0, stdout="", stderr="")
    with patch.object(sc, "sys") as fake_sys, patch.object(
        sc, "_run_shutdown", return_value=fake
    ) as run:
        fake_sys.platform = "win32"
        res = await sc.shutdown_system()
    assert res["status"] == "success"
    assert res["data"]["delay_s"] == 30
    assert "/s" in res["data"]["command"]
    run.assert_called_once()


@pytest.mark.asyncio
async def test_sleep_s0_posts_monitor_power() -> None:
    timer_targets: list = []

    class FakeTimer:
        def __init__(self, delay: float, fn) -> None:  # noqa: ANN001
            self.delay = delay
            self.fn = fn
            timer_targets.append(fn)

        def start(self) -> None:
            return None

    with patch.object(sc, "sys") as fake_sys, patch.object(
        sc, "_powercfg_has_s0", return_value=True
    ), patch.object(sc.threading, "Timer", FakeTimer), patch.object(
        sc, "_post_monitor_power_off"
    ) as post:
        fake_sys.platform = "win32"
        res = await sc.sleep_system("sleep")
    assert res["status"] == "success"
    assert res["data"]["method"] == "S0_monitor_off"
    assert len(timer_targets) == 1
    timer_targets[0]()
    post.assert_called_once()


@pytest.mark.asyncio
async def test_sleep_s3_uses_set_suspend_state() -> None:
    import asyncio

    with patch.object(sc, "sys") as fake_sys, patch.object(
        sc, "_powercfg_has_s0", return_value=False
    ), patch.object(sc, "_set_suspend_state") as suspend:
        fake_sys.platform = "win32"
        res = await sc.sleep_system("sleep")
        assert res["status"] == "success"
        assert res["data"]["method"] == "S3_SetSuspendState"
        await asyncio.sleep(0.05)
        suspend.assert_called_once()


@pytest.mark.asyncio
async def test_sleep_hibernate_schedules_shutdown_h() -> None:
    timers: list = []

    class FakeTimer:
        def __init__(self, delay: float, fn) -> None:  # noqa: ANN001
            timers.append((delay, fn))

        def start(self) -> None:
            return None

    with patch.object(sc, "sys") as fake_sys, patch.object(
        sc.threading, "Timer", FakeTimer
    ), patch.object(sc, "_hibernate_now") as hib:
        fake_sys.platform = "win32"
        res = await sc.sleep_system("hibernate")
        assert res["data"]["method"] == "shutdown_/h"
        assert timers and timers[0][0] == 3.0
        assert timers[0][1] is hib


@pytest.mark.asyncio
async def test_recycle_confirm_summary_text() -> None:
    with patch.object(sc, "_query_recycle_bin", return_value=(3, 12.5)):
        text = await sc._recycle_confirm_summary({})
    assert text == "3 items, 12.5 MB recycle bin se hamesha ke liye delete honge"


@pytest.mark.asyncio
async def test_empty_recycle_bin_shell32_success() -> None:
    with patch.object(sc, "sys") as fake_sys, patch.object(
        sc, "_query_recycle_bin", return_value=(2, 4.0)
    ), patch.object(sc, "_empty_recycle_bin_shell32", return_value=0):
        fake_sys.platform = "win32"
        res = await sc.empty_recycle_bin()
    assert res["status"] == "success"
    assert "4.0 MB" in res["message"]
    assert res["data"]["method"] == "SHEmptyRecycleBinW"


@pytest.mark.asyncio
async def test_empty_recycle_bin_already_empty_hresult() -> None:
    with patch.object(sc, "sys") as fake_sys, patch.object(
        sc, "_query_recycle_bin", return_value=(0, 0.0)
    ), patch.object(sc, "_empty_recycle_bin_shell32", return_value=0x80270015):
        fake_sys.platform = "win32"
        res = await sc.empty_recycle_bin()
    assert res["status"] == "success"
    assert "pehle se khali" in res["message"]


@pytest.mark.asyncio
async def test_post_monitor_power_off_uses_win32api() -> None:
    win32api = MagicMock()
    win32con = SimpleNamespace(
        HWND_BROADCAST=0xFFFF,
        WM_SYSCOMMAND=0x0112,
        SC_MONITORPOWER=0xF170,
    )
    with patch.dict(
        "sys.modules",
        {"win32api": win32api, "win32con": win32con},
    ):
        sc._post_monitor_power_off()
    win32api.PostMessage.assert_called_once_with(0xFFFF, 0x0112, 0xF170, 2)
