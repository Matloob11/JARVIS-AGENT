"""
# services/utils/jarvis_location.py
Location provider of the VPS brain (contracts K-08, K-72 and K-77; plan task C-113).

The device location is the laptop's: in vps mode the Desktop Node pushes it over the Desktop Link
(K-70 event 'location', at start and every 600 s) and agent-svc caches it in
conversations/location.json. get_current_city_data() answers from that cache while it is at most
30 min old, else with USER_CITY and fixed coordinates. Nothing here touches the network: from the VPS,
ipinfo answers for the data centre (Kuala Lumpur).

In remote mode (JARVIS_DESKTOP_MODE=remote) services.ai_core.remote_compat binds
jarvis_search.get_current_city / get_current_city_data and their by-value consumers to the two
coroutines below, until X-64 gives jarvis_search its own hook (get_device_location()).

The cache file is {"city": str, "lat": float, "lng": float, "ts": epoch s}. save_device_location()
(agent-svc's writer, C-125) writes it whole through os.replace, so a reader never sees half a file.
Importing this module reads nothing.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from contextlib import suppress
from typing import Any

from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-LOCATION")

LOCATION_FILE = "location.json"
MAX_AGE_S = 30 * 60
# A ts this far ahead of the reader's clock still counts (the K-70 freshness window).
CLOCK_SLACK_S = 120.0
SOURCE_DEVICE, SOURCE_USER_CITY = "device", "user_city"
# USER_CITY's fixed coordinates: the same fallback jarvis_search uses when ipinfo fails.
DEFAULT_CITY = "Lahore"
DEFAULT_LAT, DEFAULT_LNG = 31.5204, 74.3587
_CITY_MAX = 100

# The last cache version that was logged as unusable, so a bad file is reported once, not per call.
_last_warning: tuple[str, int] | None = None


def location_path() -> str:
    """conversations/location.json of this installation."""
    return os.path.join(config.conversations_dir, LOCATION_FILE)


def user_city() -> str:
    """USER_CITY, read on every call; Lahore when it is unset or empty."""
    return (os.getenv("USER_CITY") or "").strip() or DEFAULT_CITY


def user_city_location(now: float | None = None) -> dict[str, Any]:
    """USER_CITY with the fixed coordinates: {city, lat, lng, source 'user_city', ts}."""
    return {"city": user_city(), "lat": DEFAULT_LAT, "lng": DEFAULT_LNG, "source": SOURCE_USER_CITY,
            "ts": time.time() if now is None else now}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _coordinate(value: Any, limit: float) -> float | None:
    number = _number(value)
    return number if number is not None and -limit <= number <= limit else None


def _city(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    city = value.strip()
    if not city or len(city) > _CITY_MAX or not city.isprintable():
        return None
    return city


def _parse(raw: Any) -> dict[str, Any] | None:
    """The cache content as a location dict; None when a field is missing or invalid."""
    if not isinstance(raw, dict):
        return None
    city = _city(raw.get("city"))
    lat = _coordinate(raw.get("lat"), 90.0)
    lng = _coordinate(raw.get("lng"), 180.0)
    ts = _number(raw.get("ts"))
    if city is None or lat is None or lng is None or ts is None or ts <= 0:
        return None
    return {"city": city, "lat": lat, "lng": lng, "source": SOURCE_DEVICE, "ts": ts}


def _warn_once(path: str, problem: str) -> None:
    global _last_warning  # pylint: disable=global-statement
    try:
        version = os.stat(path).st_mtime_ns
    except OSError:
        version = 0
    if _last_warning != (path, version):
        _last_warning = (path, version)
        logger.warning("Device location cache %s: %s; using USER_CITY", os.path.basename(path), problem)


def get_device_location(path: str | None = None, *, now: float | None = None,
                        max_age_s: float = MAX_AGE_S) -> dict[str, Any] | None:
    """
    The cached device location {city, lat, lng, source 'device', ts} while it is at most max_age_s
    (30 min) old, else None: no cache, an unreadable or invalid one, or a stale or future ts.
    """
    path = path or location_path()
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as e:
        _warn_once(path, f"unreadable ({type(e).__name__})")
        return None
    location = _parse(raw)
    if location is None:
        _warn_once(path, "invalid content")
        return None
    age = (time.time() if now is None else now) - location["ts"]
    if not -CLOCK_SLACK_S <= age <= max_age_s:
        logger.debug("Device location cache is %.0f s old; using USER_CITY", age)
        return None
    return location


async def get_current_city_data() -> dict[str, Any]:
    """
    Where Sir is: the cached device location while it is at most 30 min old, else USER_CITY with fixed
    coordinates. Returns {city, lat, lng, source 'device'|'user_city', ts}; never uses the network.
    """
    return get_device_location() or user_city_location()


async def get_current_city() -> str:
    """The city of get_current_city_data()."""
    return (await get_current_city_data())["city"]


def save_device_location(city: str, lat: float, lng: float, *, ts: float | None = None,
                         path: str | None = None) -> dict[str, Any]:
    """
    agent-svc's writer (C-125): validates one device location and writes the cache whole, through a
    temporary file in the same folder and os.replace. Returns the stored location; ValueError for an
    empty or overlong city, a latitude outside [-90, 90] or a longitude outside [-180, 180].
    """
    location = _parse({"city": city, "lat": lat, "lng": lng, "ts": time.time() if ts is None else ts})
    if location is None:
        raise ValueError("invalid device location")
    path = path or location_path()
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".location-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({key: location[key] for key in ("city", "lat", "lng", "ts")}, handle,
                      ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise
    return location
