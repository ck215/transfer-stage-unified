"""`controller.user_config`: the one small file of operator choices
(brief rb-dist-app A3). Every test's file is under its own tmp_path."""
import json
from pathlib import Path

import pytest

from controller import user_config


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    where = tmp_path / "runs" / "station.json"
    monkeypatch.setenv("STATION_CONFIG", str(where))
    user_config.forget()
    yield where
    user_config.forget()


def test_the_default_file_is_beside_the_flash_stamp(monkeypatch):
    monkeypatch.delenv("STATION_CONFIG")
    assert user_config.path() == Path.home() / "transfer-stage-runs" / "station.json"


def test_the_environment_names_the_file(config):
    assert user_config.path() == config


def test_nothing_recorded_reads_as_nothing_and_creates_nothing(config):
    assert user_config.read("map_store") is None
    assert user_config.read("map_store", "x") == "x"
    assert not config.exists()


def test_a_write_goes_straight_to_disk(config):
    user_config.write("map_store", "/data/trials.sqlite")
    assert json.loads(config.read_text()) == {"map_store": "/data/trials.sqlite"}
    assert user_config.read("map_store") == "/data/trials.sqlite"
    assert list(config.parent.iterdir()) == [config], "no temp file left behind"


def test_the_file_is_read_once(config):
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"map_store": "/a.sqlite"}))
    assert user_config.read("map_store") == "/a.sqlite"
    config.write_text(json.dumps({"map_store": "/b.sqlite"}))
    assert user_config.read("map_store") == "/a.sqlite"
    user_config.forget()
    assert user_config.read("map_store") == "/b.sqlite"


def test_an_unreadable_file_is_nothing_chosen(config):
    config.parent.mkdir(parents=True)
    config.write_text("{not json")
    assert user_config.read("map_store") is None


def test_only_known_keys_are_kept(config):
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"map_store": "/a.sqlite", "junk": 1}))
    user_config.write("map_store", None)
    assert json.loads(config.read_text()) == {"map_store": None}
    with pytest.raises(KeyError):
        user_config.write("theme", "dark")
    with pytest.raises(KeyError):
        user_config.read("theme")
