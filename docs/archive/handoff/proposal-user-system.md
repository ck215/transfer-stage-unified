# Proposal: a user system for the station

Profiles, a lab-local server, and the flake database. Feature request and
design proposal, no implementation. Read-only investigation of
`mvc-refactor` at `6b491ca`, 2026-10-04. Paths are relative to
`mvc-refactor/` unless they start with `main/`. Tags: **VERIFIED
(file:line)** means read in the tree; **ASSUMED** means a lab or bench fact
I could not check. Owner decisions are listed, never answered (CLAUDE.md
standing rule). Lives in git-ignored `handoff/` beside
`proposal-probe-zeroing.md` and `proposal-flake-coordinates.md`, by the
repo's convention; mirrored to the session scratchpad.

The owner's request, verbatim: "we want a user system for the software. A
local web server in the lab would store preferences such as custom
controller binds, default controllers and transfer stage launch defaults,
as well as color profiles once the camera software is prepped, and
databases of flake info." Added by the lead the same day: "The database
could also now allow for sorting by features such as date range, material,
dimensions, etc."

The flake-coordinate proposal (`handoff/proposal-flake-coordinates.md`,
the "Sample Map": corner registration, flake markers, guidance mode) is
done. **Its record names are adopted here unchanged** (its §4.1, export
schema `"flake-coords/1"`, §12); this document owns the server, the
search and the user layer on top, and §4.9 lists every conflict and every
server-side addition against that contract.

**Revised 2026-10-04 (later)** at the owner's word, applied to both
proposals: flake `quality` and defect tags; approximate thickness
(`thickness_approx_*`, `layers_estimate`) separated from exact AFM
thickness (`thickness_afm_*`), both searchable, with an "AFM-confirmed
only" filter; the cut descriptors `feature_height_nm` and
`width_optical_um` on the Transfer Map's trials, in the one version-6
migration (§7.3, statement by statement); two owner decisions recorded
(§1.5): guidance only, no model-to-model motion for now; a separate
`data/sample_map.sqlite`. **Same day, later still**: §11, legacy data
migration ("consider how to migrate the legacy db into the new infra"):
a read-only inventory of every data source, the field mapping, an
idempotent importer with `legacy_ref`, ordering against the phases, the
`legacy/unknown` owner, and Q20-Q22.

## 0. Summary

1. Nothing persists between launches today except the Transfer Map store,
   the run folders, the log and the firmware stamp. Every preference (which
   rows launch, which pad drives which probe, speeds, steps, the view, the
   font size) is re-entered every launch or passed as a flag.
2. The design adds one model-layer service, `ProfileService`, behind an
   interface with two implementations: local files (phase 1, no server) and
   the lab server with a local cache (phase 2). The station runs fully
   offline in both; the server is never required to run the stage.
3. Identity: local accounts with a PIN at the station, a password on the
   admin web page, roles admin / member / viewer; a built-in "Station"
   profile with no PIN preserves today's behaviour when nobody signs in.
   UCI SSO is not available to a lab-local service and is not proposed.
4. Precedence is fixed: lab default < station default < user < session
   override (flags, environment, a dropdown changed in this launch). The
   merge is one pure function with an `explain(key)` that names the scope
   each value came from.
5. Five preference namespaces, each a versioned JSON document: `launch`,
   `default_controller`, `controller_binds` (per `layout_id`, on top of the
   code-owned physical layouts), `model_params` (within `Param` bounds), and
   a `camera_profile` slot whose body schema is TBD.
6. The flake store is the Sample Map's record set, `flake-coords/1`:
   `samples`, `registrations` with `corners`, `flakes` with
   `observations`, `images`. The station's own `sample_map.sqlite` stays
   the local truth; the server ingests and serves the documents, merging
   by `uid` / `flake_uid`, and adds users, reservations, materials with
   aliases, tags, an audit log, and server-only **materialised derived
   columns** (`area_um2`, `lateral_um`, `aspect_ratio`, `thickness_best_nm`)
   for search and sort.
7. Search and sort are first-class: date ranges (`exfoliated_at`,
   `searched_at`, `transferred_at`), material with alias mapping,
   dimensions from `extent_points_um`, approximate thickness
   (`layers_estimate`, `thickness_approx_nm` with its method) and exact
   AFM thickness (`thickness_afm_nm`, with an "AFM-confirmed only"
   filter), the cuts' AFM channel width, feature height and optical
   channel width through the trial links, `quality` and defect tags,
   `owner`, `status`; keyset pagination; saved searches; CSV export; a
   thumbnail grid.
8. Server: Python FastAPI over SQLite in WAL mode, images on disk by
   sha256; the same stack, migration pattern and conventions the repo
   already uses. Postgres is a later option, not a requirement.
9. Hosting: a small always-on Linux box on the lab LAN (the bench PC
   dual-boots and is often off), no internet exposure, HTTPS from a lab CA
   or plain HTTP on an isolated segment (owner call), registered with
   department IT. Security: scrypt hashes from the standard library, no
   plaintext secret anywhere, per-station device tokens, short-lived
   session tokens, soft delete with an admin-only purge, nightly
   `VACUUM INTO` backups plus an off-box copy and a scheduled restore drill.
10. Phases: 1 local profiles (same interface), 2 the server with prefs, 3
    the flake database and search (ingesting Sample Map exports first,
    live sync second), 4 camera profiles. Phase 1 alone removes the
    per-launch re-entry.

## 1. What exists today

### 1.1 Where settings live

| Setting | Where today | Scope | Tag |
|---|---|---|---|
| View (Tk / Qt / Web) | `--view`, `--tk`, `--qt`, `--web`; default `DEFAULT_VIEW = "qt"` (owner ruling 2026-09-28). `run.sh` / `run.bat` pass every flag through, store nothing. | per launch | VERIFIED `src/app.py:64,276-287`; `run.sh:1-16`; `run.bat:1-8` |
| Web port, browser, motion, font size | `--port` (8080), `--no-browser`, `--no-motion` (= `STATION_NO_MOTION=1`), `--font-size` 8-28 | per launch | VERIFIED `src/app.py:48,288-309` |
| Transfer Map store path | `--map-db` / `STATION_MAP_DB`, else `<checkout>/data/transfer_map.sqlite`; in a bundle beside the executable ("local to the installation, never global", owner ruling 2026-09-27) | per machine | VERIFIED `src/model/transfer_map.py:576-586` |
| Run output and log root | `TRANSFER_STAGE_DATA_ROOT`, else `~/transfer-stage-runs/` (`<run_id>/` CSV + `_station_meta.json`; `logs/station-*.log`) | per machine | VERIFIED `src/model/red_monitor.py:380-389`; `src/events.py:153-161` |
| Firmware stamp | `~/transfer-stage-runs/flashed.json` (`STATION_FLASH_STAMP`) | per machine | VERIFIED `src/controller/firmware.py:43-44` |
| Which rows launch, which port | Setup starts every row unticked at SIM/On; the scan auto-assigns by identity byte and ticks the rows that answered; the operator's own choice is kept in an in-memory `_chosen` set for this launch only | per launch | VERIFIED `src/controller/setup.py:277-289,930-971` |
| Which gamepad drives which probe | the Setup row's gamepad dropdown, starting at `"None"` every launch; the choice is an SDL label `"ID 0: Xbox Series X Controller"` whose index depends on plug order | per launch | VERIFIED `setup.py:285`; `src/devices/gamepad.py:71-78` |
| Step sizes, speeds, brakes, heater PID, rotator step, red threshold | `Param` class constants with `default`, `minimum`, `maximum` | code | VERIFIED `src/model/probe.py:111-122,995-997,1019-1025,1044-1046`; `heater.py:61-74`; `rotator.py:105-109`; `red_monitor.py:271-296` |
| Update, firmware and echo switches | `STATION_NO_UPDATE_CHECK`, `STATION_NO_FIRMWARE_CHECK`, `STATION_ECHO_EVENTS`; tests add `STATION_NO_WINDOWS` | environment | VERIFIED `setup.py:1256,1529`; `events.py:222` |
| QSettings, configparser, a settings JSON | none. `.gitignore` lists `config.ini` and `*.local.json` but nothing in `src/`, `legacy/src/` or `main/src/` reads either. | - | VERIFIED (grep) |

"Remembered config" in the code (`controller.add(NAME, model, config)`,
`Setup.model_from_config`) is the in-memory build config a closed tab is
reopened from. It does not survive the process. VERIFIED `setup.py:300-304,1178-1193`.

The run sidecar `_station_meta.json` is the closest thing to a persisted
"configuration": region, threshold, baseline, sample mode, source, tilt,
annotations (`specimen_id`, `consumable_id`, `note`), achieved rate
(VERIFIED `red_monitor.py:1058-1103`). It records what one run did;
nothing reads it back as a default.

### 1.2 Controller binds and the default controller

Two layers exist in the code, and the proposal keeps them apart:

- **Physical layout** (pad model → generic channels). `devices/gamepad.py`
  has four `LAYOUTS` rows, `t16000m`, `logitech_f310` (dinput / xinput),
  `xbox_bluetooth` and `xbox`, matched by SDL name substring and, on Linux,
  a Bluetooth bus GUID, each with per-platform axis and button indices.
  They produce the channel contract `NEUTRAL`: `axis_x`, `axis_y`,
  `trigger_left`, `trigger_right`, `hat_x`, `hat_y`, `bumper_left`,
  `bumper_right`. VERIFIED `gamepad.py:382-391,415-499`. The rows are code,
  pinned by `tests/test_gamepad_layouts.py`, and marked UNVERIFIED on the
  bench (GAMEPAD-11..14, STATUS "Bench" item 1).
- **Action mapping** (channels → what the probe does). `Probe` reads
  `axis_x` / `axis_y` as the X/Y jog, the two triggers as Z up and Z down
  velocity, the bumpers as Z steps and the hat as X/Y steps. It is
  hard-coded in one place. VERIFIED `src/model/probe.py:606-623`.

There is no rebinding today, no inversion, no deadzone or scale preference,
and the pad choice is a per-launch dropdown (`GamepadInput.set_gamepad`,
VERIFIED `src/model/gamepad_input.py:138-186`). "Custom controller binds"
is therefore a new feature: a per-user document on top of the channel
contract. The physical layouts stay in code, because whether `hat_x = +1`
should move the stage right is a bench ruling (GAMEPAD-13), not a
preference, and a user document must never be able to hide a wrong sign at
the lab level.

### 1.3 Launch defaults

Everything in §1.1 marked "per launch". The owner's "transfer stage launch
defaults" reads as: which view, which rows, which port policy per row, which
pad for which probe, and the model parameters a person always changes first
(steps, manual speed). Port names (`/dev/ttyACM0`, `COM3`) are a property of
the machine, not the person; the proposal puts them in the station scope
and keeps the scan's auto-assign as the default policy.

### 1.4 Camera and colour

There is no camera path. Red Percent measures the screen (`mss`) under a
region, the same approach as `main`. `main/src/camera_control.py` is a
standalone ToupCam preset tool ("doesn't work" in its own commit message,
never imported by the app) whose JSON presets carry `exposure_us`, `gain`,
`brightness`, `contrast`. VERIFIED `handoff/audit-redpercent-camera.md`
(RPC-6), `main/src/camera_control.py:21-100`. The audit's consequence
stands: red % depends on exposure and gain and neither is recorded with a
run. A colour profile is scientifically useful only if its identity is
stamped onto every run and trial it applies to. §3.6 designs the slot that
way. The `theme.py` tokens (the Signature aesthetic) are UI theming, ruled
by the owner, and are **not** what "color profiles" means here; the
proposal does not add per-user UI themes.

### 1.5 How data is persisted

- `src/schema.py` is the **UI** schema (sections, entries, buttons for the
  views), not a data schema. VERIFIED `src/schema.py` docstring; MODEL_CONTRACT step 4.
- The Transfer Map store (`TrialStore`, `src/model/transfer_map.py`) is one
  SQLite file, `PRAGMA user_version` 5, tables `trials` (34 columns at
  version 5; 30 in the version-4 file on this Mac),
  `profile` (`trial_id, t_s, red, z, x, y`) and `tips`; forward-only
  migration by `ALTER TABLE ... ADD COLUMN` plus a backfill, idempotent
  (`_CREATE` is `IF NOT EXISTS`, a cut-short upgrade finishes next time).
  Exports are three CSVs under `<output_root>/exports/`; `import_csv`
  accepts trials typed elsewhere. VERIFIED `transfer_map.py:100-235,1776-1880`.
  The file on this Mac is at version 4 with the four-column video upgrade
  pending and holds no trials (`sqlite3 -readonly` on a copy: `user_version`
  4, 0 rows in every table); the lab's real trials are on the bench PC (§11.1).
- Red Percent writes one folder per run: a CSV (`t_s, red_percent, …`) and
  the sidecar. VERIFIED `red_monitor.py:70-135,1135`.
- The Sample Map (proposed) adds `data/sample_map.sqlite` with the same
  conventions, `STATION_SAMPLE_DB` / `--sample-db`, and a JSON + CSV export
  (`flake-coords/1`) merged by `uid` / `flake_uid`, newer `updated_at`
  wins. `proposal-flake-coordinates.md` §4.2, §4.4, §12.
- The Web view binds `127.0.0.1` only, checks `Origin`/`Host`, has no
  session token (owner ruling 2026-09-21). It is the station's own
  frontend, not a service for the lab, and this proposal leaves it that way.
  VERIFIED `src/views/web/server.py:15-20,63,585-602`.
- No column anywhere names the operator. `trials` has `tip_id`, `note`,
  `origin` ('recorded' / 'imported') and no user; the Sample Map's `owner`
  is free text "the user system maps to a user". VERIFIED (store schema;
  flake-coords §4.1).

Precedents that bind the design: the owner's "no token file" ruling for the
updater (it uses the machine's own `gh auth token` / `git credential fill`,
VERIFIED `src/controller/updater.py:37-38,289-306`); the import rules
(`views/` never imports `model`/`devices`; `model/`, `devices/`, `panel.py`
never import `views`/`controller`; one owner per hardware library,
VERIFIED `tests/test_architecture.py`); "the store is local to the
installation, never global" for the Transfer Map; and the Sample Map's
rule that **stage positions are boot-relative counts, valid only within a
`position_epoch`** (flake-coords §6): the server never treats `stage_x/y`
as portable; only `sample_*_um` are.

Owner rulings of 2026-10-04, recorded here and in flake-coords §11:
**guidance only for now**, no model commands another model's motion, and
automatic go-to is revisited after zeroing lands; **a separate
`data/sample_map.sqlite`**, following the Transfer Map store conventions,
joined via `sample_id` / `flake_uid`. Both close reconcile items C8 and C9
as designed.

## 2. Users and identity

### 2.1 Options weighed

| Option | What it gives | What it costs | Verdict |
|---|---|---|---|
| **A. No auth, pick a profile** | zero friction at the bench; prefs follow the person | the audit trail is only as honest as the person who clicked a name; "who deleted my flake" is unanswerable; reservations are meaningless | acceptable for prefs, not for the flake store; kept as the **offline fallback** (§2.4) |
| **B. Local accounts, PIN at the station, password on the web** | real attribution, reservations that mean something, admin role; a 4-6 digit PIN is typeable in a glove at the bench | account management (an admin page); a PIN is weak against offline brute force (§6.1) | **recommended** |
| **C. UCI SSO (Shibboleth / Duo)** | campus identity, no passwords to manage | needs an OIT-registered service provider with a resolvable campus hostname and a certificate; a lab-local box on a private segment cannot be registered and is unreachable to the IdP's redirect flow; ties the stage's login to campus network availability | **not available** for this service. The UCInetID is still the natural *username string* (§2.2). |
| D. Shared folder of JSON files, no server | the simplest "sync" | no auth, no search, conflicts by file clobbering (a file-sync client already overwrote all four checkouts once, STATUS 2026-09-28) | rejected beyond phase 1's local files; the Sample Map's export/import by hand is the sanctioned form of it |

### 2.2 Users, roles, and the default profile

```
users
  id            TEXT PRIMARY KEY      -- UUIDv4
  username      TEXT UNIQUE NOT NULL  -- the UCInetID by convention; letters, digits, '.', '-', '_'
  display_name  TEXT NOT NULL
  email         TEXT                  -- optional, never required
  role          TEXT NOT NULL         -- 'admin' | 'member' | 'viewer'
  pin_hash      TEXT                  -- scrypt, §6.1; NULL = no PIN (the Station profile only)
  password_hash TEXT                  -- scrypt; NULL = no web login
  is_active     INTEGER NOT NULL DEFAULT 1
  created_at    TEXT NOT NULL         -- ISO 8601
  created_by    TEXT REFERENCES users(id)
  disabled_at   TEXT
```

- **admin**: the owner and the PI. Creates users, resets PINs, enrols
  stations, edits lab and station defaults, purges, restores, exports the
  whole database.
- **member**: every lab member who runs the stage. Edits their own prefs,
  creates samples and flakes, reserves, soft-deletes their own records.
- **viewer**: a collaborator or a trainee in their first week. Reads the
  flake database and the web UI; cannot sign in at a station as the
  operator.
- **The `Station` profile** (`username = "station"`, `role = member`, no
  PIN, `id` fixed): selected automatically when nobody signs in. It has no
  user-scope preferences, so lab and station defaults apply unchanged.
  This is the no-regression guarantee: a launch with nobody signed in
  behaves exactly as the station behaves today, and records written in
  that state carry `owner = "station"`.

The Sample Map's `owner` and the Transfer Map's future `operator_id` are
**usernames** (text), as the flake-coords proposal writes them; the server
resolves `username → users.id` and keeps the text as written (§4.9 C6).

### 2.3 Scopes and precedence

Four scopes, merged in this order, later wins:

```
lab default  <  station default  <  user  <  session override
```

- **lab default**: one document per namespace, generated from the code's
  own constants at build (§7.1), editable by an admin.
- **station default**: one document per namespace per enrolled station
  (port names, data roots, the camera, which rows exist on this bench).
- **user**: one document per namespace per user.
- **session override**: this launch only, never written: command-line
  flags, environment variables, and a dropdown or entry changed in Setup
  after sign-in. The views offer "Remember for me" on such a change, which
  writes it to the user scope; nothing is saved silently.

Merge rule: documents are JSON objects one level deep per namespace key
(e.g. `launch.rows["Stepper Probe"].enabled`). A key present in a later
scope replaces the earlier value; a key absent inherits; `null` is not a
value (a document cannot "unset" an inherited key, it can only set one).
Lists replace, they do not concatenate. The function is pure:
`merge(lab, station, user, session) -> (effective, provenance)` where
`provenance[key]` is the scope name, so a view can label a value "from
station default". Validation runs on the **effective** document against the
namespace schema and, for `model_params`, against each `Param`'s
`minimum`/`maximum` (§3.5); an invalid key falls back to the next lower
scope and warns once by `events.warn(..., every=)`.

### 2.4 Signing in at the station

The Setup page gets a **Profile** row first (above Update and Firmware), on
the existing `Panel` pattern so all three views render it unedited: a
dropdown of active users (the Station profile first), a PIN entry, **Sign
in**, **Sign out**, a status readout ("Ian, signed in 14:02; server
online, synced 14:03" / "offline since 13:10, 3 changes waiting"). Signing
in re-applies the effective prefs to Setup's rows before Launch; signing in
after Launch applies what can be applied live (binds, the gamepad choice)
and says what needs a relaunch (the view, the port).

Offline (server unreachable, phase 2 onwards): the dropdown lists the
users cached on this station; sign-in **without a PIN** is allowed and the
session is marked `auth = "offline-unverified"` in every record it writes
and in the audit log. The alternative, caching PIN hashes for offline
verification, gives a 10^4-10^6 keyspace to anyone with the cache file and
buys little; the threat here is mis-attribution between labmates, not an
adversary. Owner call Q1.

One operator per launch. A supervisor watching a trainee is a note, not a
second login (Q12).

## 3. What is stored: the preference namespaces

Every namespace document has the same envelope and the same lifecycle.

```
pref_documents
  id             INTEGER PRIMARY KEY
  scope          TEXT NOT NULL    -- 'lab' | 'station' | 'user'
  scope_id       TEXT NOT NULL    -- '' for lab, station id, user id
  namespace      TEXT NOT NULL    -- 'launch' | 'default_controller' | 'controller_binds' | 'model_params' | 'camera_profile'
  schema_version INTEGER NOT NULL -- the namespace's body version
  body           TEXT NOT NULL    -- JSON
  revision       INTEGER NOT NULL -- server-assigned, monotonic per (scope, scope_id, namespace)
  updated_at     TEXT NOT NULL
  updated_by     TEXT REFERENCES users(id)
  updated_from   TEXT REFERENCES stations(id)   -- NULL from the web UI
  UNIQUE (scope, scope_id, namespace)

pref_history     -- every superseded body, append-only; what "undo" and conflict review read
  id, document_id, revision, body, updated_at, updated_by, updated_from
```

**Versioning.** Each namespace has its own `schema_version` and a
`migrate_<namespace>(body, from_version) -> body` function on both the
server and the client, forward-only, tested on fixture documents of every
past version. A document newer than the reader understands is kept intact
and ignored with one warning (an old station after a server upgrade). The
client cache mirrors `pref_documents` one for one.

### 3.1 `launch` (schema 1)

```json
{
  "view": "qt",                       // "qt" | "tk" | "web"
  "web": {"port": 8080, "open_browser": true},
  "font_size": null,                  // 8-28 or null = toolkit default
  "no_motion": false,
  "rows": {
    "Stepper Probe":     {"enabled": "auto", "port": "auto", "gamepad": "default_controller"},
    "Chuck Positioner":  {"enabled": "auto", "port": "auto", "gamepad": "default_controller"},
    "DC Probe":          {"enabled": "auto", "port": "auto", "gamepad": "default_controller"},
    "Temperature Controller": {"enabled": "auto", "port": "auto"},
    "Rotator":           {"enabled": "auto", "port": "auto"},
    "Transfer Map":      {"enabled": true,   "port": "On"},
    "Sample Map":        {"enabled": true,   "port": "On"}
  }
}
```

- `enabled`: `"auto"` (tick if the scan finds the board, today's behaviour),
  `true`, `false`. `port`: `"auto"` (the scan's identity match), `"SIM"`,
  or a literal port name, which is only meaningful in the **station** scope
  (a user document naming `COM3` is rejected by validation with the reason).
  Row keys are `Model.NAME`; a row the station does not have is ignored
  with a debug line; `Setup.register` remains the authority on which rows
  exist. `gamepad` is `"default_controller"` (resolve §3.2), `"None"`, or
  a controller identity (§3.2) for this row.
- `map_db`, `sample_db` and `data_root` are **station-scope only** keys
  (they are machine paths); `--map-db`, `--sample-db`, `STATION_MAP_DB`,
  `STATION_SAMPLE_DB` remain as session overrides, so `tests/conftest.py`'s
  fixtures keep working untouched.
- The lab default is generated from `app.DEFAULT_VIEW`, `app.DEFAULT_PORT`
  and `Setup`'s row list (§7.1), never hand-copied.

### 3.2 `default_controller` (schema 1)

```json
{
  "preferred": [
    {"layout_id": "xbox", "name_contains": "Xbox Series X", "guid": "0300...5e04", "label": "my wired pad"},
    {"layout_id": "t16000m"}
  ],
  "rows": {"Stepper Probe": 0, "Chuck Positioner": 1}
}
```

A controller **identity** is `(layout_id, name_contains?, guid?)`, never an
SDL index: the index changes with plug order (VERIFIED `gamepad.py:74-78`).
`rows` maps a `Model.NAME` to an entry of `preferred`. Resolution runs when
the scan's gamepad list is published: for each row, the first attached pad
matching its preferred identity that no other row has claimed; two
identical pads fall back to index order and the Setup line says so. A
`Gamepad.identity` property (`layout_id`, SDL name, GUID) is the one small
device addition this needs; `GamepadHub.names()` already enumerates.

### 3.3 `controller_binds` (schema 1)

Keyed by `layout_id` (the ids in `LAYOUTS`), optionally by model class, on
top of the channel contract. The physical layouts stay in code.

```json
{
  "xbox": {
    "*": {
      "jog_x":      {"channel": "axis_x",        "invert": false, "deadzone": 0.08, "scale": 1.0},
      "jog_y":      {"channel": "axis_y",        "invert": false, "deadzone": 0.08, "scale": 1.0},
      "z_up":       {"channel": "trigger_left",  "scale": 1.0},
      "z_down":     {"channel": "trigger_right", "scale": 1.0},
      "step_x":     {"channel": "hat_x",         "invert": false},
      "step_y":     {"channel": "hat_y",         "invert": false},
      "step_z_up":  {"channel": "bumper_left"},
      "step_z_down":{"channel": "bumper_right"}
    },
    "Chuck Positioner": {"jog_x": {"channel": "axis_x", "invert": true}}
  }
}
```

Rules, enforced by validation on the effective document:

- Actions are the eight the probe reads today (`probe.py:606-623`); the
  lab default is exactly that mapping, so an empty user document changes
  nothing. New actions (the Sample Map's "mark" channel, flake-coords §10)
  are added in code first, then become bindable.
- A channel binds to at most one action per layout/model; `deadzone` in
  [0, 0.5]; `scale` in [0.1, 1.0] of `man_full_speed` (it can only slow
  down, never exceed the Param ceiling of 3200, VERIFIED `probe.py:91`).
- **The stop is never a bind.** Ctrl+. and the disc stay the stop path
  (owner ruling 2026-09-25); no gamepad button can be made the only stop,
  and no bind can disable one. A dedicated gamepad stop button, if wanted,
  is a separate safety feature with its own tests.
- Binds change which stick fills which field of the 42-byte jog packet;
  they never change the packet. The golden gate stays byte-identical for
  the default document; a test proves a non-default document still emits a
  well-formed frame.
- `invert` on `step_x`/`step_y` is a *preference*. The lab-level sign
  (GAMEPAD-13) is still a bench ruling; a user inverting to work around a
  wrong lab sign is exactly what §1.2 warns about, so the Setup line shows
  "inverted by your profile" whenever an invert is active.

### 3.4 `model_params` (schema 1)

```json
{
  "Stepper Probe": {"x_step": 4, "y_step": 4, "z_step": 1, "man_full_speed": 300},
  "Temperature Controller": {"ramp_rate": 5}
}
```

Keys are `Param.name` under `Model.NAME`; values must satisfy the Param's
type and bounds, and a per-namespace **tunable list** says which Params a
user may set at all (proposed: the step sizes, `man_full_speed`,
`full_speed`, `step_deg`; station-only: heater `p_term`/`i_term`/`d_term`/
`offset`, Red Percent `red_min`, the Sample Map's µm-per-count table and
per-objective `um_per_px` calibration; never: `slow_speed`,
`brake_distance` until the owner says otherwise). Bench values are the
owner's (CLAUDE.md); the list is Q4. The lab default is generated from
`PARAMS` (§7.1).

### 3.5 Applying preferences to models

`Panel.__init__` seeds each Param as an attribute from `Param.default`
(MODEL_CONTRACT step 3). The service does not reach into models.
`Setup.build` constructs the model, then calls `panel.apply_defaults(dict)`
(a small base-class addition that runs each value through the same
validation `Panel.run` uses). Binds reach the probe through
`GamepadInput`: the mixin gains `set_binds(mapping)` and `_level()` reads
through the mapping; `Setup` passes the effective binds for the pad's
`layout_id` at bind time and on every `set_gamepad`. Both are additive and
testable without hardware (the fakes in `tests/test_gamepad_input.py`).

### 3.6 `camera_profile` (schema 0: envelope only)

The camera software does not exist yet, so the slot is an envelope whose
body the server stores and returns without interpreting:

```json
{
  "profile_id": "7f3c…",                 // UUIDv4
  "name": "WSe2 on 285 nm, 50x, Oct 2026",
  "camera": {"vendor": "ToupTek", "model": null, "serial": null},   // all optional today
  "objective": "50x",
  "body_schema": "camera_profile.v0",   // bumped by the camera work, with a migration
  "body": {}                             // opaque until v1
}
```

Proposed v1 body, from `main/src/camera_control.py`'s preset keys plus
what a colour profile needs: `exposure_us`, `gain`, `brightness`,
`contrast`, `white_balance` (`{"r": , "g": , "b": }` gains or
`{"temperature_k": , "tint": }`), `gamma`, `saturation`, and the Red
Percent threshold `red_min` that the profile was tuned against (red %
depends on exposure, RPC-6). Scope: station (the camera is per bench), per
objective; a user override is allowed but rare. **The scientific
requirement is the stamp**: the active `profile_id` goes into the run
sidecar (`_station_meta`), into `trials` as `camera_profile_id` (store
schema 6, §7.3) and into a flake's `calibration_source` neighbourhood
(§4.9 A8), so two readings under different profiles are never compared
unknowingly.

## 4. The flake database

### 4.1 Principle: the Sample Map's records, served

The flake-coords proposal defines the records, the station's own store
(`data/sample_map.sqlite`), and the export document `flake-coords/1`
(its §4.1, §4.2, §12). This proposal does not define a second record set.
The server:

- **ingests** `flake-coords/1` documents (uploaded by hand from an export
  in phase 3a, pushed by the station's sync in phase 3b), merging by
  `uid` / `flake_uid` with the station's own rule, newer `updated_at` wins;
- **serves** them back as the same documents (`GET …/flake-coords?since=`),
  so a second station can import another's flakes into its own Sample Map
  and guide to them after registering the same corners (flake-coords §8.5);
- **adds** what the station cannot know alone: users, reservations, the
  materials vocabulary, tags and ratings, the audit log, thumbnails, and
  **materialised derived columns** for search and sort;
- **never** serves or compares `stage_x/y/z`: they are boot-relative
  counts, meaningful only with their `registration_id`, `frame_source` and
  `position_epoch` (flake-coords §6, §12). The server stores them as the
  raw observation they are and indexes none of them.

Record names below are the flake-coords proposal's; server-only columns
are marked **(server)**.

### 4.2 `samples` (chips)

| field | from | notes |
|---|---|---|
| `sample_id` | flake-coords | text, the human key; **is** Red Percent's `specimen_id`; may be renamed |
| `uid` | flake-coords | uuid4, the merge key |
| `material`, `substrate`, `shape`, `width_um`, `height_um`, `orientation_note` | flake-coords | `material` is the default for its flakes |
| `exfoliated_at` | flake-coords | date; the lead's "date range" axis 1 |
| `created_at`, `updated_at` | flake-coords | ISO 8601 as the station writes it (§4.9 C1) |
| `owner` | flake-coords | text, a username |
| `status` | flake-coords | `active` / `stored` / `consumed` / `discarded` |
| `note` | flake-coords | |
| `owner_user_id` **(server)** | | `users.id` resolved from `owner`; NULL when unknown, shown as the raw text |
| `material_id` **(server)** | | `materials.id` resolved through aliases from `material`; NULL when unmapped |
| `storage_location` **(server)** | | `box 3 / slot B2`; proposed as an addition to flake-coords (A2) since it is a chip fact |
| `received_at`, `source_station_id`, `deleted_at`, `deleted_by` **(server)** | | receipt and soft delete |

### 4.3 `registrations` and `corners`

Stored as received; indexed only for the join. Fields are the
flake-coords proposal's: `registration_id`, `sample_id`, `frame_source`
(`stage:<Model.NAME>` / `manual:micrometer`), `position_epoch`, `k_x_um`,
`k_y_um`, `fit_kind`, `origin_stage_x`, `origin_stage_y`, `theta_rad`,
`scale`, `a11 a12 a21 a22`, `t_x`, `t_y`, `handedness`,
`derived_width_um`, `derived_height_um`, `angle_a_deg`,
`rectangularity_um`, `closure_um`, `residual_rms_um`, `quality`,
`z_travel`, `registered_at`, `invalidated_at`, `invalidated_reason`,
`refined_from`; corners: `registration_id`, `label`, `stage_x`, `stage_y`,
`stage_z`, `method`, `image_path`, `marked_at`.

Server additions: `source_station_id` and `registration_uid` **(server)**,
because `registration_id` is a per-store integer and two stations' stores
both number from 1 (§4.9 C3). Until flake-coords carries a uid of its
own, the server keys a registration by `(source_station_id,
registration_id)` and mints `registration_uid` on receipt.

### 4.4 `flakes` and `observations`

| field | from | measured / entered | notes |
|---|---|---|---|
| `flake_uid` | flake-coords | - | uuid4, the merge key |
| `label` | flake-coords | station, renameable | `F01`.. per sample |
| `sample_id` | flake-coords | - | the human key; the server also stores `sample_uid` **(server)**, resolved from the document's `samples[]` at import (§4.9 C7) |
| `sample_x_um`, `sample_y_um` | flake-coords | **measured** | the portable position; what every map and the guidance use |
| `registration_id`, `stage_x`, `stage_y`, `stage_z` | flake-coords | measured | the raw observation; stored, never indexed, never served as "where the flake is" |
| `extent_kind`, `extent_source`, `extent_points_um` | flake-coords | measured / drawn | `none` / `bbox` / `polygon`; `stage_corners` / `image_trace` / `red_mask`; the polygon in sample µm |
| `image_path`, `image_region_px`, `um_per_px`, `image_theta_rad`, `calibration_source` | flake-coords | measured | the picture and its calibration at capture |
| `red_percent`, `red_min`, `red_run_id`, `red_baseline` | flake-coords | measured | Red Percent at the flag |
| `quality` | flake-coords (2026-10-04) | entered | 1–5, the operator's rating as a transfer candidate (flake-coords §4.1a); null = not rated |
| `defects` | flake-coords (2026-10-04) | entered | JSON list from the vocabulary `cracks`, `bubbles`, `residue`, `folds`, `wrinkles`, `tears`; `[]` = inspected and clean, null = not inspected; mirrored into `flake_defects(flake_uid, defect)` **(server)** for filtering |
| `layers_estimate` | flake-coords | entered (or derived, Q16) | integer or null; **approximate** |
| `thickness_approx_nm`, `thickness_approx_method`, `thickness_approx_source` | flake-coords (2026-10-04) | entered / station | the **approximate** thickness from optics; method `optical_contrast` / `red_percent` / `colour` / `eye` / `raman`; source names the run, the calibration curve or "eye". Replaces `thickness_nm` / `thickness_method`. |
| `thickness_afm_nm`, `thickness_afm_sigma_nm`, `afm_measured_at`, `afm_by`, `afm_file` | flake-coords (2026-10-04) | measured | the **exact** thickness once AFM is done; `afm_file` is an attachment by sha (§4.5, kind `afm`); flows back from a linked trial's `thickness_nm` / `thickness_sigma_nm` at AFM attach, never from anything optical |
| `thickness_best_nm`, `thickness_is_afm` **(server, materialised)** | | computed | `thickness_afm_nm` when present, else `thickness_approx_nm`; the one sortable thickness column, with the flag that says which it is; recomputed on every write |
| `material` | flake-coords | entered | text; defaults to the sample's |
| `status` | flake-coords | entered; `transferred` set by a trial link | `candidate` / `selected` / `transferred` / `consumed` / `discarded` |
| `owner` | flake-coords | | text, a username |
| `searched_at`, `transferred_at` | flake-coords | | the lead's "search date" and "transfer date" |
| `note` | flake-coords | | |
| `trial_ids`, `run_ids`, `tip_ids` | flake-coords | | JSON lists: the joins to cutting and red-percent data |
| `created_at`, `updated_at` | flake-coords | | |
| `area_um2`, `lateral_um`, `aspect_ratio` **(server, materialised)** | | **computed** | from `extent_points_um` by the station's own `sample_frame` functions (shoelace; maximum Feret diameter; minimum-area bounding box side ratio); recomputed on every write, never accepted from a client, never exported in a `flake-coords/1` document (the contract says derived, not stored); exported in the server's CSV. The lead's recommendation, adopted. NULL when `extent_kind = none`; a `bbox` extent gives a bbox-quality value and `extent_kind` says so in the grid. |
| `chip_cell` **(server, materialised)** | | computed | a 3x3 cell (`NW` … `SE`) from `sample_x/y_um` against the sample's `width_um`/`height_um` or the registration's `derived_*`; the lead's "location on chip"; NULL without a chip size |
| `material_id`, `owner_user_id` **(server)** | | | resolved as for samples |
| `is_reserved`, `reserved_by` **(server, derived)** | | | from the active reservation; **not** a `status` value (§4.9 C5) |
| `camera_profile_id` **(server)** | | | §3.6, NULL until the camera exists (A8) |
| `received_at`, `source_station_id`, `deleted_at`, `deleted_by` **(server)** | | | |

`observations` (`flake_uid`, `registration_id`, `stage_x`, `stage_y`,
`predicted_x`, `predicted_y`, `error_um`, `observed_at`) are stored as
received with `source_station_id`; the server does nothing with them but
keep them and serve them back. They are the measured error budget.

`flake_tags(flake_uid, tag)` **(server)** with a `tags` table: the lead's
"tags", free labels set on the web UI; proposed as an addition to
flake-coords (A5) so a flag at the station can carry them.

### 4.5 `images`

The document lists `{path, sha256, flake_uid | corner}`; files travel
beside it. The server stores the bytes at `images/<sha[:2]>/<sha>.<ext>`
(content-addressed, duplicates dedupe) and keeps **(server)** `kind`
(`flake` / `corner` / `overview` / `afm`, the last an AFM data file the
flake's `afm_file` names, any format, no thumbnail), `format`, `width_px`, `height_px`,
`bytes`, `thumb_sha256` (256 px JPEG q80, made at upload), `received_at`,
`source_station_id`; `path` stays as the station wrote it, for the
round trip. A `corner` reference resolves to `(registration_uid, label)`
(§4.9 C3).

**Sizing.** The Transfer Map's whole-screen PNG is ~0.7 MB on this Mac
and more on the lab display (STATUS 2026-09-28; ASSUMED 1-2 MB there); a
capture-region PNG is 100-300 KB; a thumbnail ~20 KB. The Sample Map
stores the capture region per flake and per corner. Budget 0.5 MB per
flake and 0.3 MB per corner; at 200 flakes and 40 registrations a month
across the lab (ASSUMED) that is ~0.15 GB a month, ~2 GB a year; with an
overview per sample, ~3 GB. Trial videos (15 fps MP4 for minutes,
VERIFIED `transfer_map.py` `VIDEO_FPS`) would dominate at 5-30 MB each if
mirrored; they stay local by default (§4.8, Q6). A 1 TB SSD covers five
years with everything mirrored. Blobs are never stored in SQLite, so the
database file stays small and the nightly backup fast.

### 4.6 `materials`, `measurements`

```
materials (server)
  id TEXT PK, name TEXT UNIQUE,      -- 'graphene', 'hBN', 'MoS2', 'WSe2', 'WS2', 'MoSe2', 'NbSe2', 'black phosphorus', ...
  formula TEXT, aliases TEXT (JSON), -- ['boron nitride', 'h-BN', 'BN'] ; ['molybdenum disulfide', 'MoS₂']
  layer_thickness_nm REAL,           -- 0.335 graphene, 0.333 hBN, 0.65 MoS2, 0.70 WSe2 (literature values, ASSUMED until the owner's list)
  is_active INTEGER

measurements (server)
  id, flake_uid, kind ('afm_thickness' | 'optical_contrast' | 'red_percent' | 'raman' | 'manual'),
  value REAL, sigma REAL, unit TEXT, method_notes TEXT,
  trial_ref TEXT,                    -- "<station>/<map_db_uuid>/<trial_id>" when it came from a Transfer Map AFM attachment
  measured_at, measured_by, created_at
```

`flakes.material` and `samples.material` stay the text the operator typed;
`material_id` is resolved through `aliases` at write and at search (a
query for "boron nitride" finds `hBN`). A `measurements` row never changes
the flake's thickness by itself; the web UI offers "use this as the
flake's thickness", which writes `thickness_afm_*` (for an `afm_thickness`
row, with `afm_measured_at`, `afm_by` and `afm_file`) or
`thickness_approx_*` (for an optical row) through the normal path and
audits it; an AFM value never lands in the approximate fields or the
reverse. The initial materials list and who curates it are Q7.

### 4.7 Search, filter, sort (first-class)

**Filterable and sortable fields** (names as stored).

| axis | fields | filter forms |
|---|---|---|
| dates | `samples.exfoliated_at`, `searched_at`, `transferred_at`, `updated_at` | `after`, `before`, `last_days` |
| material | `material` via `material_id` and aliases; `samples.substrate` | one or many; `unmapped` |
| dimensions | `lateral_um`, `area_um2`, `aspect_ratio` (materialised), `extent_kind` | `min`, `max`; NULL excluded unless `include_unmeasured`; `extent_kind` to require a polygon |
| thickness, approximate | `layers_estimate`, `thickness_approx_nm`, `thickness_approx_method` | `layers` exact or list, `approx_min`, `approx_max`, `approx_method` |
| thickness, exact (AFM) | `thickness_afm_nm`, `afm_measured_at`, `afm_by` | `afm_min`, `afm_max`, `afm_after`, `afm_by`; **`afm_only=true`** = `thickness_afm_nm IS NOT NULL` ("AFM-confirmed only") |
| thickness, best | `thickness_best_nm`, `thickness_is_afm` (materialised) | `min`, `max`; the default sort column for "thickness"; the grid marks AFM values |
| cuts (via `trial_links`) | `width_um` (the AFM channel width, documented alias `width_afm_um`), `feature_height_nm`, `width_optical_um`, `width_optical_method`, `trial_status` | `min`, `max` on each; `has_cut`; `cut_afm_only` = a linked trial with `width_um IS NOT NULL`; sort by the flake's best channel width (AFM, else optical) |
| optics | `red_percent`, `red_min` | `min`, `max` |
| who / where | `owner`, `sample_id`, `sample_uid`, `source_station_id`, `chip_cell` | one or many |
| state | `status`, `is_reserved`, `reserved_by` | |
| quality, defects, tags | `quality`, `defects`, `tags` | `quality_min`; `defects_none` (inspected and clean), `defects_any_of`, `defects_none_of`; `tags_all_of` / `tags_any_of` |
| text | `label`, `note`, `material`, `samples.sample_id`, `samples.note`, `orientation_note` | full text |

**Sort** by any numeric or date field above, ascending or descending, with
`flake_uid` as the tiebreaker; default `-searched_at`.

**Query design (SQLite).** Plain B-tree indexes on `(sample_uid)`,
`(material_id)`, `(status)`, `(owner_user_id)`, `(searched_at)`,
`(transferred_at)`, `(thickness_best_nm)`, `(thickness_afm_nm)`,
`(layers_estimate)`, `(quality)`, `(lateral_um)`, `(area_um2)`,
`(deleted_at)`; `flake_defects(defect, flake_uid)`; `trial_links(flake_uid)`,
`trial_links(width_um)`, `trial_links(feature_height_nm)`; a partial index on reservations `WHERE
released_at IS NULL`; an **FTS5** virtual table over `label`, `note`,
`material`, `samples.sample_id`, `samples.note`, maintained by triggers
(FTS5 is compiled into the venv's SQLite 3.53, VERIFIED by
`pragma_compile_options`). The API's filter object is turned into
parameterised SQL by one builder with a column whitelist, the pattern
`transfer_map.py` already uses (`_checked`, VERIFIED `transfer_map.py:166-171`);
no field name from a request ever reaches SQL text. A lab-scale table
(10^4-10^5 flakes) answers any combination in milliseconds with these
indexes; the combined example "monolayer WSe2, > 20 µm, found in the last
two weeks, unreserved, AFM-confirmed, quality 4 or better" is
`material=WSe2&layers=1&min_lateral_um=20&searched_after=<now-14d>&reserved=false&afm_only=true&quality_min=4&sort=-lateral_um`.
A cut query, "flakes whose cut has an AFM channel width under 2 µm and a
feature height over 10 nm", is
`cut_afm_only=true&max_width_um=2&min_feature_height_nm=10&sort=width_um`.

**Postgres path.** The same schema; `tsvector` with a GIN index replaces
FTS5, `pg_trgm` serves alias fuzziness, and `JSONB` holds
`extent_points_um` with a GIN index if polygon queries are ever wanted.
The search module is the one place with two implementations; everything
else is portable SQL. Not proposed until the lab has a reason (§5.1).

**Pagination.** Keyset, not OFFSET: the response carries
`next_cursor = base64((sort_value, flake_uid))` and the next request passes
it back; a page is stable while others insert. `limit` 1-200, default 50.

**Saved searches.** `saved_searches(id, user_id, name, query JSON, is_shared, created_at)`.
The web UI's filter panel has "Save this search"; shared ones appear for
everyone; the station's Sample Map can list the signed-in user's saved
searches to pick a target flake to guide to (its "Go to flake", flake-coords
§7), using only `sample_*_um`.

**Web UI.** A filter panel on the left, a **thumbnail grid** (the newest
`flake` image's thumbnail, with `label`, `material`, `layers_estimate`,
`thickness_best_nm` with an AFM glyph when `thickness_is_afm`,
`lateral_um` with an `extent_kind` glyph, `quality` as 1–5 dots, `owner`,
a reserved badge), a
list view with sortable columns, a detail page (images, measurements,
reservations, the trial and run joins, the audit rows for this flake),
sample pages with every flake drawn on the chip outline at
`sample_x/y_um`, the registration history and its `quality`.

**CSV export of results.** `GET /api/v1/flakes.csv?<the same filters>`
writes the effective result set with the stored column names, one row per
flake, plus `sample_uid`, `material_name`, `reserved_by`, `image_count`,
the materialised `area_um2`, `lateral_um`, `aspect_ratio`, `chip_cell`,
`thickness_best_nm`, `thickness_is_afm`, `defects` joined by `;`, and the
linked cuts' `width_um`, `feature_height_nm`, `width_optical_um` (the
newest linked trial's, with `trial_count`); the same filters guarantee
the file is exactly the grid
shown. A full-database export is §6.3; a `flake-coords/1` document export
of any selection is `GET /api/v1/flake-coords?<filters>` (derived columns
omitted, by the contract).

### 4.8 Reservations and the cutting-data joins

```
reservations (server)
  id, flake_uid, user_id, reserved_at, expires_at, released_at,
  release_reason ('released' | 'expired' | 'transferred' | 'admin'), purpose TEXT, note TEXT
  -- at most one row per flake with released_at IS NULL (partial unique index)
```

"Reserve this flake" is a server-only operation (it needs the single
source of truth; offline the control is greyed with "Reservations need the
lab server"). It refuses with the holder's name when taken; an admin can
release anyone's; expiry (Q8) releases automatically and is audited as
`expired`. A flake reaching `transferred` or `consumed` releases its
reservation. Every reserve and release is an audit row. `is_reserved` is
derived; the station's `status = selected` ("chosen for cutting this
session", flake-coords §4.1) is a different thing and the UI copy keeps
them apart (§4.9 C5).

**Joins.** The flake carries `trial_ids`, `run_ids`, `tip_ids`
(flake-coords §4.3): `trial_ids` are integers in **one station's**
Transfer Map store, `run_ids` are Red Percent run folders, `tip_ids` match
`tips.tip_id`. The server normalises them into

```
trial_links (server)
  flake_uid, source_station_id, map_db_uuid, trial_id,          -- which store, which trial (§7.3 gives the store a uuid)
  tilt_deg, speed_steps_s, speed_measured_steps_s,
  width_um, width_sigma_um,                                       -- the AFM channel width (store column; documented alias width_afm_um)
  feature_height_nm, feature_height_sigma_nm,                     -- AFM, store version 6
  width_optical_um, width_optical_sigma_um, width_optical_method, -- approximate, optical microscopy, store version 6
  thickness_nm, thickness_sigma_nm,                               -- the AFM sample thickness at the cut (alias thickness_afm_nm)
  force_indices (JSON), trial_status, started_at, linked_at
```

filled from the Transfer Map's **trials CSV export** when the station
uploads it beside the flake document (phase 3b): a link plus a summary,
enough to search flakes by their cutting data, without making the server
the store of record for trials (owner ruling: the store is local to the
installation). When a linked trial carries `thickness_nm`, the station's
AFM attach performs, and the web UI offers, the flow-back into the
flake's `thickness_afm_nm` / `thickness_afm_sigma_nm`, with
`afm_measured_at` the attach time and `afm_by` the trial's `operator_id`;
the trial's channel widths and feature height stay on the trial.
Mirroring whole trials with profiles and videos is the owner's call (Q6).

### 4.9 Reconcile with `flake-coords/1`: conflicts and additions

Names are adopted; these are the points where the two documents disagree
(**C**) or where this one adds a field the other does not have (**A**).
Each needs one decision before either lands.

| # | item | flake-coords says | this proposal | resolution proposed |
|---|---|---|---|---|
| C1 | timestamps | ISO 8601, **local time**, "as the station writes everywhere" | the server needs an unambiguous order across stations | the server stores every `*_at` as received and adds `received_at` in UTC; ask the station to write the UTC offset (`2026-10-04T15:40:12-07:00`), which is additive and unambiguous; `transfer_map._now()` is the shared helper to change |
| C2 | merge rule | merge by `uid` / `flake_uid`, **newer `updated_at` wins** | optimistic concurrency with field-level merge | adopt the station's rule for every record that arrives in a document; the server keeps the loser in `audit_log.before` so nothing is lost; a web-UI edit bumps `updated_at` on the server and wins or loses by the same rule; `version` is server-only and never exported |
| C3 | registration identity | `registration_id` integer, per store; `corners` and `images` reference it | two stations' stores both number from 1 | ask flake-coords to add `registration_uid` (uuid4; additive, no schema bump); until then the server keys by `(source_station_id, registration_id)` and mints one |
| C4 | red percent naming | `red_percent` on flakes | the map store's column is `red`; the run CSV header is `red_percent` | adopt `red_percent` for flake records; the store keeps `red`; note it in both docs so nobody "fixes" one to match the other |
| C5 | flake status vocabulary | `candidate` / `selected` / `transferred` / `consumed` / `discarded` | this draft had `found` / `reserved` / `used` / `lost` | adopt flake-coords; `reserved` becomes the server-derived `is_reserved`, never a status; copy: "Selected" = chosen at the bench this session, "Reserved by Ian" = claimed across sessions |
| C6 | `owner`, `material` | free text | foreign keys | adopt text; the server resolves `owner_user_id` and `material_id` beside the text and shows the raw text when unresolved |
| C7 | the flake's sample reference | `sample_id` (human key, renameable) | a stable reference | the server stores `sample_uid` resolved from the document's `samples[]`; ask flake-coords to add `sample_uid` to the flake record (additive) so a rename between two exports cannot orphan a flake |
| C8 | stage positions on the server | boot-relative, valid within `position_epoch`, never portable | this draft had `GET /flakes/{id}/stage_position` | **dropped**; the server never serves a stage position; guidance is computed on the station from its current registration and the served `sample_*_um`. **Ruled 2026-10-04**: guidance only, no model commands another's motion until zeroing lands, so nothing would consume a served stage position either |
| C9 | the store of record for flakes | `data/sample_map.sqlite` on the station | this draft had a cache-and-outbox for flakes | adopt: the Sample Map store is the local truth; the sync (§5.5) pushes and pulls `flake-coords/1` documents; the cache-and-outbox is for prefs only. **Ruled 2026-10-04**: a separate `data/sample_map.sqlite`, Transfer Map conventions, joined via `sample_id` / `flake_uid` |
| C10 | Transfer Map store 6 | adds `trials.sample_id TEXT`, `trials.flake_uid TEXT` | adds `trials.operator_id`, `trials.camera_profile_id`, `meta(map_db_uuid)`, and (owner, 2026-10-04) `feature_height_nm`, `feature_height_sigma_nm`, `width_optical_um`, `width_optical_sigma_um`, `width_optical_method` | **one** version-6 migration with the union (§7.3); `flake_uid` (not `flake_id`); `width_um` keeps its name (alias `width_afm_um` in docs only); both documents now say the same |
| C11 | timestamps on `samples` | `created_at` only (`updated_at` appears in §12 and the lead's list) | the merge needs `updated_at` on every record | confirm `updated_at` is on `samples` too |
| C12 | flake thickness fields | had `thickness_nm`, `thickness_method` | the owner (2026-10-04) wants approximate and exact apart | **both documents now carry** `layers_estimate`, `thickness_approx_nm`, `thickness_approx_method`, `thickness_approx_source` and `thickness_afm_nm`, `thickness_afm_sigma_nm`, `afm_measured_at`, `afm_by`, `afm_file` (flake-coords §4.1a); the server derives `thickness_best_nm` / `thickness_is_afm`; no store exists yet, so no migration and no schema bump |
| C13 | cut descriptors | `trials.width_um` is the AFM channel width | the owner wants a feature height and an optical width beside it | columns on `trials` in §7.3; the flake record carries none of them (they describe the cut) and the server reaches them through `trial_links`; `trials.thickness_nm` stays the AFM sample thickness and flows back to the flake's `thickness_afm_*` |
| A1 | derived dimensions | derived at read time, never stored; the server computes them | materialised, server-only, indexed | agreed with the lead: `area_um2`, `lateral_um`, `aspect_ratio`, `chip_cell` are server columns recomputed on write from `extent_points_um` with the station's `sample_frame` functions, never in a document |
| A2 | `samples.storage_location` | - | where the chip physically is | propose adding to flake-coords (additive); server column meanwhile |
| A3 | flake quality | **resolved 2026-10-04**: `quality` 1–5 and `defects` on the flake record (flake-coords §4.1a) | adopted; the server adds `flake_defects` for filtering | done |
| A4 | AFM uncertainty | **resolved 2026-10-04**: `thickness_afm_sigma_nm` on the flake record | adopted | done |
| A5 | tags | - | the lead's "tags" | server `flake_tags`; propose `tags` (JSON list) on the flake record as additive |
| A6 | `source_station_id` on every record | the document has `station.name` at the top | the server needs it per record for C3 and for "where was this seen" | the server fills it from the document header; no change to the record |
| A7 | reservations, users, materials, audit, saved searches | out of scope there | here | server-only; nothing in the station store |
| A8 | `camera_profile_id` | `calibration_source` covers µm/px only | the colour profile stamp | add to the flake record when the camera lands (phase 4); server column meanwhile |

Resolved by reading the proposal (no longer open): the label scheme is
`F01`.. per sample and `sample_id` **is** Red Percent's `specimen_id`
(the "labelled like the red-percent function" ask); the sample frame's
origin, axes and handedness are its §3.1; the transform's redundant forms
are all kept; corners are `A`..`D` plus edge points and the check; the
scale lives in `k_x_um` / `k_y_um` on the registration and `um_per_px` +
`calibration_source` on the flake; `frame_source` replaces any
"stage model" field; images are referenced by `path` + `sha256`.

## 5. Architecture

### 5.1 The server

**Python, FastAPI, uvicorn, SQLite (WAL), files on disk.** Justification:

- The repo is Python 3.13+ (`pyproject.toml`), its authors are the lab, and
  the client already uses `sqlite3` with a `PRAGMA user_version`
  forward-only migration (`TrialStore._migrate`; the Sample Map adopts the
  same). One language, one migration idiom, one set of reviewers.
- FastAPI gives typed request/response models and an OpenAPI document for
  free; that document **is** the API contract, and the `flake-coords/1`
  document is validated by a pydantic model generated from flake-coords
  §4.1. The server is a separate package (`server/` in this repo or its
  own repository, Q15) with its own venv; FastAPI, pydantic and uvicorn are
  **never** imported under `src/`, and `tests/test_packaging.py` keeps them
  out of the bundle. The server venv installs the station distribution
  (`pip install -e <repo>` without `[qt]`) to import the pure
  `model.sample_frame` for the derived columns, so there is one
  implementation of the geometry.
- SQLite in WAL mode: a handful of stations, well under ten writes a
  second, one file to back up (`VACUUM INTO`), zero administration. The
  search module is the only SQLite-specific code (§4.7).
- Postgres when, and only when: more than one lab shares the server, the
  data outgrows tens of GB of metadata, or concurrent writers show lock
  waits in the server log. The schema is written to port.
- Alternatives considered: a shared folder (§2.1 D); an off-the-shelf lab
  notebook with an API (eLabFTW is open source and has one, but it has no
  sample frame, no stage coordinates, no live station client); Postgres
  from day one (administration the lab does not need yet).

Web UI: server-rendered templates (Jinja2) with vanilla JavaScript, as
the station's Web view is, no Node toolchain; `theme.py`'s tokens exported
as CSS variables so the pages look like the station (DESIGN_BRIEF).

### 5.2 Hosting

- **A small always-on Linux box** (a NUC-class mini PC with an SSD, or a
  Raspberry Pi 5 with an NVMe hat; not an SD card for a research database)
  on the lab's wired LAN, with a DHCP reservation or static address and a
  hostname the stations are configured with. The bench PC is a dual-boot
  Linux Mint / Windows machine (VERIFIED `README.md:35-41`) that is
  rebooted into Windows for Bluetooth repairs (`README.md:175-183`) and is
  off between sessions; a server there would be down exactly when another
  station needs it. Running the server **and** a station on the same
  machine is still supported (phase 2 can start that way).
- **UCI network.** A device on the campus network should be registered
  with the department's IT contact (hostname, MAC, responsible person) and
  must not expose services beyond the lab (ASSUMED: OIT's standard
  device-registration and host-security expectations; confirm with
  department IT, Q2). The server listens on the lab segment only, with a
  host firewall allowing the stations' addresses and the admin's; no port
  forwarding, no public hostname, no internet exposure. If the lab has no
  private segment, a small unmanaged switch behind the lab's uplink makes
  one; the stations keep their campus access through the same switch.
- **TLS.** Either HTTPS with a lab CA (one self-signed CA, a server cert,
  the CA file shipped to each station at enrolment and pinned by the
  client) or plain HTTP on the isolated segment. HTTPS costs a one-time
  key ceremony and a renewal reminder; HTTP means a PIN crosses the lab
  wire in the clear once per sign-in. Recommendation: HTTPS with the lab
  CA. Owner call Q3.
- Runs as a dedicated system user under systemd, data under
  `/var/lib/station-server/{db,images,backups}`, logs to journald.

### 5.3 API (sketch)

Versioned under `/api/v1`, JSON, `Authorization: Bearer <token>`; every
write takes an `Idempotency-Key` so a retried request is applied once.

```
auth
  POST /auth/station            enrol code -> station token (admin-issued code, one use)
  POST /auth/session            station token + username + pin -> session token (8 h)
  POST /auth/web                username + password -> cookie session (admin UI)
  DELETE /auth/session

users (admin unless noted)
  GET  /users                   (any role; names and roles only)
  POST /users, PATCH /users/{id}, POST /users/{id}/pin, POST /users/{id}/password
  GET  /me                      (any)

prefs
  GET  /prefs/effective?station={id}&user={id}            merged + provenance, all namespaces
  GET  /prefs/{scope}/{scope_id}/{namespace}               one document with revision
  PUT  /prefs/{scope}/{scope_id}/{namespace}               If-Match: revision; 409 on conflict with both bodies
  GET  /prefs/{scope}/{scope_id}/{namespace}/history

flake store (documents)
  POST /flake-coords                                       a flake-coords/1 document (+ multipart images by sha); merge by uid/flake_uid, newer updated_at wins; per-record results
  GET  /flake-coords?since=<server_seq>&<filters>          a flake-coords/1 document of what changed (or matches); derived columns omitted
  POST /flake-coords/trials-csv                            a Transfer Map trials export + map_db_uuid -> trial_links

flake store (records, for the web UI and search)
  GET  /samples, GET /samples/{uid}, PATCH /samples/{uid} (updated_at bumped), DELETE (soft)
  GET  /flakes?<filters>&sort=&limit=&cursor=                §4.7
  GET  /flakes.csv?<filters>
  GET  /flakes/{flake_uid}, PATCH /flakes/{flake_uid}, DELETE (soft), POST /flakes/{flake_uid}/restore (admin)
  GET  /images/{sha}, GET /images/{sha}/thumb
  POST /flakes/{flake_uid}/measurements
  POST /flakes/{flake_uid}/afm_file (multipart, sha checked; stores the attachment and sets afm_file)
  POST /flakes/{flake_uid}/reserve, POST /flakes/{flake_uid}/release
  GET/POST /materials (admin for POST), GET/POST/DELETE /saved_searches, GET/POST /tags
  GET  /audit?entity=&entity_id=&user=&after=               admin and the record's owner

admin
  POST /admin/export (zip of JSON + CSV + images), POST /admin/import, GET /admin/backups, POST /admin/purge
  GET  /healthz
```

Not in the API, by design: anything that returns a stage position (C8).

### 5.4 Client integration into the station's MVC

The station gains one service, one device, one panel row, and a few
small additive hooks. Nothing in it is required to run the stage.

```
src/model/profile.py          ProfileService + the PrefsSource interface + the merge (pure) + the prefs cache
src/devices/lab_server.py     LabServerClient(Device): the ONE importer of the HTTP client (stdlib urllib / http.client);
                              open/close/is_open/status in {'connected','offline','unauthorised','closed'}; never raises past itself
src/controller/setup.py       builds the service from the station config, adds the Profile row, applies effective prefs in build()
src/panel.py                  apply_defaults(dict) on Panel (validated like run())
src/model/gamepad_input.py    set_binds(mapping); _level() reads through it
src/devices/gamepad.py        Gamepad.identity (layout_id, name, guid)
src/model/sample_map.py       (flake-coords' model) gains `sync_now` and reads the client by duck type: push its changed records as a
                              flake-coords/1 document, pull others' into its own store (its import rule)
src/model/transfer_map.py     schema 6: the union of §7.3; `upload_trials_csv` beside `export_csv`
```

Import rules hold: `model/profile.py` and `model/sample_map.py` import
`devices.lab_server`, not the other way; nothing under `views/` learns
about profiles except through `schema()`/`state()`/`run()` of the Profile
row; `tests/test_architecture.py` gains `("urllib", "devices/lab_server.py")`
in `test_one_owner_per_hardware_library` and a rule that `fastapi`,
`pydantic`, `uvicorn` never appear under `src/`.

The interface:

```python
class PrefsSource(Protocol):
    def documents(self, scope, scope_id) -> dict[str, Document]: ...   # namespace -> (body, schema_version, revision)
    def put(self, scope, scope_id, namespace, body, if_revision) -> Document: ...
    def users(self) -> list[User]: ...
    def sign_in(self, username, pin) -> Session | None: ...            # None offline

class LocalFilesSource(PrefsSource):   # phase 1: JSON files under <data_root>/profiles/
class CachedServerSource(PrefsSource): # phase 2: SQLite cache + outbox; LabServerClient behind it
```

`ProfileService(source, station_id)` exposes `effective(user_id) ->
(dict, provenance)`, `remember(user_id, namespace, key, value)`,
`sign_in`, `sign_out`, `status` (online / offline / pending count) and
`current_user` (what the Sample Map writes into `owner` and the Transfer
Map into `operator_id`, read by duck type like `position_deg`). `Setup`
is the composition root (its docstring already names it so, VERIFIED
`setup.py:33-34`) and is the only place that constructs either.

### 5.5 Offline: the cache and the sync

- **Prefs**: one SQLite file, `<data_root>/station_cache.sqlite`
  (`TRANSFER_STAGE_DATA_ROOT`, else `~/transfer-stage-runs/`, the root
  the logs already use so Windows paths are the same `expanduser` they
  are today), tables mirroring `users` (names, roles, no hashes) and
  `pref_documents`, plus `outbox(id, created_at, method, path,
  idempotency_key, body, attempts, last_error, state)` and
  `sync_state(server_seq, last_ok_at)`.
- **Flakes**: no second cache. The Sample Map's own store is the local
  truth (C9). `sync_now` (a button, and automatic after every flag or
  registration when online) exports the records changed since the last
  sync as a `flake-coords/1` document, `POST`s it with its images, then
  `GET`s what changed on the server since `server_seq` and imports it by
  the store's own rule. A flake found on another station appears in this
  one's Sample Map with its `sample_*_um` and no stage position until the
  chip is registered here.
- **Boot order.** `Setup.__init__` reads the cache synchronously (it is
  local and fast), publishes the Profile row, and starts one background
  thread that tries the server with a 2 s connect timeout; the scan does
  not wait for it (never a blocking network call on the launch path). If
  the server answers, the cache refreshes and the outbox drains; the
  Profile row flips to "online". If not, "offline since …" and a retry
  every 30 s with backoff to 5 min.
- **Reads** always come from the local store or cache. **Writes** go
  local first; the UI never waits on the network. The server is
  authoritative on receipt; the local copy is corrected on the next pull.
- **Server-only operations** are refused offline with the reason in the
  refusal line: reserve/release, creating users, lab-scope edits.
- **Stale** is shown, never hidden: "flake list as of 13:10".
- The cache file is derived data, excluded from any backup of the station
  and never committed (the data root is outside the tree).

### 5.6 Conflict resolution between stations

| record kind | rule |
|---|---|
| user / station / lab pref document | **last writer wins by server receipt**, the loser kept in `pref_history`. A 409 on `PUT` with a stale `If-Match` returns both bodies; the client keeps the local one in the outbox flagged `conflict`, applies the server's, and the Profile row says once "your binds saved on Station B at 14:02 replaced the ones saved here at 13:58; Restore mine." |
| sample, flake | **newer `updated_at` wins** (flake-coords §4.4, C2), on the server and on every station that imports; the loser is in `audit_log.before`. Two people editing the same flake on two benches in the same minute is rare and the audit row shows it. |
| registration, corners, observations | append-only per station; keyed by `(source_station_id, registration_id)` until `registration_uid` exists (C3); never merged. |
| image | content-addressed; the same bytes from two stations are one file. |
| reservation | server-only; first request wins; no offline path. |
| measurement, trial link, audit row | append-only. |
| delete vs edit | a soft delete is an update with `deleted_at` and wins or loses by `updated_at` like any other; a restore is another update. The audit row carries both sides. |

Clocks: every record carries the station's `updated_at` and the server's
`received_at`; ordering between stations uses `updated_at` (the merge
rule) and the server's receipt is the tie-break and the audit order.
Station clocks are expected to drift; C1 asks for the offset so "newer"
is well-defined across a dual-boot machine's two clocks.

## 6. Security and data

### 6.1 Secrets and tokens

- **No plaintext secret anywhere.** PINs and passwords are hashed with
  `hashlib.scrypt` from the standard library (available in the venv's
  Python 3.14, VERIFIED) with `n=2**15, r=8, p=1`, a 16-byte random salt
  (`secrets.token_bytes`), stored as `scrypt$n$r$p$salt$hash`. Sign-in
  attempts are rate-limited per user and per station (5 failures → 60 s,
  doubling). `events` must never log a PIN, a token or an
  `Authorization` header: the client redacts before `events.debug`, and a
  test greps a captured log for a known test PIN.
- **Station (device) token.** Issued once at enrolment from an admin's
  one-use code, 32 random bytes URL-safe, stored **hashed** on the server
  with `station_id`, `name`, `enrolled_at`, `last_seen_at`, `revoked_at`.
  On the station it must be stored somewhere. The owner ruled "no token
  file" for the GitHub updater (`updater.py:37`), where the machine's own
  sign-in existed; here there is no equivalent. Options: (a) a file
  `<data_root>/station_token` with mode 0600 (and the Windows ACL
  equivalent), (b) the OS keychain through the `keyring` package (a new
  dependency, and a bundle concern), (c) no station token at all when the
  owner chooses option A of §2.1. Recommendation: (a), revocable per
  station from the admin page, named in the audit log; owner call Q5.
- **Session token.** Issued at PIN sign-in, bound to the station token,
  8 hours or until Sign out / Quit, held in memory only, sent as a Bearer
  header. The web UI uses an HttpOnly, SameSite=Strict cookie.
- Authorisation is checked server-side on every route; the client's role
  knowledge is for greying controls only.

### 6.2 Backups: research data loss is the worst failure

- Nightly `VACUUM INTO '/var/lib/station-server/backups/db-<date>.sqlite'`
  (a consistent snapshot, no downtime), `rsync` of `images/` to the same
  backup disk, then an **off-box copy**: an encrypted archive to a second
  machine or the department's file share (ASSUMED available; a USB disk
  rotated monthly is the floor). Retention: 30 daily, 12 monthly, kept
  forever for the yearly.
- A **restore drill** is part of the backup job: restore the newest
  snapshot into a temporary directory, open it, count rows per table,
  checksum a sample of images, and write the result to the admin page
  ("last verified restore: 2026-10-03 03:14, 1 284 flakes, 4 102 images
  OK"). A backup that has not been restored is a hope, not a backup.
- The admin page shows backup age; the station's Profile row warns once a
  day if the server reports a backup older than 48 h.
- Station-side: the prefs cache is derived data. The Sample Map and
  Transfer Map stores remain local and are **also** on the server after a
  sync (flakes in full; trials as summaries), which is the first off-machine
  copy either store has had; Q6 is the path to mirroring trials in full.

### 6.3 Export and import

- Full export (admin): a zip of every table as JSON lines and as CSV, the
  OpenAPI document, the schema version, and `images/` by sha, plus one
  `flake-coords/1` document of everything so a station can import it
  without the server. Import of the same bundle into an empty server, or
  a merge by uid into a running one (duplicates skipped, conflicts
  reported).
- Filtered CSV of flakes and filtered `flake-coords/1` documents from the
  web UI and the API (§4.7).
- A CSV **import** of flakes (`sample_id, label, material, …` with the
  stored column names) for records kept in spreadsheets today; unknown
  materials are mapped through aliases or left as text.
- Transfer Map trials CSVs are ingested into `trial_links` (§4.8); the
  store's own export format does not change.

### 6.4 Deletion, audit, privacy

- **Who may delete:** a member soft-deletes their own samples and flakes;
  an admin soft-deletes anyone's. Soft-deleted records disappear from
  search and the grid, remain in the detail page as "deleted by X on
  date", and can be restored by an admin for 30 days. **Hard purge** is
  admin-only, batch, after the retention window, logged with the counts.
  Audit rows, measurements, observations and trial links are never deleted.
  A station that imports a soft-deleted flake sees `deleted_at` and hides
  it the same way (the field travels in the document; propose it as an
  additive field to flake-coords alongside A2-A5).
- **Audit trail** (`audit_log`): `id, at (server), client_at, user_id,
  station_id, auth ('pin' | 'offline-unverified' | 'web'), entity,
  entity_id, action ('create' | 'update' | 'delete' | 'restore' |
  'reserve' | 'release' | 'import' | 'export' | 'sign_in' | 'sign_out' |
  'enrol' | 'revoke'), before (JSON), after (JSON), request_id`.
  Append-only: no API deletes or updates it; the backup carries it. A
  flake's page shows its rows; the admin page searches them.
- **Privacy** is minimal: a name, a username, an optional email, and who
  did what to lab records. No tracking beyond the audit log. The audit log
  is lab-internal and visible to admins and to the record's owner. If a
  member leaves, the account is deactivated, never deleted (their records
  keep an author).

## 7. Migration of today's settings

### 7.1 The lab default documents are generated, not copied

A script in the server package, `gen_lab_defaults.py`, imports the station
package and writes the lab-scope documents from the code's own constants:
`launch` from `app.DEFAULT_VIEW`, `app.DEFAULT_PORT`, `Setup`'s registered
rows; `controller_binds` from the mapping in `probe.py:606-623` for every
`LAYOUTS` row; `model_params` from every registered class's `PARAMS`
(`Param.default`); `default_controller` empty. A test asserts the generated
documents equal what the server holds for the lab scope on a fresh
install, so the default can never drift from the code. When a `Param`
default changes in code, the lab document follows at the next release; an
admin's edit to the lab document is a deliberate override, kept in
`pref_history`.

### 7.2 First launch on a station

1. No cache, no server configured: `LocalFilesSource` (phase 1) or an
   empty cache; the Station profile is active; behaviour is identical to
   today's. Nothing is written until someone presses "Remember for me".
2. Enrolment (phase 2, admin at the station or by code): the station
   document is seeded from this launch: the scan's port assignments as
   hints (`port: "auto"` stays the policy; the names are shown, not
   fixed), `data_root`, `map_db` and `sample_db` from the environment if
   set, the OS and hostname. `flashed.json` stays where it is; it is
   firmware state, not a preference.
3. Existing habits: `run.sh --web`, `--map-db`, `STATION_MAP_DB`,
   `TRANSFER_STAGE_DATA_ROOT` keep working as session overrides with the
   highest precedence, so every script, test and desktop shortcut in use
   today is unchanged. The Profile row shows "view: web (from this
   launch's flags)".

### 7.3 The Transfer Map store, schema 6 (one migration, both proposals)

One bump, `SCHEMA_VERSION = 6`, by the existing `_migrate` path: every
column below is appended to `TRIAL_COLUMNS`, so the loop at
`transfer_map.py:222-228` adds each missing one with `ALTER TABLE trials
ADD COLUMN` and skips any that exist (an upgrade cut short finishes next
time); `_CREATE` gains the `meta` table; a `version < 6` branch backfills
`meta` the way `_BACKFILL_TIPS` backfilled tips at version 3; then
`PRAGMA user_version = 6`. Proven on seeded version-4 and version-5 files
as schema 2-5 were. **NULL means not measured** for every new REAL and
TEXT column; nothing is backfilled into them. `tips` and `profile` are
unchanged. `update.sh`'s "the Transfer Map changed: your trial database is
kept" line already covers an upgrade (VERIFIED `update.sh:95-97`).

| statement | from | meaning |
|---|---|---|
| `ALTER TABLE trials ADD COLUMN sample_id TEXT` | flake-coords §4.3 | the chip (= Red Percent `specimen_id`); from the "Flake being cut" dropdown |
| `ALTER TABLE trials ADD COLUMN flake_uid TEXT` | flake-coords §4.3 | the flake being cut; arming sets the flake's `transferred_at` |
| `ALTER TABLE trials ADD COLUMN operator_id TEXT` | this proposal | a username; `station` when nobody is signed in |
| `ALTER TABLE trials ADD COLUMN camera_profile_id TEXT` | §3.6 | the colour profile in force |
| `ALTER TABLE trials ADD COLUMN feature_height_nm REAL` | owner, 2026-10-04 | the cut's **feature height by AFM**, nm; exact definition and sign are Q17 |
| `ALTER TABLE trials ADD COLUMN feature_height_sigma_nm REAL` | | its uncertainty |
| `ALTER TABLE trials ADD COLUMN width_optical_um REAL` | owner, 2026-10-04 | the **approximate channel width by optical microscopy**, µm |
| `ALTER TABLE trials ADD COLUMN width_optical_sigma_um REAL` | | its uncertainty |
| `ALTER TABLE trials ADD COLUMN width_optical_method TEXT` | | `image_px` (pixels on the station's picture at the Sample Map's `um_per_px`), `reticle`, `vendor_tool`, `estimate`; the vocabulary is Q19 |
| `CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)` | this proposal | |
| `INSERT OR IGNORE INTO meta VALUES ('map_db_uuid', <uuid4>), ('created_at', <now>)` | | the store's identity for `trial_links`; written once, never changed |
| `PRAGMA user_version = 6` | | |

Columns that keep their names, and what the documents call them:

| column | stays | documented as | why |
|---|---|---|---|
| `width_um`, `width_sigma_um` | yes | **`width_afm_um`**: the channel width measured by AFM (`attach_afm` refuses without it: "Type the channel width measured by AFM.", VERIFIED `transfer_map.py:1699-1700`) | an existing store; a rename would break every file and export on disk |
| `thickness_nm`, `thickness_sigma_nm` | yes | **`thickness_afm_nm`**: the sample thickness by AFM at the cut; flows back to the flake's `thickness_afm_*` | same |
| `status = "measured"` | yes | set only by `attach_afm` (an AFM channel width exists, `transfer_map.py:1707`); an optical width never changes `status` | the 3D map's "hollow" keeps meaning "no AFM width yet" |

**UI fields** (schema-declared, all three views unedited; tier 2 under
Configure Transfer Map, where today's "AFM measurement" section is,
VERIFIED `transfer_map.py:2187-2204`):

- "AFM measurement" gains `Feature height` (`feature_height_nm`: float,
  nm, minimum 0, 1 decimal) and `Feature height uncertainty`
  (`feature_height_sigma_nm`); both join `attach_afm`'s `inputs`. The
  width refusal is unchanged; feature height and thickness stay optional.
  The "Channel width" entry's label becomes "Channel width (AFM)". The
  event line names what was attached: "Trial 12: width 1.8 um, feature
  height 12.0 nm attached."
- A new section "Optical measurement": `Trial` (the same `afm_trial_id`
  Param), `Channel width (optical)` (`width_optical_um`: float, µm,
  minimum 0, 3 decimals), `Uncertainty` (`width_optical_sigma_um`),
  `Method` (a dropdown over the vocabulary), and the button `Attach
  optical width` (`attach_optical`): refuses an armed trial and a width
  of 0, writes the three columns, leaves `status` as it was, logs
  "Optical Width Attached". When the Sample Map is open and the trial
  names a flake with a calibrated picture, the station offers `image_px`
  and says the `um_per_px` it will use.
- `trials_log` (`transfer_map.py:2035-2050`) reads `1.8 um (AFM)`,
  `~2.1 um (optical)`, or `no width`; with both, the AFM value first and
  `optical 2.1 um` after it; feature height appended when present. The
  trials export gains every new column (it writes `TRIAL_COLUMNS`);
  `import_csv` reads `feature_height_nm`, `feature_height_sigma_nm`,
  `width_optical_um`, `width_optical_sigma_um`, `width_optical_method`,
  `sample_id`, `flake_uid` and `operator_id` when present, and sets
  `measured` only when `width_um` is present, as today.

**How the figures pick a width** (`plot_data.py`: `WIDTH_LABEL` is
"channel width (um)"; `transfer_request` reads `width` / `width_sigma`
per row and `measured` is `width is not None`, VERIFIED
`plot_data.py:414-459`):

- `_map_rows` (`transfer_map.py:1903-1912`) emits per trial `width_afm`,
  `width_afm_sigma`, `width_optical`, `width_optical_sigma`, and the
  chosen pair `width`, `width_sigma` with `width_source` in {`afm`,
  `optical`, None}: **AFM when present, else optical**. The rule is one
  pure function, `analysis.pick_width(row) -> (width, sigma, source)`, so
  every figure, the log and the export agree.
- `map3d`: `c` is the chosen width; `measured` stays AFM-only (filled
  marker); an optical-only trial draws **ringed** (a filled marker with a
  contrasting ring); no width stays hollow. Title: "Transfer map (filled:
  AFM width; ringed: optical width; hollow: no width yet)". `c_label`
  stays "channel width (um)" and the legend names the two sources, so the
  axis is one quantity with its provenance visible.
- `slice` (the Gaussian process): **AFM widths only by default**. A new
  dropdown "Width source" (`AFM only` / `AFM, else optical`) lets the
  operator include optical widths; an included optical point's noise term
  is its `width_optical_sigma_um`, or `OPTICAL_SIGMA_FACTOR` (proposed 3,
  Q19) times the AFM default `0.05 * spread`, so the GP trusts it less.
  The title says which sources and how many of each.
- `compare`: the same chosen width and the same dropdown; optical points
  drawn with the ringed marker.
- A figure made from mixed sources always says so in its title; sources
  are never silently mixed.

The Sample Map store starts at version 1 with flake-coords' schema, which
now includes `quality`, `defects`, `thickness_approx_*` and
`thickness_afm_*` (no store exists yet: design-time changes, not a
migration); the additive fields proposed in §4.9 (A2, A5, `deleted_at`,
`registration_uid`, `sample_uid`, the UTC offset) land in its version 2 if
accepted.

## 8. Phases

| phase | delivers | depends on | route |
|---|---|---|---|
| **0. Design freeze** | this document and `proposal-flake-coordinates.md` reconciled on §4.9 (C1-C11, A1-A8); owner answers to §10 | - | lead + owner |
| **1. Local profiles (MVP)** | `ProfileService` + `LocalFilesSource` (JSON under `<data_root>/profiles/`), the merge with provenance, the Profile row (profile pick, no PIN), `launch`, `default_controller`, `controller_binds`, `model_params`, `Panel.apply_defaults`, `GamepadInput.set_binds`, `Gamepad.identity`, "Remember for me", generated lab defaults, `current_user` for the Sample Map's `owner` and the store's `operator_id`, tests. No server, no network. | nothing outside the tree | one worktree (`agy`-class work: many files, its own verification pass); core hooks by the lead |
| **2. The lab server, prefs** | `server/` package: FastAPI, SQLite, users, roles, PIN/password hashing, station enrolment, prefs API with history, admin web UI (users, stations, lab/station documents), systemd unit, backup job with restore drill, TLS with the lab CA; station side: `LabServerClient`, `CachedServerSource`, outbox, sync, the Profile row's online/offline state, store schema 6 | phase 1; a box; department IT registration | server: one worktree; client: one worktree; lead verifies the offline launch path |
| **3a. The flake database by upload** | `POST /flake-coords` and the trials-CSV ingest, the record tables with materialised derived columns, materials with aliases, tags, quality and defects, the approximate / AFM thickness split with the "AFM-confirmed only" filter, the cut descriptors through `trial_links`, reservations, measurements, the search/sort API with keyset pagination, saved searches, CSV and document export, the web UI grid/list/detail/sample pages, audit log, full export/import. Fed by hand from the Sample Map's existing JSON export. | phase 2; Sample Map phase 1 (its export) | server worktree |
| **3b. Live sync** | the Sample Map's `sync_now` and automatic sync through `LabServerClient`; pulled flakes appear in another station's store; the Transfer Map's `upload_trials_csv` | 3a; Sample Map phase 2 | client worktree |
| **4. Camera profiles** | `camera_profile` v1 body and migration, the stamp in `_station_meta`, `trials` and the flake record, the station applying a profile through the camera software | the camera software | with the camera work |
| later | Postgres if needed; mirrored trials; a gamepad stop button; viewer accounts for collaborators | owner | - |

Phase 1 alone ends re-entering the gamepad, the rows and the speeds every
launch, which is the daily cost today.

## 9. Testing

### 9.1 Unit tests (fast suite, no hardware, no network)

- **Precedence merge**: table-driven over the four scopes for every
  namespace: absent inherits, present replaces, lists replace, `null`
  rejected, provenance names the right scope for every key; the effective
  document validates; an out-of-bounds `model_params` value falls back one
  scope and warns once (`events` captured, `every=` respected); a user
  document naming a literal port is rejected with the reason.
- **Binds**: the default document reproduces today's mapping exactly for
  every `LAYOUTS` row (one jog tick through the fake pad yields the same
  levels `probe.py` reads today); a duplicate channel is refused; `scale`
  and `deadzone` bounds; no action named `stop` or `estop` is accepted; a
  non-default document still produces a well-formed 42-byte jog frame
  (`struct` unpack in the test; the golden gate remains byte-identical for
  the default).
- **Default controller**: resolution by identity with one pad, two
  identical pads (index order, the note), a preferred pad absent (falls to
  `"None"`, says so), a pad another row claimed (hub claims respected).
- **Offline cache and sync**: a fake `LabServerClient` that is offline,
  then online, then returns 409, 401, 5xx and a timeout; the launch path
  never blocks (assert elapsed under a bound with the fake sleeping);
  pref writes land in the cache and the outbox immediately; the outbox
  drains in order, idempotency keys reused on retry, a 409 flags the item
  and applies the server body, a 401 marks the station unauthorised and
  stops retrying; the Sample Map's `sync_now` exports only records changed
  since the last sync, imports the pulled document by its own rule, and
  a pulled flake has no stage position until registered; reserve is
  refused offline with the reason; stale-as-of text.
- **Document round trip**: a seeded Sample Map store's `flake-coords/1`
  export `POST`ed to the server and `GET` back equals the original record
  for record (derived columns absent, `received_at` present); merging the
  same document twice is a no-op; an older `updated_at` never overwrites
  a newer one in either direction; a renamed `sample_id` keeps every
  flake attached (via `sample_uid`); two stations' `registration_id = 1`
  do not collide.
- **Derived columns**: the server's `area_um2`, `lateral_um`,
  `aspect_ratio` equal `model.sample_frame`'s on the same
  `extent_points_um` (the one implementation, imported); NULL for
  `extent_kind = none`; a client value in those fields is ignored;
  `chip_cell` at the boundaries and without a chip size;
  `thickness_best_nm` / `thickness_is_afm` recomputed on every write and
  AFM when both exist.
- **Thickness and quality**: approximate and AFM filters are independent;
  `afm_only=true` excludes every flake without `thickness_afm_nm`; the
  flow-back from a linked trial fills only the AFM fields, with
  `afm_measured_at` and `afm_by`; `quality` outside 1-5 and a defect
  outside the vocabulary are refused by the API with the reason;
  `defects_none` matches `[]` and not NULL.
- **Store version 6**: a seeded version-5 file gains every new column with
  NULLs and a `meta.map_db_uuid` that is stable across reopen; a seeded
  version-4 file gets the version-5 and version-6 columns in one pass;
  `attach_afm` writes `feature_height_nm` and leaves `width_optical_*`
  alone; `attach_optical` writes `width_optical_*`, leaves `status` as it
  was and never sets `measured`; `import_csv` reads both sets and sets
  `measured` only on `width_um`; the export carries every new column;
  `trials_log` says `(AFM)` and `(optical)`.
- **Width preference in `plot_data`**: `transfer_request` on rows with AFM
  only, optical only, both and neither: `width` is AFM when present else
  optical, `width_source` names it, `measured` is AFM-only, the title and
  legend say so; the slice uses AFM widths by default and includes optical
  ones only when asked, with the larger default sigma; the compare figure
  the same; the figure never renders in the stop red.
- **Schema migrations**: client cache and server database fixture files
  at every past version are brought to current with rows kept (the
  `TrialStore` test pattern, VERIFIED `tests/test_transfer_map.py` seeds a
  version-1 file); each namespace's `migrate_*` on fixture documents; a
  newer-than-known document is kept and ignored with one warning; the
  Transfer Map store's schema 6 on seeded version 4 and 5 files with the
  union of columns; a `flake-coords/1` document with unknown additive
  fields is accepted and the fields kept.
- **Search builder**: every filter alone and in combination produces
  parameterised SQL with only whitelisted columns; a field name from input
  never appears in SQL text; keyset pagination is stable under inserts;
  sort tiebreaker; alias resolution ("boron nitride" → hBN); the CSV
  export equals the API result for the same filters; stage columns are
  not filterable or sortable (the builder refuses them).
- **Server API** (FastAPI `TestClient`, SQLite in a temp dir): auth and
  roles on every route (a viewer cannot write, a member cannot touch
  another's prefs, an admin can), rate limiting, `If-Match` and
  idempotency, reservation uniqueness under concurrent requests, soft
  delete / restore / purge, audit rows for every write, image upload with
  a wrong sha refused, thumbnails generated, export/import round trip on a
  seeded database (row counts and checksums equal).
- **Secrets never in logs**: sign in with a known test PIN and token; grep
  the captured event log and the server log for both.
- **Architecture**: `model/profile.py`, `model/sample_map.py` and
  `devices/lab_server.py` obey the import rules; `urllib` has one owner;
  no `fastapi`/`pydantic` under `src/`; `tests/test_packaging.py` still
  lists every module and excludes `server/`.
- **Generated lab defaults** equal the code's constants.

### 9.2 Hardware and bench checks the owner must do in person

1. Each pad model (`xbox` wired, `xbox_bluetooth` on Linux, `logitech_f310`
   in both switch positions, `t16000m`) with the **default** document:
   the stage moves as it does today on every axis and sign. Then with a
   user document that inverts `step_x`: the hat moves the other way and
   the Setup line says "inverted by your profile". This also closes
   GAMEPAD-11..14 for the layouts in use.
2. Default-controller resolution with two pads attached, plugged in
   either order, on the Linux Mint bench PC and after a Windows session
   (the Bluetooth pairing-key loop in `README.md:175-183` is the
   adversarial case).
3. Enrolment on the bench PC; a launch with the server's cable pulled
   (offline sign-in, "offline since", a Remember that queues); plug back
   in; the outbox drains and the Profile row says synced.
4. Stations on the lab LAN reach the server by hostname through whatever
   switch and firewall department IT provides; nothing reaches it from
   outside the lab (a laptop on campus Wi-Fi cannot).
5. PIN entry at the bench with the keyboard the station has (a 6-digit
   PIN on a glove is the ergonomics test).
6. A restore drill run by hand once: wipe a test server, restore
   yesterday's snapshot, count.
7. A chip registered on the bench, a flake flagged with a bbox extent,
   synced, then found in the web UI with `lateral_um` agreeing with the
   micrometer within the registration's `residual_rms_um`; the same chip
   registered on the second rig after a pull, and the guidance landing on
   the flake (flake-coords §7 and §8.5 are the acceptance).
8. AFM round trip: attach an AFM channel width, feature height and sample
   thickness to a real trial that names a flake; after a sync the flake's
   page shows `thickness_afm_*` with `afm_by` the operator and the
   approximate fields untouched; an optical width attached earlier is
   kept beside the AFM one, and the 3D map's marker turns from ringed to
   filled.
9. Later: a camera profile applied through the camera software changes
   the red % under a fixed region as expected, and the stamp appears in
   the sidecar, the trial row and the flake record.

## 10. Open questions for the owner, and risks

### 10.1 Questions

| # | question | why it matters |
|---|---|---|
| Q1 | PIN at the station, or pick-a-name only? Is offline sign-in **without** a PIN acceptable (records marked `offline-unverified`), or should PIN hashes be cached on stations despite the small keyspace? | §2.1, §2.4 |
| Q2 | Where does the server live: a dedicated always-on box, or the bench PC for now? Who is the department IT contact for registering a host, and does the lab have (or may it make) a private wired segment? | §5.2 |
| Q3 | HTTPS with a lab CA pinned on the stations, or plain HTTP on the isolated segment? | §5.2 |
| Q4 | Which `Param`s may a **user** set as defaults (proposed: step sizes, manual and autonomous speed, rotator step), which are **station-only** (heater PID and offset, `red_min`, the µm-per-count table, `um_per_px`), which **never** (brake fields)? | §3.4 |
| Q5 | A 0600 station-token file under the data root, given the "no token file" ruling for GitHub? Or the OS keychain (a new dependency), or no station auth? | §6.1 |
| Q6 | Should Transfer Map trials be **mirrored** to the server (summary only as in `trial_links`, or whole trials with profiles, or with videos too), given "the store is local to the installation, never global"? | §4.8, §6.2 |
| Q7 | The initial materials list, its per-layer thickness constants, and who curates it. | §4.6 |
| Q8 | Reservation expiry (14 days?), whether admins may release anyone's, and whether the holder is notified (the Profile row, email, nothing). | §4.8 |
| Q9 | Retention before hard purge (30 days?), and who is admin besides the owner (the PI?). | §6.4 |
| Q10 | Which camera and SDK the camera software will use, whether a profile is per objective, and roughly when. | §3.6 |
| Q11 | Accept the additive fields asked of `flake-coords/1` (C1 UTC offset, C3 `registration_uid`, C7 `sample_uid`, A2 `storage_location`, A5 `tags`, `deleted_at`), or keep them server-only? (A3 and A4 are resolved.) | §4.9 |
| Q12 | One operator per launch, or a supervisor + trainee pair with both names on the record? | §2.4 |
| Q13 | A viewer role for collaborators outside the lab, now or later? | §2.2 |
| Q14 | Image policy on the server: keep every capture-region PNG per flake and corner, or JPEG; keep every image or the newest N per flake. | §4.5 |
| Q15 | The server as `server/` in this repository (one history, excluded from the bundle) or its own repository. | §5.1 |
| Q16 | The dimension people quote: longest chord (`lateral_um`) or the bounding box; and whether `layers_estimate` / `thickness_approx_nm` may be derived from `red_percent` (flake-coords Q8) so a "monolayer" filter means something before AFM. | §4.4, §4.7 |
| Q17 | **Feature height**: the exact definition of `feature_height_nm` for a cut (the AFM step height from the substrate to the top of the transferred channel, the depth of the trench the tip left in the flake, or both as two columns) and its sign. | §7.3; flake-coords Q13 |
| Q18 | **Quality**: accept the 1-5 scale and the defect vocabulary (`cracks`, `bubbles`, `residue`, `folds`, `wrinkles`, `tears`), or name the lab's own; may a flake be rated before it has an extent? | §4.4; flake-coords Q14 |
| Q19 | **Optical channel width**: how it is measured today (pixels on the capture-region picture with the Sample Map's `um_per_px`, an eyepiece reticle, the vendor viewer's tool), which fixes `width_optical_method`; and whether the Transfer Map's slice may use optical widths at all (proposed: AFM only by default, optical on request with a 3x default sigma). | §7.3; flake-coords Q15 |
| Q20 | **Legacy data, the authoritative copy**: how many trials, runs and session databases does the bench PC hold (`data/transfer_map.sqlite`, `transfer_map_<date>_<time>.sqlite`, `~/transfer-stage-runs/`), and if the same store exists on more than one machine (the sync incident), which copy is the truth? May the importer refuse duplicates by `map_db_uuid` and ask, rather than merge? | §11.1, §11.4 |
| Q21 | **What to drop or flag**: archive `main`'s `.txt` red logs as files only (nothing structured is recoverable), or skip them; annotate assumed-AFM widths in the trial's `note` on the bench store too, or only flag them on the server; import the heater CSVs as files or leave them alone. | §11.2 |
| Q22 | **History elsewhere**: which other lab PCs, laptops or shared drives hold run folders, exports, AmLite presets or spreadsheets of chips and flakes (a Google Sheet, a notebook) that should be in the import, and who owns each. | §11.1 (L11), §11.6 |

### 10.1a Owner decisions, 2026-10-04 evening (answers to 10.1 and flake-coords §11)

| # | decision |
|---|---|
| Q1 | Pick a name + PIN. Offline sign-in without a PIN is allowed; those records are marked `offline-unverified`. No PIN hashes are cached on stations. |
| Q2 | The server runs on the bench PC for now; a dedicated box later. |
| Q3 | HTTPS with a lab CA pinned on the stations. |
| Q4 | As proposed. User: step sizes, manual/autonomous speed, rotator step. Station-only: heater PID and offset, `red_min`, the µm-per-count table, `um_per_px`. Never: brake fields. |
| Q5 | A 0600 station-token file under the data root. |
| Q6 | Mirror trial **summaries** only (`trial_links`); the bench store stays the source of truth. |
| Q8 | Reservations last until released (no expiry); admins release stale ones by hand. |
| Q9 | Soft-delete for 30 days, then hard purge. Admins: the owner and the PI. |
| Q12 | One operator per launch; training goes in a note. |
| Q13 | Viewer role later, not in Phase 1. |
| Q14 | PNG, newest 3 per flake and corner. |
| Q15 | `server/` in this repository, excluded from the station bundle. |
| Q16 | `lateral_um` = longest chord. **No red-percent estimate:** `layers_estimate` / `thickness_approx_nm` stay blank until AFM or an operator's manual estimate (this also settles flake-coords decision 8). |
| Q17 | Both, two nullable columns: `channel_height_nm` (AFM step, substrate → channel top, positive up) and `trench_depth_nm` (depth the tip cut into the flake, positive down). Replaces the single `feature_height_nm` (+ each its own `_sigma_nm`). |
| Q18 | The 1–5 scale and the six defect tags are accepted; a flake may be rated before it has an extent. |
| Q19 | Optical width = pixels on the capture-region picture × the Sample Map's `um_per_px` (`width_optical_method = capture_px`). The Transfer Map fit uses AFM widths by default; optical only on request, with a 3× default sigma. |
| FC-6 | Optical crosshair for every mark; the tip never touches a corner. |

| Q11 | Accept all the additive `flake-coords/1` fields (C1 UTC offset, C3 `registration_uid`, C7 `sample_uid`, A2 `storage_location`, A5 `tags`, `deleted_at`) into the station schema. |
| FC-12 | Store version 6 approved (trials name their flake, plus the cut descriptors as decided in Q17 and Q19). |

Still open: Q7 (materials list), Q10 (camera/SDK), Q20–Q22 (legacy data on the bench PC), flake-coords bench facts 1–5 and 7, and 10 (gamepad mark button).

### 10.2 Risks

- **The server becoming a dependency of the stage.** Mitigated by the
  interface, the local-first reads, the non-blocking boot, the Station
  profile and the tests in §9.1 that assert the launch path with the
  server down; the rule in §5.4 that nothing under `src/` imports server
  code.
- **Drift between the two proposals.** The names are now one set; the
  eleven conflicts and eight additions in §4.9 are the remaining surface,
  and each is a one-line decision.
- **Stage positions leaking into "where is the flake".** The server
  stores them and refuses to index, filter or serve them (C8, §9.1); the
  web UI shows them only on a registration's detail page with its epoch.
- **Scope creep away from the end goal** (the Transfer Map). Phase 1 is
  small and pays daily; phases 3 and 4 wait on their prerequisites rather
  than on each other.
- **A user bind hiding a wrong lab-level sign.** Mitigated by keeping
  physical layouts in code, the "inverted by your profile" line, and
  bench check 1.
- **Data loss on the server.** Mitigated by §6.2, in particular the
  automated restore drill; the residual risk is a backup disk in the same
  room as the server, which the off-box copy addresses.
- **UCI network behaviour**: a campus segment that blocks station-to-server
  traffic, or a policy that forbids an unregistered host. Resolved by Q2
  before any box is bought.
- **PIN weakness.** A PIN protects attribution, not secrets; the design
  says so and rate-limits online attempts; offline verification is the
  owner's call (Q1).
- **Clock skew across stations** making "newer `updated_at`" wrong on a
  dual-boot machine whose two operating systems disagree about the clock.
  C1's offset and the server's `received_at` make it visible; NTP on the
  bench PC is the real fix (bench).
- **SQLite write contention** if the lab grows to many stations syncing
  at once. WAL mode and document-sized batches cover lab scale; the
  Postgres path is written down.
- **Windows paths and permissions** for the cache and the token file on a
  dual-boot bench PC: the data root already works there for logs; the
  token file's ACL needs the bench check.
- **New dependencies**: none on the station (stdlib `urllib`, `sqlite3`,
  `hashlib.scrypt`, `secrets`, `json`); FastAPI, uvicorn, pydantic,
  Jinja2 and Pillow (thumbnails; Pillow is already a station dependency)
  on the server only, plus the station distribution for `sample_frame`.

## 11. Legacy data migration (owner, 2026-10-04: "consider how to migrate the legacy db into the new infra")

Shared by both proposals; `proposal-flake-coordinates.md` §4.4 and §12
point here. Inventory taken read-only on 2026-10-04: SQLite files were
copied to the session scratchpad and opened with `sqlite3 -readonly` on
the copy (sha256 of copy = original, checked); no store was written, no
app run.

### 11.1 Inventory: every data source that exists

Three generations of the app wrote data (station-map): `main` (the lab's
original Tk app, Aug 2026 and before), `legacy/src` (the MVC repair the
lab ran 2026-08-26 → 09-22, frozen, deleted once `tests/TEST_PORTING.md`
is worked off), and `src/` (the rebuild, at the bench since 2026-09-26).
Only the rebuild has a database. The bench PC is a Linux Mint / Windows
dual-boot machine; **this Mac holds no lab data**, so every row count
below for the bench is an owner question (Q20).

| # | source | location | format | schema / version | size (this Mac) | key fields | tag |
|---|---|---|---|---|---|---|---|
| L1 | **Transfer Map store** (rebuild) | `<checkout>/data/transfer_map.sqlite`, git-ignored, "local to the installation"; or `STATION_MAP_DB`; in a bundle `<install>/data/` | SQLite | `PRAGMA user_version` 1-5; the file here is **4** with 30 `trials` columns (v5 has 34: + `video_path`, `video_index_path`, `video_frames`, `video_dropped`) | 28 KB, **0 trials, 0 profile rows, 0 tips** (an empty store made by the SIM / tests). The bench PC's copy holds the real trials since 2026-09-27 (count unknown, Q20) | `trials`: `id`, `started_at`, `tip_id`, `tilt_deg`, `speed_steps_s`, `width_um`, `width_sigma_um`, `thickness_nm`, `thickness_sigma_nm`, `status`, `origin`, `note`, picture and video paths, marks, red extrema, `force_given`; `profile`: `trial_id, t_s, red, z, x, y`; `tips`: `tip_id`, `created_at`, `first/last_trial_id`, `broke_trial_id`, `retired_at`, `note` | VERIFIED `transfer_map.py:83-136`; copy at `scratchpad/user-system/inventory/transfer_map.copy.sqlite` |
| L1a | the store's **pictures, videos and exports** | `<output_root>/transfer_map/<trial id>/` (`before_full.png`, `trial.mp4` or `frames/`, `video_index.csv`, `first_frame.png`, `mark_frame.png`; pre-2026-09-28 trials: `before.png`, `mark.png`, `after.png` + `_full` twins); `<output_root>/exports/transfer_map_<stamp>_{trials,profile,tips}.csv`; `transfer_map_<date>_<time>.sqlite` session files from "New session database" | PNG, MP4/JPEG, CSV, SQLite | paths are columns of L1; `video_index.csv` columns `frame, t_s, red, z, marked` | none here; bench: ~0.7-2 MB per whole-screen PNG, 5-30 MB per video (ASSUMED) | the trial id in the folder name; the store's path columns | VERIFIED `RECORDING_A_TRIAL.md`; `transfer_map.py:1740-1770` |
| L1b | a second empty store | `rb-dist-build/dist/station 2/data/transfer_map.sqlite` (a packaging smoke artifact) | SQLite | version 5, 0 rows | 28 KB | - | VERIFIED on a copy. Not lab data; ignore |
| L2 | **Red Percent runs** (rebuild) | `<TRANSFER_STAGE_DATA_ROOT or ~/transfer-stage-runs>/<run_id>/<run_id>_position.csv` + `<run_id>_station_meta.json` | CSV + JSON sidecar | CSV header `t_s, red_percent, <axis>_position_steps, <axis>_velocity_steps_per_s, position_age_s`; sidecar keys `run_id`, `probe_name`, `probe_tilt_angle`, `source_name`, `sync_axes`, `region`, `baseline_red`, `red_threshold`, `sample_mode`, `sample_interval_s`, `position_rate_hz`, `frames_captured`, `rows_written`, `grab_failures`, `mean/max_frame_interval_s`, `achieved_rate_hz`, `duration_s`, `started_at`, `stopped_at`, **`annotations`** = `{specimen_id, consumable_id, note}` (+ `run_name`, `probe_name`, `probe_tilt_angle`) | **0 runs on this Mac**; bench unknown (Q20) | `annotations.specimen_id` (= the future `samples.sample_id`), `annotations.consumable_id` (= `tips.tip_id`), `run_id`, `started_at` | VERIFIED `red_monitor.py:70-135,1058-1103`; `plot_data.py:39-66` |
| L3 | **Red Percent runs** (`legacy/src`, Aug 26 → Sep 22) | same root, `<run_id>/<run_id>_position.csv` + `_station_meta.json` | CSV + JSON | CSV header **`Red Percent, Timestamp, Stepper X Location, Stepper X Velocity, …`** (the rebuild's `plot_data.load_run` still reads it: `_LEGACY_RED`, `_LEGACY_TIME`, `_LEGACY_POSITION`); sidecar keys `run_id`, `probe_name`, `selected_probe_name`, `probe_tilt_angle`, `sync_dimensions`, `focus_area`, `baseline_red`, `red_threshold`, `sample_count`, `started_at`, `stopped_at`, `annotations` | unknown; bench | as L2, older names (`focus_area` = region, `sync_dimensions` = axes) | VERIFIED `legacy/src/model/redpercent_system.py:22-36,88-105,524-575`; `plot_data.py:53-60` |
| L4 | **Red Percent logs** (`main`, before Aug 26) | wherever the operator's Save-As dialog put them, default extension `.txt` | plain text lines `BASELINE SET - Red: x.x%`, `BASELINE RESET - Red: x.x%`, `RED: x.x%`; **no timestamps, no position, no annotations** | none | unknown; bench, or lost | the file name is the only key | VERIFIED `main/src/lib/redpercent.py:236-258`; `handoff/audit-redpercent-camera.md` RPC-4 |
| L5 | Camera presets (`main`) | operator-chosen `.json` | JSON `{exposure_us, gain, brightness, contrast}` | none | unknown; probably none ("doesn't work") | - | VERIFIED `main/src/camera_control.py:73-100` |
| L6 | **Heater PID data** (Tier P1, 2026-09-27) | bench `~/transfer-stage-runs/heater/` (data and scripts; `tools/heater_plot.py` on an unmerged branch) | CSV | ad hoc (live temperature CSV) | unknown; bench only | time, setpoint, temperature | VERIFIED STATUS.md:408-414 (location only) |
| L7 | Firmware stamp | `~/transfer-stage-runs/flashed.json` (`STATION_FLASH_STAMP`) | JSON | per board: sketch hash and time | none here; bench | board → hash | VERIFIED `firmware.py:43-44`. Machine state, **not migrated** |
| L8 | Station logs | `~/transfer-stage-runs/logs/station-<timestamp>.log` | text | one per launch | **154 logs, 8.2 MB** here (2026-09-21 → 10-02); bench unknown | - | VERIFIED. Not research data; **not migrated** (kept as is) |
| L9 | Preferences | **none exist** (§1.1): no QSettings, config file or JSON | - | - | - | - | VERIFIED (grep) |
| L10 | Flake, sample or registration records | **none exist**: no table, file or spreadsheet names a flake; the only chip identifiers on disk are L2/L3 `annotations.specimen_id` strings and `trials.tip_id` / `note` text | - | - | - | - | VERIFIED (grep of `src/`, `docs/`, `handoff/`, `README.md`) |
| L11 | Spreadsheets, notebooks, other CSV/JSON named in docs or handoff | none that hold lab data: every `.csv`/`.json`/`.txt` hit in `handoff/` is an audit's scratch file (`report.json`, `dom.json`, `out_patched.txt`, …) or the station's own artifacts above; `tests/golden/*.json` are wire fixtures | - | - | - | - | VERIFIED (grep). Whatever the lab keeps outside the repo (a Google Sheet, a notebook) is Q22 |

What the inventory means: **the "legacy db" is small and recent**. The
only structured research data is L1 (trials, profiles, tips, since
2026-09-27, on the bench PC) with its L1a media, plus L2/L3 run folders.
Nothing older than Aug 2026 is structured. There are no legacy flakes;
there are legacy *chips* (as `specimen_id` strings) and legacy *cuts*.

### 11.2 Mapping: legacy fields to the new records

| legacy | new record | rule | unrecoverable / flagged |
|---|---|---|---|
| L1 `trials` row, versions 1-5 | `trials` **version 6** (§7.3), same row, same `id` | the in-place `_migrate`: new columns NULL. `sample_id` ← the row's own text if a later Sample Map import can name it, else NULL; `flake_uid` NULL; `operator_id` ← `legacy/unknown` (§11.5); `camera_profile_id` NULL | **`width_um` provenance**: the current refusal says "measured by AFM" (`transfer_map.py:1700`) and `status = "measured"` is only set by `attach_afm`, so every existing `width_um` is **assumed AFM**, and the importer writes `note` += ` [legacy: width assumed AFM]` only if Q21 says to annotate; the server's `trial_links` carries `width_provenance = "assumed_afm"` for them. Imported-origin rows (`origin = "imported"`) have no profile and never will. `feature_height_nm`, `width_optical_um` are NULL (never measured). |
| L1 `tips` | `tips`, unchanged; server `trial_links.tip_id` | as is | - |
| L1 `profile` | unchanged | as is | - |
| L1a pictures, video | paths stay; the server ingests only what the trials CSV names (none of the media by default, Q6) | - | pre-09-28 trials have `before.png`/`after.png` instead of the video; nothing to do |
| L2/L3 run folder with `annotations.specimen_id` | **`samples`**: one per distinct non-empty `specimen_id` (trimmed, case kept); `sample_id` = the string, `uid` minted, `material`/`substrate`/dimensions NULL, `exfoliated_at` NULL, `created_at` = the earliest run's `started_at`, `owner` = `legacy/unknown`, `status` = `stored`, `note` = "legacy import from N Red Percent run(s)", `legacy_ref` = the list of run ids | material, substrate, size, exfoliation date: **not recorded anywhere**; the operator fills them on the web UI later |
| L2/L3 `annotations.consumable_id` | `tips.tip_id` (create a tip record when the store has none, as `import_csv` does today) | as is | - |
| L2/L3 run as a whole | `flakes.run_ids` on a flake **only if** an operator maps it (below); otherwise the run stays a run folder, listed by `sample_id` on the sample's page through a server table `legacy_runs(run_id, sample_id, started_at, path, legacy_ref)` | - | which flake a run measured is **not recorded**; `note` free text may say |
| L1 `trials` with a `note` or `tip_id` that names a chip or flake | **`flakes`**, created **only from an operator-supplied mapping sheet** (a CSV the importer reads: `trial_id, sample_id, flake_label, material, layers_estimate, quality, note`), never guessed from free text | each mapped flake: `flake_uid` minted, `label` from the sheet, `status = transferred` (it was cut), `transferred_at` = the trial's `started_at`, `trial_ids = [id]`, `owner = legacy/unknown`, `extent_kind = none`, `searched_at` NULL, every thickness field NULL unless the sheet gives `thickness_approx_*`; a trial's `thickness_nm` flows to `thickness_afm_nm` with `afm_by = legacy/unknown`, `afm_measured_at` NULL | **no sample-frame coordinates**: `sample_x_um`, `sample_y_um`, `stage_*` NULL; the flake references one synthetic **registration per legacy sample** with `frame_source = "legacy"`, `fit_kind` NULL, no transform, `quality = "unchecked"`, `invalidated_at` = import time, `invalidated_reason = "legacy import: no corners were marked"`, so flake-coords' rule that a flake has a registration holds and guidance is withdrawn for it. Reconcile: `frame_source` gains the value `legacy` (R-L1 in §11.7) |
| L4 `main` `.txt` logs | nothing structured can be recovered: no time base, no position, no chip | **archived as files** under the server's `legacy_files/` with sha256 and the original name; not imported as runs | everything but the red-percent sequence is lost |
| L5 camera presets | `camera_profile` v0 envelope, `body` = the JSON as is, `name` = file name, `body_schema = "amlite_preset.v0"` | import if any exist | camera identity unknown |
| L6 heater CSVs | not part of this system; archived as files with sha256 | - | - |
| L7, L8 | not migrated (machine state; logs) | - | - |
| L9 prefs | nothing to migrate; the lab default is generated from the code (§7.1) | - | - |
| users | there are no user rows anywhere; every legacy row's owner/operator is `legacy/unknown` (§11.5) | - | who did what before the user system is **not recoverable**, except by the owner's memory and the bench notebook |

### 11.3 The importer

One tool, `server/tools/import_legacy.py`, run by an admin, in three
modes that share one code path: **`--dry-run`** (reads everything, writes
a report, touches nothing), **`--report`** (the same, as a file for Ian to
read), **`--apply`** (writes to the server, and only to the server).

- **Inputs**: one or more Transfer Map stores (any version 1-5, or 6), a
  run root (`~/transfer-stage-runs` or a copy of it), the optional mapping
  sheet (§11.2), and a `--station <id>` naming where the data came from.
  It never opens an original: it **copies each input to a staging folder,
  checksums both** (sha256, recorded in the report and in
  `audit_log.after`) and reads the copy read-only (`?mode=ro` URI). The
  originals are never modified; the station's own in-place v6 upgrade is
  the station's job at its next launch, not the importer's.
- **Idempotent**: every record it creates carries `legacy_ref`, a string
  key `"<station>/<map_db_uuid or sha256[:16]>/trials/<id>"`,
  `".../runs/<run_id>"`, `".../tips/<tip_id>"`, `".../specimen/<text>"`;
  the server has a unique index on `(entity, legacy_ref)`. A re-run finds
  every key and updates nothing unless `--refresh` is given, in which case
  it re-applies the same mapping and reports the diff. Idempotency keys on
  every write (§5.3) make a crashed run resumable.
- **Validation report** (dry-run and apply): per source, rows read and
  rows written per entity (trials in = `trial_links` out; distinct
  `specimen_id` in = `samples` out; mapping-sheet rows in = `flakes` out),
  **orphans** (a trial whose `tip_id` has no tip record; a run whose
  `specimen_id` is empty; a mapping row naming a trial id the store does
  not have; a picture path the folder does not contain), **conflicts**
  (two stores with the same `map_db_uuid`; the same `specimen_id` with
  different capitalisation; a `(station, trial id)` already imported with
  different content), **assumptions applied** (the count of `width_um`
  rows assumed AFM), and a checksum table. Counts that do not reconcile
  fail the run before `--apply` writes anything.
- **Transaction and rollback**: `--apply` runs inside one server
  transaction per source and writes an `import_batches` row (`id`,
  `started_at`, `source_sha256`, `station_id`, `report_path`, `counts
  JSON`, `status`). **Rollback** is `import_legacy.py --undo <batch>`:
  every record whose `audit_log.request_id` is the batch's is soft-deleted
  (or hard-deleted when nothing newer references it), reservations and
  edits made since are reported, never silently discarded. Because the
  originals were never touched, the station-side rollback is "do nothing".
- **Attribution**: everything the importer writes is audited as `user =
  <the admin running it>`, `auth = "import"`, with the batch id as
  `request_id`.

### 11.4 Ordering with the phases, and the un-migrated station

Two migrations, deliberately separate:

1. **Local, per station, phase 1-2**: the Transfer Map store's in-place
   upgrade to version 6 happens on the first `open()` by the new code
   (§7.3), with NULL in every new column; `sample_map.sqlite` is created
   empty by the Sample Map's `open()` (flake-coords §5.4). No tool, no
   operator step, no server. **A station on un-migrated data keeps
   working**: the store is additive, so a version-5 file opened by
   version-6 code is upgraded and kept; a version-6 file opened by an
   older build (a lab machine not yet updated) still works, because
   `_migrate` leaves a newer file alone (`transfer_map.py:219-221`) and
   the old code never reads the new columns. Run folders need nothing.
2. **Server, phase 3a**: the importer of §11.3, once, per station, by the
   admin, after the server exists. Until then nothing changes for the
   bench. The importer reads version 1-6 stores alike (it upgrades its
   own staging copy to 6 with the station's `_migrate`, then reads).

**Divergent copies across stations.** Today there is one bench; soon two
rigs. Each station's store is its own file with its own `map_db_uuid`
(§7.3), so two stations never collide on trial ids on the server
(`trial_links` is keyed by `(station, map_db_uuid, trial_id)`). The things
that *can* diverge are the human keys: the same chip typed as `S41`,
`s41` and `S-41` on two benches, or the same tip id reused. The strategy:
- `samples` are merged by **normalised `sample_id`** (trim, case-fold,
  strip `-`/`_`) with the first-seen spelling kept as `sample_id` and the
  others recorded in `aliases` (server column); the report lists every
  merge for Ian to confirm or split.
- tips are merged by exact `tip_id`; a tip that appears on two stations
  is one tip with two stations' trials, flagged in the report (a physical
  tip is on one bench at a time, so two concurrent uses are a data error).
- flakes exist only from the mapping sheet, so there is no flake
  divergence to merge in the legacy import; after phase 3b the
  `flake-coords/1` rule (newer `updated_at` wins, §5.6) applies.
- the same store copied to two machines (a file-sync incident like the
  one in STATUS 2026-09-28) shares a `map_db_uuid`: the importer refuses
  to import both and asks which is authoritative (Q20).

### 11.5 Owner and operator attribution

One built-in user, `legacy/unknown` (`username = "legacy"`, `role =
viewer`, no PIN, no password, `is_active = 0`, cannot sign in), owns every
legacy sample and flake and is the `operator_id` / `afm_by` of every
legacy trial link. The admin web UI has **bulk reassign**: filter by
`owner = legacy`, by station, by date range or by `sample_id`, pick a
user, apply; each reassignment is one audit row with `before`/`after`, so
it is reversible; the station's own store is never touched by it
(`trials.operator_id` on the bench stays `legacy` or NULL; the server's
copy is what the lab searches). The same button serves records created
under the no-PIN Station profile later.

### 11.6 Owner steps and acceptance

1. Copy the bench PC's `data/` and `~/transfer-stage-runs/` to an external
   disk **before** the first update that brings version 6 (a plain copy; a
   second copy off-site). This is also the first backup the lab's trial
   data has had.
2. Run `import_legacy.py --dry-run` against the copies; read the report;
   fill the mapping sheet for the trials that cut a known flake (only if
   wanted; it can be empty).
3. `--apply`; check the web UI: trial count equals the bench's "Trials"
   readout, every `specimen_id` is a sample, every assumed-AFM width is
   flagged.
4. Bulk-reassign `legacy` rows to the people who ran them, from the bench
   notebook.

### 11.7 Reconcile items this adds (both proposals)

| # | item | proposed |
|---|---|---|
| R-L1 | `registrations.frame_source` gains the value **`legacy`** (no corners, no transform, always invalidated) | flake-coords §4.1 vocabulary: `stage:<Model.NAME>` / `manual:micrometer` / `legacy` |
| R-L2 | `legacy_ref TEXT` on `samples`, `flakes`, `registrations` (nullable; additive; travels in `flake-coords/1`) and on the server's `trial_links`, `legacy_runs`, `legacy_files` | so a record's origin survives export and re-import |
| R-L3 | `trial_links.width_provenance` ∈ {`afm`, `assumed_afm`}; after version 6, new rows are `afm` (set by `attach_afm`) | the flag the owner asked for on old `width_um` |
| R-L4 | `samples.aliases` (server) for merged spellings of `sample_id` | §11.4 |

### 11.8 Risks specific to migration

- The bench store is the only copy of the real trials and lives in a
  git-ignored folder inside a synced checkout that was once overwritten
  by a sync client (STATUS 2026-09-28). Step 1 of §11.6 before anything
  else.
- `specimen_id` was free text with no validation; the merge heuristic in
  §11.4 can over-merge (`S4` and `S41`? no: normalisation does not strip
  digits) or under-merge (`chip A` vs `sample A`); the report lists every
  decision and nothing merges silently.
- "Assumed AFM" may be wrong for a trial where someone typed an optical
  estimate into the AFM box before the optical field existed; the flag is
  permanent on the server row and Ian can correct individual rows.
- Dates: `started_at` is local time without an offset (C1); legacy rows
  keep it as text and the server's `received_at` orders them.

## Appendix A. The effective-prefs response (example)

```json
{
  "station": "bench-linux", "user": "ialbinog",
  "effective": {
    "launch": {"view": "qt", "rows": {"Stepper Probe": {"enabled": "auto", "port": "auto", "gamepad": "default_controller"}, "...": "..."}},
    "default_controller": {"preferred": [{"layout_id": "xbox", "name_contains": "Xbox Series X"}], "rows": {"Stepper Probe": 0}},
    "controller_binds": {"xbox": {"*": {"jog_x": {"channel": "axis_x", "invert": false, "deadzone": 0.08, "scale": 1.0}, "...": "..."}}},
    "model_params": {"Stepper Probe": {"x_step": 4, "y_step": 4, "man_full_speed": 300}}
  },
  "provenance": {
    "launch.view": "lab", "launch.rows.Stepper Probe.gamepad": "user",
    "model_params.Stepper Probe.x_step": "user", "model_params.Stepper Probe.man_full_speed": "user",
    "controller_binds.xbox.*.jog_x.deadzone": "lab"
  },
  "revisions": {"lab/launch": 3, "station/bench-linux/launch": 1, "user/ialbinog/model_params": 7}
}
```

## Appendix B. Today's values that become the lab default

| key | value | source |
|---|---|---|
| `launch.view` | `qt` | `app.DEFAULT_VIEW` (`app.py:64`) |
| `launch.web.port` | 8080 | `app.DEFAULT_PORT` (`app.py:48`) |
| `launch.rows.*` | `enabled: auto, port: auto` (SIM/On until a board answers) | `setup.py:277-289,930-971` |
| `model_params."Stepper Probe"` | steps 1/1/1, `full_speed` 400, `man_full_speed` 400, `slow_speed` 0, `brake_distance` 0 | `probe.py:111-122,995-997` |
| `model_params."DC Probe"` | steps 1/1/1, speeds 120/120 | `probe.py:1019-1025` |
| `model_params."Chuck Positioner"` | steps 2/2/2, speeds 400/400 (inherited) | `probe.py:1044-1046` |
| `model_params."Temperature Controller"` | `setpoint` 0, `ramp_rate` 10, `p_term` 2.0, `i_term` 0.5, `d_term` 0.1, `offset` 0 | `heater.py:61-74` |
| `model_params.Rotator` | `target_deg` 0, `step_deg` 1.0 | `rotator.py:105-109` |
| `model_params."Red Percent"` | `red_min` 150 (station-only) | `red_monitor.py:284` |
| `controller_binds.<layout>.*` | the eight actions as `probe.py:606-623` reads them, `invert` false, `deadzone` as the device applies today, `scale` 1.0 | `probe.py`, `gamepad.py` |
| speed ceiling | `MAX_SPEED` 3200 is a bound, not a default; a document cannot raise it | `probe.py:91` |

## Appendix C. Reconcile checklist for the lead (one line each; details in §4.9)

C1 timestamps: local vs UTC, ask for the offset · C2 merge rule: newer
`updated_at` wins, adopted · C3 `registration_id` collides across
stations, ask for `registration_uid` · C4 `red_percent` on flakes, `red`
in the map store, both stay · C5 status vocabulary adopted, `reserved` is
derived · C6 `owner` / `material` stay text, server resolves · C7 ask for
`sample_uid` on the flake · C8 the server never serves a stage position ·
C9 the Sample Map store is the local truth, sync by document · C10 one
store-6 migration with both proposals' columns · C11 confirm `updated_at`
on `samples` · C12 thickness split into `thickness_approx_*` and
`thickness_afm_*`, both documents · C13 cut descriptors live on `trials`
(version 6), reached through `trial_links` · A1 derived dimensions
materialised server-side only · A2 `storage_location` · A3 resolved:
`quality` + `defects` adopted · A4 resolved: `thickness_afm_sigma_nm`
adopted · A5 `tags` · A6 `source_station_id` from the header · A7
server-only tables · A8 `camera_profile_id` later.
