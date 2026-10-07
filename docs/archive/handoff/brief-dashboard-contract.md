# The hosted-model contract (owner ruling 2026-09-28: "Red Percent and the Transfer Map should be a single dashboard")

Core landed by the lead on `mvc-refactor` (read it before the view work):
`Model.HOST` (`src/model/base.py`), `RedMonitor.HOST = "Transfer Map"`,
`Controller.state()` publishes `host` per model (`src/controller/controller.py`:
the host's NAME while the host is launched, else None, never itself),
Setup ticks the hosted rows when a host is ticked (`src/controller/setup.py`
`_enable`), `docs/rebuild/MODEL_CONTRACT.md` step 1, tests in
`tests/test_core_controller.py`, `tests/test_setup.py`,
`tests/test_model_contract.py`.

The models are untouched: Red Percent keeps its Panel, commands, params,
stop path, wire and every test. **Only where a view DRAWS it changes.**
Read `state["models"][name]["host"]`; never a class name.

## What every view does with a hosted model H whose host is M

1. **No page of its own.** H has no link in the page list and no compact
   entry of its own on the Overview. Its stop state (latched, unconfirmed,
   live) is folded into M's link and M's Overview entry: the worse of the
   two shows (latched beats unconfirmed beats live). The rail lamps and the
   tray keep reporting H by its own name (events already do).
2. **On M's device page**, in this order:
   - M's tier-1 sections, as today.
   - A **group** for H: a heading with H's NAME and its state word (its
     `mode` from state, as M's own head shows M's), then H's tier-1
     sections drawn exactly as they would be on H's own page, including
     H's own safety section (its stop switch) if that is tier 1.
   - M's tier-2 disclosure(s) as today ("Configure Transfer Map",
     Diagnostics inside).
   - H's tier-2 disclosure(s), with H's own disclosure text
     ("Red Percent details", its Diagnostics inside), as they would be on
     H's page.
3. **Every element in H's group binds to H**: values from H's state,
   `is_enabled` from H's mode, commands run against H's name, refusals
   and confirms shown at the element, the same as on H's own page. A
   region picker in H's group asks H's `data_command`.
4. **Lifecycle.** M closed while H stays open: `host` turns None and H
   gets its own page and Overview entry back. H closed: its group and its
   disclosures leave M's page; M is untouched. H launched without M: an
   ordinary page (nothing in this contract applies).
5. **Navigation to H by name** (a tray line, a keyboard route, `showPage`
   or its equivalent) lands on M's page, scrolled to H's group.
6. **Focus order**: H's group follows M's tier 1 in the Tab order; the
   existing focus rules of the view apply inside it.
7. **No platform branch, no class name, no new copy beyond the heading.**
   The heading is H's NAME; the state word is the view's existing word.

## Proof

Tests first, against the pre-change view: with a fake host and a fake
hosted model (`host` set in state), the page list has one link, the
host's page holds the hosted group in the order above, a command pressed
in the group reaches the hosted model's name, closing the host gives the
hosted model its page back. Then the real pair in SIM through your view's
usual harness.
