import asyncio
from unittest.mock import patch

import pytest

from services.utils.jarvis_telemetry import JarvisTelemetry


@pytest.mark.asyncio
async def test_start_interaction_cleanup_does_not_deadlock(tmp_path):
    telemetry = JarvisTelemetry(tmp_path / "telemetry.jsonl")

    with patch("services.utils.jarvis_telemetry.time.time", return_value=10.0):
        await asyncio.wait_for(
            telemetry.start_interaction("session-1", "hello"),
            timeout=1,
        )

    assert telemetry.session_data["session-1"]["user_input"] == "hello"
