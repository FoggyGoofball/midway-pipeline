"""
test_vision.py — unit tests for multimodal image routing helpers.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pipeline import extract_images_from_messages, _strip_data_uri, VISION_MODEL
from pipeline import set_force_vision, _consume_force_vision


def test_strip_data_uri():
    assert _strip_data_uri("data:image/png;base64,AAAA") == "AAAA"
    assert _strip_data_uri("AAAA") == "AAAA"


def test_extract_ollama_native():
    msgs = [{"role": "user", "content": "look at this",
             "images": ["AAAA", "data:image/png;base64,BBBB"]}]
    assert extract_images_from_messages(msgs) == ["AAAA", "BBBB"]


def test_extract_openai_parts():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "what texture is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,CCCC"}},
    ]}]
    assert extract_images_from_messages(msgs) == ["CCCC"]


def test_extract_none():
    assert extract_images_from_messages([]) == []
    assert extract_images_from_messages([{"role": "user", "content": "hello"}]) == []


def test_vision_model_is_configured():
    assert VISION_MODEL and isinstance(VISION_MODEL, str)


def test_force_vision_flag_lifecycle():
    assert _consume_force_vision() is False  # starts cleared
    set_force_vision(True)
    assert _consume_force_vision() is True   # consumed exactly once
    assert _consume_force_vision() is False  # cleared after consumption
    set_force_vision(0)                       # falsy clears it too
    assert _consume_force_vision() is False
