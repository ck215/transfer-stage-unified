import pytest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
from app import parse_controller_id

def test_parse_valid():
    assert parse_controller_id("Joy 0: Xbox") == 0
    assert parse_controller_id("Joy 12") == 12

def test_parse_none():
    assert parse_controller_id(None) is None
    assert parse_controller_id("None") is None

def test_parse_malformed():
    assert parse_controller_id("Joy") is None
    assert parse_controller_id("Joy 🕹️") is None

def test_parse_missing_space():
    assert parse_controller_id("Joy0: controller") == 0

def test_parse_non_string():
    assert parse_controller_id(123) is None
