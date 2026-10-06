"""Plan task C-64: the log pipeline in services/utils/jarvis_logger.py and services/utils/jarvis_audit.py.

- UIBridgeHandler: WARNING+ and allow-listed INFO only (UI_LOG_LEVEL=INFO forwards every INFO line), never a C-124
  private line or the bridge's own loggers; it only queues.
- UiLogShipper: a 5 lines/s bucket (burst 20), batches of at most 50 lines under 128 KiB, a 200-line buffer that
  evicts INFO first, dropped counting, Retry-After and backoff, never a batch posted twice, events before lines.
- SharedRotatingFileHandler: one handler per file per process, LOG_FILE_LEVEL (default INFO), a Windows-safe rollover
  that a second open handle postpones without losing a line or a backup, and a path that another process rotated is
  reopened.
- jarvis_audit: the shared 5 MiB security.log handler and the rate-capped CRITICAL mirror (agent role only).

Every file lives in tmp_path, every POST goes to a recorder or an in-memory httpx transport, and every secret is a
throwaway value: nothing reaches the live logs, a bridge or a real env file.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import ast
import importlib
import io
import itertools
import json
import logging
import os
import sys
import threading
import time
import weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from starlette.requests import Request

from services.utils import jarvis_audit, jarvis_bridge, jarvis_logger, jarvis_security
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import SharedRotatingFileHandler, UIBridgeHandler, UiLogShipper
from src.core import bridge_security
from src.core.bridge_security import NonceCache

ROOT = Path(__file__).resolve().parents[2]
AGENT_TOKEN = "c64-test-agent-token-" + "a" * 24
HMAC_SECRET = "c64-test-hmac-secret-" + "b" * 24
LEGACY_TOKEN = "c64-test-legacy-token-" + "c" * 24
FAKE_KEY = "AQ.FAKE-c64-google_key-0123456789"
WINDOWS = sys.platform == "win32"


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Poster:
    """Stands in for post_notify_blocking: answers from a script ((outcome, reason, retry_after) or a callable) and
    records every post."""

    def __init__(self, *script: Any, default: Any = ("delivered", "", None)) -> None:
        self.script = list(script)
        self.default = default
        self.posts: list[tuple[str, Any]] = []
        self.lock = threading.Lock()

    def __call__(self, event_type: str, payload: Any, timeout: float) -> tuple[str, str, float | None]:
        with self.lock:
            self.posts.append((event_type, json.loads(json.dumps(payload))))
            step = self.script.pop(0) if self.script else self.default
        return step(event_type, payload) if callable(step) else step

    def batches(self) -> list[dict[str, Any]]:
        return [payload for event_type, payload in self.posts if event_type == "vortex_log_batch"]


def _item(n: int, level: str = "INFO", text: str | None = None) -> dict[str, Any]:
    return {"text": text if text is not None else f"line {n}", "category": "TEST", "level": level, "ts": 1000.0 + n}


def _record(name: str, level: int, msg: str, *args: Any, **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(name, level, __file__, 1, msg, args or None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _close(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        if not isinstance(handler, UIBridgeHandler):  # the process's shared UI handler stays usable
            handler.close()


@pytest.fixture
def shipper() -> UiLogShipper:
    return UiLogShipper(clock=Clock(), post=Poster())


@pytest.fixture
def handlers():
    """Shared file handlers made by a test: closed (and out of the registry) afterwards, so tmp_path can go."""
    made: list[logging.Handler] = []

    def make(path: Any, **kwargs: Any) -> SharedRotatingFileHandler:
        handler = jarvis_logger.shared_file_handler(path, **kwargs)
        made.append(handler)
        return handler

    yield make
    for handler in made:
        handler.close()


@pytest.fixture
def ui_env(monkeypatch):
    """UI_LOG_LEVEL / UI_LOG_INFO_ALLOW unset (the defaults)."""
    for name in ("UI_LOG_LEVEL", "UI_LOG_INFO_ALLOW", "LOG_FILE_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ---------------------------------------------------------------- what reaches the UI


def test_info_is_forwarded_only_from_the_allow_list(ui_env, shipper):
    handler = UIBridgeHandler(shipper=shipper)
    assert handler.min_level == logging.WARNING
    assert handler.info_allow == {"AGENT-RUNNER", "JARVIS-CORE", "PLUGIN-MANAGER", "JARVIS-LOOPS"}
    for record in (_record("AGENT-RUNNER", logging.INFO, "Connected to room: %s", "jarvis-hybrid"),
                   _record("JARVIS-MEMORY", logging.INFO, "memory saved"),            # not allow-listed
                   _record("JARVIS-MEMORY", logging.WARNING, "memory slow"),
                   _record("JARVIS-CLIPBOARD", logging.ERROR, "clipboard failed"),
                   _record("JARVIS-LOOPS", logging.CRITICAL, "SYSTEM OVERLOAD"),
                   _record("JARVIS-LOOPS", logging.DEBUG, "tick"),                     # below INFO
                   _record("PLUGIN-MANAGER", logging.INFO, "Converting tool to LiveKit format: %s", "x")):  # noise
        handler.handle(record)
    assert [(item["category"], item["level"], item["text"]) for item in shipper.snapshot()] == [
        ("MEMORY", "WARNING", "memory slow"), ("CLIPBOARD", "ERROR", "clipboard failed"),
        ("LOOPS", "CRITICAL", "SYSTEM OVERLOAD"), ("AGENT", "INFO", "Connected to room: jarvis-hybrid")]


def test_ui_log_level_info_forwards_every_info_line_and_the_allow_list_is_configurable(ui_env, shipper):
    ui_env.setenv("UI_LOG_LEVEL", "info")
    ui_env.setenv("UI_LOG_INFO_ALLOW", " JARVIS-MEMORY , ")
    handler = UIBridgeHandler(shipper=shipper)
    assert (handler.min_level, handler.info_allow) == (logging.INFO, {"JARVIS-MEMORY"})
    handler.handle(_record("JARVIS-MEMORY", logging.INFO, "memory saved"))
    handler.handle(_record("PLUGIN-MANAGER", logging.INFO, "Converting tool to LiveKit format: %s", "x"))
    handler.handle(_record("JARVIS-LOOPS", logging.DEBUG, "tick"))
    assert [item["text"] for item in shipper.snapshot()] == ["memory saved", "Converting tool to LiveKit format: x"]

    ui_env.setenv("UI_LOG_LEVEL", "ERROR")
    ui_env.setenv("UI_LOG_INFO_ALLOW", "")  # set but empty: no INFO line at all
    strict = UIBridgeHandler(shipper=UiLogShipper(clock=Clock()))
    assert (strict.min_level, strict.info_allow) == (logging.ERROR, frozenset())
    ui_env.setenv("UI_LOG_LEVEL", "LOUD")  # unknown: the default
    assert UIBridgeHandler(shipper=shipper).min_level == logging.WARNING


def test_private_lines_skipped_loggers_and_libraries_are_never_forwarded(shipper):
    handler = UIBridgeHandler(shipper=shipper, min_level=logging.DEBUG, info_allow={"JARVIS-WINDOW"})
    for record in (
            _record("JARVIS-WINDOW", logging.INFO, "Focused window: %s", "Bank - Chrome"),
            _record("JARVIS-WINDOW", logging.DEBUG, "Clipboard says %s", "x", jarvis_private=True),
            _record("UI-BRIDGE", logging.ERROR, "bridge error"),
            _record("JARVIS-BRIDGE", logging.WARNING, "Bridge notify failing"),
            _record("BRIDGE-NOTIFIER", logging.ERROR, "x"),
            _record("UI-LOG-SHIPPER", logging.WARNING, "refused"),
            _record("httpx", logging.WARNING, "x"), _record("livekit.agents", logging.ERROR, "x"),
            _record("asyncio", logging.ERROR, "x"), _record("socketio.client", logging.ERROR, "x"),
            _record("engineio", logging.ERROR, "x"), _record("urllib3.connectionpool", logging.WARNING, "x"),
            _record("httpcore", logging.ERROR, "x")):
        handler.handle(record)
    assert shipper.snapshot() == []
    handler.handle(_record("JARVIS-WINDOW", logging.WARNING, "Focused window: %s", "kept at WARNING"))
    handler.handle(_record("livekit_link", logging.ERROR, "not a library prefix"))
    assert [item["text"] for item in shipper.snapshot()] == ["Focused window: kept at WARNING",
                                                            "not a library prefix"]


def test_a_line_is_masked_cut_to_1000_chars_and_carries_its_level_and_time(monkeypatch, shipper):
    monkeypatch.setenv("GOOGLE_API_KEY", FAKE_KEY)
    monkeypatch.setattr(jarvis_security, "_runtime_secrets", set())
    handler = UIBridgeHandler(shipper=shipper)
    record = _record("JARVIS-SEARCH", logging.ERROR, "failed for key=%s %s", FAKE_KEY, "y" * 3000)
    handler.handle(record)
    [item] = shipper.snapshot()
    assert set(item) == {"text", "category", "level", "ts"}
    assert len(item["text"]) == 1000 and item["text"].startswith("failed for key=*** yyy")
    assert FAKE_KEY not in json.dumps(item)
    assert (item["category"], item["level"], item["ts"]) == ("SEARCH", "ERROR", round(record.created, 3))


def test_emit_never_does_io_and_never_raises(shipper):
    handler = UIBridgeHandler(shipper=shipper)

    class Broken:
        def __str__(self) -> str:
            raise RuntimeError("no text")

    handler.handle(_record("JARVIS-X", logging.ERROR, "bad %s", Broken()))
    handler.handle(_record("JARVIS-X", logging.ERROR, "two %s %s", "only one"))  # a bad format
    assert shipper.snapshot() == [] and shipper._post_override.posts == []


def test_a_record_logged_while_one_is_handled_is_skipped_not_recursed(shipper):
    handler = UIBridgeHandler(shipper=shipper)
    logger = logging.getLogger("JARVIS-TEST-C64-REENTRY")
    logger.propagate = False
    logger.addHandler(handler)

    class Chatty:
        def __str__(self) -> str:
            logger.error("logged while formatting")  # same thread, inside emit
            return "chatty"

    try:
        logger.error("outer %s", Chatty())
    finally:
        logger.removeHandler(handler)
    assert [item["text"] for item in shipper.snapshot()] == ["outer chatty"]


def test_a_second_thread_is_not_dropped_while_the_first_is_handled(shipper):
    """Before C-64 a class-wide flag dropped every other thread's record while one was being emitted."""
    handler = UIBridgeHandler(shipper=shipper)
    entered, release = threading.Event(), threading.Event()

    class Slow:
        def __str__(self) -> str:
            entered.set()
            release.wait(5)
            return "slow"

    first = threading.Thread(target=handler.handle, args=(_record("JARVIS-A", logging.ERROR, "a %s", Slow()),))
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=handler.handle, args=(_record("JARVIS-B", logging.ERROR, "b"),))
    second.start()
    release.set()
    first.join(5)
    second.join(5)
    assert sorted(item["text"] for item in shipper.snapshot()) == ["a slow", "b"]


# ---------------------------------------------------------------- the shipper: bucket, batches, buffer


def test_the_bucket_lets_20_lines_out_at_once_then_5_a_second(shipper):
    clock, poster = shipper._clock, shipper._post_override
    for n in range(100):
        shipper.offer(_item(n))
    assert shipper.tick() is True
    assert len(poster.batches()[-1]["items"]) == 20
    assert shipper.tick() is False and len(poster.posts) == 1  # no token left: no POST at all
    clock.now += 1.0
    shipper.tick()
    assert [item["text"] for item in poster.batches()[-1]["items"]] == [f"line {n}" for n in range(20, 25)]
    clock.now += 100.0  # a long pause refills only up to the burst
    shipper.tick()
    assert len(poster.batches()[-1]["items"]) == 20


def test_a_batch_holds_at_most_50_lines_and_stays_under_128_kib():
    poster = Poster()
    shipper = UiLogShipper(clock=Clock(), post=poster, burst=500, rate=500)
    for n in range(120):
        shipper.offer(_item(n))
    shipper.tick()
    assert len(poster.batches()[0]["items"]) == 50
    big = UiLogShipper(clock=Clock(), post=poster, burst=500, rate=500)
    for n in range(60):
        big.offer(_item(n, text="\U0001f600" * 999 + f"{n}"))  # 4 bytes a char in UTF-8: about 4 KiB a line
    for _ in range(3):
        big.tick()
    sizes = [len(jarvis_bridge.encode_event("vortex_log_batch", batch)) for batch in poster.batches()[1:]]
    assert len(sizes) == 2 and all(size <= jarvis_logger.UI_BATCH_MAX_BYTES for size in sizes)
    assert len(poster.batches()[1]["items"]) < 50  # the size cap, not the line cap, ended the first batch
    assert sum(len(batch["items"]) for batch in poster.batches()[1:]) == 60


def test_lines_go_out_oldest_first_with_warnings_ahead_of_info_and_the_batch_sorted_by_time():
    poster = Poster()
    shipper = UiLogShipper(clock=Clock(), post=poster, burst=3)
    shipper.offer(_item(1))
    shipper.offer(_item(2, "WARNING"))
    shipper.offer(_item(3))
    shipper.offer(_item(4, "ERROR"))
    shipper.tick()
    assert [item["text"] for item in poster.batches()[0]["items"]] == ["line 1", "line 2", "line 4"]
    assert [item["text"] for item in shipper.snapshot()] == ["line 3"]


def test_a_full_buffer_evicts_info_first_and_counts_every_drop():
    poster = Poster()
    shipper = UiLogShipper(clock=Clock(), post=poster)
    for n in range(150):
        shipper.offer(_item(n, "WARNING"))
    for n in range(150, 250):
        shipper.offer(_item(n))
    assert shipper.pending() == 200 and shipper.dropped == 50
    assert [item["text"] for item in shipper.snapshot()][150:152] == ["line 200", "line 201"]  # oldest INFO went
    for n in range(250, 310):
        shipper.offer(_item(n, "ERROR"))
    kept = [item["text"] for item in shipper.snapshot()]
    assert shipper.dropped == 110 and len(kept) == 200
    assert not any(item["level"] == "INFO" for item in shipper.snapshot())  # every INFO went before a WARNING
    assert kept[0] == "line 10"  # then the oldest WARNINGs
    shipper.tick()
    batch = poster.batches()[0]
    assert batch["dropped"] == 110 and batch["source"] == "agent"
    assert shipper.dropped == 0  # delivered: settled


def test_a_dropped_count_alone_goes_out_only_when_no_line_waits():
    poster = Poster(("uncertain", "HTTP 500", None))
    shipper = UiLogShipper(clock=Clock(), post=poster)
    shipper.offer(_item(1))
    shipper.tick()  # uncertain: the line counts as dropped
    assert shipper.dropped == 1 and shipper.pending() == 0
    shipper.tick()
    assert poster.batches()[-1] == {"items": [], "dropped": 1, "source": "agent"}
    assert shipper.dropped == 0
    assert shipper.tick() is False  # nothing left to say: no POST


def test_counts_add_up_with_four_threads_offering_while_batches_go_out():
    clock = Clock()
    outcomes = itertools.cycle([("delivered", "", None), ("uncertain", "HTTP 504", None),
                                ("retry", "ConnectError", None), ("failed", "HTTP 401", None)])
    delivered = [0]

    def answer(_event_type: str, payload: Any) -> tuple[str, str, float | None]:
        outcome = next(outcomes)
        if outcome[0] == "delivered":
            delivered[0] += len(payload["items"])
        return outcome

    shipper = UiLogShipper(clock=clock, post=Poster(default=answer), burst=30, rate=30)

    def produce(base: int) -> None:
        for n in range(1500):
            shipper.offer(_item(base + n, "WARNING" if n % 7 == 0 else "INFO"))

    threads = [threading.Thread(target=produce, args=(base,)) for base in (0, 10_000, 20_000, 30_000)]
    for thread in threads:
        thread.start()
    while any(thread.is_alive() for thread in threads):
        clock.now += 100.0  # past any pause
        shipper.tick()
    for _ in range(3):
        clock.now += 100.0
        shipper.tick()
    assert shipper.offered == 6000
    assert shipper.offered == shipper.delivered + shipper.discarded + shipper.pending()
    assert delivered[0] == shipper.delivered > 0 and shipper.discarded > 0


def test_a_429_puts_the_lines_back_and_its_retry_after_is_the_next_wait():
    clock = Clock()
    poster = Poster(("retry", "HTTP 429 rate_limited", 7.0))
    shipper = UiLogShipper(clock=clock, post=poster)
    for n in range(3):
        shipper.offer(_item(n))
    shipper.tick()
    assert shipper.pending() == 3 and shipper.dropped == 0
    clock.now += 6.9
    assert shipper.tick() is False and len(poster.posts) == 1
    clock.now += 0.2
    shipper.tick()
    assert poster.batches()[0] == poster.batches()[1]  # the same lines, in the same order
    assert shipper.pending() == 0 and shipper.delivered == 3
    retry = UiLogShipper(clock=clock, post=Poster(("retry", "HTTP 429", 300.0)))
    retry.offer(_item(1))
    retry.tick()
    assert retry._resume_at == pytest.approx(clock.now + 30.0)  # at most 30 s


def test_not_sent_backs_off_from_1_to_30_seconds_and_a_delivery_resets_it():
    clock = Clock()
    poster = Poster(default=("retry", "ConnectError: refused", None))
    shipper = UiLogShipper(clock=clock, post=poster)
    shipper.offer(_item(1))
    waits = []
    for _ in range(7):
        start = clock.now
        shipper.tick()
        waits.append(round(shipper._resume_at - start, 3))
        clock.now = shipper._resume_at
    assert waits == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    poster.default = ("delivered", "", None)
    shipper.tick()
    assert shipper._backoff_s == 0.0 and shipper.pending() == 0


@pytest.mark.parametrize("outcome", [("uncertain", "ReadTimeout: slow", None), ("uncertain", "HTTP 500", None),
                                     ("uncertain", "HTTP 504", None), ("failed", "HTTP 400 bad_body", None),
                                     ("failed", "HTTP 401 stale", None), ("failed", "HTTP 413 too_large", None)])
def test_a_batch_that_may_have_arrived_or_was_refused_is_never_posted_twice(outcome):
    clock = Clock()
    poster = Poster(outcome)
    shipper = UiLogShipper(clock=clock, post=poster)
    shipper.offer(_item(1))
    shipper.offer(_item(2))
    shipper.tick()
    assert shipper.pending() == 0 and shipper.dropped == 2 and shipper.discarded == 2
    clock.now += 120.0
    shipper.tick()
    assert [len(batch["items"]) for batch in poster.batches()] == [2, 0]  # the second one only reports the drop
    assert poster.batches()[1]["dropped"] == 2


def test_a_refusal_pauses_sending_a_minute_with_one_warning_per_10_minutes(caplog):
    logger = logging.getLogger("UI-LOG-SHIPPER")
    logger.addHandler(caplog.handler)
    try:
        clock = Clock()
        poster = Poster(default=("failed", "HTTP 401 bad_token", None))
        shipper = UiLogShipper(clock=clock, post=poster)
        for minute in range(12):
            shipper.offer(_item(minute))
            shipper.tick()
            assert shipper.tick() is False  # paused
            clock.now += 61.0
    finally:
        logger.removeHandler(caplog.handler)
    warnings = [r.getMessage() for r in caplog.records if r.name == "UI-LOG-SHIPPER"]
    assert len(warnings) == 2 and "HTTP 401 bad_token" in warnings[0] and LEGACY_TOKEN not in warnings[0]
    assert len(poster.posts) == 12


def test_queued_events_go_first_at_most_5_a_round_once_and_at_most_20_wait():
    poster = Poster()
    shipper = UiLogShipper(clock=Clock(), post=poster)
    shipper.offer(_item(1, "ERROR"))
    for n in range(25):
        shipper.offer_event("audit_event", {"action": f"A{n}"})
    assert len(shipper.queued_events()) == 20 and shipper.events_dropped == 5
    shipper.tick()  # LS-1: at most UI_OUTBOX_PER_TICK events a round, so a wedged bridge never holds one 40 s
    assert [event_type for event_type, _payload in poster.posts] == ["audit_event"] * 5 + ["vortex_log_batch"]
    assert poster.posts[0][1] == {"action": "A5"}
    for _ in range(3):
        shipper.tick()
    assert [event_type for event_type, _payload in poster.posts] == (
        ["audit_event"] * 5 + ["vortex_log_batch"] + ["audit_event"] * 15)
    assert [payload["action"] for event_type, payload in poster.posts if event_type == "audit_event"] == [
        f"A{n}" for n in range(5, 25)]
    shipper.tick()
    assert len([e for e, _p in poster.posts if e == "audit_event"]) == 20  # nothing posted twice


def test_an_uncertain_batch_warns_once_per_10_minutes_naming_the_lines_lost(caplog):
    """C-64 review COR-1: an hour of read timeouts is no longer silent."""
    logger = logging.getLogger("UI-LOG-SHIPPER")
    logger.addHandler(caplog.handler)
    try:
        clock = Clock()
        shipper = UiLogShipper(clock=clock, post=Poster(default=("uncertain", "ReadTimeout: slow", None)))
        for minute in range(12):
            shipper.offer(_item(minute))
            shipper.offer(_item(minute + 100, "ERROR"))
            shipper.tick()
            clock.now += 61.0
    finally:
        logger.removeHandler(caplog.handler)
    warnings = [r.getMessage() for r in caplog.records if r.name == "UI-LOG-SHIPPER"]
    assert len(warnings) == 2 and all("uncertain" in w and "ReadTimeout: slow" in w for w in warnings)
    assert "2 UI log line(s) lost" in warnings[0]


def test_a_lost_or_refused_outbox_event_moves_the_events_dropped_counter():
    """C-64 review SP-5/LS-4: a mirrored CRITICAL audit event that vanishes is visible in the counters."""
    poster = Poster(("uncertain", "HTTP 504", None), ("delivered", "", None), ("failed", "HTTP 401", None))
    shipper = UiLogShipper(clock=Clock(), post=poster)
    for n in range(3):
        shipper.offer_event("audit_event", {"action": f"A{n}"})
    shipper.tick()  # uncertain (counted), delivered, refused (counted, pauses)
    assert shipper.events_dropped == 2
    assert [payload["action"] for _t, payload in poster.posts] == ["A0", "A1", "A2"]
    assert shipper.queued_events() == []  # consumed: never posted twice


def test_flush_at_exit_posts_at_most_2_events_and_skips_when_the_thread_still_runs():
    """C-64 review LS-1 and COR-3: a bounded exit flush, and never a second tick beside a live thread."""
    poster = Poster()
    shipper = UiLogShipper(clock=Clock(), post=poster)
    shipper._pid = os.getpid()
    for n in range(6):
        shipper.offer_event("audit_event", {"action": f"A{n}"})
    shipper.offer(_item(1, "ERROR"))
    shipper.flush_at_exit()
    assert [event_type for event_type, _p in poster.posts] == ["audit_event"] * 2 + ["vortex_log_batch"]

    stuck = UiLogShipper(clock=Clock(), post=poster)
    stuck._pid = os.getpid()
    stuck.offer(_item(2, "ERROR"))
    blocked, release = threading.Event(), threading.Event()

    def hold() -> None:
        blocked.set()
        release.wait(5)

    thread = threading.Thread(target=hold, daemon=True)  # stands in for a sender blocked in client.post
    thread.start()
    stuck._thread = thread
    assert blocked.wait(5)
    before = len(poster.posts)
    stuck.flush_at_exit()  # the thread owns the last flush: no double-reported dropped count
    release.set()
    thread.join(5)
    assert len(poster.posts) == before


def test_transcript_and_chat_previews_are_private_info_templates(tmp_path, ui_env, shipper):
    """C-64 review SP-1: user transcript and chat text never persists in the file log or the UI panel."""
    for template in ("\U0001f3a4 User Transcription (Final, %d chars): %s",
                     "\U0001f3a4 User Transcription (Final): %s",
                     "\U0001f3a4 Partial Input: %s",
                     "CHAT_ROUTER voice id=%s chars=%d: %s",
                     "CHAT_ROUTER voice id=%s full text: %s",
                     "CHAT_ROUTER reply id=%s turn=%s source=%s chars=%d: %s",
                     "CHAT_ROUTER reply id=%s full text: %s",
                     "CHAT_ROUTER %s id=%s full text: %s"):
        assert template in jarvis_logger.PRIVATE_INFO_TEMPLATES
    ui_env.setenv("LOG_FILE_LEVEL", "DEBUG")
    path = tmp_path / "transcripts.log"
    handler = UIBridgeHandler(shipper=shipper, min_level=logging.INFO, info_allow={"AGENT-RUNNER"})
    logger = jarvis_logger.setup_logger("AGENT-RUNNER", path)
    try:
        for h in logger.handlers:
            if type(h) is logging.StreamHandler:  # pylint: disable=unidiomatic-typecheck
                h.setStream(io.StringIO())
        logger.info("\U0001f3a4 User Transcription (Final, %d chars): %s", 19, "mera private sawal")
        logger.debug("\U0001f3a4 User Transcription (Final): %s", "mera private sawal, full")
        logger.info("ordinary runner line")
        handler.handle(_record("AGENT-RUNNER", logging.INFO,
                               "\U0001f3a4 User Transcription (Final, %d chars): %s", 19, "mera private sawal"))
        handler.handle(_record("AGENT-RUNNER", logging.INFO, "kept for the panel"))
        for h in logger.handlers:
            h.flush()
        text = path.read_text(encoding="utf-8")
        assert "mera private sawal" not in text and "ordinary runner line" in text
        assert [item["text"] for item in shipper.snapshot()] == ["kept for the panel"]
    finally:
        _close(logger)


def test_an_event_the_bridge_never_got_waits_for_the_next_round():
    clock = Clock()
    poster = Poster(("retry", "ConnectError", None))
    shipper = UiLogShipper(clock=clock, post=poster)
    shipper.offer_event("audit_event", {"action": "A"})
    shipper.offer(_item(1))
    shipper.tick()
    assert [event_type for event_type, _ in poster.posts] == ["audit_event"]  # lines wait too
    clock.now += 1.0
    shipper.tick()
    assert [event_type for event_type, _ in poster.posts] == ["audit_event", "audit_event", "vortex_log_batch"]


def test_the_thread_starts_once_per_process_sends_and_flushes_at_exit():
    sent = threading.Event()
    poster = Poster(default=lambda *_args: (sent.set(), ("delivered", "", None))[1])
    shipper = UiLogShipper(post=poster, interval=0.01)
    shipper.offer(_item(1, "ERROR"))
    try:
        assert shipper.start(source="svc") is True
        assert shipper.start() is False  # already running in this process
        assert shipper.running() and shipper._thread.name == "ui-log-shipper" and shipper._thread.daemon
        assert sent.wait(5)
    finally:
        shipper.stop()
    assert poster.batches()[0]["source"] == "svc" and not shipper.running()
    for n in range(80):  # more than the bucket and one batch allow
        shipper.offer(_item(n))
    shipper.flush_at_exit()  # one last batch, the bucket ignored
    assert len(poster.batches()[-1]["items"]) == 50


def test_a_forked_child_starts_with_an_empty_shipper(shipper):
    shipper.offer(_item(1))
    shipper.offer_event("audit_event", {"action": "A"})
    shipper._pid = os.getpid()
    shipper._after_fork_in_child()
    assert shipper.pending() == 0 and shipper.queued_events() == [] and shipper._thread is None
    assert shipper._pid is None and shipper.offered == 0
    shipper.flush_at_exit()  # never started here: nothing to flush
    assert shipper._post_override.posts == []


def test_the_test_suite_never_starts_the_process_shipper():
    assert jarvis_logger.start_ui_log_shipping() is False
    assert not jarvis_logger.ui_log_shipper.running()


def test_the_outcome_names_match_jarvis_bridge():
    assert (jarvis_logger.OUTCOME_DELIVERED, jarvis_logger.OUTCOME_NOT_SENT, jarvis_logger.OUTCOME_UNCERTAIN,
            jarvis_logger.OUTCOME_REFUSED) == (jarvis_bridge._DELIVERED, jarvis_bridge._RETRY,
                                               jarvis_bridge._RETRY_IF_IDEMPOTENT, jarvis_bridge._FAILED)


# ---------------------------------------------------------------- the shipper over the real blocking poster


@pytest.fixture
def link(monkeypatch):
    monkeypatch.setattr(config, "agent_token", AGENT_TOKEN)
    monkeypatch.setattr(config, "bridge_hmac_secret", HMAC_SECRET)
    monkeypatch.setattr(config, "security_token", LEGACY_TOKEN)
    monkeypatch.setattr(config, "env", "development")
    monkeypatch.setattr(config, "bridge_url", "http://bridge.test/vortex")
    monkeypatch.setattr(jarvis_bridge, "_link_logged", set())


def _mock_client(answer: Any) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(request) if callable(answer) else answer

    return httpx.Client(transport=httpx.MockTransport(handle)), seen


async def test_a_batch_goes_out_signed_and_the_bridge_checks_accept_it(link):
    client, seen = _mock_client(httpx.Response(200, json={"status": "ok"}))
    shipper = UiLogShipper(clock=Clock())
    shipper._client = client
    shipper.offer(_item(1, "ERROR"))
    assert shipper.tick() is True
    [request] = seen
    assert str(request.url) == "http://bridge.test/vortex/notify"
    scope = {"type": "http", "method": "POST", "path": "/notify",
             "headers": [(name.lower().encode(), value.encode()) for name, value in request.headers.items()]}
    auth = await bridge_security.verify_notify_request(
        Request(scope), request.content, agent_token=AGENT_TOKEN, legacy_token=LEGACY_TOKEN, legacy_enabled=False,
        nonces=NonceCache())
    assert auth.data["type"] == "vortex_log_batch"
    assert auth.data["payload"] == {"items": [_item(1, "ERROR")], "dropped": 0, "source": "agent"}
    assert shipper.delivered == 1
    client.close()


def test_without_the_v2_keys_the_batch_carries_the_legacy_headers(link, monkeypatch):
    monkeypatch.setattr(config, "agent_token", None)
    client, seen = _mock_client(httpx.Response(200, json={"status": "ok"}))
    outcome = jarvis_bridge.post_notify_blocking("vortex_log_batch", {"items": [], "dropped": 1}, client=client)
    assert outcome == ("delivered", "", None)
    [request] = seen
    data = json.loads(request.content)
    assert request.headers["x-vortex-token"] == LEGACY_TOKEN and "x-agent-token" not in request.headers
    assert jarvis_security.security_manager.verify_signature(json.dumps(data, sort_keys=True),
                                                              request.headers["x-vortex-signature"])
    client.close()


@pytest.mark.parametrize(("answer", "expected"), [
    (httpx.Response(429, json={"error": "rate_limited", "class": "logs"}, headers={"Retry-After": "3"}),
     ("retry", "HTTP 429 rate_limited", 3.0)),
    (httpx.Response(429, json={"error": "rate_limited"}, headers={"Retry-After": "120"}),
     ("retry", "HTTP 429 rate_limited", 30.0)),
    (httpx.Response(503, text="<html>nginx</html>"), ("retry", "HTTP 503", None)),
    (httpx.Response(502, text="bad gateway"), ("retry", "HTTP 502", None)),
    (httpx.Response(500, json={"error": "boom"}), ("uncertain", "HTTP 500 boom", None)),
    (httpx.Response(504, text="slow"), ("uncertain", "HTTP 504", None)),
    (httpx.Response(401, json={"error": "stale"}), ("failed", "HTTP 401 stale", None)),
    (httpx.Response(409, json={"error": "replay"}), ("delivered", "", None)),
])
def test_the_blocking_poster_maps_every_answer(link, answer, expected):
    client, _seen = _mock_client(answer)
    assert jarvis_bridge.post_notify_blocking("vortex_log_batch", {"items": []}, client=client) == expected
    client.close()


@pytest.mark.parametrize(("error", "outcome"), [(httpx.ConnectError("refused"), "retry"),
                                                (httpx.ConnectTimeout("syn"), "retry"),
                                                (httpx.ReadTimeout("slow"), "uncertain"),
                                                (httpx.RemoteProtocolError("cut"), "uncertain")])
def test_the_blocking_poster_maps_transport_errors(link, error, outcome):
    def raise_it(_request: httpx.Request) -> httpx.Response:
        raise error

    client, _seen = _mock_client(raise_it)
    result = jarvis_bridge.post_notify_blocking("vortex_log_batch", {"items": []}, client=client)
    assert result[0] == outcome and result[2] is None
    client.close()


def test_the_blocking_poster_never_sends_what_cannot_be_sent(link):
    client, seen = _mock_client(httpx.Response(200))
    assert jarvis_bridge.post_notify_blocking("x", {"v": float("nan")}, client=client) == ("failed", "ValueError",
                                                                                            None)
    assert jarvis_bridge.post_notify_blocking("x", "y" * jarvis_bridge.NOTIFY_MAX_BYTES, client=client) == (
        "failed", "body over the bridge limit", None)
    assert seen == []
    client.close()


# ---------------------------------------------------------------- one rotating handler per file


def test_two_loggers_share_one_handler_and_a_re_setup_leaves_no_stream_behind(tmp_path, ui_env):
    path = tmp_path / "shared.log"
    first = jarvis_logger.setup_logger("JARVIS-TEST-C64-SHARE-A", path)
    second = jarvis_logger.setup_logger("JARVIS-TEST-C64-SHARE-B", path)
    try:
        file_a = [h for h in first.handlers if isinstance(h, SharedRotatingFileHandler)]
        file_b = [h for h in second.handlers if isinstance(h, SharedRotatingFileHandler)]
        assert len(file_a) == len(file_b) == 1 and file_a[0] is file_b[0]
        assert jarvis_logger._FILE_HANDLERS[jarvis_logger._handler_key(path)] is file_a[0]
        assert file_a[0].stream is None  # opened on the first record
        first.info("one")
        stream = file_a[0].stream
        legacy = logging.FileHandler(tmp_path / "legacy.log", encoding="utf-8")
        first.addHandler(legacy)
        again = jarvis_logger.setup_logger("JARVIS-TEST-C64-SHARE-A", path)
        assert again is first and legacy.stream is None  # an old per-logger handle is closed, not orphaned
        assert [type(h).__name__ for h in first.handlers] == ["StreamHandler", "SharedRotatingFileHandler",
                                                              "UIBridgeHandler"]
        assert file_a[0].stream is stream  # the shared one stays open
        second.info("two")
        file_a[0].flush()
        assert [line.split(" - ")[-1] for line in path.read_text(encoding="utf-8").splitlines()] == ["one", "two"]
    finally:
        _close(first)
        _close(second)


def test_a_handler_closed_by_a_logging_reset_stays_the_one_handler_of_its_file(tmp_path, ui_env):
    """uvicorn's dictConfig (the bridge) closes every existing handler while the loggers keep theirs: the file still
    gets exactly one handler, which reopens it on the next record."""
    path = tmp_path / "reset.log"
    first = jarvis_logger.setup_logger("JARVIS-TEST-C64-RESET-A", path)
    second = None
    try:
        [handler] = [h for h in first.handlers if isinstance(h, SharedRotatingFileHandler)]
        first.info("before")
        logging.shutdown([weakref.ref(handler)])  # what dictConfig's _clearExistingHandlers does to every handler
        assert handler.stream is None
        second = jarvis_logger.setup_logger("JARVIS-TEST-C64-RESET-B", path)
        assert [h for h in second.handlers if isinstance(h, SharedRotatingFileHandler)] == [handler]
        assert jarvis_logger.shared_file_handler(path) is handler
        first.info("after, old logger")
        second.info("after, new logger")
        handler.flush()
        assert [line.split(" - ")[-1] for line in path.read_text(encoding="utf-8").splitlines()] == [
            "before", "after, old logger", "after, new logger"]
    finally:
        _close(first)
        if second is not None:
            _close(second)


def test_the_file_takes_info_by_default_even_with_log_level_warning(tmp_path, ui_env):
    ui_env.setenv("LOG_LEVEL", "WARNING")
    path = tmp_path / "level.log"
    logger = jarvis_logger.setup_logger("JARVIS-TEST-C64-LEVEL", path)
    try:
        console = next(h for h in logger.handlers if type(h) is logging.StreamHandler)  # pylint: disable=unidiomatic-typecheck
        console.setStream(io.StringIO())
        file_handler = next(h for h in logger.handlers if isinstance(h, SharedRotatingFileHandler))
        assert (console.level, file_handler.level, logger.level) == (logging.WARNING, logging.INFO, logging.INFO)
        logger.debug("not kept")
        logger.info("kept info")
        logger.warning("kept warning")
        file_handler.flush()
        assert "kept info" not in console.stream.getvalue() and "kept warning" in console.stream.getvalue()
        text = path.read_text(encoding="utf-8")
        assert "kept info" in text and "kept warning" in text and "not kept" not in text
    finally:
        _close(logger)


def test_log_file_level_is_configurable_and_an_explicit_level_still_rules(tmp_path, ui_env):
    ui_env.setenv("LOG_FILE_LEVEL", "ERROR")
    path = tmp_path / "strict.log"
    logger = jarvis_logger.setup_logger("JARVIS-TEST-C64-STRICT", path)
    explicit = jarvis_logger.setup_logger("JARVIS-TEST-C64-EXPLICIT", tmp_path / "explicit.log",
                                          level=logging.CRITICAL)
    try:
        for handler in logger.handlers + explicit.handlers:
            if type(handler) is logging.StreamHandler:  # pylint: disable=unidiomatic-typecheck
                handler.setStream(io.StringIO())
        logger.info("an info line")
        logger.error("an error line")
        explicit.error("below the explicit level")
        assert explicit.level == logging.CRITICAL
        for handler in logger.handlers:
            handler.flush()
        text = path.read_text(encoding="utf-8")
        assert "an error line" in text and "an info line" not in text
        assert not (tmp_path / "explicit.log").exists()  # nothing reached it: never opened
    finally:
        _close(logger)
        _close(explicit)


def test_private_lines_never_reach_the_file_even_at_debug(tmp_path, ui_env):
    ui_env.setenv("LOG_FILE_LEVEL", "DEBUG")
    path = tmp_path / "debug.log"
    logger = jarvis_logger.setup_logger("JARVIS-TEST-C64-PRIVATE", path)
    try:
        for handler in logger.handlers:
            if type(handler) is logging.StreamHandler:  # pylint: disable=unidiomatic-typecheck
                handler.setStream(io.StringIO())
        logger.info("Focused window: %s", "Bank - Chrome")          # a private template, demoted
        logger.debug("Clipboard says %s", "PIN 0000", extra=jarvis_logger.PRIVATE)
        logger.debug("an ordinary debug line")
        logger.warning("Focused window: %s", "kept at WARNING")
        for handler in logger.handlers:
            handler.flush()
        text = path.read_text(encoding="utf-8")
        assert "Bank - Chrome" not in text and "PIN 0000" not in text
        assert "an ordinary debug line" in text and "kept at WARNING" in text
    finally:
        _close(logger)


def test_the_bridge_role_gets_no_ui_handler_and_the_agent_role_the_one_shared(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ROLE", "bridge")
    bridge = jarvis_logger.setup_logger("JARVIS-TEST-C64-ROLE-BRIDGE", tmp_path / "role.log")
    monkeypatch.setenv("JARVIS_ROLE", "agent")
    agent = jarvis_logger.setup_logger("JARVIS-TEST-C64-ROLE-AGENT", tmp_path / "role.log")
    try:
        assert not any(isinstance(h, UIBridgeHandler) for h in bridge.handlers)
        [ui] = [h for h in agent.handlers if isinstance(h, UIBridgeHandler)]
        assert ui is jarvis_logger.ui_bridge_handler()
    finally:
        _close(bridge)
        _close(agent)


def test_a_basic_rollover_keeps_every_line(tmp_path, handlers):
    path = tmp_path / "small.log"
    handler = handlers(path, max_bytes=400, backup_count=3, formatter=logging.Formatter("%(message)s"))
    logger = logging.getLogger("JARVIS-TEST-C64-ROLL")
    logger.propagate, logger.level = False, logging.INFO
    logger.addHandler(handler)
    try:
        for n in range(40):
            logger.info("line %03d %s", n, "x" * 20)
    finally:
        logger.removeHandler(handler)
    handler.flush()
    files = [path] + [Path(f"{path}.{n}") for n in (1, 2, 3)]
    assert all(file.exists() for file in files)
    lines = [line for file in files for line in file.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == len(set(lines)) and sorted(lines)[-1].startswith("line 039")
    assert not list(tmp_path.glob("*.rotating-*"))


@pytest.mark.skipif(not WINDOWS, reason="only Windows refuses to rename a file another handle holds")
def test_a_second_open_handle_postpones_the_rollover_without_an_error_or_a_lost_line(tmp_path, monkeypatch):
    clock = Clock()
    path = tmp_path / "held.log"
    handler = SharedRotatingFileHandler(path, max_bytes=300, backup_count=2, clock=clock)
    handler.setFormatter(logging.Formatter("%(message)s"))
    errors: list[logging.LogRecord] = []
    monkeypatch.setattr(handler, "handleError", errors.append)
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    logger = logging.getLogger("JARVIS-TEST-C64-HELD")
    logger.propagate, logger.level = False, logging.INFO
    logger.addHandler(handler)
    try:
        logger.info("first")
        other = open(path, "a", encoding="utf-8")  # another process or a reader  # pylint: disable=consider-using-with
        try:
            for n in range(40):
                logger.info("line %03d %s", n, "x" * 20)
            handler.flush()
            assert path.read_text(encoding="utf-8").count("line ") == 40  # nothing lost, nothing rotated
            assert not Path(f"{path}.1").exists() and errors == []
            assert handler.postponed == 1  # retried only after a minute
            clock.now += 61.0
            logger.info("still held")
            assert handler.postponed == 2 and not Path(f"{path}.1").exists()
        finally:
            other.close()
        clock.now += 61.0
        logger.info("after release")
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert Path(f"{path}.1").exists() and errors == []
    assert Path(f"{path}.1").read_text(encoding="utf-8").count("line ") == 40
    assert path.read_text(encoding="utf-8").strip() == "after release"
    assert stderr.getvalue().count("postponed") == 1  # one notice per file and hour
    assert "--- Logging error ---" not in stderr.getvalue()


@pytest.mark.skipif(not WINDOWS, reason="only Windows refuses to rename a file another handle holds")
def test_failed_rollovers_never_shift_or_delete_the_backups(tmp_path):
    clock = Clock()
    path = tmp_path / "keep.log"
    for n in (1, 2, 3):
        Path(f"{path}.{n}").write_text(f"backup {n}\n", encoding="utf-8")
    handler = SharedRotatingFileHandler(path, max_bytes=100, backup_count=3, clock=clock)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("JARVIS-TEST-C64-KEEP")
    logger.propagate, logger.level = False, logging.INFO
    logger.addHandler(handler)
    try:
        logger.info("x" * 120)
        with open(path, encoding="utf-8"):  # a reader holds the live file for many minutes
            for _ in range(6):
                clock.now += 61.0
                logger.info("y" * 120)
        assert handler.postponed == 6
        assert [Path(f"{path}.{n}").read_text(encoding="utf-8") for n in (1, 2, 3)] == [
            "backup 1\n", "backup 2\n", "backup 3\n"]
        clock.now += 61.0
        logger.info("z")
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert Path(f"{path}.2").read_text(encoding="utf-8") == "backup 1\n"  # one ordinary shift, at last
    assert Path(f"{path}.3").read_text(encoding="utf-8") == "backup 2\n"
    assert "y" * 120 in Path(f"{path}.1").read_text(encoding="utf-8")


def test_a_file_already_rotated_by_another_process_is_not_shifted_again(tmp_path):
    path = tmp_path / "gone.log"
    Path(f"{path}.1").write_text("rotated by the other process\n", encoding="utf-8")
    handler = SharedRotatingFileHandler(path, max_bytes=100, backup_count=2)
    try:
        handler.doRollover()  # the live file is not there (another process just moved it)
        assert Path(f"{path}.1").read_text(encoding="utf-8") == "rotated by the other process\n"
        assert not Path(f"{path}.2").exists() and handler.stream is not None and path.exists()
    finally:
        handler.close()


class _ElsewhereStream(io.StringIO):
    """An open stream whose path (name) now names another file, as after another process rotated it."""

    def __init__(self, name: str, other_fd: int) -> None:
        super().__init__()
        self.name = name
        self._fd = other_fd

    def fileno(self) -> int:
        return self._fd


def test_a_path_that_names_another_file_now_is_reopened_within_a_second(tmp_path):
    clock = Clock()
    path = tmp_path / "moved.log"
    path.write_text("", encoding="utf-8")
    other = tmp_path / "other.log"
    with open(other, "w", encoding="utf-8") as other_file:
        handler = SharedRotatingFileHandler(path, max_bytes=10_000, backup_count=2, clock=clock)
        handler.setFormatter(logging.Formatter("%(message)s"))
        stale = _ElsewhereStream(str(path), other_file.fileno())
        handler.stream = stale
        handler._next_path_check = clock.now + 0.5  # checked a moment ago
        try:
            handler.emit(_record("X", logging.INFO, "within the second"))
            assert handler.stream is stale and "within the second" in stale.getvalue()
            clock.now += 1.0
            handler.emit(_record("X", logging.INFO, "after the check"))
            assert handler.stream is not stale and stale.closed
            handler.flush()
            assert path.read_text(encoding="utf-8") == "after the check\n"
        finally:
            handler.close()


@pytest.mark.skipif(WINDOWS, reason="Windows refuses to rename an open file: covered by the test above")
def test_posix_a_rotation_by_another_process_is_followed(tmp_path):
    clock = Clock()
    path = tmp_path / "shared.log"
    handler = SharedRotatingFileHandler(path, max_bytes=10_000, backup_count=2, clock=clock)
    handler.setFormatter(logging.Formatter("%(message)s"))
    try:
        handler.emit(_record("X", logging.INFO, "before"))
        os.rename(path, f"{path}.1")  # another job process rotated it
        handler.emit(_record("X", logging.INFO, "same second"))  # still the old file (kept, not lost)
        clock.now += 1.0
        handler.emit(_record("X", logging.INFO, "after"))
        handler.flush()
    finally:
        handler.close()
    assert Path(f"{path}.1").read_text(encoding="utf-8") == "before\nsame second\n"
    assert path.read_text(encoding="utf-8") == "after\n"


# ---------------------------------------------------------------- jarvis_audit: security.log and the mirror


@pytest.fixture
def audit(monkeypatch, tmp_path):
    """security.log in tmp_path, a fresh shipper and mirror window, and a fake clock for the mirror cap."""
    real = [h for h in jarvis_audit.security_logger.handlers if getattr(h, "jarvis_security_log", False)]
    for handler in real:
        jarvis_audit.security_logger.removeHandler(handler)
    path = tmp_path / "security.log"
    temporary = jarvis_audit.security_log_handler(str(path))
    jarvis_audit.security_logger.addHandler(temporary)
    shipper = UiLogShipper(clock=Clock(), post=Poster())
    monkeypatch.setattr(jarvis_logger, "ui_log_shipper", shipper)
    monkeypatch.setattr(jarvis_audit, "_mirrored", type(jarvis_audit._mirrored)())
    clock = Clock()
    monkeypatch.setattr(jarvis_audit, "time", SimpleNamespace(monotonic=clock, time=lambda: 1759550000.0))
    monkeypatch.delenv("JARVIS_ROLE", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", FAKE_KEY)
    monkeypatch.setattr(jarvis_security, "_runtime_secrets", set())
    yield SimpleNamespace(path=path, shipper=shipper, clock=clock, handler=temporary)
    jarvis_audit.security_logger.removeHandler(temporary)
    temporary.close()
    for handler in real:
        jarvis_audit.security_logger.addHandler(handler)


def test_security_log_is_one_shared_5_mib_rotating_handler(audit):
    handler = audit.handler
    assert isinstance(handler, SharedRotatingFileHandler)
    assert (handler.maxBytes, handler.backupCount, handler.level) == (5 * 1024 * 1024, 5, logging.INFO)
    assert getattr(handler, "jarvis_security_log") is True
    assert jarvis_audit.security_log_handler(str(audit.path)) is handler  # one per file
    assert handler.stream is None  # opened on the first record


def test_the_live_security_log_keeps_one_marked_shared_handler_after_reloads():
    for _ in range(2):
        importlib.reload(jarvis_audit)
    marked = [h for h in jarvis_audit.security_logger.handlers if getattr(h, "jarvis_security_log", False)]
    assert len(marked) == 1 and isinstance(marked[0], SharedRotatingFileHandler)
    assert jarvis_logger._FILE_HANDLERS[jarvis_logger._handler_key(jarvis_audit.SECURITY_LOG_PATH)] is marked[0]


def test_a_critical_event_is_mirrored_masked_and_short(audit):
    jarvis_audit.JarvisAudit.log_threat(f"injection with key={FAKE_KEY} " + "z" * 500, source="TypedChat:ui")
    [(event_type, payload)] = audit.shipper.queued_events()
    assert event_type == "audit_event"
    assert payload["action"] == "THREAT_DETECTED" and payload["severity"] == "CRITICAL"
    assert payload["detail"].startswith("SOURCE=TypedChat:ui | DESC=injection with key=***")
    assert len(payload["detail"]) == 300 and FAKE_KEY not in json.dumps(payload)
    assert payload["ts"] == 1759550000.0
    audit.handler.flush()
    assert "EVENT=THREAT_DETECTED" in audit.path.read_text(encoding="utf-8")
    assert FAKE_KEY not in audit.path.read_text(encoding="utf-8")


def test_the_mirror_takes_at_most_six_a_minute_and_the_rest_stay_in_the_file(audit):
    for n in range(8):
        jarvis_audit.JarvisAudit.log_event("THREAT_DETECTED", f"n={n}", "CRITICAL")
    assert [p["detail"] for _t, p in audit.shipper.queued_events()] == [f"n={n}" for n in range(6)]
    audit.clock.now += 60.0
    jarvis_audit.JarvisAudit.log_event("THREAT_DETECTED", "n=8", "CRITICAL")
    assert len(audit.shipper.queued_events()) == 7
    audit.handler.flush()
    assert audit.path.read_text(encoding="utf-8").count("EVENT=THREAT_DETECTED") == 9


def test_info_warning_and_other_roles_are_never_mirrored(audit, monkeypatch):
    jarvis_audit.JarvisAudit.log_event("QUERY_PROCESSED", "Duration=0.10s")
    jarvis_audit.JarvisAudit.log_event("TOOL_CONFIRM", "x", "WARNING")
    jarvis_audit.JarvisAudit.log_tool("t", "high", "error")
    for role in ("bridge", "svc", "desktop"):
        monkeypatch.setenv("JARVIS_ROLE", role)
        jarvis_audit.JarvisAudit.log_threat("x")
    assert audit.shipper.queued_events() == []
    audit.handler.flush()
    assert audit.path.read_text(encoding="utf-8").count("EVENT=") == 6


# ---------------------------------------------------------------- where the shipper starts, and where it never runs


def _main_block_calls(source: str) -> list[str]:
    tree = ast.parse(source)
    calls: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test):
            calls += [ast.unparse(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)]
    return calls


def test_the_agent_starts_the_shipper_before_run_app_and_in_every_job():
    calls = _main_block_calls((ROOT / "src" / "core" / "agent.py").read_text(encoding="utf-8"))
    assert "start_ui_log_shipping" in calls
    assert calls.index("start_ui_log_shipping") < calls.index("agents.cli.run_app")
    runner = ast.parse((ROOT / "src" / "core" / "agent_runner.py").read_text(encoding="utf-8"))
    entry = next(node for node in runner.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "entrypoint")
    first_calls = [ast.unparse(stmt.value.func) for stmt in entry.body[:4]
                   if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)]
    assert "start_ui_log_shipping" in first_calls


def test_the_local_bridge_gets_its_own_log_file(monkeypatch):
    vortex = importlib.import_module("src.core.vortex")
    started: list[tuple[str, str, Any]] = []

    class Proc:
        stdout = io.StringIO("")
        pid = 1

        @staticmethod
        def poll() -> int:
            return 1

    def fake_start(command: str, name: str, extra_env: Any = None) -> Proc:
        started.append((command, name, extra_env))
        return Proc()

    monkeypatch.setenv("JARVIS_HYBRID", "0")
    monkeypatch.setenv("JARVIS_BRAIN", "pc")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")  # main() sets it on Windows: restored afterwards
    monkeypatch.setattr(vortex.agent_lock, "is_locked", lambda: True)
    monkeypatch.setattr(vortex, "start_process", fake_start)
    monkeypatch.setattr(vortex, "time", SimpleNamespace(sleep=lambda _s: None))
    with pytest.raises(SystemExit):
        vortex.main()
    [(command, name, extra_env)] = started
    assert name == "UI-BRIDGE" and "src.core.ui_bridge" in command
    assert extra_env == {"LOG_FILE": os.path.join("logs", "ui_bridge_local.log")}
    helper = (ROOT / "src" / "core" / "start_agent_with_bridge.py").read_text(encoding="utf-8")
    assert 'LOG_FILE=os.path.join("logs", "ui_bridge_local.log")' in helper


def test_the_dead_batch_writer_is_gone():
    source = (ROOT / "services" / "utils" / "jarvis_logger.py").read_text(encoding="utf-8")
    for name in ("_log_queue", "_batch_writer_worker", "_writer_task", "BatchedFileHandler", "_bg_tasks"):
        assert name not in source
    assert jarvis_logger._BRIDGE_NOTIFY_LOG is None  # kept as a name only


def test_emit_is_cheap_enough_for_the_voice_loop(shipper):
    handler = UIBridgeHandler(shipper=shipper)
    record = _record("JARVIS-X", logging.WARNING, "a warning %s", "x" * 200)
    started = time.perf_counter()
    for _ in range(2000):
        handler.handle(record)
    assert (time.perf_counter() - started) / 2000 < 0.002  # well under a millisecond each, with no I/O
    assert shipper.pending() == 200 and shipper.dropped == 1800
