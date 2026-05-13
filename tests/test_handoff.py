"""Tests for phase 4 handoff: recent prose extraction."""

import importlib.util
import os

import pytest


@pytest.fixture
def qm_module():
    spec = importlib.util.spec_from_file_location(
        "qm_server",
        os.path.join(os.path.dirname(__file__), "..", "quadmux-server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extract_recent_prose_empty_when_no_buffer(qm_module):
    qm_module.shell_buffers = [[]]
    assert qm_module.extract_recent_prose(0) == ""


def test_extract_recent_prose_picks_clean_lines(qm_module):
    qm_module.shell_buffers = [[
        "\x1b[33m⠋\x1b[0m thinking...\n",
        "Here is a clean line of prose with enough length.\n",
        "● Read(/foo)\n",
        "And another reasonable sentence about the result.\n",
    ]]
    prose = qm_module.extract_recent_prose(0)
    assert "clean line of prose" in prose
    assert "another reasonable sentence" in prose
    # Tool invocation should be filtered out
    assert "Read(/foo)" not in prose
    # Spinner already stripped
    assert "⠋" not in prose


def test_extract_recent_prose_invalid_index(qm_module):
    qm_module.shell_buffers = [[]]
    assert qm_module.extract_recent_prose(5) == ""
    assert qm_module.extract_recent_prose(-1) == ""
