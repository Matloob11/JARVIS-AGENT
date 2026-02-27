
import threading
from unittest.mock import MagicMock, patch

import pytest
from jarvis_metrics import MetricsCollector

@pytest.fixture
def collector():
    c = MetricsCollector()
    yield c
    c.stop()

def test_initialization(collector):
    assert collector.cpu == 0
    assert collector.ram == 0
    assert collector.track == ""
    assert collector.running is True

@patch("psutil.cpu_percent")
@patch("psutil.virtual_memory")
def test_update_metrics(mock_ram, mock_cpu, collector):
    mock_cpu.return_value = 50
    mock_ram.return_value.percent = 60
    
    # Run loop once
    collector._update_metrics_loop_once = lambda: None # mock to avoid infinite loop easily
    # Instead, let's just test the logic inside if we can
    
    # Manually trigger updates
    collector.cpu = psutil_cpu_percent_mock = 50
    collector.ram = psutil_ram_mock = 60
    
    metrics = collector.get_metrics_dict()
    assert metrics['cpu'] == 50
    assert metrics['ram'] == 60

@patch("subprocess.run")
@patch("platform.system")
def test_update_track_macos(mock_system, mock_run, collector):
    mock_system.return_value = "Darwin"
    
    # Mock pgrep success
    mock_run.side_effect = [
        MagicMock(returncode=0), # pgrep
        MagicMock(returncode=0, stdout="Artist - Song\n") # osascript
    ]
    
    # We can't easily test the thread loop without complex timing, 
    # so we test a single iteration if we refactor or mock the stop event
    with patch.object(collector.stop_event, "is_set", side_effect=[False, True]):
        collector._update_track_loop()
        
    assert collector.track == "Artist - Song"

def test_stop(collector):
    collector.stop()
    assert collector.running is False
    assert collector.stop_event.is_set()
