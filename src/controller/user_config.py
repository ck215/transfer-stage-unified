"""The operator's persisted choices: one small JSON file outside the install
(brief rb-dist-app A3; owner decision 4, 2026-09-30: "the trial store is
chosen by the operator ... the choice is remembered").

    ~/transfer-stage-runs/station.json      ($STATION_CONFIG overrides)

It holds `map_store` (the Transfer Map's SQLite file, an absolute path, or
null) and `sample_store` (the Sample DB's, 2026-10-07), and with each its
cloud home, `map_store_home` / `sample_store_home` (2026-10-08: the drive
location a local working copy syncs back to; null for a local store,
`model.store_choice`): a Guest's choices;
a signed-in user's are in their account settings. With accounts on, a map
never opens what this file holds for a signed-in user (owner ruling
2026-10-08: no own setting -> the store prompt, never another user's data;
`controller.setup._StationChoices`). Outside the install on purpose: an update
replaces the install folder, and a choice kept there would go with it.

Read once per file and cached; every write goes straight through to disk
(read-modify-write, then an atomic replace), so a crash never leaves half a
file and two panels in one process never disagree. A missing or unreadable
file is "nothing chosen", never an error.
"""
import json
import os
import threading
from pathlib import Path

ENV = "STATION_CONFIG"
DEFAULT = Path.home() / "transfer-stage-runs" / "station.json"
#: The keys this file may hold; anything else is refused, not stored.
KEYS = ("map_store", "sample_store", "map_store_home", "sample_store_home")

_lock = threading.Lock()
_cache = {}                 # resolved path -> the dict read from it


def path():
    """The file: $STATION_CONFIG, else the default."""
    return Path(os.environ.get(ENV) or DEFAULT).expanduser()


def _load(where):
    key = str(where)
    if key not in _cache:
        try:
            data = json.loads(where.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            data = {}
        _cache[key] = {k: v for k, v in (data.items() if isinstance(data, dict) else ())
                       if k in KEYS}
    return _cache[key]


def read(key, default=None):
    """The recorded value of `key`, or `default`."""
    if key not in KEYS:
        raise KeyError(f"{key!r} is not an operator choice; known: {', '.join(KEYS)}")
    with _lock:
        return _load(path()).get(key, default)


def write(key, value):
    """Record `value` for `key` and write the file now. Raises OSError when
    the file cannot be written (the caller says so to the operator)."""
    if key not in KEYS:
        raise KeyError(f"{key!r} is not an operator choice; known: {', '.join(KEYS)}")
    where = path()
    with _lock:
        data = dict(_load(where))
        data[key] = value
        where.parent.mkdir(parents=True, exist_ok=True)
        tmp = where.with_name(where.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, where)
        _cache[str(where)] = data
    return value


def forget():
    """Drop the cache: the next read goes to disk again."""
    with _lock:
        _cache.clear()
