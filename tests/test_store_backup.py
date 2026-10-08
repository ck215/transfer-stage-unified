"""The stores' backup (2026-10-07, B) and the per-user store choice (A)."""
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from controller import backup
from events import events
from model import store_choice
from model.sample_map import SampleMap


def _db(path, rows=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS t (x)")
    db.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    db.commit()
    db.close()
    return path


def _titled(title, since):
    return [e for e in events.since(since) if e.title == title] \
        if hasattr(events, "since") else []


def test_target_rules(tmp_path):
    assert backup.target(None) is None                       # Guest
    assert backup.target("a@b.c", env="off") is None
    assert backup.target("a@b.c", setting=str(tmp_path / "x"), env="").folder == tmp_path / "x"
    assert backup.target("a@b.c", env=str(tmp_path)).folder == tmp_path / "a@b.c"
    t = backup.target("a@b.c", env="", home=tmp_path)
    assert t.folder == tmp_path / "QMDL_Drive" / "transfer-stage-dbs" / "a@b.c"
    with pytest.raises(backup.NoFolder):
        t.ready()                                            # no drive: no folder


def test_an_unmounted_drive_folder_is_unavailable_and_never_written(tmp_path, monkeypatch):
    """2026-10-08: `~/QMDL_Drive` there but nothing mounted on it (rclone not
    running) is NOT the drive: nothing is written into the mountpoint."""
    store = _db(tmp_path / "live" / "s.sqlite")
    (tmp_path / "QMDL_Drive").mkdir()                    # the bare mountpoint
    t = backup.target("a@b.c", env="", home=tmp_path)
    mounted = []
    monkeypatch.setattr(backup.os.path, "ismount",
                        lambda p: Path(p) in mounted)
    with pytest.raises(backup.Unavailable, match="not mounted"):
        t.ready()
    monkeypatch.setattr(backup.events, "warn", lambda *a, **k: None)
    service = backup.BackupService()
    assert not service.run(backup.Job(t, [(store, [])]))
    assert list((tmp_path / "QMDL_Drive").iterdir()) == []   # untouched
    assert "Backup failed" in service.status()
    # Mounted: the same target works.
    mounted.append(tmp_path / "QMDL_Drive")
    assert service.run(backup.Job(t, [(store, [])]))
    assert (t.folder / "s.sqlite").is_file()
    # A folder the user set has no anchor: used as it is, mounted or not.
    own = backup.target("a@b.c", setting=str(tmp_path / "mine"), env="")
    assert own.anchor is None and own.ready() == tmp_path / "mine"


def test_a_folder_set_on_the_unmounted_drive_is_unavailable_too(tmp_path, monkeypatch):
    """Architecture audit 2026-10-08: a user's own folder (or the station's
    STATION_BACKUP_DIR) under `~/QMDL_Drive` gets the default's mount check;
    without it the folder was made inside the bare mountpoint."""
    store = _db(tmp_path / "live" / "s.sqlite")
    drive = tmp_path / "QMDL_Drive"
    drive.mkdir()                                        # the bare mountpoint
    monkeypatch.setattr(backup.os.path, "ismount", lambda p: False)
    monkeypatch.setattr(backup.events, "warn", lambda *a, **k: None)
    for t in (backup.target("a@b.c", setting=str(drive / "mine"), env="", home=tmp_path),
              backup.target("a@b.c", env=str(drive / "root"), home=tmp_path)):
        assert t.anchor == drive
        with pytest.raises(backup.Unavailable, match="not mounted"):
            t.ready()
        assert not backup.BackupService().run(backup.Job(t, [(store, [])]))
    assert list(drive.iterdir()) == []                   # untouched


def test_a_backup_folder_that_is_a_stores_own_folder_is_refused(tmp_path, monkeypatch):
    """Architecture audit 2026-10-08: backing up INTO the store's own folder
    renamed the snapshot over the live database (and mirrored its folders
    onto themselves). Refused; the live file is never replaced."""
    store = _db(tmp_path / "live" / "s.sqlite", rows=3)
    (store.parent / "images").mkdir()
    (store.parent / "images" / "a.png").write_bytes(b"one")
    warned = []
    monkeypatch.setattr(backup.events, "warn",
                        lambda title, *a, **k: warned.append(title))
    inode = store.stat().st_ino
    service = backup.BackupService()
    for folder in (store.parent, store.parent / "images" / "backup"):
        job = backup.Job(backup.Target(folder), [(store, [store.parent / "images"])])
        assert not service.run(job)
    assert store.stat().st_ino == inode
    assert warned == ["Backup Failed"] and "store's own folder" in service.status()
    assert not (store.parent / "images" / "backup").exists()   # never made
    ok = backup.Job(backup.Target(tmp_path / "elsewhere"),
                    [(store, [store.parent / "images"])])
    assert service.run(ok)                               # another folder works


def test_backup_writes_a_valid_sqlite_via_rename_and_copies_new_files_only(tmp_path):
    store = _db(tmp_path / "live" / "transfer_map.sqlite", rows=5)
    pics = store.parent / "transfer_map" / "1"
    pics.mkdir(parents=True)
    (pics / "a.png").write_bytes(b"one")
    dest = tmp_path / "backup"
    service = backup.BackupService()
    job = backup.Job(backup.Target(dest), [(store, [store.parent / "transfer_map"])])
    assert service.run(job)
    copy = dest / "transfer_map.sqlite"
    db = sqlite3.connect(copy)
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 5
    db.close()
    assert not [p for p in dest.rglob("*.part")]
    assert (dest / "transfer_map" / "1" / "a.png").read_bytes() == b"one"
    assert sorted(service.copied) == ["transfer_map.sqlite", "transfer_map/1/a.png"]
    # nothing changed: nothing copied
    assert service.run(job) and service.copied == []
    # one new file: only it
    (pics / "b.png").write_bytes(b"two")
    assert service.run(job) and service.copied == ["transfer_map/1/b.png"]
    # a fresh service (a restart) reads the manifest: nothing again
    again = backup.BackupService()
    assert again.run(job) and again.copied == []


def test_requests_coalesce(tmp_path, monkeypatch):
    store = _db(tmp_path / "live" / "s.sqlite")
    service = backup.BackupService()
    gate = threading.Event()
    real = service.run

    def slow(job):
        gate.wait(5)
        return real(job)

    monkeypatch.setattr(service, "run", slow)
    job = backup.Job(backup.Target(tmp_path / "b"), [(store, [])])
    for _ in range(5):
        service.request(job)
    gate.set()
    assert service.wait(10)
    assert 1 <= service.runs <= 2


def test_a_missing_or_readonly_folder_warns_once(tmp_path, monkeypatch):
    store = _db(tmp_path / "live" / "s.sqlite")
    warned = []
    monkeypatch.setattr(backup.events, "warn",
                        lambda title, *a, **k: warned.append(title))
    drive = tmp_path / "drive"
    drive.write_text("not a folder")                 # an unmounted, odd drive
    job = backup.Job(backup.Target(drive / "x", anchor=drive), [(store, [])])
    service = backup.BackupService()
    assert not service.run(job) and not service.run(job)
    assert warned == ["Backup Failed"]
    assert "Backup failed" in service.status()
    if os.geteuid() != 0:
        ro = tmp_path / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        try:
            assert not service.run(backup.Job(backup.Target(ro), [(store, [])]))
        finally:
            ro.chmod(0o700)
        assert warned == ["Backup Failed"]           # still the same streak
    ok = backup.Job(backup.Target(tmp_path / "ok"), [(store, [])])
    assert service.run(ok) and "Last backup" in service.status()


def test_the_sample_db_with_no_choice_prompts(monkeypatch, tmp_path):
    monkeypatch.delenv("STATION_SAMPLE_DB")
    monkeypatch.setattr(SampleMap, "choices", None)
    model = SampleMap()
    assert not model.has_store and model.phase == "new_store"
    assert model.state["store"] == {"path": None, "chosen": False}
    assert model.store_dir                              # a suggested folder
    model.store_dir = str(tmp_path / "mine")
    path = Path(model.new_store())
    assert path.is_file() and model.has_store and model.phase == "sample"


def test_the_sample_db_refuses_the_install_and_offers_the_legacy_store(
        monkeypatch, tmp_path):
    monkeypatch.delenv("STATION_SAMPLE_DB")
    monkeypatch.setattr(SampleMap, "choices", None)
    install = tmp_path / "install"
    legacy = _db(install / "data" / "sample_map.sqlite", rows=3)
    (install / "data" / "images" / "S1").mkdir(parents=True)
    (install / "data" / "images" / "S1" / "p.png").write_bytes(b"p")
    monkeypatch.setattr(store_choice, "install_root", lambda: install)
    model = SampleMap()
    assert model.store_path == str(legacy) and "earlier version" in model.legacy_text
    from result import Refused
    model.store_dir = str(install / "data")
    with pytest.raises(Refused, match="inside the station"):
        model.new_store()
    with pytest.raises(Refused, match="inside the station"):
        model.open_store()
    model.store_dir = str(tmp_path / "home")
    copied = Path(model.copy_legacy_store())
    assert copied == (tmp_path / "home" / "sample_map.sqlite").resolve()
    assert legacy.is_file()                             # never moved
    assert (copied.parent / "images" / "S1" / "p.png").read_bytes() == b"p"
    assert model.has_store and model.db_path == copied


class _Choices:
    def __init__(self):
        self.data = {}

    def read(self, key, default=None):
        return self.data.get(key, default)

    def write(self, key, value):
        self.data[key] = value


def test_the_sample_store_is_remembered_and_opened(monkeypatch, tmp_path):
    monkeypatch.delenv("STATION_SAMPLE_DB")
    choices = _Choices()
    monkeypatch.setattr(SampleMap, "choices", choices)
    model = SampleMap()
    model.store_dir = str(tmp_path / "s")
    path = model.new_store()
    assert choices.data == {"sample_store": path}
    again = SampleMap()
    assert again.has_store and str(again.db_path) == path


def test_sample_writes_request_a_backup(monkeypatch, tmp_path):
    asked = []
    model = SampleMap(db_path=tmp_path / "s" / "sample_map.sqlite")
    model.backup_hook = asked.append
    model.open()
    assert asked and asked[0] is model
    assert model.backup_sources() == [(model.db_path, [model.output_root / "images",
                                                       model.output_root / "sample_map"])]
