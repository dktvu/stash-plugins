"""Tests for Whisparr duplicate-add detection."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

# whisparr-bridge.py imports stashapi at load time; not required for pure helpers.
sys.modules.setdefault("stashapi", MagicMock())
sys.modules.setdefault("stashapi.stashapp", MagicMock())
sys.modules.setdefault("stashapi.log", MagicMock())


def _load_bridge_module():
    path = Path(__file__).resolve().parents[1] / "whisparr-bridge.py"
    spec = importlib.util.spec_from_file_location("whisparr_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = _load_bridge_module()


def test_is_already_exists_409():
    is_dup, movie_id = bridge.is_already_exists_error(409, {})
    assert is_dup is True
    assert movie_id is None


def test_is_already_exists_movie_exists_validator():
    resp = [{"errorCode": "MovieExistsValidator", "existingMovieId": 42}]
    is_dup, movie_id = bridge.is_already_exists_error(400, resp)
    assert is_dup is True
    assert movie_id == 42


def test_is_already_exists_unrelated_400():
    is_dup, movie_id = bridge.is_already_exists_error(400, {"message": "bad request"})
    assert is_dup is False
    assert movie_id is None
