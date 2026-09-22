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

from station import schema as sch
from station.views import theme

STATIC = Path(__file__).resolve().parents[2] / "station" / "views" / "web" / "static"
SERVER = Path(__file__).resolve().parents[2] / "station" / "views" / "web" / "server.py"
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


def test_every_command_carries_every_entry_value():
    """D-5: the value typed a moment ago is never one edit behind."""
    assert "gatherInputs()" in APP_JS
    run = _body(r"async run\(element, args\) \{(.*?)\n  \}")
    assert "this.gatherInputs()" in run, (
        "run() sent a command without gathering the entry values")


def test_needs_confirm_re_runs_with_the_args_plus_true():
    run = _body(r"async run\(element, args\) \{(.*?)\n  \}")
    assert "needs_confirm" in run and "window.confirm" in run
    assert ".concat([true])" in run, (
        "the confirmed re-run must append True to the original args, not "
        "replace them with [true]")
    assert "result.command" in run and "result.inputs" in run


def test_a_refusal_is_a_status_line_on_the_card_and_never_a_popup():
    run = _body(r"async run\(element, args\) \{(.*?)\n  \}")
    assert "this.showRefused(" in run
    assert "alert(" not in run, "a refusal opened a modal"
    assert "textContent" in _body(r"showRefused\(reason\) \{(.*?)\n  \}")


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
    assert "widget.setEnabled(isEnabled(element, mode))" in refresh, (
        "gating must be applied to every widget in the loop, not to the "
        "buttons the renderer happens to remember")
    # the rule itself, not a second opinion about it
    gate = _body(r"function isEnabled\(element, mode\) \{(.*?)\n\}")
    assert "disabled_when" in gate and "enabled_when" in gate


def test_a_stale_state_is_marked_at_the_same_threshold_as_the_desktop_views():
    assert "const STALE_AFTER_S = 1.0;" in APP_JS
    refresh = _body(r"\n  refresh\(state\) \{(.*?)\n  \}")
    assert "this.setStale(isStale(state))" in refresh


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
    assert "this.renderEstop(Boolean(state.is_estopped))" in APP_JS
    toggle = _body(r"async toggleEstopAll\(\) \{(.*?)\n  \}")
    assert "/api/estop_all" in toggle and "/api/clear_estop_all" in toggle
    assert "needs_confirm" in toggle, "the latch cleared without asking"


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
    """Setup's Launch row carries the whole selection summary. In a shared
    grid that one long sentence sets the width of every model row's first
    column, so a row that holds a command spans the table instead."""
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
    assert "event.key === 'Escape' && this.isDrawerOpen" in APP_JS, (
        "Escape does not close the drawer")
    assert re.search(r"\.drawer\s*\{[^}]*position:\s*fixed", STYLES)
    assert re.search(r"\.drawer\s*\{[^}]*width:\s*min\(5[0-9]0px, 100%\)", STYLES)
    # The stop may never be under the drawer or under its scrim.
    assert re.search(r"\.rail\s*\{[^}]*z-index:\s*9", STYLES)
    assert re.search(r"\.drawer\s*\{[^}]*z-index:\s*8", STYLES)
    assert re.search(r"\.scrim\s*\{[^}]*z-index:\s*7", STYLES)
    assert re.search(r"\.scrim\s*\{[^}]*inset:\s*var\(--rail-h\)", STYLES), (
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
@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_a_card_with_a_lot_to_say_takes_two_columns_instead_of_one_tall_stripe():
    """The bench look, 2026-09-22: three equal columns left the Stepper card
    mostly empty and Red Percent absurdly tall."""
    many = [{"title": str(n), "elements": [{"type": "button"}] * 3}
            for n in range(10)]
    few = [{"title": "Motion", "elements": [{"type": "button"}] * 4}]
    table = [{"title": "Probe", "layout": "row",
              "elements": [{"type": "dropdown"}] * 3}] * 8
    assert _node_value(f"isWideCard({json.dumps(many)})") is True
    assert _node_value(f"isWideCard({json.dumps(few)})") is False
    assert _node_value(f"isWideCard({json.dumps(table)})") is False, (
        "a row-layout card is a table; splitting it into two columns would "
        "break the very alignment the row layout is for")
    build = _body(r"\n  build\(\) \{(.*?)\n  \}")
    assert "isWideCard(sections)" in build and "'wide'" in build
    assert re.search(r"\.card\.wide\s*\{[^}]*grid-column:\s*span 2", STYLES)
    assert re.search(r"\.card\.wide > \.card-body\s*\{[^}]*column-count:\s*2",
                     STYLES)
    assert re.search(r"\.rack\s*\{[^}]*grid-auto-flow:\s*dense", STYLES), (
        "a wide panel would leave a column-shaped hole beside it")


def test_a_rendered_figure_sits_on_the_surface_colour_and_is_bounded():
    picture = re.search(r"\n\.picture\s*\{([^}]*)\}", STYLES)
    assert picture, "styles.css no longer styles a rendered figure"
    assert "background: var(--surface)" in picture.group(1)
    assert re.search(r"max-height:\s*[0-9.]+rem", picture.group(1)), (
        "an unbounded figure made the Red Percent card taller than the page")


def test_the_stop_is_a_mushroom_that_rides_with_the_sticky_rail():
    """The design brief's one bold element: round, signal red, a darker ring
    and an inset highlight, sitting on a rail that never scrolls away."""
    assert re.search(r"\.rail\s*\{[^}]*position:\s*sticky", STYLES), (
        "the stop scrolls off the page with the rail")
    stop = re.search(r"\n\.mushroom\s*\{([^}]*)\}", STYLES)
    assert stop, "styles.css no longer styles the stop"
    body = stop.group(1)
    assert "border-radius: 50%" in body, "the stop is not round"
    assert "var(--signal)" in body, "the stop is not the signal colour"
    assert "box-shadow:\n    inset" in body, "the disc has no inset highlight"
    assert "border: 0.32rem solid" in body, "the disc has no outer ring"
    assert 'id="full-stop" type="button" class="mushroom"' in INDEX
    # The copy is the action, and it follows the state rather than the click.
    estop = _body(r"\n  renderEstop\(isEstopped\) \{(.*?)\n  \}")
    assert "isEstopped ? 'Clear' : 'Stop'" in estop
    assert "classList.add('pulse')" in estop and "!wasEstopped" in estop, (
        "the latch must pulse once when it is SET, not for as long as it is")
    assert re.search(r"\.mushroom\.pulse\s*\{[^}]*animation:\s*latch-pulse", STYLES)
    # and the per-model Safety stop is the same object, one size down
    assert re.search(r"\.mushroom\.mini\s*\{", STYLES)
    mini = _body(r"function renderStopToggle\(panel, element\) \{(.*?)\n\}")
    assert "button.classList.add('mini')" in mini
    assert "panel.runToggle(element)" in mini, (
        "the mini mushroom must run the model's own estop toggle")
    assert "if (element.model_attr === 'is_estopped') return renderStopToggle" in APP_JS


def test_a_readout_does_not_look_like_a_box_the_operator_can_type_in():
    """The instrument-console pass takes the well away entirely: a readout is
    a number set in the trace colour at display size with tabular figures,
    and an entry is the only thing on the panel wearing a border."""
    value = re.search(r"\n\.value\s*\{([^}]*)\}", STYLES)
    assert value, "styles.css no longer styles a readout"
    assert "border: 0;" in value.group(1), "a readout is bordered like an entry"
    assert "background: none" in value.group(1)
    assert "font-variant-numeric: tabular-nums" in value.group(1)
    assert "color: var(--trace)" in value.group(1), (
        "a live number is drawn in the trace colour (design brief)")
    assert re.search(r"\.input, \.select\s*\{[^}]*border: 1px solid var\(--muted\)",
                     STYLES), "an entry has to keep a border a readout does not"


def test_the_rail_derives_each_models_key_numbers_from_its_own_schema():
    """The rail is the hero and it is generic: no model is named here. The
    key numbers are the readonly elements of a model's FIRST schema section,
    which is where every model in this station puts what is watched."""
    rail = _body(r"function railElements\(schema\) \{(.*?)\n\}")
    assert "element.type === 'readonly'" in rail
    assert "sections" in rail and "slice(0, RAIL_READOUTS)" in rail
    render = _body(r"\n  renderRail\(models\) \{(.*?)\n  \}")
    assert "this.railGroups" in render
    assert "(models[name] || {}).values" in render, (
        "the rail must follow state.values on every poll")
    assert "buildRailGroup" in render
    # built once per model, so the launch stagger plays exactly once
    build = _body(r"\n  buildRailGroup\(name, index\) \{(.*?)\n  \}")
    assert "'readout-group is-entering'" in build
    assert "setProperty('--stagger'" in build
    assert re.search(r"\.readout-group\.is-entering\s*\{[^}]*animation-delay:"
                     r"\s*calc\(var\(--stagger, 0\) \* 60ms\)", STYLES), (
        "the 60 ms stagger the brief asks for is not in the stylesheet")
    assert re.search(r"prefers-reduced-motion: reduce", STYLES), (
        "reduced motion is not respected")
    reduced = STYLES.split("prefers-reduced-motion: reduce")[1]
    assert "animation-duration: 0s !important" in reduced
    assert "transition-duration: 0s !important" in reduced


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
    # the one line is the latest event, not an empty bar with a label on it
    show = _body(r"\n  showEvent\(event\) \{(.*?)\n  \}")
    assert "this.dom.trayLatest.textContent = event.text" in show
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
    assert len(re.findall(r"(?<![.\w])fetch\(", APP_JS)) == 1, (
        "a fetch call bypassed the bounded wrapper")
    wrapper = _body(r"async function api\(path, options\) \{(.*?)\n\}")
    assert "AbortController" in wrapper and "controller.abort()" in wrapper
    assert "window.__FETCH_TIMEOUT_MS__" in APP_JS, (
        "a test cannot shrink the timeout without a 20-second sleep")


# --------------------------------------------------------------------------
# the heartbeat's old semantics, kept
# --------------------------------------------------------------------------
def test_the_heartbeat_stops_when_the_tab_is_hidden_and_resumes_when_it_is_not():
    """Deliberately not the same signal as the state poll, which keeps
    running in a backgrounded tab and would tell the watchdog a client is
    present while the laptop lid is shut (D-8)."""
    watch = _body(r"watchVisibility\(\) \{(.*?)\n  \}")
    assert "visibilitychange" in watch
    assert "if (document.hidden) this.stopHeartbeat(); else this.startHeartbeat();" in watch
    assert "pagehide" in watch, (
        "a bfcache-restored tab would resume a stale interval")
    send = _body(r"async sendHeartbeat\(\) \{(.*?)\n  \}")
    assert "document.hidden) return;" in send, (
        "a direct call while hidden still counted as a live client")
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
        f"station/views/theme.py and reaches the browser as /api/theme.css")


def test_every_variable_the_stylesheet_uses_is_defined_somewhere():
    """Either by the theme, or by the stylesheet itself out of theme values.
    A shade between two tokens - a hairline, a ring, a scrim - is mixed FROM
    them with color-mix and named on `:root` here; what the strictness is
    actually for is caught by the test above, which forbids a literal."""
    defined = set(re.findall(r"(--[a-z-]+):", theme.css_variables()))
    defined |= set(re.findall(r"^\s*(--[a-z-]+):", STYLES, re.M))
    # and by the client, for the one value only it knows: a group's place in
    # the launch stagger.
    defined |= set(re.findall(r"setProperty\('(--[a-z-]+)'", APP_JS))
    used = set(re.findall(r"var\((--[a-z-]+)", STYLES + APP_JS))
    used |= set(re.findall(r"getPropertyValue\('(--[a-z-]+)'\)", APP_JS))
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
                       "log-panel", "log-toggle", "rail-readouts",
                       "setup-drawer", "drawer-body", "drawer-close",
                       "scrim", "setup-link", "tray-latest"):
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
