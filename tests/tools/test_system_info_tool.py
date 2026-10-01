"""X-28: get_laptop_info + collect_pc_vitals (battery / desktop / low battery)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.system import jarvis_system_info as mod


def _vitals(
    *,
    battery: int | None = 42,
    plugged: bool | None = False,
    cpu: float = 12.0,
    ram: float = 40.0,
) -> dict[str, object]:
    return {
        "cpu": cpu,
        "ram": ram,
        "disk": 55.0,
        "battery": battery,
        "plugged": plugged,
        "secs_left": 3600 if battery is not None else None,
    }


@pytest.mark.asyncio
async def test_get_laptop_info_with_battery_on_battery_power() -> None:
    with patch.object(mod, "collect_pc_vitals", return_value=_vitals(battery=42, plugged=False)):
        result = await mod.get_laptop_info()

    assert result["status"] == "success"
    assert "42%" in result["message"]
    assert "battery par chal raha hai" in result["message"]
    assert result["data"]["battery"] == 42
    assert result["data"]["plugged"] is False


@pytest.mark.asyncio
async def test_get_laptop_info_without_battery_desktop() -> None:
    with patch.object(
        mod,
        "collect_pc_vitals",
        return_value=_vitals(battery=None, plugged=None),
    ):
        result = await mod.get_laptop_info()

    assert result["status"] == "success"
    assert result["data"]["battery"] is None
    assert result["data"]["plugged"] is None
    assert "maloomat available nahi" in result["message"]


@pytest.mark.asyncio
async def test_get_laptop_info_low_battery_warning() -> None:
    with patch.object(mod, "collect_pc_vitals", return_value=_vitals(battery=15, plugged=False)):
        result = await mod.get_laptop_info()

    assert result["status"] == "success"
    assert "charger laga lein" in result["message"]
    assert "15%" in result["message"]


@pytest.mark.asyncio
async def test_get_laptop_info_high_cpu_adds_usage_without_ask() -> None:
    with patch.object(
        mod,
        "collect_pc_vitals",
        return_value=_vitals(battery=80, plugged=True, cpu=90.0, ram=30.0),
    ):
        result = await mod.get_laptop_info()

    assert "CPU 90%" in result["message"]
    assert "RAM 30%" in result["message"]


@pytest.mark.asyncio
async def test_get_laptop_info_include_usage_when_asked() -> None:
    with patch.object(
        mod,
        "collect_pc_vitals",
        return_value=_vitals(battery=80, plugged=True, cpu=10.0, ram=20.0),
    ):
        result = await mod.get_laptop_info(include_usage=True)

    assert "CPU 10%" in result["message"]
    assert "RAM 20%" in result["message"]


def test_collect_pc_vitals_shape_with_battery() -> None:
    fake_bat = SimpleNamespace(percent=42.7, power_plugged=False, secsleft=1200)
    with (
        patch.object(mod.psutil, "cpu_percent", return_value=5.0),
        patch.object(
            mod.psutil,
            "virtual_memory",
            return_value=SimpleNamespace(percent=33.0),
        ),
        patch.object(
            mod.psutil,
            "disk_usage",
            return_value=SimpleNamespace(percent=60.0),
        ),
        patch.object(mod.psutil, "sensors_battery", return_value=fake_bat),
    ):
        v = mod.collect_pc_vitals()

    assert v["cpu"] == 5.0
    assert v["ram"] == 33.0
    assert v["disk"] == 60.0
    assert v["battery"] == 43
    assert v["plugged"] is False
    assert v["secs_left"] == 1200


def test_collect_pc_vitals_no_battery_sensor() -> None:
    with (
        patch.object(mod.psutil, "cpu_percent", return_value=1.0),
        patch.object(
            mod.psutil,
            "virtual_memory",
            return_value=SimpleNamespace(percent=2.0),
        ),
        patch.object(
            mod.psutil,
            "disk_usage",
            return_value=SimpleNamespace(percent=3.0),
        ),
        patch.object(mod.psutil, "sensors_battery", return_value=None),
    ):
        v = mod.collect_pc_vitals()

    assert v["battery"] is None
    assert v["plugged"] is None
    assert v["secs_left"] is None
