"""The stores' backup (2026-10-07, B) and the per-user store choice (A)."""
import os
import sqlite3
import threading
import time
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


def _age(*paths, seconds=60):
    """Make files look written `seconds` ago (past `BackupService.SETTLE_S`)."""
    when = time.time() - seconds
    for path in paths:
        os.utime(path, (when, when))


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
    assert (t.folder / backup.store_subfolder(store) / "s.sqlite").is_file()
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
    _age(pics / "a.png")                    # settled (`SETTLE_S`)
    dest = tmp_path / "backup"
    service = backup.BackupService()
    job = backup.Job(backup.Target(dest), [(store, [store.parent / "transfer_map"])])
    assert service.run(job)
    sub = backup.store_subfolder(store)
    copy = dest / sub / "transfer_map.sqlite"
    db = sqlite3.connect(copy)
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 5
    db.close()
    assert not [p for p in dest.rglob("*.part")]
    assert (dest / sub / "transfer_map" / "1" / "a.png").read_bytes() == b"one"
    assert sorted(service.copied) == [f"{sub}/transfer_map.sqlite",
                                      f"{sub}/transfer_map/1/a.png"]
    # nothing changed: nothing copied
    assert service.run(job) and service.copied == []
    # one new file: only it
    (pics / "b.png").write_bytes(b"two")
    _age(pics / "b.png")
    assert service.run(job) and service.copied == [f"{sub}/transfer_map/1/b.png"]
    # a fresh service (a restart) reads the manifest: nothing again
    again = backup.BackupService()
    assert again.run(job) and again.copied == []


def test_stores_with_the_same_name_never_share_a_backup(tmp_path):
    """Audit 2026-10-08 item 6: one flat folder per user, so two stores
    named `transfer_map.sqlite` in different folders (the default name), and
    their `exports/`, backed up over each other. Each store now has its own
    subfolder; a flat copy an earlier version left there is not touched."""
    dest = tmp_path / "backup"
    dest.mkdir()
    (dest / "transfer_map.sqlite").write_bytes(b"an older version's copy")
    stores = []
    for who, rows in (("one", 2), ("two", 7)):
        store = _db(tmp_path / who / "transfer_map.sqlite", rows=rows)
        (store.parent / "exports").mkdir()
        (store.parent / "exports" / "trials.csv").write_text(who)
        _age(store.parent / "exports" / "trials.csv")
        stores.append(store)
    service = backup.BackupService()
    job = backup.Job(backup.Target(dest), [(s, [s.parent / "exports"]) for s in stores])
    assert service.run(job)
    subs = [backup.store_subfolder(s) for s in stores]
    assert len(set(subs)) == 2 and all(sub.startswith("transfer_map-") for sub in subs)
    for store, sub, rows, who in zip(stores, subs, (2, 7), ("one", "two")):
        db = sqlite3.connect(dest / sub / "transfer_map.sqlite")
        assert db.execute("SELECT COUNT(*) FROM t").fetchone()[0] == rows
        db.close()
        assert (dest / sub / "exports" / "trials.csv").read_text() == who
    assert (dest / "transfer_map.sqlite").read_bytes() == b"an older version's copy"


def test_a_file_still_being_written_waits_for_the_next_backup(tmp_path):
    """Architecture audit 2026-10-08 item 14: a file modified in the last
    `SETTLE_S` seconds (a video still recording, a picture being saved) is
    not copied half-written; it is not marked done either, so the next run
    copies it."""
    store = _db(tmp_path / "live" / "transfer_map.sqlite")
    pics = store.parent / "transfer_map" / "7"
    pics.mkdir(parents=True)
    old, busy = pics / "before.png", pics / "trial.mp4"
    old.write_bytes(b"settled")
    _age(old)
    busy.write_bytes(b"half a vid")                 # written just now
    dest = tmp_path / "backup"
    service = backup.BackupService()
    job = backup.Job(backup.Target(dest), [(store, [store.parent / "transfer_map"])])
    assert service.run(job)
    sub = backup.store_subfolder(store)
    assert (dest / sub / "transfer_map" / "7" / "before.png").is_file()
    assert not (dest / sub / "transfer_map" / "7" / "trial.mp4").exists()
    busy.write_bytes(b"the whole video")
    _age(busy)                                      # the recording ended
    assert service.run(job)
    assert service.copied == [f"{sub}/transfer_map/7/trial.mp4"]
    assert (dest / sub / "transfer_map" / "7" / "trial.mp4").read_bytes() == b"the whole video"


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
    # A local store has no cloud home (2026-10-08: a store on the drive
    # is a local working copy whose home is remembered beside it).
    assert choices.data == {"sample_store": path, "sample_store_home": None}
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


def test_the_quit_backup_wait_is_one_budget_for_every_store(monkeypatch):
    """Audit 2026-10-08 item 4: at Quit Setup waited up to QUIT_WAIT_S
    (20 s) per store model, so both maps on a hung mount held the Quit 40 s,
    past the 30 s a SIGTERM close waits. The wait is one budget for the
    whole Quit (the Controller notifies "removed" only once every device
    has closed, `test_close_closes_every_model_before_any_view_hears_removed`)."""
    import types
    from controller import setup as setup_module
    from controller.setup import Setup

    clock = [1000.0]
    monkeypatch.setattr(setup_module.time, "monotonic", lambda: clock[0])
    waited = []

    class _HungBackup:
        QUIT_WAIT_S = backup.BackupService.QUIT_WAIT_S

        def request(self, job):
            return True

        def wait(self, timeout):
            waited.append(timeout)
            clock[0] += max(0.0, timeout)       # the mount never answers
            return False

    stub = types.SimpleNamespace(
        backup=_HungBackup(), controller=types.SimpleNamespace(_closed=True),
        _closing_store_models={"Transfer Map": object(), "Sample DB": object()},
        _request_backups=lambda models: True, NAME="Setup")
    Setup._on_models_changed(stub, "removed", "Transfer Map")
    Setup._on_models_changed(stub, "removed", "Sample DB")
    assert len(waited) == 2
    assert sum(waited) <= 15.0, f"Quit waited {sum(waited):g} s for backups"


# -- what counts as remote: a live store is never on the cloud drive (audit
# 2026-10-08 item 7; since the owner ruling of 2026-10-08 morning such a store
# is worked on through a local copy, below) -----------------------------------

def _fake_mounts(tmp_path, monkeypatch, rows):
    """A private mount table (`/proc/mounts` format) and a private home, so
    nothing here looks at the real drive."""
    table = tmp_path / "mounts"
    table.write_text("".join(f"{src} {point} {kind} rw 0 0\n"
                             for src, point, kind in rows))
    monkeypatch.setattr(store_choice, "MOUNTS", str(table))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def test_a_store_under_the_drive_folder_is_remote_even_unmounted(tmp_path, monkeypatch):
    """`~/QMDL_Drive/...` is refused by its path alone: mounted it is the
    rclone drive (live SQLite over FUSE), unmounted it is the bare
    mountpoint (files there hide under the drive once it mounts)."""
    home = _fake_mounts(tmp_path, monkeypatch, [("/dev/sda1", "/", "ext4")])
    reason = store_choice.remote_reason(home / "QMDL_Drive" / "stores" / "x.sqlite")
    assert reason and "QMDL_Drive" in reason
    assert "local" in reason and "backup" in reason
    assert store_choice.remote_reason(home / "stores" / "x.sqlite") is None


def test_a_store_on_a_fuse_or_network_mount_is_remote(tmp_path, monkeypatch):
    cloud, sshfs, nfs = tmp_path / "cloud", tmp_path / "ssh", tmp_path / "nfs"
    disk = tmp_path / "data"
    _fake_mounts(tmp_path, monkeypatch, [
        ("/dev/sda1", "/", "ext4"),
        ("remote:", str(cloud), "fuse.rclone"),
        ("me@host:", str(sshfs), "fuse.sshfs"),
        ("srv:/x", str(nfs), "nfs4"),
        ("/dev/sdb1", str(disk), "fuseblk")])            # NTFS on a local disk
    for where in (cloud, sshfs, nfs):
        reason = store_choice.remote_reason(where / "a" / "x.sqlite")
        assert reason and str(where) in reason, where
    assert store_choice.remote_reason(disk / "x.sqlite") is None
    assert store_choice.remote_reason(tmp_path / "cloudy" / "x.sqlite") is None
    # No mount table (not Linux): only the path rule.
    monkeypatch.setattr(store_choice, "MOUNTS", str(tmp_path / "no-such-file"))
    assert store_choice.remote_reason(cloud / "x.sqlite") is None


# -- a store on the drive is worked on through a local copy (owner ruling
# 2026-10-08 morning: "Store on cloud is fine, just make a local copy for
# stability of db ops") ------------------------------------------------------

class _DictChoices(dict):
    """`choices` for a model: what it remembers, in a dict."""

    def read(self, key, default=None):
        return self.get(key, default)

    def write(self, key, value):
        self[key] = value
        return value


def _drive_setup(tmp_path, monkeypatch):
    """A temporary "drive" folder that `remote_reason` calls remote, and a
    local stores folder; nothing real is touched."""
    drive = tmp_path / "drive"
    drive.mkdir()
    local = tmp_path / "local"

    def remote(path):
        path = Path(path).expanduser()
        return "on the drive" if (path == drive or drive in path.parents) else None

    monkeypatch.setattr(store_choice, "remote_reason", remote)
    monkeypatch.delenv("STATION_SAMPLE_DB", raising=False)
    monkeypatch.delenv("STATION_MAP_DB", raising=False)
    return drive, local


def _models(local):
    from model.transfer_map import TransferMap
    out = []
    for cls in (SampleMap, TransferMap):      # the class's `choices` is None
        model = cls()
        model.choices = _DictChoices()
        model.suggested_store_dir = str(local)
        out.append(model)
    return out


def _side(model):
    """A side folder of `model`'s kind, relative to its database folder."""
    return "images" if isinstance(model, SampleMap) else "exports"


def _rows(path, table="t"):
    db = sqlite3.connect(path)
    try:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        db.close()


def test_open_store_on_the_drive_opens_a_local_working_copy(tmp_path, monkeypatch):
    from model.transfer_map import TransferMap
    drive, local = _drive_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(TransferMap, "choices", None)
    monkeypatch.setattr(SampleMap, "choices", None)
    said = []
    monkeypatch.setattr(store_choice.events, "info",
                        lambda title, text, **k: said.append(text))
    for model in _models(local):
        home = _db(drive / model.NAME.replace(" ", "_") / "store.sqlite", rows=3)
        (home.parent / _side(model)).mkdir()
        (home.parent / _side(model) / "a.png").write_bytes(b"pic")
        model.store_path = str(home)
        opened = Path(model.open_store())
        assert local in opened.parents and opened.name == "store.sqlite"
        assert model.db_path == opened and model.has_store
        assert _rows(opened) == 3                      # the drive's data
        assert (opened.parent / _side(model) / "a.png").read_bytes() == b"pic"
        key = model.STORE_KEY
        assert model.choices[key] == str(opened)       # the setting is local
        assert model.choices[key + "_home"] == str(home)   # the cloud home
        assert model.store_home == home
        assert any(str(opened) in t and str(home) in t for t in said)
        model.close()


def test_the_backup_writes_the_working_copy_back_to_its_home(tmp_path, monkeypatch):
    from model.transfer_map import TransferMap
    drive, local = _drive_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(TransferMap, "choices", None)
    monkeypatch.setattr(SampleMap, "choices", None)
    warned = []
    monkeypatch.setattr(backup.events, "warn",
                        lambda title, *a, **k: warned.append(title))
    for model in _models(local):
        home = _db(drive / model.NAME.replace(" ", "_") / "store.sqlite", rows=2)
        _age(home)
        model.store_path = str(home)
        working = Path(model.open_store())
        fresh = backup.BackupService()
        assert fresh.run(backup.home_jobs([model])[0])    # the schema it added
        again = backup.BackupService()                     # a restart
        assert again.run(backup.home_jobs([model])[0])
        assert "store.sqlite" not in again.copied          # the two are the same
        _db(working, rows=5)                          # a save on this computer
        side = working.parent / _side(model)
        side.mkdir(exist_ok=True)
        (side / "new.png").write_bytes(b"new")
        _age(side / "new.png")
        jobs = backup.home_jobs([model])
        assert len(jobs) == 1 and jobs[0].target.folder == home.parent
        service = backup.BackupService()
        assert service.run(jobs[0])
        assert _rows(home) == 7                       # 2 + 5, written back
        assert (home.parent / _side(model) / "new.png").read_bytes() == b"new"
        assert not list(home.parent.rglob("*.part"))
        # Edited elsewhere since: never overwritten by the write-back.
        _db(home, rows=100)
        _db(working, rows=1)
        assert not service.run(backup.home_jobs([model])[0])
        assert _rows(home) == 107 and "Backup Failed" in warned
        model.close()


def test_a_newer_local_working_copy_is_kept(tmp_path, monkeypatch):
    from model.transfer_map import TransferMap
    drive, local = _drive_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(TransferMap, "choices", None)
    monkeypatch.setattr(SampleMap, "choices", None)
    for model in _models(local):
        home = _db(drive / model.NAME.replace(" ", "_") / "store.sqlite", rows=2)
        _age(home, seconds=600)
        model.store_path = str(home)
        working = Path(model.open_store())
        _db(working, rows=4)                          # newer here
        model.store_path = str(home)
        assert Path(model.open_store()) == working    # no question asked
        assert _rows(working) == 6 and _rows(home) == 2
        model.store_path = str(working)               # the remembered setting
        assert Path(model.open_store()) == working
        model.close()


def test_a_newer_drive_copy_asks_before_replacing_the_local_one(tmp_path, monkeypatch):
    from model.transfer_map import TransferMap
    from result import NeedsConfirm
    drive, local = _drive_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(TransferMap, "choices", None)
    monkeypatch.setattr(SampleMap, "choices", None)
    for model in _models(local):
        home = _db(drive / model.NAME.replace(" ", "_") / "store.sqlite", rows=2)
        _age(home, seconds=600)
        model.store_path = str(home)
        working = Path(model.open_store())
        _age(working, seconds=300)
        _db(home, rows=10)                            # edited elsewhere
        for typed in (home, working):                 # Open, or the setting
            model.store_path = str(typed)
            with pytest.raises(NeedsConfirm) as ask:
                model.open_store()
            assert "drive" in ask.value.prompt and ask.value.command == "open_store"
            assert _rows(working) == 2                # untouched until asked
        assert Path(model.open_store(True)) == working
        assert _rows(working) == 12                   # the drive's copy
        kept = [p for p in working.parent.glob("store.local-*.sqlite")]
        assert len(kept) == 1 and _rows(kept[0]) == 2   # the old one is kept
        model.close()


def test_new_store_on_the_drive_is_made_locally_and_synced(tmp_path, monkeypatch):
    from model.transfer_map import TransferMap
    drive, local = _drive_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(TransferMap, "choices", None)
    monkeypatch.setattr(SampleMap, "choices", None)
    for model in _models(local):
        folder = drive / model.NAME.replace(" ", "_")
        model.store_dir, model.store_name = str(folder), "fresh"
        made = Path(model.new_store())
        assert local in made.parents and made.is_file()
        assert not (folder / "fresh.sqlite").exists()     # not live on the drive
        assert model.choices[model.STORE_KEY] == str(made)
        assert model.choices[model.STORE_KEY + "_home"] == str(folder / "fresh.sqlite")
        assert backup.BackupService().run(backup.home_jobs([model])[0])
        assert store_choice.is_sqlite(folder / "fresh.sqlite")
        # A local store has no home and nothing to write back.
        model.store_dir, model.store_name = str(tmp_path / "plain" / model.NAME), "x"
        model.new_store()
        assert model.store_home is None and backup.home_jobs([model]) == []
        assert model.choices[model.STORE_KEY + "_home"] is None
        model.close()


def test_install_folder_is_still_refused(tmp_path, monkeypatch):
    from model.transfer_map import TransferMap
    from result import Refused
    drive, local = _drive_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(store_choice, "install_root", lambda: tmp_path / "install")
    monkeypatch.setattr(TransferMap, "choices", None)
    monkeypatch.setattr(SampleMap, "choices", None)
    for model in _models(local):
        model.store_dir, model.store_name = str(tmp_path / "install" / "data"), "x"
        with pytest.raises(Refused, match="station's own folder"):
            model.new_store()
        model.close()


def test_an_env_store_on_the_drive_opens_through_a_working_copy(tmp_path, monkeypatch):
    """`--map-db` / `--sample-db` naming a file on the drive: never opened
    live there, its working copy is (under the stores folder)."""
    from model.transfer_map import TransferMap
    drive, local = _drive_setup(tmp_path, monkeypatch)
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path / "data"))
    for cls, env in ((SampleMap, "STATION_SAMPLE_DB"), (TransferMap, "STATION_MAP_DB")):
        home = _db(drive / env / "store.sqlite", rows=3)
        monkeypatch.setenv(env, str(home))
        model = cls()
        assert model.has_store and store_choice.suggested_dir() in model.db_path.parents
        assert model.store_home == home and _rows(model.db_path) == 3
        model.close()
