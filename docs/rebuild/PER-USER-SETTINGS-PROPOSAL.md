# Per-user layout, default fields, sample base: a proposal (C-12, 2026-10-11)

Status: **proposal, nothing implemented.** `STATUS.md` says these are "reserved
settings keys only (`53abaee`); nothing reads them." Evidence as of
`fix/xyz-followups` `83a3bb2`. The store is the accounts file's `settings(email,
key, value)` table: JSON values, read by `UserStore.settings/setting`
(`src/model/user_store.py:334-346`), written by `put_setting` (`:347`), keys named
at `:88`. A signed-in user's other keys (`map_store`, `sample_store`, `backup_dir`)
are already read and written this way (`src/controller/setup.py:3772-3783`).

## What each key would mean, where it is read, the minimal default

| Key | Meaning | Read at | Recommended minimal default |
|---|---|---|---|
| `layout` | How the Dashboard is arranged for a person: tile order and which tiles are Wide, today `{order:[names], wide:[names]}` and nothing more. | The Web console, `loadTiles` / `saveTiles` / `tileOrder` (`src/views/web/static/app.js:5087-5110`, key `TILES_KEY` `:29`), which today keeps this per browser in `localStorage`. Server side: published in `User.state` (`src/model/user.py:363`), saved by one internal User command. | `{}` meaning the launch order and no Wide tile (what `loadTiles` returns for an empty store). A Guest has none. Do not add open disclosures or collapsed sections now. |
| `default_fields` | `{model: {attr: value}}` for text attributes that are not remembered parameters. | `User.load_into` -> `_apply` (`src/model/user.py:221-262`), through an optional hook beside `Panel.apply_defaults` (`src/panel.py:238`); a model without the hook is untouched. | `{}`, and no hook until one model asks. A text field that is a `Param` (e.g. `run_name`, `src/model/rgb_analysis.py:440`) needs no new key: add it to `USER_PARAMS` (`src/model/profile.py:54`) and the `preferences` table carries it. |
| `sample_base` | Which Sample DB store a person works in. | Already answered by the `sample_store` setting, read when a user signs in (`src/controller/setup.py:3070-3086`, `STORE_KEY`), per-user by owner ruling 2026-10-08. | Delete the reserved name (`user_store.py:88`, the docstring `:35-37`, and its test line). It names a decision already taken. |

## Migration and compatibility

* No schema change. `settings` is key/value rows; `PRAGMA user_version` stays 1
  (`user_store.py:63`). An absent key means the default above, so every existing
  accounts file works untouched, and an older build ignores the new rows
  (`settings()` returns them, no old consumer asks for them).
* A value that is not valid JSON of the expected shape is dropped on read, never
  an error (`settings()` already skips undecodable rows, `:334-342`); the consumer
  validates the shape (lists of strings) and falls back to the default.
* `layout` first sign-in: if the server holds none and this browser's
  `localStorage` does, the page posts it once, then the server copy wins. The
  browser copy stays as the Guest and offline fallback (the page already draws
  fine without storage, `app.js:5082-5086`).
* Guest: nothing is read or written for `layout`; Guests keep the per-browser copy.
* Rollback is free: delete the rows, or ignore them.

## Tests that would prove it

* `tests/test_user_store.py` (extend `:273`): a round trip for `layout`; a corrupt
  value reads as the default; `sample_base` no longer in `RESERVED_SETTINGS`.
* `tests/test_user.py`: `state` carries `layout` for a signed-in user and none for a
  Guest; the save command refuses a non-list or a non-string name and stores nothing.
* `tests/test_view_web_client.py` (node) and a browser test beside
  `tests/test_view_web_tiles.py`: server layout wins over `localStorage`; the
  one-time upload happens once; a Guest is unchanged.
* `tests/test_setup.py`: signing in as a second user draws that user's order, not
  the first user's.
* If the `default_fields` hook is ever built: a fake model with the hook receives
  exactly its own entries, one without it is untouched, a bad value is refused by name.

## The one question for Ian

Should the Dashboard layout follow the person's account to any browser or station
(build `layout` server-side as above), or stay per browser as it is today, in which
case `layout` and `sample_base` are deleted from the reserved list and only
`default_fields` stays reserved?
