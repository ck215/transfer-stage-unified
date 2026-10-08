# The device log (2026-10-08)

Owner ruling, 2026-10-08: "the heater log should be stored with our db info for
trials; the heater log in terms of diagnosing failures I'm not concerned about.
Perhaps instead a separate db maintains a verbose log of such information across
ALL devices which is stored locally, this is more comprehensive. It can also be
tied into the existing events system."

So there are two homes for device data:

- **With the trial**: the Temperature Controller's readings while a trial is live
  go into the Transfer Map's trial store (`trial_heater`) and a `heater.csv` in the
  trial's folder. See `RECORDING_A_TRIAL.md`, "The heater during a trial".
- **The device log** (this page): one local SQLite file for the whole station,
  every device, every session. It replaced the heater-only per-session CSV of
  P1 (`~/transfer-stage-runs/heater/heater-<stamp>.csv`), which is no longer
  written. Old CSVs still plot with `tools/heater_plot.py FILE.csv`.

Code: `src/controller/device_log.py` (`DeviceLog`); started and stopped by the
composition root (`src/app.py`). Tests: `tests/test_device_log.py`.

## Where it lives

`<data root>/logs/device_log.sqlite`, beside the text logs
(`station-<stamp>.log`). The data root is `TRANSFER_STAGE_DATA_ROOT`, else
`~/transfer-stage-runs`. On the lab PC that is
`~/transfer-stage-runs/logs/device_log.sqlite`.

It is always local. A path under `~/QMDL_Drive`, or on any FUSE (rclone, sshfs)
or network mount, is refused with one warning ("Device Log Off") and the
station runs without the log. It is not part of any store's backup.

## What it records

| Table | Rows |
|---|---|
| `events` | Every event the event log publishes (info, warnings, errors): `t` (Unix seconds), `iso`, `severity`, `source`, `title`, `message`, `text`, `exception` (its repr), `thread`. `debug()` lines are not published, so they stay in the text log only. A repeat folded into its first line inside the 5 s dedupe window is not a new event. The log's own rows have source `Device Log`: Opened, Closed, Overflow. |
| `readings` | Every open model, sampled once a second: `(t, model, key, value, num)`. Keys: `open`, `mode`, `model_mode`, `phase`, `is_active`, `is_energized`, `is_estopped`, `is_faulted`, `fault`, `stop_confirmed`, `device.<class>` (port status), `link.<status/losses/reconnects/dropped/stalls>`, `values.<attr>` (every schema value, as the panel shows it), and `reading.<field>` for a model with a `last_reading` (the heater: `temperature_c`, `setpoint_c` (the board's ramped one), `endpoint_c`, `ramp_s_per_c`, `kp`, `ki`, `kd`, `offset_c`, `heater_on`). `value` is the text (cut at 200 characters); `num` is the number in it, or NULL. |
| `meta` | `schema_version`, `created_at`. |

A key is written only when its value changes. Every key of every model is
written again every 60 s (a keyframe), so any time window has a starting value.
`open` is 0 the first poll after a model closes.

## Never in the way

- Publishing an event costs one append to a bounded queue (20 000 entries). When
  the queue is full the oldest entry is dropped and counted. A
  `Device Log Overflow` row records the count once the writer catches up.
- One writer thread does all the disk work, in batches, with SQLite in WAL mode,
  so `sqlite3` and `heater_plot` can read while it writes. A writer stuck on the
  disk holds no publisher, no model and no FULL STOP. The tests stall it on
  purpose and run `estop_all`.
- A model whose `state` raises is skipped for that poll.
- At Quit the log closes last, after the Controller has closed every device and
  Setup has waited for the final backups. It closes within 2 s. Anything still
  queued after that is abandoned.

## Retention

Rows are kept for 14 days or up to 200 MB (live pages), whichever limit comes
first. Pruning runs at start and once a day: first by age, then the oldest
tenth at a time until the file fits. Freed pages go back to the disk
(`auto_vacuum = INCREMENTAL`). The constants are `MAX_AGE_S`, `MAX_BYTES` and
`PRUNE_EVERY_S` in `device_log.py`.

Rough size: the heater changes about four keys a second, which is about 25 MB a
day. A probe writes rows only while it moves.

## Querying it

```
sqlite3 ~/transfer-stage-runs/logs/device_log.sqlite

-- the last hour's warnings and errors
SELECT iso, severity, source, title, message FROM events
 WHERE severity IN ('warning', 'error') AND t > strftime('%s','now') - 3600
 ORDER BY t;

-- the heater's temperature between two times (local)
SELECT datetime(t, 'unixepoch', 'localtime'), num FROM readings
 WHERE model = 'Temperature Controller' AND key = 'reading.temperature_c'
   AND t BETWEEN strftime('%s', '2026-10-08 09:00', 'utc') AND strftime('%s', '2026-10-08 11:00', 'utc')
 ORDER BY t;

-- which models were energized, and when
SELECT datetime(t, 'unixepoch', 'localtime'), model, value FROM readings
 WHERE key = 'is_energized' ORDER BY t;
```

To plot the heater:

```
python3 tools/heater_plot.py                                  # the device log, last 2 h
python3 tools/heater_plot.py --log PATH --from 2026-10-08T09:00 --to 11:30
python3 tools/heater_plot.py --trial 12 --store <store>.sqlite  # one trial's readings
python3 tools/heater_plot.py ... --stats                      # step-response metrics as JSON
python3 tools/heater_plot.py ... --follow                     # redraw every 5 s
```
