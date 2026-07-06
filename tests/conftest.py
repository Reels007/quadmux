import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import status_bus as _status_bus  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_bus_log(tmp_path, monkeypatch):
    # StatusBus._log appends to the real ~/.quadmux/bus.jsonl, which the live
    # server and tray also read. Redirect every test to a throwaway file.
    monkeypatch.setattr(_status_bus, "BUS_LOG", str(tmp_path / "bus.jsonl"))
