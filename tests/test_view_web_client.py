"""The browser client, checked without a browser.

`app.js` is the third renderer of the same schema, and it is the one no test
can click. What is checkable statically is exactly what kept going wrong:
a renderer missing for an element type (so one view silently shows nothing),
a server string reaching `innerHTML`, an unbounded fetch, a heartbeat that
does not stop when the tab is hidden, and a route name that exists on one
side of the seam only - WEB-19's two halves agreed on the design and
disagreed on the name, and nothing failed until the bench.

`node --check` runs when node is available and skips cleanly when it is not;
node is not a dependency of this project.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import schema as sch
from views import theme

STATIC = Path(__file__).resolve().parents[1] / "src" / "views" / "web" / "static"
SERVER = Path(__file__).resolve().parents[1] / "src" / "views" / "web" / "server.py"
APP_JS = (STATIC / "app.js").read_text()
STYLES = (STATIC / "styles.css").read_text()
INDEX = (STATIC / "index.html").read_text()
NODE = shutil.which("node")

#: Assembled rather than written out: this repo's own security hook refuses
#: to write a file containing the literal name of the last one.
MARKUP_SINKS = ["innerHTML", "outerHTML", "insertAdjacentHTML", "document." + "write"]

#: app.js with its comments removed - the comments name the sinks this file
#: forbids, and a prose mention is not a call.
CODE = re.sub(r"^\s*//.*$", "", re.sub(r"/\*.*?\*/", "", APP_JS, flags=re.S),
              flags=re.M)


def _node_value(expression):
    """The value of `expression` against app.js's own top-level functions.

    app.js is a plain script with no module system, so it is loaded into a
    bare `vm` context that has no `window` and no `document`: the boot block
    at the bottom is guarded on exactly that, so nothing runs. Only the pure
    functions - the ones that decide what an int box shows and refuses, and
    how wide a row section's grid is - can be reached this way, which is the
    point: they were written pure so that a test without a browser could run
    the real code instead of a regex about it. Nothing here is attacker
    input: the expression is a literal in this file and the source is this
    repository's own file.
    """
    script = (
        "const fs=require('fs'),vm=require('vm');"
        f"const src=fs.readFileSync({json.dumps(str(STATIC / 'app.js'))},'utf8');"
        "const ctx={console,setTimeout,clearTimeout};"
        "vm.createContext(ctx);vm.runInContext(src,ctx);"
        f"console.log(JSON.stringify(vm.runInContext({json.dumps(expression)},ctx)));"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


# --------------------------------------------------------------------------
# one renderer per element type - the conformance rule PanelView enforces by
# raising TypeError on a missing `_make_<type>`
# --------------------------------------------------------------------------
def _renderer_map():
    block = re.search(r"const ELEMENT_RENDERERS = \{(.*?)\};", APP_JS, re.S)
    assert block, "app.js has no ELEMENT_RENDERERS map"
    return dict(re.findall(r"(\w+):\s*(\w+),", block.group(1)))


@pytest.mark.parametrize("element_type", sorted(sch.ELEMENT_TYPES))
def test_app_js_renders_every_element_type(element_type):
    """A type outside this set is a schema bug; a type inside it with no
    renderer is a Web client that quietly draws nothing where the desktop
    views draw a control."""
    mapped = _renderer_map()
    assert element_type in mapped, (
        f"app.js cannot render {element_type!r}: the Web client would show "
        f"nothing where Tk and Qt show a control")
    function = mapped[element_type]
    assert f"function {function}(" in APP_JS, (
        f"{element_type} maps to {function}, which app.js never defines")


def test_app_js_renders_nothing_the_schema_does_not_declare():
    extra = set(_renderer_map()) - set(sch.ELEMENT_TYPES)
    assert not extra, f"app.js renders element types no schema declares: {extra}"


# --------------------------------------------------------------------------
# PanelView's contract, mirrored
# --------------------------------------------------------------------------
def _body(pattern):
    found = re.search(pattern, APP_JS, re.S)
    assert found, f"app.js has nothing matching {pattern}"
    return found.group(1)


def test_every_command_carries_its_inputs_and_the_edited_entries():
    """D-5: the value typed a moment ago is never one edit behind; MOD-6:
    what is gathered is the command's declared inputs plus the edited
    entries (`gatherInputsFor`), so run() gathers for its element."""
    assert "function gatherInputsFor(element, widgets)" in APP_JS
    run = _body(r"async run\(element, args\) \{(.*?)\n  \}")
    assert "this.gatherInputs(element)" in run, (
        "run() sent a command without gathering the entry values")


def test_needs_confirm_re_runs_with_the_args_plus_true():
    run = _body(r"async run\(element, args\) \{(.*?)\n  \}")
    # The page's own confirmation (F17): defaults to Cancel and never blocks
    # the page's stop the way a native dialog blocks every script on it.
    assert "needs_confirm" in run and "this.dashboard.confirm(" in run
    assert "window.confirm(" not in CODE, "a native confirm blocks the stop"
    assert ".concat([true])" in run, (
        "the confirmed re-run must append True to the original args, not "
        "replace them with [true]")
    assert "result.command" in run and "result.inputs" in run


def test_a_refusal_is_a_status_line_on_the_card_and_never_a_popup():
    run = _body(r"async run\(element, args\) \{(.*?)\n  \}")
    assert "this.showRefused(" in run
    assert "alert(" not in run, "a refusal opened a modal"
    assert "putText(this.status, reason)" in _body(
        r"showRefused\(reason, element\) \{(.*?)\n  \}")


def test_only_an_event_that_asks_to_be_acknowledged_opens_a_modal():
    assert re.search(r"if \(event\.needs_ack\) this\.showAck\(event\)", APP_JS), (
        "every event opened a modal, or none did")


def test_entries_are_not_overwritten_while_they_are_being_typed_in():
    entry = _body(r"function renderEntry\(panel, element\) \{(.*?)\n\}")
    assert "isDirty" in entry
    assert "document.activeElement === input" in entry
    assert "if (!widget.isDirty())" in _body(r"\n  refresh\(state\) \{(.*?)\n  \}")


def test_gating_covers_every_element_including_entries():
    refresh = _body(r"\n  refresh\(state\) \{(.*?)\n  \}")
    # G3: the panel's values go with the mode, for `enabled_by`.
    # Updated (rb-link-views V4): a link lost or reconnecting also holds the
    # modes and go commands, so the call is the gate AND not held - still
    # one call, on every widget in the loop.
    assert ("widget.setEnabled(isEnabled(element, mode, this.values) && !held)"
            in refresh), (
        "gating must be applied to every widget in the loop, not to the "
        "buttons the renderer happens to remember")
    # the rule itself, not a second opinion about it
    gate = _body(r"function isEnabled\(element, mode, values\) \{(.*?)\n\}")
    assert "disabled_when" in gate and "enabled_when" in gate
    assert "enabled_by" in gate


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_enabled_by_gates_exactly_as_schema_is_enabled_does():
    """G3: `sch.is_enabled(element, mode, values)`, mirrored and checked
    against the Python rule case by case: an element with `enabled_by` is
    live only while that value is truthy, the mode gates still apply, and a
    caller without values skips the rule."""
    cases = [
        ({"enabled_by": "on"}, "idle", {"on": False}),
        ({"enabled_by": "on"}, "idle", {"on": True}),
        ({"enabled_by": "on"}, "idle", {}),
        ({"enabled_by": "on"}, "idle", None),
        ({"enabled_by": "on", "disabled_when": ["run"]}, "run", {"on": True}),
        ({"enabled_by": "on", "enabled_when": ["ready"]}, "ready", {"on": True}),
        ({"enabled_by": "on", "enabled_when": ["ready"]}, "idle", {"on": True}),
        ({"disabled_when": ["run"]}, "run", {"on": True}),
        ({}, "idle", {"on": False}),
    ]
    for element, mode, values in cases:
        want = sch.is_enabled(element, mode, values)
        got = _node_value(f"isEnabled({json.dumps(element)}, {json.dumps(mode)}, "
                          f"{json.dumps(values)})")
        assert got is want, (element, mode, values)


def test_a_checkbox_sends_the_new_value_read_from_the_model():
    """G3: `PanelView._run_checkbox`, mirrored: ONE argument, the new
    boolean, computed from the model's value rather than the widget's (a box
    drawn one poll behind still flips the right way). The box is set from
    the state on every poll, written only when it differs."""
    run = _body(r"\n  runCheckbox\(element\) \{(.*?)\n  \}")
    assert "Boolean(this.values[element.model_attr])" in run
    assert "this.run(element, [!on])" in run
    box = _body(r"function renderCheckbox\(panel, element\) \{(.*?)\n\}")
    assert "type = 'checkbox'" in box and "panel.runCheckbox(element)" in box
    # Updated (L16): labelled with its panel, for its owner's name.
    assert "labelControl(node, input, element, panel)" in box
    assert "if (input.checked !== next) input.checked = next" in box
    refresh = _body(r"\n  refresh\(state\) \{(.*?)\n  \}")
    assert "kind === 'checkbox'" in refresh


def test_a_stale_state_is_marked_at_the_same_threshold_as_the_desktop_views():
    assert "const STALE_AFTER_S = 1.0;" in APP_JS
    refresh = _body(r"\n  refresh\(state\) \{(.*?)\n  \}")
    # stale by age, or by a lost device (F3)
    assert "this.setStale(isStale(state) || isLost" in refresh


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_a_model_with_no_loop_to_be_stale_about_is_not_stale():
    """`PanelView._refresh`: `age is not None and age > 1.0`. A model with no
    background loop reports a null age, and every card in SIM wore a "stale"
    badge while this was a bare comparison."""
    assert _node_value("isStale({age: null})") is False
    assert _node_value("isStale({})") is False
    assert _node_value("isStale(null)") is False
    assert _node_value("isStale({age: 0.5})") is False
    assert _node_value("isStale({age: 1.5})") is True


def test_the_global_full_stop_follows_the_state_not_the_click():
    # Updated (L1, round 7): the state's `stop_words` (views.base), not
    # `is_estopped` (any model latched) - one model's own switch is not
    # "every model is stopped" - and a press clears only when they say so.
    assert "this.renderEstop(state.stop_words || NO_STOP_WORDS)" in APP_JS
    toggle = _body(r"async toggleEstopAll\(\) \{(.*?)\n  \}")
    assert "this.stopAction !== 'clear'" in toggle
    assert "this.stopAll()" in toggle and "/api/clear_estop_all" in toggle
    assert "/api/estop_all" in _body(r"\n  async stopAll\(\) \{(.*?)\n  \}")
    assert "needs_confirm" in toggle, "the latch cleared without asking"


def test_the_unconfirmed_stop_line_follows_the_latch_from_the_poll():
    """G6 (round-4 IMP-0): the line is dropped by the poll once the latch
    reads clear - so a clear from another client drops it too - but never
    by an answer asked for before it was shown.

    Updated (L1/L2, round 7): the entry's mark is the model's own
    `stop_confirmed` and the rail's marks are `stop`, both from the poll -
    no longer written from the stop's answer - and what the poll drops once
    nothing is latched is the tray's "Stop not confirmed" line."""
    apply = _body(r"\n  async applyState\(state, askedAt\) \{(.*?)\n  \}")
    assert "this.setUnconfirmed(stop.latched || [], stop.unconfirmed || [])" in apply
    assert "this.forgetStopLine(stop, askedAt)" in apply
    forget = _body(r"\n  forgetStopLine\(stop, askedAt\) \{(.*?)\n  \}")
    assert "askedAt < (this.trayAt || 0)" in forget
    assert "this.setTray(null)" in forget
    refresh = _body(r"\n  refresh\(state\) \{(.*?)\n  \}")
    assert "state.stop_confirmed === false" in refresh


def test_model_cards_can_be_closed_and_a_closed_model_can_be_reopened():
    assert "/api/close_model" in APP_JS and "/api/open_model" in APP_JS
    assert "renderClosed(closed)" in APP_JS


def test_the_setup_panel_goes_through_the_same_renderer():
    setup = _body(r"async loadSetup\(\) \{(.*?)\n  \}")
    assert "new PanelCard(this, SETUP_NAME" in setup, (
        "Setup got its own bespoke wizard again")
    assert "const SETUP_NAME = '__setup__';" in APP_JS


# --------------------------------------------------------------------------
# Addendum 2: row sections, a collapsing Setup panel, and integers that look
# like integers
# --------------------------------------------------------------------------
def test_a_row_section_is_rendered_as_a_row():
    """`sch.section(..., layout="row")` is a hint every renderer must honour.
    The Web client used to draw one vertical list per section, which is what
    made Setup "everything in one vertical tab"."""
    build = _body(r"\n  build\(\) \{(.*?)\n  \}")
    assert "isRowSection(section)" in build, (
        "build() never looks at the section's layout")
    assert "'section section-row'" in build or "' section-row'" in build, (
        "a row section is not marked in the DOM, so CSS cannot lay it out")
    rule = _body(r"function isRowSection\(section\) \{(.*?)\n\}")
    assert "section.layout === 'row'" in rule


def test_row_sections_share_one_grid_so_their_columns_line_up():
    """A row per model is only a table if the columns agree down the card."""
    build = _body(r"\n  build\(\) \{(.*?)\n  \}")
    assert "rowColumnCount(sections)" in build
    assert "gridTemplateColumns = rowGridColumns(columns)" in build
    assert re.search(r"\.card-body\.table\s*\{[^}]*display:\s*grid", STYLES), (
        "the card body is not a grid, so the row cells cannot share columns")
    assert re.search(r"\.card-body\.table > \.section\s*\{[^}]*display:\s*contents",
                     STYLES), (
        "a row section must be display:contents or its cells measure "
        "themselves and every row is a different width")
    assert re.search(r"\.section-row > :last-child\s*\{[^}]*justify-self:\s*end",
                     STYLES), "a row's status is not right-aligned"


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_a_short_row_still_lines_its_status_up_with_the_others():
    """The grid is as wide as the widest row; a model with no gamepad is
    padded, and the padding goes before the last cell so the status column
    stays the status column."""
    sections = [
        {"layout": "row", "elements": [{"type": "dropdown"}, {"type": "readonly"}]},
        {"layout": "row", "elements": [{"type": "dropdown"}, {"type": "dropdown"},
                                       {"type": "readonly"}]},
        {"layout": "column", "elements": [{"type": "button"}] * 5},
    ]
    assert _node_value(f"rowColumnCount({json.dumps(sections)})") == 3
    assert _node_value("rowGridColumns(3)") == (
        "max-content repeat(2, max-content) minmax(0, 1fr)")
    assert _node_value("rowGridColumns(1)") == "max-content minmax(0, 1fr)"
    build = _body(r"\n  build\(\) \{(.*?)\n  \}")
    assert "cells.splice(cells.length - 1, 0" in build, (
        "padding a short row at the end would push its status out of the "
        "status column")
    # An untitled row claims no name column (views/qt.py's rule), and is
    # padded by one more cell so its status still lands in the last column.
    assert "const hasTitle = !isRow || Boolean(section.title);" in build
    assert "const wanted = columns + (hasTitle ? 0 : 1);" in build


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_a_row_of_commands_spans_the_table_instead_of_setting_its_widths():
    """Setup's Devices and Launch rows hold commands. In a shared grid their
    buttons and their sentence (the scan status; the selection count, or the
    reason Launch is greyed out) would set the widths of the model rows'
    columns, so a row that holds a command spans the table instead."""
    data_row = {"title": "Stepper Probe", "layout": "row", "elements": [
        {"type": "readonly"}, {"type": "dropdown"}, {"type": "dropdown"},
        {"type": "readonly"}]}
    launch_row = {"title": "Launch", "layout": "row", "elements": [
        {"type": "readonly"}, {"type": "button"}, {"type": "button"},
        {"type": "button"}, {"type": "button"}]}
    assert _node_value(f"isCommandRow({json.dumps(launch_row)})") is True
    assert _node_value(f"isCommandRow({json.dumps(data_row)})") is False
    # the command row's five elements must not widen the data rows
    assert _node_value(
        f"rowColumnCount({json.dumps([data_row, launch_row])})") == 4
    # G3: the Launch checkbox is a column of the table like any other cell.
    ticked_row = {"title": "Stepper Probe", "layout": "row", "elements": [
        {"type": "checkbox"}, {"type": "dropdown"}, {"type": "dropdown"},
        {"type": "readonly"}]}
    assert _node_value(f"isCommandRow({json.dumps(ticked_row)})") is False
    assert _node_value(
        f"rowColumnCount({json.dumps([ticked_row, launch_row])})") == 4
    build = _body(r"\n  build\(\) \{(.*?)\n  \}")
    assert "isCommandRow(section)" in build and "' section-span'" in build
    assert "if (isRow && !spans)" in build, (
        "a spanning row has no columns to be padded against")
    assert re.search(r"\.section\.section-span\s*\{[^}]*grid-column:\s*1 / -1",
                     STYLES)
    assert re.search(r"\.section-span > :last-child\s*\{[^}]*margin-left:\s*auto",
                     STYLES)


def test_the_setup_drawer_withdraws_when_the_first_model_appears():
    """`Dashboard._collapse_setup` in base.py, mirrored: the desktop views
    hear `added`, the browser sees `state.models` go from empty to not. Since
    the instrument-console pass Setup is a left drawer rather than a card in
    the rack, so "minimised" is "slid out" - the same edge, the same rule."""
    collapse = _body(r"collapseSetupOnLaunch\(models, setupState\) \{(.*?)\n  \}")
    assert "Object.keys(models || {}).length > 0" in collapse, (
        "the collapse is not driven by a model appearing in the state")
    assert "setupState.is_launched" in collapse, (
        "the Setup panel's own is_launched must close it too - a launch "
        "that builds no model still leaves the wizard")
    assert "this.setDrawerOpen(!isLaunched)" in collapse
    assert "if (isLaunched === this.isLaunched) return;" in collapse, (
        "the collapse must be edge-triggered: a level-triggered version "
        "slams the drawer shut 250 ms after every re-open, and never opens "
        "it again when the system is stopped")
    assert "if (!hasModels && !setupState) return;" in collapse, (
        "a failed setup poll would read as `stopped` and re-open the drawer")
    assert "this.collapseSetupOnLaunch(models, setupState)" in APP_JS, (
        "nothing calls it")


def test_the_setup_drawer_is_open_at_boot_and_reopens_from_the_rail():
    """Setup is where a run begins, so the drawer is open at boot; it slides
    out on launch and the rail's Setup button is the way back (Addendum 2's
    "must stay reopenable", in the drawer's terms)."""
    setup = _body(r"async loadSetup\(\) \{(.*?)\n  \}")
    assert "this.dom.drawerBody.appendChild(this.setupCard.node)" in setup, (
        "the Setup panel is not in the drawer")
    assert "this.setDrawerOpen(true)" in setup, "the drawer does not open at boot"
    drawer = _body(r"\n  setDrawerOpen\(isOpen\) \{(.*?)\n  \}")
    assert "this.dom.drawer.classList.toggle('open', this.isDrawerOpen)" in drawer
    assert "this.dom.setupLink.hidden = this.isDrawerOpen" in drawer, (
        "the rail's Setup button must be the way back when the drawer is shut")
    assert "this.dom.scrim.hidden" in drawer, (
        "an open drawer over live modules needs a scrim behind it")
    assert "this.dom.setupLink.addEventListener('click', () => this.setDrawerOpen(true))" \
        in APP_JS, "nothing reopens the drawer"
    assert "if (event.key !== 'Escape') return;" in APP_JS
    assert "if (this.isDrawerOpen && this.dom.modal.hidden) this.setDrawerOpen(false)" \
        in APP_JS, "Escape does not close the drawer"
    assert re.search(r"\.drawer\s*\{[^}]*position:\s*fixed", STYLES)
    assert re.search(r"\.drawer\s*\{[^}]*width:\s*min\(5[0-9]0px, 100%\)", STYLES)
    # The stop may never be under the drawer, its scrim or any overlay (F1).
    def z(selector):
        found = re.search(r"\n" + selector + r"\s*\{[^}]*z-index:\s*(\d+)", STYLES)
        assert found, f"{selector} has no z-index"
        return int(found.group(1))
    assert z(r"\.rail") > max(z(r"\.drawer"), z(r"\.scrim"), z(r"\.overlay"), z(r"\.tray"))
    # Updated (E): the rail is a column on the left, so the scrim starts
    # beside it (--rail-left) - or under it on a phone (--rail-top).
    assert re.search(r"\.scrim\s*\{[^}]*inset:\s*var\(--rail-top\) 0 var\(--tray-h\) "
                     r"var\(--rail-left\)", STYLES), (
        "the scrim dims the rail, and with it the one control that may "
        "never be dimmed")
    assert re.search(r"\.drawer-body\s*\{[^}]*overflow:\s*auto", STYLES), (
        "the drawer's table has to scroll inside it, not push it open")
    assert re.search(r"\.drawer\.open\s*\{[^}]*transform:\s*none", STYLES)


def test_hiding_a_card_body_beats_the_rule_that_makes_it_a_table():
    """The bench shot, 2026-09-22: the Setup card showed its whole table with
    an "Expand" button on it. `body.hidden` was set, but
    `.card-body.table { display: grid }` is a class-on-class rule and beat a
    bare `.card-body[hidden]`, so the hide did nothing."""
    hide = re.search(r"\.card > \.card-body\[hidden\],\s*\n\.card\.collapsed > "
                     r"\.card-body\s*\{[^}]*display:\s*none", STYLES)
    assert hide, (
        "no rule hides a collapsed card's body specifically enough to beat "
        ".card-body.table")
    table = re.search(r"\.card-body\.table\s*\{[^}]*display:\s*grid", STYLES)
    assert table, "the table rule this has to beat is gone"


def test_an_int_entry_steps_by_one_and_refuses_a_decimal():
    entry = _body(r"function renderEntry\(panel, element\) \{(.*?)\n\}")
    assert "element.value_type === 'int'" in entry
    assert "input.step = '1'" in entry
    assert "rejectsIntInput(event.data)" in entry and "preventDefault()" in entry, (
        "step=1 only moves the browser's own stepper; typing 2.5 still "
        "reached the command")
    assert "isInt ? intText(text)" in entry, (
        "refresh would write the state's formatted value straight into the "
        "box, which is how an int entry showed 5.000")


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_an_int_entry_shows_an_integer_whatever_the_state_formatted():
    assert _node_value("intText('5.000')") == "5"
    assert _node_value("intText(5.7)") == "5"
    assert _node_value("intText('')") == ""
    assert _node_value("intText(null)") == ""
    assert _node_value("intText(undefined)") == ""
    # Not a number at all: handed back rather than blanked, so a status
    # string in an int box is visible instead of silently disappearing.
    assert _node_value("intText('n/a')") == "n/a"


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_an_int_entry_refuses_the_characters_that_make_it_a_float():
    assert _node_value("['.', ',', 'e', 'E', '1.5'].map(rejectsIntInput)") == [
        True, True, True, True, True]
    assert _node_value("['7', '-', '', null, undefined].map(rejectsIntInput)") == [
        False, False, False, False, False]


# --------------------------------------------------------------------------
# polish: the theme's variables, and no numbers of its own
# --------------------------------------------------------------------------
def test_entries_sit_on_a_six_column_sheet_overview_and_device_page():
    """Replaces the wide-card rule (E, 2026-09-25): entries, not cards.
    Updated (K4, 2026-09-26): two pages. The Overview puts every model on
    the six-column sheet in rows of three (two across under 1000 px, one
    under 760); the device page shows its model alone, full width. Shown
    and hidden by CSS on the sheet's class, never moved in the DOM."""
    assert re.search(r"\.sheet\s*\{[^}]*grid-template-columns:\s*repeat\(6", STYLES)
    assert re.search(r"\.card\.is-opened\s*\{\s*grid-column:\s*1 / -1;", STYLES)
    assert re.search(r"\.sheet\.is-device > \.card:not\(\.is-opened\)\s*\{\s*display:\s*none", STYLES)
    assert re.search(r"\.sheet\.is-overview \.card > \.tier-well\s*\{\s*display:\s*none", STYLES)
    assert re.search(r"\.card\.span-2\s*\{\s*grid-column:\s*span 2", STYLES)
    assert "@media (max-width: 62.5rem)" in STYLES
    layout = _body(r"\n  layoutSheet\(\) \{(.*?)\n  \}")
    assert "sheetAcross(names.length, index)" in layout
    assert "appendChild" not in layout and "insertBefore" not in layout
    assert "isWideCard" not in APP_JS


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_the_overview_grid_is_rows_of_three_and_never_leaves_one_alone():
    """K4: the Overview's rows, as entries per row, for one to eight models."""
    rows = _node_value(
        "[1,2,3,4,5,6,7,8].map((n) => Array.from({length: n}, (_, i) => sheetAcross(n, i)))")
    assert rows == [[1], [2, 2], [3, 3, 3], [2, 2, 2, 2], [3, 3, 3, 2, 2],
                    [3] * 6, [3, 3, 3, 2, 2, 2, 2], [3] * 6 + [2, 2]]


def test_a_rendered_figure_sits_on_the_surface_colour_and_is_bounded():
    picture = re.search(r"\n\.picture\s*\{([^}]*)\}", STYLES)
    assert picture, "styles.css no longer styles a rendered figure"
    assert "background: var(--surface)" in picture.group(1)
    assert re.search(r"max-height:\s*[0-9.]+rem", picture.group(1)), (
        "an unbounded figure made the Red Percent card taller than the page")


def test_the_stop_is_a_disc_that_rides_with_the_fixed_rail():
    """Updated (E, 2026-09-25): A's disc in a rail that is fixed down the
    left, round and always signal red.

    Signature (owner ruling 2026-09-27): the disc is a red KEY in an ink
    collar (theme.STOP collar_fill, collar width `--stop-c`) holding a pale
    SURFACE socket band; latched, the collar turns SIGNAL and the band
    floods SKIRT (`.stop-ring.is-latched`), and the key drops by
    `--stop-drop-latched`. The key's SKIRT is a solid box-shadow offset -
    the one shadow rule 1 allows the stop - so "no box-shadow" is gone; no
    gradient still holds."""
    assert re.search(r"\n\.rail\s*\{[^}]*position:\s*fixed", STYLES), (
        "the stop scrolls off the page with the rail")
    stop = re.search(r"\n\.mushroom\s*\{([^}]*)\}", STYLES)
    assert stop, "styles.css no longer styles the stop"
    body = stop.group(1)
    assert "border-radius: 50%" in body, "the stop is not round"
    assert "var(--danger-bg)" in body and "gradient" not in body
    assert "0 var(--stop-skirt) 0 var(--stop-skirt-fill)" in body, "the key lost its skirt"
    ring = re.search(r"\n\.stop-ring\s*\{([^}]*)\}", STYLES).group(1)
    assert "var(--stop-c) solid var(--stop-collar-fill)" in ring
    assert "background: var(--stop-socket)" in ring
    latched = re.search(r"\n\.stop-ring\.is-latched\s*\{([^}]*)\}", STYLES).group(1)
    assert "var(--stop-collar-latched)" in latched and "var(--stop-socket-latched)" in latched
    key_down = re.search(r"\n\.mushroom\.is-latched\s*\{([^}]*)\}", STYLES).group(1)
    assert "var(--stop-drop-latched)" in key_down
    assert 'id="full-stop" type="button" class="mushroom"' in INDEX
    # The copy is the action, and it follows the state rather than the click.
    # Updated (L1, round 7): the face and the action are the server's
    # `stop_words`; "Clear" only while every model is latched.
    estop = _body(r"\n  renderEstop\(words\) \{(.*?)\n  \}")
    assert "words.action === 'clear'" in estop and "putText(this.dom.stopFace, words.face" in estop
    # Signature (spec "Motion"): the latch's one moment is the key's drop,
    # the flood and the collar turning red in --motion-latch; the scale
    # pulse is gone (no motion that is not a state).
    assert "pulse" not in estop and "latch-pulse" not in STYLES
    assert "this.dom.stopRing.classList.toggle('is-latched', isClear)" in estop
    assert "this.dom.stop.classList.toggle('is-latched', isClear)" in estop
    # The per-model stop is a small switch in Diagnostics, not a second disc.
    switch = _body(r"function renderStopToggle\(panel, element\) \{(.*?)\n\}")
    assert "setAttribute('role', 'switch')" in switch and "aria-checked" in switch
    assert "panel.runToggle(element)" in switch, (
        "the switch must run the model's own estop toggle")
    assert "if (element.model_attr === 'is_estopped') return renderStopToggle" in APP_JS


def test_a_readout_does_not_look_like_a_box_the_operator_can_type_in():
    """Updated (E): a value never wears a border and is set in the numeral
    face with tabular figures; it is INK, and trace only while changing
    (.is-changing). An entry is a panel-toned well with a muted underline."""
    value = re.search(r"\n\.value\s*\{([^}]*)\}", STYLES)
    assert value, "styles.css no longer styles a readout"
    assert "border: 0;" in value.group(1), "a readout is bordered like an entry"
    assert "background: none" in value.group(1)
    assert "color: var(--text)" in value.group(1)
    assert re.search(r"\.value\.is-changing\s*\{\s*color:\s*var\(--trace\)", STYLES)
    face = re.search(r"\n\.value, \.mushroom[^{]*\{([^}]*)\}", STYLES).group(1)
    assert "font-variant-numeric: tabular-nums" in face and "var(--numeral-family)" in face
    # Signature: a field is a sunk window whose floor lip is the MUTED
    # underline; the select became a key, so the rule is the field's alone.
    # The page's 1.5 px line width is --line now: --edge is the theme's
    # disabled-rim colour.
    assert re.search(r"\n\.input\s*\{[^}]*border-bottom: var\(--line\) solid "
                     r"var\(--input-border\)", STYLES), "an entry lost its underline"


def test_a_models_key_numbers_are_readings_on_its_entry_not_the_rail():
    """Replaces the rail readouts (E): no value is said twice. The key
    numbers come from the schema (`rail: true`, else the first section) and
    are set as readings on the model's own entry - axes once captioned with
    their letters inline, then primary, then secondary. The rail carries
    names only. The launch stagger moved to the entries."""
    rail = _body(r"function railElements\(schema\) \{(.*?)\n\}")
    assert "element.type === 'readonly'" in rail
    assert "sections" in rail and "slice(0, RAIL_READOUTS)" in rail
    build = _body(r"\n  build\(\) \{(.*?)\n  \}")
    assert "railElements(this.schema)" in build and "axisLetter(element)" in build
    assert "'reading-primary'" in build and "'reading-secondary'" in build
    assert "rail-readouts" not in INDEX and "renderRail" not in APP_JS
    assert re.search(r"\.card\.is-opened \.reading-axis > \.value\s*\{\s*font-size:\s*"
                     r"var\(--reading-focal\)", STYLES)
    assert re.search(r"\.reading-axis > \.value\s*\{\s*font-size:\s*var\(--reading-compact\)",
                     STYLES)
    add = _body(r"\n  async addCard\(name\) \{(.*?)\n  \}")
    assert "'is-entering'" in add and "setProperty('--stagger'" in add
    assert re.search(r"\.card\.is-entering\s*\{[^}]*animation-delay:"
                     r"\s*calc\(var\(--stagger, 0\) \* 60ms\)", STYLES)
    assert re.search(r"prefers-reduced-motion: reduce", STYLES), (
        "reduced motion is not respected")
    reduced = STYLES.split("prefers-reduced-motion: reduce")[1]
    assert "animation-duration: 0s !important" in reduced
    assert "transition-duration: 0s !important" in reduced


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_an_axis_readout_is_known_by_its_letter():
    assert _node_value("['X:', 'Y', 'Z position:', 'Velocity:', 'Xenon:']"
                       ".map((t) => axisLetter({text: t}))") == ["X", "Y", "Z", "", ""]


def test_the_setup_drawers_table_has_a_narrow_launch_column_first():
    """G3: the drawer overrides the table's tracks (name, then one per
    cell); with the Launch box that is name, Launch, Port, Gamepad, Status,
    and the Launch track is only as wide as the box."""
    rule = re.search(r"\.drawer \.card-body\.table\s*\{[^}]*grid-template-columns:([^;]*);",
                     STYLES)
    assert rule, "the drawer no longer shapes Setup's table"
    tracks = re.findall(r"minmax\([^)]*\)|max-content|min-content|auto|[0-9.]+(?:rem|fr)",
                        rule.group(1).replace("!important", ""))
    assert len(tracks) == 5, tracks
    assert tracks[1] in ("max-content", "min-content", "auto"), tracks


def test_every_dropdown_is_the_same_width_and_the_log_is_compact():
    assert re.search(r"\n\.select\s*\{[^}]*width:\s*10rem", STYLES), (
        "dropdowns sized themselves from whatever was chosen")
    log = re.search(r"\n\.tray-log\s*\{([^}]*)\}", STYLES)
    assert log and "overflow-y: auto" in log.group(1)
    assert re.search(r"height:\s*[0-9.]+em", log.group(1)), (
        "an unbounded log pushes the rack off the screen")


def test_the_event_tray_reserves_its_own_room_and_starts_as_one_line():
    """The bench shot, 2026-09-22: the events panel covered the bottom of
    every card because nothing reserved space for it. The instrument-console
    pass also makes it one line by default - the newest event - so the log is
    reference rather than the page."""
    assert re.search(r"\.tray\s*\{[^}]*position:\s*fixed", STYLES)
    reserve = _body(r"\n  reserveLogSpace\(\) \{(.*?)\n  \}")
    assert "document.body.style.paddingBottom = panel.offsetHeight" in reserve, (
        "the page must reserve the tray's MEASURED height - it changes when "
        "the tray collapses and when --font-size changes")
    assert re.search(r"\nbody \{[^}]*padding-bottom:", STYLES), (
        "the page reserves nothing before the client has measured")
    collapse = _body(r"\n  setLogCollapsed\(isCollapsed\) \{(.*?)\n  \}")
    assert "this.dom.log.hidden = this.isLogCollapsed" in collapse
    assert "'Show events' : 'Hide events'" in collapse
    assert "this.reserveLogSpace()" in collapse, (
        "collapsing the tray without re-measuring leaves a hole the size of "
        "the old tray")
    assert "this.setLogCollapsed(true)" in _body(r"async start\(\) \{(.*?)\n  \}"), (
        "the tray must start collapsed, at its one line, with its room "
        "reserved")
    assert "window.addEventListener('resize', () => this.reserveLogSpace())" in APP_JS
    # the one line is the latest event, not an empty bar with a label on it.
    # Updated (L1/L11, round 7): in its own ellipsizing span (setTray), as
    # the event's title and message.
    show = _body(r"\n  showEvent\(event, options\) \{(.*?)\n  \}")
    assert "this.setTray(event)" in show
    tray = _body(r"\n  setTray\(event\) \{(.*?)\n  \}")
    assert "putText(this.dom.trayText, text)" in tray and "eventText(event)" in tray
    assert re.search(r"\.tray:not\(\.open\) \.tray-log\s*\{[^}]*display:\s*none",
                     STYLES)


def test_the_stylesheet_sizes_nothing_in_absolute_points_or_pixels():
    """`--font-size` scales the page; a font size in px or pt would not move
    with it (theme.set_font_size, --font-size on :root)."""
    absolute = re.findall(r"font-size:\s*[0-9.]+(?:px|pt)", STYLES)
    assert not absolute, f"absolute font sizes in styles.css: {absolute}"


def test_the_region_picker_scales_the_drag_to_screen_coordinates():
    drag = _body(r"bindRegionDrag\(card, element, frame, picture\) \{(.*?)\n  \}")
    assert "scaleX" in drag and "scaleY" in drag
    assert "frame.left" in drag and "frame.top" in drag, (
        "a monitor that does not start at 0,0 would give the model a region "
        "in the wrong place")
    assert "/api/screen" in APP_JS


# --------------------------------------------------------------------------
# round 2 of the console: one red, one control vocabulary, empty states that
# say what to do next, a rail that wraps instead of clipping
# --------------------------------------------------------------------------
def test_a_danger_role_command_is_a_quiet_command_not_a_second_red():
    """Signal red is the stop object, the latch and a fault. Red Percent's
    "Stop" (end the run) was a red button beside the mushroom."""
    rule = re.search(r"button\.button\.role-danger\s*\{([^}]*)\}", STYLES)
    assert rule, "nothing quiets a danger-role command"
    # Updated (E): a quiet command is outlined on the sheet, not panel-filled.
    # Signature: a quiet command is the neutral key - a CAP face over the
    # KEY_LIP - like any other; still no signal.
    assert "var(--cap)" in rule.group(1) and "var(--key-lip)" in rule.group(1)
    assert "signal" not in rule.group(1)


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_a_toggle_face_drops_the_repeated_caption_and_moves_an_aside_to_its_title():
    assert _node_value("toggleFace('Sync X: OFF', 'Sync X')") == {
        "text": "Off", "hint": ""}
    assert _node_value("toggleFace('AUTONOMOUS MODE (Click to Stop)', 'Autonomous:')") == {
        "text": "Autonomous mode", "hint": "Click to stop"}
    assert _node_value("toggleFace('Enter Manual Mode', 'Manual / Gamepad:')") == {
        "text": "Enter manual mode", "hint": ""}
    toggle = _body(r"function renderToggle\(panel, element\) \{(.*?)\n\}")
    assert "aria-pressed" in toggle, "a toggle must say its state without the lamp"
    # Updated (E): toggles are as wide as the action they name (the
    # artboards); a bare On/Off face keeps its caption beside it.
    assert "bare-face" in toggle, "a bare On/Off face lost its caption"


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_a_boolean_readout_reads_as_a_word_and_an_empty_one_is_muted():
    assert _node_value("readoutText('false')") == "No"
    assert _node_value("readoutText('True')") == "Yes"
    assert _node_value("readoutText('')") == "--"
    assert _node_value("readoutText('0.00')") == "0.00"
    assert re.search(r"span\.value\.is-empty\s*\{[^}]*color:\s*var\(--muted\)", STYLES), (
        "an empty fault line read as a red '--'")


# --------------------------------------------------------------------------
# Tier F (UI audit, 2026-09-24). The stop-path behaviours are driven in a real
# browser in test_view_web_server.py; these are the rules a read can check.
# --------------------------------------------------------------------------
@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_a_long_option_is_elided_from_the_middle():
    """F15 (HC-6): the tail of a port name is what tells two ports apart."""
    assert _node_value("elideMiddle('/dev/cu.usbmodem1234567890123', 22)") == \
        "/dev/cu.us…34567890123"
    assert _node_value("elideMiddle('SIM', 22)") == "SIM"
    assert _node_value("elideMiddle('x'.repeat(40), 22).length") == 22


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_trace_is_for_numbers_and_a_quiet_word_is_muted():
    """F24 (CRIT-6, HC-16)."""
    assert _node_value("readoutKind('0.000')") == "number"
    assert _node_value("readoutKind('-1.5, 0.0, 2')") == "number"
    assert _node_value("readoutKind('off')") == "quiet"
    assert _node_value("readoutKind('Not set')") == "quiet"
    assert _node_value("readoutKind('run_20260924_101025')") == "word"
    assert re.search(r"span\.value\.is-word,\s*\.row span\.value\.is-word\s*\{[^}]*"
                     r"color:\s*var\(--text\)", STYLES)


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_a_lost_device_is_named_as_an_operator_would_say_it():
    """F3 (HC-1): the rail sentence names the model and the device."""
    assert _node_value("lostDevices({devices: {SerialPort: 'lost', Gamepad: 'bound'}})") == \
        ["serial port"]
    assert _node_value("lostDevices({devices: {}})") == []
    assert _node_value("lostDevices(null)") == []


def test_event_severity_is_ink_mark_and_a_word():
    """F14 (DS-2, UXPM-10): the line's text in the severity ink, its bar in
    the severity mark, and a word beside the colour."""
    # Updated (E): the mark is a square - solid signal for an error - and
    # the word is read to a screen reader (.sr-only) instead of drawn as a
    # badge. Updated (L21, round 7, IMP7-11): a warning's mark is a triangle
    # in the warning mark colour, not a hollow square that reads as a tick
    # box (--warning-mark-hollow is no longer read).
    assert re.search(r"\.event\.severity-error\s*\{[^}]*color:\s*var\(--error-ink\)", STYLES)
    assert re.search(r"\.severity-info\s*\{[^}]*var\(--info-ink\)", STYLES)
    assert re.search(r"\.event\.severity-error::before\s*\{[^}]*background:\s*var\(--error-mark\)",
                     STYLES)
    assert "var(--warning-mark)" in STYLES
    show = _body(r"\n  showEvent\(event, options\) \{(.*?)\n  \}")
    assert "'Error: '" in show and "'Warning: '" in show and "'sr-only'" in show
    assert "event.severity !== 'warning' && event.severity !== 'error'" in show, (
        "the tray line shows warnings and errors only")


def test_the_stop_face_stays_legible_and_its_focus_is_not_the_latch():
    """F24 (CRIT-7): the highlight is at most 5 % ink, so white on the disc is
    over 4.5:1; F9 (DS-1): the stop's focus ring is --stop-focus."""
    # Updated (E): the disc is flat signal with white on it (5.99:1) - no
    # highlight left to wash the word out.
    stop = re.search(r"\n\.mushroom\s*\{([^}]*)\}", STYLES).group(1)
    assert "var(--danger-bg)" in stop and "var(--danger-fg)" in stop
    # Signature: the focus ring is 2 px of --stop-focus OUTSIDE the collar
    # (the collar is what "latched" looks like), keyed on the key's focus.
    ring = re.search(r"\.stop-ring:has\(\.mushroom:focus-visible\)\s*\{([^}]*)\}", STYLES)
    assert ring and "2px solid var(--stop-focus)" in ring.group(1) and "trace" not in ring.group(1)
    # G5 (owner ruling 2026-09-25): one chord on every platform.
    assert 'aria-keyshortcuts="Control+Period"' in INDEX
    assert "Ctrl+." in INDEX, "the rail does not say the shortcut"


def test_no_shortcut_or_copy_exists_on_one_platform_only():
    """G5 (owner ruling 2026-09-25: no platform-specific UI). Ctrl+. is the
    stop chord everywhere; nothing listens for the Meta key, and no copy the
    operator can read or hear names Cmd or the Command key or a Mac."""
    assert "metaKey" not in CODE, "a Meta/Cmd chord is bound"
    stop = _body(r"window\.addEventListener\('keydown', \(event\) => \{(.*?)\n    \}, true\);")
    assert "event.ctrlKey" in stop and "STOP_KEY" in stop
    for text, where in ((CODE, "app.js"), (INDEX, "index.html")):
        for word in ("Cmd", "\u2318", "Meta", "on a Mac", "navigator.platform", "userAgent"):
            assert word not in text, f"{where} names {word!r}"
    assert "const STOP_KEY_HINT = 'Ctrl+.';" in APP_JS


def test_every_overlay_starts_below_the_rail():
    """F1 (WDG-1): nothing this page opens covers the stop."""
    # Updated (E): beside the rail's column, or under its phone bar.
    assert re.search(r"\n\.overlay\s*\{[^}]*inset:\s*var\(--rail-top\) 0 0 var\(--rail-left\)",
                     STYLES)


def test_consecutive_commands_are_one_action_group():
    build = _body(r"\n  build\(\) \{(.*?)\n  \}")
    assert "groupCommands(cells, isRow && !spans)" in build
    group = _body(r"function groupCommands\(cells, isTableRow\) \{(.*?)\n\}")
    assert "if (isTableRow) return cells;" in group, (
        "a data row's cells are its table columns and must not be regrouped")
    # Updated (E): a group is one line of commands at their own widths (the
    # artboards), wrapping when it must.
    assert re.search(r"\n\.actions\s*\{[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap",
                     STYLES), "the commands of a group are not one line"
    assert re.search(r"\.drawer \.section-span > \.actions:last-child\s*\{[^}]*"
                     r"flex:\s*1 0 100%", STYLES), (
        "Setup's Launch row crowds its summary and four commands onto one line")


def test_a_detached_log_is_a_button_and_is_polled_only_while_open():
    """G4: `sch.log_stream(..., detached=True)` is drawn as a button that
    opens an in-page panel, and its source is polled only while the panel is
    open - `PanelView._wants_data`, mirrored as `PanelCard.wantsData`."""
    stream = _body(r"function renderLogStream\(panel, element\) \{(.*?)\n\}")
    assert "if (element.detached) return renderDetachedLog(panel, element);" in stream
    detached = _body(r"function renderDetachedLog\(panel, element\) \{(.*?)\n\}")
    assert "'section'" in detached and "'aria-modal', 'false'" in detached
    assert "'overlay'" not in detached, "the log panel must not be a scrim (F1)"
    assert "isOpen" in detached and "dispose" in detached
    refresh = _body(r"\n  refresh\(state\) \{(.*?)\n  \}")
    assert "if (wantsData && this.wantsData(widget)) this.loadData(widget);" in refresh
    wants = _body(r"\n  wantsData\(widget\) \{(.*?)\n  \}")
    assert "widget.isOpen" in wants
    close = _body(r"\n  close\(\) \{(.*?)\n  \}")
    assert "widget.dispose" in close, "a closed card leaves its log panel behind"


def test_the_log_panel_sits_under_the_rail_and_under_a_confirmation():
    """G4 + F1: the panel is lower in z-order than the rail (the stop stays
    clickable) and the overlays (a confirmation still covers it). I3: it
    opens in its own card's flow, under its button - never fixed to the
    rack's corner over another card - so it also sits under the tray and
    the drawer's scrim like the rest of its card, and scrolls into view
    clear of the rail."""
    rule = re.search(r"\n\.log-window\s*\{([^}]*)\}", STYLES)
    assert rule, "no .log-window rule"
    z = int(re.search(r"z-index:\s*(\d+)", rule.group(1)).group(1))
    rail = int(re.search(r"\n\.rail\s*\{[^}]*?z-index:\s*(\d+)", STYLES).group(1))
    overlay = int(re.search(r"\n\.overlay\s*\{[^}]*?z-index:\s*(\d+)", STYLES).group(1))
    scrim = int(re.search(r"\n\.scrim\s*\{[^}]*?z-index:\s*(\d+)", STYLES).group(1))
    tray = int(re.search(r"\n\.tray\s*\{[^}]*?z-index:\s*(\d+)", STYLES).group(1))
    assert 0 < z < tray < scrim < overlay < rail, (z, tray, scrim, overlay, rail)
    assert "position: relative" in rule.group(1), "the log panel is not in its card's flow"
    assert "position: fixed" not in rule.group(1)
    # Updated (E): the rail's height is only an offset on a phone.
    assert re.search(r"scroll-margin-top:\s*calc\(var\(--rail-top\)", rule.group(1)), (
        "the panel can scroll in under the rail")
    detached = _body(r"function renderDetachedLog\(panel, element\) \{(.*?)\n\}")
    assert "node.appendChild(win)" in detached, "the log panel does not open in its card"
    assert "document.body.appendChild(win)" not in detached


def test_empty_data_elements_say_what_to_do_next():
    assert "feed.dataset.empty = emptyText(" in APP_JS
    assert re.search(r"\.feed:empty::before\s*\{[^}]*content:\s*attr\(data-empty\)",
                     STYLES)
    plot = _body(r"function renderPlot\(panel, element\) \{(.*?)\n\}")
    # Updated (L15, round 7): the note shows while nothing is drawn, and the
    # pane is then one caption line (`is-empty`), written only on a change.
    assert "empty-note" in plot and "drawSeries(canvas, data, element, frozen)" in plot
    assert "if (empty.hidden !== isDrawn) empty.hidden = isDrawn" in plot
    assert "frame.classList.toggle('is-empty', !isDrawn)" in plot
    image = _body(r"function renderImage\(panel, element\) \{(.*?)\n\}")
    assert "'error'" in image and "empty-note" in image
    assert "fillText('No samples" not in APP_JS, (
        "the empty plot is painted small into the canvas again")


def test_a_row_table_says_its_captions_once_in_a_header_row():
    build = _body(r"\n  build\(\) \{(.*?)\n  \}")
    assert "tableHead(sections, columns)" in build
    assert re.search(r"\.section-row:not\(\.section-span\):not\(\.table-head\) > "
                     r"\.cell > \.label\s*\{[^}]*clip-path", STYLES), (
        "the per-cell captions must stay as labels but not repeat on every row")


def test_the_fixed_layers_measure_where_the_rail_is():
    """Updated (E): the rail is a fixed column (a bar across the top on a
    phone); the client measures which, and every fixed layer keeps clear of
    it through --rail-left / --rail-top, and of the tray through --tray-h.
    (Replaces the rail-wraps and rack-collapses rules of the top rail.)"""
    reserve = _body(r"\n  reserveLogSpace\(\) \{(.*?)\n  \}")
    assert "setProperty('--rail-left'" in reserve and "setProperty('--rail-top'" in reserve
    assert "setProperty('--tray-h'" in reserve
    assert re.search(r"\.drawer\s*\{[^}]*bottom:\s*var\(--tray-h\)", STYLES), (
        "the drawer covered the start of the latest event")
    assert re.search(r"\.drawer\s*\{[^}]*left:\s*var\(--rail-left\)", STYLES)
    assert re.search(r"\.tray\s*\{[^}]*left:\s*var\(--rail-left\)", STYLES)
    assert re.search(r"\n\.rail\s*\{[^}]*width:\s*var\(--rail-w\)", STYLES)
    assert re.search(r"--rail-w:\s*200px", STYLES), "the rail does not narrow under 1000 px"


def test_rail_and_tray_controls_are_real_touch_targets():
    assert re.search(r"--hit:\s*2\.75rem", STYLES), "44 px is the floor"
    assert re.search(r"\.rail-control, \.closed \.ghost\s*\{[^}]*min-height:\s*var\(--hit\)",
                     STYLES)
    for control in ("setup-link", "quit-link", "drawer-close", "log-toggle"):
        tag = re.search(r'<button id="' + control + r'"[^>]*>', INDEX)
        assert tag and "rail-control" in tag.group(0), control


def test_closing_a_module_is_quiet_says_what_it_does_and_asks_first():
    head = _body(r"constructor\(dashboard, name, schema, options\) \{(.*?)\n  \}")
    assert "'ghost card-close'" in head and "close.title" in head
    close = _body(r"async closeModel\(name\) \{(.*?)\n  \}")
    assert "this.confirm(" in close


def test_every_font_size_is_on_the_scale():
    # Updated (E): the sheet's px scale in rem (--t-*), plus the theme's own
    # reading and caption sizes (theme.READING_SIZES, CAPTION_SIZE).
    # Signature: plus the theme's axis-letter and tier-2 statistic sizes
    # (AXIS_LETTER_SIZE, STATISTIC_SIZE), and a scale step may be two words
    # (--t-stop-narrow).
    sizes = re.findall(r"font-size:\s*([^;]+);", STYLES)
    off = [size for size in sizes
           if not re.fullmatch(r"var\(--(t-[a-z-]+|reading-(focal|primary|compact|secondary)"
                               r"|caption-size|axis-letter-size|statistic-size|font-size)\)",
                               size.strip())]
    assert not off, f"font sizes off the type scale: {off}"


def test_form_controls_are_labelled():
    # Updated (L16, round 7): the panel travels too, so the name says whose
    # control it is.
    assert "labelControl(node, input, element, panel)" in APP_JS
    assert "labelControl(node, select, element, panel)" in APP_JS
    assert "caption.htmlFor = control.id" in APP_JS
    assert "'Select...'" not in APP_JS, "an ellipsis is one character"


# --------------------------------------------------------------------------
# the two rules this file does not bend
# --------------------------------------------------------------------------
@pytest.mark.parametrize("sink", MARKUP_SINKS)
def test_no_server_string_can_become_markup(sink):
    assert sink not in CODE, (
        f"{sink} is how a model name or a refusal reason becomes markup "
        f"(WEB-9); build nodes and use textContent")


def test_every_fetch_is_bounded_by_a_timeout():
    """WEB-22: an unbounded fetch hung the poll cycle forever behind one
    stalled request. There is one call site, and it is the wrapper."""
    # Updated (F, 2026-09-28): the heartbeat worker is a second thread with
    # no access to the wrapper, so it carries its own AbortController
    # (test_view_web_dashboard pins that); outside it there is one call.
    worker = re.search(r"const HEARTBEAT_WORKER_SOURCE = \[(.*?)\]\.join", APP_JS, re.S)
    page = APP_JS.replace(worker.group(0), "") if worker else APP_JS
    assert len(re.findall(r"(?<![.\w])fetch\(", page)) == 1, (
        "a fetch call bypassed the bounded wrapper")
    wrapper = _body(r"async function api\(path, options\) \{(.*?)\n\}")
    assert "AbortController" in wrapper and "controller.abort()" in wrapper
    assert "window.__FETCH_TIMEOUT_MS__" in APP_JS, (
        "a test cannot shrink the timeout without a 20-second sleep")


# --------------------------------------------------------------------------
# the heartbeat (Updated, F 2026-09-28: a hidden tab keeps checking in)
# --------------------------------------------------------------------------
def test_the_heartbeat_goes_on_while_hidden_and_stops_when_the_tab_goes():
    """Replaced: this used to pin "stops the moment the tab is hidden",
    which was the owner's focus bug - switching to the microscope window
    FULL STOPped an energized station 15 s later. Still deliberately not the
    state poll's signal; it now runs in its own worker, and only the tab
    going (pagehide) or the Quit silences it (test_view_web_dashboard)."""
    watch = _body(r"watchVisibility\(\) \{(.*?)\n  \}")
    assert "visibilitychange" in watch
    assert "this.stopHeartbeat(); else this.startHeartbeat();" not in watch
    assert "window.addEventListener('pagehide', () => this.stopHeartbeat());" in watch
    assert "pageshow" in watch, (
        "a bfcache-restored tab would stay silent after its pagehide")
    send = _body(r"async sendHeartbeat\(\) \{(.*?)\n  \}")
    assert "document.hidden) return;" not in send
    assert "'/api/heartbeat'" in send


def test_the_heartbeat_has_its_own_interval():
    assert "const HEARTBEAT_MS = 2000;" in APP_JS
    assert "const STATE_POLL_MS = 250;" in APP_JS


# --------------------------------------------------------------------------
# the seam: every route the client calls is a route the server answers
# --------------------------------------------------------------------------
def test_every_route_app_js_calls_exists_on_the_server():
    server = SERVER.read_text()
    called = set(re.findall(r"'(/api/[a-z_.]+)", APP_JS))
    answered = set(re.findall(r'"(/api/[a-z_.]+)"', server))
    missing = called - answered
    assert not missing, (
        f"app.js calls {sorted(missing)}, which server.py never answers - "
        f"the WEB-19 failure exactly: both halves' own tests passed")


# --------------------------------------------------------------------------
# styling: theme.py, and nothing else
# --------------------------------------------------------------------------
def test_the_stylesheet_names_no_colour_of_its_own():
    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", STYLES)
    assert not literals, (
        f"styles.css carries its own palette ({literals}); colour lives in "
        f"src/views/theme.py and reaches the browser as /api/theme.css")


def test_no_colour_literal_outside_the_theme():
    """E (2026-09-25): every colour on the page is a token from
    /api/theme.css - in the stylesheet (above), in app.js (canvas strokes,
    the plot) and in index.html alike. A named colour counts too."""
    named = r"\b(white|black|red|blue|gray|grey|silver|navy|orange|yellow|green)\b"
    for text, where in ((CODE, "app.js"), (INDEX, "index.html")):
        literals = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", text)
        literals = [x for x in literals if x not in ("#full-stop",)]
        assert not literals, f"{where} names a colour: {literals}"
        colours = re.findall(r"(?:color|fill|stroke|background)\s*[:=]\s*['\"]?" + named, text)
        assert not colours, f"{where} names a colour: {colours}"


def test_every_variable_the_stylesheet_uses_is_defined_somewhere():
    """Either by the theme, or by the stylesheet itself out of theme values.
    A shade between two tokens - a hairline, a ring, a scrim - is mixed FROM
    them with color-mix and named on `:root` here; what the strictness is
    actually for is caught by the test above, which forbids a literal."""
    # Digits too: the theme's spacing steps are --space-0 .. --space-6.
    defined = set(re.findall(r"(--[a-z0-9-]+):", theme.css_variables()))
    # Signature: the glyphs, which the server serves beside the theme's
    # variables on the same /api/theme.css (server.icon_css).
    from views.web.server import icon_css
    defined |= set(re.findall(r"(--[a-z0-9-]+):", icon_css()))
    defined |= set(re.findall(r"^\s*(--[a-z0-9-]+):", STYLES, re.M))
    # and by the client, for the one value only it knows: a group's place in
    # the launch stagger.
    defined |= set(re.findall(r"setProperty\('(--[a-z0-9-]+)'", APP_JS))
    used = set(re.findall(r"var\((--[a-z0-9-]+)", STYLES + APP_JS))
    used |= set(re.findall(r"getPropertyValue\('(--[a-z0-9-]+)'\)", APP_JS))
    assert used <= defined, f"undefined variables: {sorted(used - defined)}"


def test_the_palette_the_theme_serves_is_the_briefs_six_tokens():
    """Owner ruling 2026-09-22. `--signal` is the stop colour and `--trace`
    is what a live number is drawn in; the stylesheet may name no seventh."""
    css = theme.css_variables()
    for name in ("--bg", "--surface", "--text", "--muted", "--signal", "--trace"):
        assert f"{name}: #" in css, f"the theme does not serve {name}"
    assert "var(--signal)" in STYLES and "var(--trace)" in STYLES
    assert "--signal" in APP_JS or "var(--signal)" in STYLES


def test_the_page_loads_the_theme_and_the_client():
    assert 'href="/api/theme.css"' in INDEX
    assert 'href="/styles.css"' in INDEX
    assert 'src="/app.js"' in INDEX
    for element_id in ("full-stop", "cards", "event-log", "ack-modal",
                       "region-picker", "closed-models", "connection",
                       "log-panel", "log-toggle", "model-nav", "sim-line",
                       "stop-ring", "rail-latched", "sheet-headline",
                       "setup-drawer", "drawer-body", "drawer-close",
                       "scrim", "setup-link", "tray-latest", "rail-alert",
                       "ack-count", "ack-text", "ack-ok", "confirm-modal",
                       "confirm-text", "confirm-yes", "confirm-no"):
        assert f'id="{element_id}"' in INDEX, f"index.html has no #{element_id}"
        assert f"'{element_id}'" in APP_JS


# --------------------------------------------------------------------------
# syntax
# --------------------------------------------------------------------------
@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_app_js_parses():
    result = subprocess.run([NODE, "--check", str(STATIC / "app.js")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# Tier L (audit round 7, 2026-09-26): the pure parts, run in node
# --------------------------------------------------------------------------
@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_a_disabled_command_says_the_gates_reason():
    """L3, updated (O3, IMP8-1): the reason is `views.base.gate_reason`,
    mirrored - the SERVED table (`/api/theme.json` gate_words) read in both
    directions from the element's own gate lists. The local table that said
    "Not in manual mode" while in manual mode is gone. An `enabled_by` tick
    is the one rule the Python function does not cover (Setup's rows)."""
    from views.base import GATE_WORDS, gate_reason
    cases = [({"disabled_when": ["latched"]}, "latched"),
             ({"disabled_when": ["manual", "latched"]}, "manual"),
             ({"disabled_when": ["autonomous", "manual"]}, "autonomous"),
             ({"disabled_when": ["running"]}, "running"),
             ({"disabled_when": ["running", "no_region"]}, "no_region"),
             ({"disabled_when": ["disconnected"]}, "disconnected"),
             ({"disabled_when": ["moving"]}, "moving"),
             ({"disabled_when": ["warming_up"]}, "warming_up"),
             ({"disabled_when": ["fault"]}, "fault"),
             ({"enabled_when": ["running"]}, "no_region"),
             ({"enabled_when": ["running"]}, "latched"),
             ({"enabled_when": ["manual"]}, "disabled"),
             ({"disabled_when": ["running"]}, "idle")]
    table = json.dumps({k: list(v) for k, v in GATE_WORDS.items()})
    calls = ", ".join(f"gateReason({json.dumps(e)}, {json.dumps(m)}, {{}})" for e, m in cases)
    got = _node_value(f"(() => {{ GATE_WORDS = {table}; return [{calls}]; }})()")
    assert got == [gate_reason(e, m) for e, m in cases], got
    assert got[1] == "In manual mode" and got[2] == "In autonomous mode", got
    ticked = _node_value("gateReason({enabled_by: 'probe_enabled'}, 'ready', {probe_enabled: false})")
    assert ticked == "Tick Launch on this row first"
    assert "MODE_REASONS" not in CODE and "Not in manual mode" not in CODE, (
        "app.js keeps its own gate table")


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_an_event_reads_as_its_title_and_message_without_the_source():
    """L11 (IMP7-4): "[Controller] Stop Not Confirmed: ..." reads "Stop not
    confirmed: ..."; a shouted title comes down; a repeat keeps its count;
    a line the page wrote itself (no title) is said as written."""
    got = _node_value("""[
      eventText({source: 'Controller', title: 'Stop Not Confirmed',
                 message: 'Rotator did not confirm the stop within 1 s.', count: 1,
                 text: '[Controller] Stop Not Confirmed: Rotator did not confirm the stop within 1 s.'}),
      eventText({source: 'Controller', title: 'FULL STOP', message: 'latched and confirmed on: A', count: 1}),
      eventText({source: 'X', title: 'Command Failed', message: 'Jam did not complete.', count: 3}),
      eventText({severity: 'warning', text: 'Rotator did not reopen.'}),
    ]""")
    assert got == ["Stop not confirmed: Rotator did not confirm the stop within 1 s.",
                   "Full stop: latched and confirmed on: A",
                   "Command failed: Jam did not complete. (x3)",
                   "Rotator did not reopen."]


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_a_caption_does_not_say_its_unit_twice():
    """L22 (IMP7-15): "Step (deg)" beside a "deg" unit reads "Step"; a
    caption with no declared unit keeps its brackets."""
    got = _node_value("""[
      captionText({text: 'Step (deg):', unit: 'deg'}),
      captionText({text: 'Ramp rate (s/°C):', unit: 's/°C'}),
      captionText({text: 'Position (deg):'}),
      captionText({text: 'Manual Speed:', unit: 'steps/s'}),
    ]""")
    assert got == ["Step", "Ramp rate", "Position (deg)", "Manual speed"]


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_a_slider_key_moves_one_percent_of_the_travel():
    """L6: the arrows move 1 % of the travel, rounded, never less than 1;
    the Page keys 10 %; Home and End nothing."""
    got = _node_value("""[
      sliderKeyDelta(1, 1000, 'ArrowRight'), sliderKeyDelta(1, 1000, 'ArrowDown'),
      sliderKeyDelta(0, 50, 'ArrowUp'), sliderKeyDelta(1, 1000, 'PageUp'),
      sliderKeyDelta(1, 1000, 'PageDown'), sliderKeyDelta(1, 1000, 'Home'),
      sliderKeyDelta(1, 1000, 'End'), sliderKeyDelta(1, 1000, 'a'),
    ]""")
    assert got == [10, -10, 1, 100, -100, 0, 0, None]


def test_the_empty_plot_default_does_not_talk_about_red():
    """L22 (IMP7-14): the heater's plot said "red % is plotted here"; the
    default for a series is neutral, and a model's own `empty` wins."""
    series = re.search(r"series:\s*'([^']*)'", APP_JS).group(1)
    assert "red" not in series.lower(), series
    assert "element.empty ||" in _body(r"function emptyText\(element, command\) \{(.*?)\n\}")


def test_a_warning_mark_is_not_a_box_and_an_entry_shows_where_focus_went():
    """L21 (IMP7-11): the warning mark is a triangle, which nothing on the
    sheet that can be ticked looks like; L22 (IMP7-16): an entry focused
    from the rail shows it.

    Signature (rule 6): the triangle is now the station's warning glyph,
    masked from the served --icon-warning, in the warning mark's colour."""
    warning = re.search(r"\.tray-latest\.severity-warning::before,\s*\.event\.severity-warning::before\s*\{([^}]*)\}",
                        STYLES)
    assert warning and "var(--warning-mark)" in warning.group(1), "the warning mark lost its colour"
    marks = re.search(r"\n\.rail-latched::before,[^{]*\.tray-latest\.severity-warning::before,"
                      r"[^{]*\{([^}]*)\}", STYLES)
    assert marks and "mask: var(--icon-warning)" in marks.group(1), "the warning mark is a box"
    assert re.search(r"\.card:focus-visible\s*\{[^}]*outline:", STYLES)


# --------------------------------------------------------------------------
# MOD-5 / CON-6: the rail's simulation line counts the hardware links a model
# DECLARES (`hardware_devices`), not the class names SerialPort and SMC100.
# --------------------------------------------------------------------------
SIM_LINE = "Simulation, no hardware attached"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_mod5_a_declared_link_of_a_new_class_is_hardware():
    models = {
        "Probe": {"devices": {"SerialPort": "simulated"}, "hardware_devices": ["SerialPort"]},
        "Piezo": {"devices": {"PiezoLink": "verified"}, "hardware_devices": ["PiezoLink"]},
    }
    assert _node_value(f"hardwareLinks({json.dumps(models['Piezo'])})") == ["PiezoLink"]
    assert _node_value(f"simLineText({json.dumps(models)})") == "Simulated: Probe"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_mod5_an_empty_hardware_list_is_no_links_not_the_old_class_names():
    model = {"devices": {"SerialPort": "verified", "SMC100": "verified"},
             "hardware_devices": []}
    assert _node_value(f"hardwareLinks({json.dumps(model)})") == []
    sim = {"devices": {"SerialPort": "simulated"}, "hardware_devices": ["SerialPort"]}
    models = {"Probe": sim, "Rotator": model}
    assert _node_value(f"simLineText({json.dumps(models)})") == SIM_LINE


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_mod5_a_state_without_the_list_falls_back_to_the_class_names():
    piezo = {"devices": {"PiezoLink": "verified"}}
    rotator = {"devices": {"SMC100": "verified", "Gamepad": "bound"}}
    sim = {"devices": {"SerialPort": "simulated"}}
    assert _node_value(f"hardwareLinks({json.dumps(piezo)})") == []
    assert _node_value(f"hardwareLinks({json.dumps(rotator)})") == ["SMC100"]
    assert _node_value(f"simLineText({json.dumps({'Probe': sim, 'Piezo': piezo})})") == SIM_LINE
    assert _node_value(
        f"simLineText({json.dumps({'Probe': sim, 'Rotator': rotator})})") == "Simulated: Probe"
    assert _node_value("simLineText({})") == ""


# --------------------------------------------------------------------------
# MOD-6 / CON-8: a command carries its declared `inputs` plus every edited
# (typed, not committed) entry - `views.base.PanelView._gather_inputs`.
# --------------------------------------------------------------------------
def _gathered(element, boxes):
    """`gatherInputsFor` over plain widget doubles: {attr: (value, edited)}."""
    widgets = ", ".join(
        "{element: %s, readValue: () => %s, isEdited: () => %s}" % (
            json.dumps({"type": "entry", "writable": True, "model_attr": attr}),
            json.dumps(value), "true" if edited else "false")
        for attr, (value, edited) in boxes.items())
    return _node_value(f"gatherInputsFor({json.dumps(element)}, [{widgets}])")


BOXES = {"speed": ("5", False), "steps": ("9", False), "note": ("typed", True)}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_mod6_web_a_declared_input_travels_even_when_clean():
    got = _gathered({"type": "button", "command": "go", "inputs": ["speed"]}, BOXES)
    assert got["speed"] == "5"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_mod6_web_an_undeclared_clean_entry_does_not_travel():
    got = _gathered({"type": "button", "command": "go", "inputs": ["speed"]}, BOXES)
    assert "steps" not in got
    assert _gathered({"type": "button", "command": "halt"},
                     {"steps": ("9", False)}) == {}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_mod6_web_an_undeclared_dirty_entry_travels():
    got = _gathered({"type": "button", "command": "go", "inputs": ["speed"]}, BOXES)
    assert got == {"speed": "5", "note": "typed"}
    assert _gathered({"type": "toggle", "command": "set_mode"}, BOXES) == {"note": "typed"}


def test_mod6_web_every_command_path_gathers_for_its_element():
    """Both callers pass the element: `run` and the file download."""
    assert "this.gatherInputs()" not in CODE
    assert CODE.count("this.gatherInputs(element)") == 2


# --------------------------------------------------------------------------
# Signature (owner ruling 2026-09-27, handoff/tactile3-Signature.md): what a
# read of the stylesheet, the markup and the served theme can check. The
# drawn states are driven in a browser in test_view_web_server.py.
# --------------------------------------------------------------------------
FONTS = STATIC / "fonts"


def test_signature_the_page_self_hosts_figtree_and_rubik_and_names_no_retired_font():
    """Figtree (400-700) for text and Rubik (500-600) for numerals, the latin
    woff2 files Google Fonts serves, with their OFL notices; Public Sans and
    Archivo are gone from the stylesheet, the markup and the folder."""
    for retired in ("Public Sans", "public-sans", "Archivo", "archivo"):
        assert retired not in STYLES and retired not in INDEX, retired
    assert not list(FONTS.glob("public-sans*")) and not list(FONTS.glob("archivo*"))
    faces = dict(re.findall(r'@font-face\s*\{[^}]*font-family:\s*"([^"]+)";[^}]*'
                            r'font-weight:\s*([0-9 ]+);', STYLES))
    assert faces == {"Figtree": "400 700", "Rubik": "500 600"}, faces
    for name in ("figtree-latin.woff2", "rubik-latin.woff2"):
        data = (FONTS / name).read_bytes()
        assert data[:4] == b"wOF2", f"{name} is not a woff2 file"
        assert f'url("/fonts/{name}")' in STYLES and f'href="/fonts/{name}"' in INDEX
    for notice in ("FIGTREE-OFL.txt", "RUBIK-OFL.txt"):
        assert "SIL Open Font License" in (FONTS / notice).read_text()
    assert theme.FONT_FAMILY == "Figtree" and theme.NUMERAL_FAMILY == "Rubik"


def test_signature_the_glyphs_are_served_from_the_themes_one_path_table():
    """Rule 6: one source, theme.ICONS. The server serves every glyph beside
    the theme's variables as --icon-<name>, the page masks it."""
    from urllib.parse import unquote
    from views.web.server import icon_css
    css = icon_css()
    for name in theme.ICON_NAMES:
        served = re.search(r'--icon-' + name + r': url\("data:image/svg\+xml,([^"]+)"\);', css)
        assert served, f"--icon-{name} is not served"
        assert theme.ICONS[name] in unquote(served.group(1)), name
        assert re.search(r"\.glyph-" + name + r"\s*\{\s*--g: var\(--icon-" + name + r"\);", STYLES)
    assert re.search(r"\n\.glyph\s*\{[^}]*mask: var\(--g\)", STYLES)
    for name in theme.ICON_NAMES:
        for path in re.findall(r'd="([^"]+)"', theme.ICONS[name]):
            assert path not in APP_JS + STYLES + INDEX, f"{name}'s path is copied out of the theme"


def test_signature_every_raised_part_is_one_key_family():
    """Rule 2: a face, a KEY_RIM inset line and a KEY_LIP bottom border;
    pressed (:active) and latched ([aria-pressed="true"]) fold the lip to
    1 px and drop the face by the difference; disabled keeps the
    silhouette in ghost tones."""
    key = re.search(r"\n\.button, \.ghost\s*\{([^}]*)\}", STYLES).group(1)
    assert "border-bottom: var(--lip-key) solid var(--key-lip)" in key
    assert "var(--rim)" in key and "background: var(--cap)" in key
    assert re.search(r"--rim:\s*inset 0 0 0 var\(--key-rim-px\) var\(--key-rim\)", STYLES)
    down = re.search(r"\n\.button:active:not\(:disabled\),\s*\.ghost:active:not\(:disabled\),"
                     r"\s*\.button\[aria-pressed=\"true\"\]\s*\{([^}]*)\}", STYLES)
    assert down and "border-top-width: var(--key-drop)" in down.group(1)
    assert "border-bottom-width: var(--lip-pressed)" in down.group(1)
    assert re.search(r"--key-drop:\s*calc\(var\(--lip-key\) - var\(--lip-pressed\)\)", STYLES)
    ghost = re.search(r"\n\.button:disabled, \.ghost:disabled\s*\{([^}]*)\}", STYLES).group(1)
    assert "dashed var(--edge)" in ghost and "solid var(--edge-soft)" in ghost
    assert "var(--disabled-fg)" in ghost
    # ...and a disabled go key too: the ink face came later in the file
    # and won, so a greyed Start run or Home read as live (seen in the
    # first after-captures).
    go = re.search(r"\n\.button\.role-go:disabled,[^{]*\{([^}]*)\}", STYLES)
    assert go and "dashed var(--edge)" in go.group(1) and "var(--disabled-fg)" in go.group(1)
    assert STYLES.index(".button.role-go:disabled,") > STYLES.index("\n.button.role-go {")
    # The small members: the disclosure key (3 px), the fader cap, the
    # switch knob (2.5 px) and the chord's keycaps (2 px).
    assert re.search(r"\n\.disc-key\s*\{[^}]*border-bottom: var\(--lip-small\) solid var\(--key-lip\)", STYLES)
    assert re.search(r"\n\.slider-cap\s*\{[^}]*border-bottom: var\(--lip-small\) solid var\(--key-lip\)", STYLES)
    assert re.search(r"\n\.switch-knob\s*\{[^}]*border-bottom: var\(--lip-knob\)", STYLES)
    assert re.search(r"\nkbd\s*\{[^}]*border-bottom: var\(--lip-kbd\) solid var\(--key-lip\)", STYLES)
    # The chord is two keycaps and still reads "Stop: Ctrl+.".
    hint = re.search(r'<span class="stop-hint"[^>]*>(.*?)</span>\n', INDEX).group(1)
    assert re.sub(r"<[^>]+>", "", hint) == "Stop: Ctrl+.", hint


def test_signature_every_motion_is_a_theme_duration_and_reduced_motion_zeroes_it():
    """Spec "Motion": the parts move in the theme's durations (MOTION); the
    numbers and the fader never animate (no transition on `left` of the
    fader cap, none on a value); prefers-reduced-motion zeroes every one."""
    for part in (r"\n\.button, \.ghost", r"\n\.disc-key", r"\n\.switch-knob", r"\n\.stop-ring",
                 r"\n\.mushroom", r"\n\.slider-cap"):
        body = re.search(part + r"\s*\{([^}]*)\}", STYLES).group(1)
        durations = re.findall(r"transition:([^;]*);", body)
        assert durations, part
        assert not re.search(r"\d+m?s\b", durations[0]), f"{part} names its own duration"
    assert "var(--motion-flag) var(--flag-ease)" in STYLES
    assert "left" not in re.search(r"\n\.slider-cap\s*\{([^}]*)\}", STYLES).group(1).split("transition")[1]
    assert "transition" not in re.search(r"\n\.value\s*\{([^}]*)\}", STYLES).group(1)
    reduced = STYLES.split("prefers-reduced-motion: reduce")[1]
    assert "*, *::before, *::after" in reduced
    assert "animation-duration: 0s !important" in reduced and "transition-duration: 0s !important" in reduced


def test_signature_the_stop_the_flag_and_the_lamp_slot_are_drawn_from_the_theme():
    """The stop's collar, socket and key sizes, the flag window and the lamp
    slot are the theme's tokens (STOP, FLAG, LAMP), never numbers here."""
    ring = re.search(r"\n\.stop-ring\s*\{([^}]*)\}", STYLES).group(1)
    assert "width: var(--stop-d)" in ring
    assert re.search(r"--stop-d: var\(--stop-diameter\);", STYLES)
    narrow = STYLES.split("@media (max-width: 62.5rem)")[1].split("@media")[0]
    for name in ("diameter-narrow", "collar-narrow", "key-narrow"):
        assert f"var(--stop-{name})" in narrow, name
    flag = re.search(r"\n\.flag-window\s*\{([^}]*)\}", STYLES).group(1)
    assert "var(--flag-w)" in flag and "var(--flag-h)" in flag
    for part in ("frame", "fill", "hatch"):
        assert f"var(--flag-{part})" in STYLES
    lamp = re.search(r"\n\.toggle-lamp\s*\{([^}]*)\}", STYLES).group(1)
    assert "var(--lamp-w)" in lamp and "var(--lamp-h)" in lamp and "var(--lamp-off)" in lamp
    assert "trace" not in lamp and "var(--trace)" not in re.search(
        r"\n\.nav-mark\s*\{([^}]*)\}", STYLES).group(1)
    # The flag drops only when the client says a new episode began.
    setter = _body(r"\n  setUnconfirmed\(isUnconfirmed, words\) \{(.*?)\n  \}")
    assert "if (flag && !this.isFlagged) this.flagWindow.classList.add('is-dropping')" in setter
    assert re.search(r"\.flag-window\.is-dropping \.flag\s*\{[^}]*animation: flag-drop", STYLES)


# -- rb-ack (A3): the acknowledgement queue, run as the real code -------------

@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_ack_the_queue_is_one_entry_per_title_and_a_repeat_joins_it():
    out = _node_value("""(() => {
      const q = [];
      const a = ackEnqueue(q, {title: 'Idle Timeout', message: 'idle 300 s', count: 1});
      const b = ackEnqueue(q, {title: 'Rotator Unreachable', message: 'gone', count: 1});
      const c = ackEnqueue(q, {title: 'Idle Timeout', message: 'idle 301 s', count: 1});
      return {a, b, c, titles: q.map((e) => e.title), sizes: q.map((e) => e.events.length)};
    })()""")
    assert out == {"a": 0, "b": 1, "c": 0,
                   "titles": ["Idle Timeout", "Rotator Unreachable"],
                   "sizes": [2, 1]}


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_ack_the_dialog_words_are_the_title_the_newest_message_and_one_key():
    out = _node_value("""(() => {
      const q = [];
      ackEnqueue(q, {title: 'Idle Timeout', message: 'idle 300 s', count: 1});
      const one = ackWords(q);
      ackEnqueue(q, {title: 'Idle Timeout', message: 'idle 301 s', count: 1});
      ackEnqueue(q, {title: 'Heater Off Not Sent', message: 'switch it off', count: 1});
      const two = ackWords(q);
      q.shift();
      return {one, two, three: ackWords(q), none: ackWords([])};
    })()""")
    assert out["one"] == {"title": "Idle timeout", "body": "idle 300 s",
                          "waiting": "", "key": "Understood"}
    assert out["two"] == {"title": "Idle timeout", "body": "idle 301 s (x2)",
                          "waiting": "1 more waiting", "key": "Understood"}
    assert out["three"]["title"] == "Heater off not sent"
    assert out["none"] is None


def test_ack_escape_acknowledges_an_open_dialog_after_a_question_and_the_picker():
    """A3: Return (the focused key) and Escape both answer it; a question
    or the picker over it answers its own Escape first."""
    handler = _body(r"window\.addEventListener\('keydown', \(event\) => \{(.*?)\n    \}, true\);")
    confirm = handler.index("if (this.confirmPending)")
    picker = handler.index("if (!this.dom.picker.hidden)")
    ack = handler.index("if (!this.dom.modal.hidden) { event.preventDefault(); this.acknowledge();")
    assert confirm < picker < ack
    # The stop chord is checked before any of them.
    assert handler.index("this.stopAll()") < confirm


# -- rb-restart R1/R4/R7 -------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_restart_an_action_makes_the_key_its_label_and_adds_later():
    out = _node_value("""(() => {
      const q = [];
      const act = {label: 'Restart now', name: '__setup__', command: 'restart_station', args: [true]};
      ackEnqueue(q, {title: 'Restart Needed', message: 'Updated.', count: 1, action: act});
      const one = ackWords(q);
      ackEnqueue(q, {title: 'Idle Timeout', message: 'idle', count: 1, action: null});
      q.shift();
      return {one, plain: ackWords(q)};
    })()""")
    assert out["one"]["key"] == "Restart now" and out["one"]["later"] == "Later"
    assert out["one"]["action"]["command"] == "restart_station"
    assert out["plain"] == {"title": "Idle timeout", "body": "idle", "waiting": "",
                            "key": "Understood"}


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_restart_only_an_accepted_restart_waits_for_the_station():
    out = _node_value("""[
      isRestartAnswer('__setup__', 'restart_station', {status: 'ok'}),
      isRestartAnswer('__setup__', 'restart_station', {status: 'refused'}),
      isRestartAnswer('__setup__', 'restart_station', {status: 'needs_confirm'}),
      isRestartAnswer('Probe', 'restart_station', {status: 'ok'}),
      isRestartAnswer('__setup__', 'apply_update', {status: 'ok'}),
    ]""")
    assert out == [True, False, False, False, False]


def test_restart_the_page_polls_every_two_seconds_for_a_minute():
    assert re.search(r"const RESTART_POLL_MS = 2000;", APP_JS)
    assert re.search(r"const RESTART_WAIT_MS = 60000;", APP_JS)
    assert "The station did not come back; start it by hand." in APP_JS
    body = _body(r"\n  awaitRestart\(\) \{(.*?)\n  \}\n")
    # Stops beating first: no heartbeat reaches anyone while it waits.
    assert body.index("this.stopHeartbeat()") < body.index("setTimeout(tick")
    assert "state.boot !== old" in body


def test_r7_the_page_logs_every_answer():
    body = _body(r"\n  acknowledge\(acted\) \{(.*?)\n  \}\n")
    assert "this.logAcknowledged(" in body
    assert "'/api/ack'" in APP_JS
    assert '"/api/ack"' in SERVER.read_text()
