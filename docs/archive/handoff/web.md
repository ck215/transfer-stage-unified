# Handoff — `station/views/web/` (branch `rb-web`)

Commit: `107fbdf rebuild(views/web): WebView, ApiHandler and the browser client`

Write set touched, and nothing else: `station/views/web/**`,
`tests/station/test_view_web*.py`.

## DONE

### `station/views/web/server.py` (~640 lines)

**`ApiHandler(BaseHTTPRequestHandler)`** — every contract member implemented:
`do_GET`, `do_POST`, `_is_local`, `_send_json`, `_serve_static`. The server
instance carries the `WebView` (`_StationServer.view`), so the handler keeps
no class-level state — the shape that made ERRORS-12's log lines land in an
adapter nothing read.

Routes, all JSON unless noted:

| Route | Behaviour |
|---|---|
| `GET /api/state` | `controller.state()` |
| `GET /api/schema?name=` | `controller.schema(name)`; `__setup__` → `setup.schema`; unknown → 404 |
| `GET /api/setup` | `{"schema": setup.schema, "state": setup.state}` |
| `GET /api/events?since=` | `events.since(id)` + `latest_id`, non-destructive |
| `GET /api/theme.css` | `theme.css_variables()`, `text/css` |
| `GET /api/data?name=&command=` | series → JSON `data`, `list[str]` → JSON `lines`, `bytes` → `image/png` |
| `GET /api/file?name=&command=&inputs=` | runs the save command, checks the path, streams it as an attachment |
| `GET /api/screen?name=` | JSON `{image: data-URI, width, height, left, top}` |
| `POST /api/run` | `{name, command, inputs, args}` → `Result.to_dict()` (+ `inputs`/`args`); `__setup__` targets Setup |
| `POST /api/options` | `{name, command}` → `{options: [...]}`; a `Refused` comes back as `status: refused` |
| `POST /api/estop_all` | `{confirmed: {...}, unconfirmed: [...], is_estopped}` |
| `POST /api/clear_estop_all` | `{confirmed}` → `Result.to_dict()` (asks first) |
| `POST /api/close_model` / `POST /api/open_model` | `controller.remove` / `reopen` |
| `POST /api/heartbeat` | `view.beat()` |
| anything else | `static/`, with a traversal guard; `/api/*` → JSON 404 |

Security, per the owner ruling: bind `127.0.0.1`; `_is_local` refuses any
non-loopback client address on **every** route; every state-changing route
(all POSTs, plus `GET /api/file` and `GET /api/screen`, which run a command
and read the screen) additionally requires `Content-Type: application/json`
(POST only) and an `Origin`/`Referer` and `Host` naming this server. No
session token anywhere, including in the served HTML. 1 MiB body cap, drained
before the 413 so the client sees the answer rather than a broken pipe. Both
verbs run under a `try/except` that answers with a JSON 500 (WEB-14).

**`WebView(controller, setup, *, host, port, open_browser, clock)`** —
`heartbeat_age`, `beat`, `open`, `close`, `_check_heartbeat`, `_watch_loop`,
plus `url` / `is_serving`. `open()` binds (walking the port range only on
EADDRINUSE, reporting exhaustion as an `events.error` and returning `""`),
starts the serve thread and the watchdog thread, and opens the browser unless
told not to. `close()` stops the watchdog, shuts the server down, joins, then
calls `controller.close()`. Thresholds are class attributes:
`WARN_SECONDS = 5.0`, `STOP_SECONDS = 15.0` (PROVISIONAL, D-8a),
`WATCH_SECONDS = 0.5`.

### `station/views/web/static/` (app.js ~780, index.html ~50, styles.css ~230)

`app.js` mirrors `PanelView` member for member: `ELEMENT_RENDERERS` has one
`render<Type>` per entry in `schema.ELEMENT_TYPES` (all 13); `gatherInputs()`
sends every writable entry's current value with every command; `run()`
handles `needs_confirm` → `window.confirm` → re-run with
`result.inputs` and `result.args.concat([true])`; `refused` becomes a
non-modal status line on the card; `refresh()` is `PanelView._refresh` line
for line, including entries left alone while focused or dirty, gating from
`isEnabled(element, mode)` applied to **every** widget, and the stale marker
at `age > 1.0`. `Dashboard` does the model cards (with Close), the reopen
list from `state.closed`, the global FULL STOP from `state.is_estopped`, the
event log, a modal for `needs_ack` only, the Setup panel through the same
renderer, and the region picker (drag on `/api/screen`, scaled to screen
coordinates with the monitor offset). No `innerHTML` anywhere; every fetch
goes through one `AbortController`-bounded wrapper. `styles.css` contains no
colour literal — every colour is a `var()` from `/api/theme.css`.

### Not implemented / deliberately different

* **`file_open` cannot pass a path.** The browser has no route that takes a
  server path (that is the finding-9 design), and there is no upload route in
  my brief, so the renderer runs the command with no argument and shows the
  model's refusal if it wants one. `RedMonitor.load_run` (the old "upload a
  CSV and plot it" flow) is therefore not reachable from the Web today. See
  CORE CHANGE REQUESTS.
* **`/api/screen` does not capture anything itself.** See the same section.

## TESTS

* `tests/station/test_view_web_server.py` — 44 tests (real `Controller`, real
  `Panel` subclass as the model, ephemeral port, `urllib` as the browser).
* `tests/station/test_view_web_watchdog.py` — 11 tests (fake clock; nothing
  sleeps).
* `tests/station/test_view_web_client.py` — 37 tests (static analysis of
  app.js / styles.css / index.html; `node --check` when node exists).

92 of my own; `python3 -m pytest tests/station -q -p no:cacheprovider` last
line, verbatim:

```
145 passed in 2.91s
```

## MUST-SATISFY

**`ApiHandler._is_local` — [CARRY] localhost bind + Origin/Host on every
state-changing route, JSON content type (MANAGER-15, WEB-10).**
Ported as three checks in one method (client address, content type,
Origin/Referer + Host), applied to every POST and to the two GETs that are
not reads. Tests: `test_a_cross_site_form_post_is_refused_on_content_type`,
`test_a_post_from_another_origin_is_refused`,
`test_a_post_with_a_foreign_host_header_is_refused`,
`test_the_dashboards_own_origin_is_accepted`,
`test_the_screen_route_is_guarded_like_a_command`,
`test_a_non_local_client_is_refused_whatever_it_sends`,
`test_reads_stay_open_so_the_first_paint_works`,
`test_there_is_no_session_token_left_to_leak`.

**[BENCH] WEB-19 / WEB-23 — the heartbeat's warn/stop seconds become ONE pair
on WebView, still set at the bench.**
`WARN_SECONDS`/`STOP_SECONDS` are class attributes on `WebView` and nowhere
else; no model has a liveness gate. The three rules are carried over intact.
Tests: `test_silence_before_any_client_never_stops_anything`,
`test_silence_while_idle_never_stops_anything`,
`test_silence_while_active_warns_once_then_stops_once`,
`test_a_fresh_heartbeat_rearms_the_gate`,
`test_the_latch_lands_before_the_popup`,
`test_the_thresholds_are_class_attributes_a_bench_session_can_change`,
`test_open_starts_the_watchdog_and_close_stops_it`. Client half (old app.js
semantics kept): `test_the_heartbeat_stops_when_the_tab_is_hidden_and_
resumes_when_it_is_not`, `test_the_heartbeat_has_its_own_interval`.

**[CARRY] ERRORS cluster — non-destructive `since(id)`, no `innerHTML`,
handler exceptions return JSON (ERRORS-2, ERRORS-3, WEB-9, WEB-14, WEB-17).**
Tests: `test_events_are_not_consumed_by_the_first_reader`,
`test_events_since_the_latest_id_is_empty`,
`test_a_route_that_raises_answers_with_json_not_a_dropped_connection`,
`test_no_server_string_can_become_markup[...]` (4 sinks).

**[OUT] Result / Refused — a refusal is rendered, never reported as success
or as a fault (ERRORS-1, DC-6, WEB-12, WEB-18).**
Every route returns the `Result` dict; a refused command is HTTP 200 with
`status: "refused"` (see NOTES on the old 403 convention). Tests:
`test_a_refusal_is_a_refusal_and_never_a_server_fault`,
`test_a_command_the_schema_does_not_declare_is_refused`,
`test_a_run_against_a_model_that_is_not_open_is_refused`,
`test_an_undeclared_options_command_is_refused_not_served`,
`test_a_data_command_the_schema_does_not_declare_is_refused`, plus the client
side `test_a_refusal_is_a_status_line_on_the_card_and_never_a_popup`.

**[VIEW] theme + PanelView — one palette, no dead controls, schema-driven
gating (WEB-24, REDPERCENT-19).**
Tests: `test_the_stylesheet_names_no_colour_of_its_own`,
`test_every_variable_the_stylesheet_uses_is_one_the_theme_defines`,
`test_app_js_renders_every_element_type[...]` (13),
`test_app_js_renders_nothing_the_schema_does_not_declare`,
`test_gating_covers_every_element_including_entries`,
`test_a_stale_state_is_marked_at_the_same_threshold_as_the_desktop_views`.

**[CARRY] WEB-16 — port in use.**
`test_a_busy_port_is_reported_as_an_event_not_a_traceback`,
`test_a_busy_port_walks_up_to_the_next_one`.

**[CARRY] WEB-21/WEB-22 — bounded bodies, bounded fetches.**
`test_a_body_larger_than_the_cap_is_refused_before_it_is_read`,
`test_every_fetch_is_bounded_by_a_timeout`.

**Old finding 9 — no view passes a path to the server.**
`test_the_saved_file_downloads_as_an_attachment`,
`test_a_file_outside_the_models_output_root_is_refused`,
`test_a_file_route_without_an_output_root_refuses_rather_than_guesses`,
`test_a_save_command_carries_the_entry_values_like_any_other`.

**Seam (the WEB-19 lesson, generalised):**
`test_every_route_app_js_calls_exists_on_the_server` fails if either half
renames a route.

## UNVERIFIED

* **Nothing was opened in a browser.** Every app.js test is static analysis or
  `node --check`. Layout, the canvas plot, the region drag and the download
  link are unexercised by any runtime. This is the largest gap in this
  handoff; an operator opening the page is the only real test.
* **`/api/screen` end to end.** The route is tested against a fake screen
  command. No model supplies one yet (see below), so the region picker cannot
  work at the bench today.
* **`/api/file` against a real model.** Tested against a fake whose `save`
  returns a path; `RedMonitor.save`'s actual return value and its
  `output_root` exposure are that agent's.
* **Two tabs at once.** `events.since` is non-destructive and per-model
  commands are serialised by the Controller, so it should hold, but nothing
  drives two clients concurrently.
* **Deployment realities:** no reverse proxy, no IPv6-only host, no
  `HTTP/1.1` keep-alive stress. `protocol_version` is HTTP/1.1 with an exact
  `Content-Length` on every response.

## CORE CHANGE REQUESTS

**1. `station/schema.py` — `region_select` needs an image source.**
The Web picker has to draw the screen before the operator can drag on it, and
a view may not import `Screen` (`test_architecture.py`), so the picture must
come from the model as a data command. `/api/screen` already looks for
`element["data_command"]` on a `region_select`; today nothing sets it, so the
route answers 503.

```diff
-def region_select(text, command, *, model_attr=None, role="neutral"):
+def region_select(text, command, *, model_attr=None, data_command=None,
+                  role="neutral"):
     """Pick a rectangular region of the screen or image."""
-    return {
+    element = {
         "type": "region_select", "text": text, "command": command,
         "model_attr": model_attr, "writable": False, "role": role,
     }
+    if data_command is not None:
+        element["data_command"] = data_command
+    return element
```

and, for `rb-redmonitor`: a `screen_image` property returning either PNG
bytes or `{"image": <png bytes>, "width", "height", "left", "top"}` in screen
coordinates (bounded — the old route thumbnailed to 800x600 JPEG at quality
60), declared as `sch.region_select("Focus Area", "set_region",
model_attr="focus_area", data_command="screen_image")`.

**2. `station/result.py` — `Result.to_dict()` drops what a confirm re-run
needs.** `PanelView` re-runs with `result.inputs` and `(*result.args, True)`;
the dict carries neither, so any serialising view has to add them back. I add
them in `_result_dict`, which means the Web and the desktop views disagree
about what a `Result` *is*.

```diff
     def to_dict(self):
         return {"status": self.status, "reason": self.reason,
-                "command": self.command, "value": _plain(self.value)}
+                "command": self.command, "value": _plain(self.value),
+                "inputs": dict(self.inputs), "args": list(self.args)}
```

I will drop `_result_dict`'s two lines the moment this lands.

**3. An output root in `Model.state`, or a `Model.output_root` property.**
`/api/file` must prove the file a save command wrote is inside the model's
own output root. It currently reads `state["values"]["output_root"]`, falling
back to `controller.config(name)`, and refuses with a 409 when it finds
neither. Any model with a `file_save` element needs to expose one — a
`sch.readonly("Output folder", "output_root")` is enough.

**4. (Owner decision, not a diff.) An upload route.** The old Web could
upload a CSV and get a plot back (`POST /api/plot`). Nothing replaces it:
`file_open` runs its command with no argument. If that flow matters, it needs
either `POST /api/upload` (bytes in, a path under the model's output root
out, then an ordinary `run`) or an explicit ruling that loading a file is a
desktop-only action.

## NOTES FOR THE LEAD

* **A refused command is HTTP 200 with `status: "refused"` in the body.** The
  old repo used 403 for a refused write (STEPPER-11, DC-6). That convention
  existed because the old adapter returned `{"code": N}` and the status was
  the only channel. Now the `Result` says it, and the client renders from
  `result.status` like the desktop views do. Route-level failures keep proper
  codes (403 locality, 404 unknown model/route, 409 reopen/no output root,
  413 body, 415 content type, 500 handler). Say the word and I will map
  `refused` → 403 on `/api/run`, but the client would then have to read the
  body anyway.
* **`ApiHandler` must not define a `setup` attribute.** `BaseHTTPRequestHandler`
  calls `self.setup()` in its own constructor; a `setup` property shadowing it
  kills every connection before a byte is written, with no traceback on the
  client. It cost me a debugging round; worth a line in the brief for whoever
  touches this next. The handler reads `self.view.setup`.
* **The watchdog fires `estop_all` and then publishes `events.error`** (latch
  before report). Note the popup will usually be seen by nobody — the browser
  is gone, which is why the watchdog fired — so the log file is the real
  record. That is an argument for `events.warn` instead if the lead prefers;
  I kept the old code's severity.
* **`WATCH_SECONDS = 0.5`** is a poll interval, not a threshold, and
  `serve_forever(poll_interval=0.05)` only bounds how quickly `close()`
  returns. Neither is a busy loop.
* **Diagnostics**: every request logs `<VERB> <route>` with status, byte count
  and milliseconds at debug, rate-limited to one line a second for
  `/api/state`, `/api/events`, `/api/data` and `/api/screen`; the stdlib
  access log is redirected into `events.debug` so nothing reaches the
  terminal; heartbeat gaps, watchdog warn/stop, port-in-use walks, refused
  locality checks and browser-open failures all log.
* **`station/views/web/__init__.py`** now exports `WebView`, `ApiHandler` and
  `SETUP_NAME`, so `app.launch()` can do `from station.views.web import
  WebView`.
* `WebView.open()` returns the URL or `""`; it never raises on a bad port, so
  a launcher should check the return value if it wants to fall back.
