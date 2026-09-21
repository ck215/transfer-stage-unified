"""SKELETON - to be implemented. See REBUILD_BRIEF.md.

The member names below are the contract (design.json). Private helpers may be
added; public names may not change without the lead.
"""
from station.controller import Controller
from station.setup import Setup

# The only entry module. main() picks a view, builds the one Controller, hands both to the view. Replaces app + app_bootstrap + lifecycle + devices.

def launch(*args, **kwargs):
    """was <app>.run_legacy_app, <app>.run_pyside_app, <app>.run_web_app
    three 390-line launcher functions, each nesting its own setup wizard,
    become one launch(view_name) over a 3-entry table; the wizards are
    replaced by the Setup panel
    """
    raise NotImplementedError


def main(*args, **kwargs):
    """was <app>.main
    """
    raise NotImplementedError


def pick_view(*args, **kwargs):
    """was <app>.select_view
    verb scheme; 'select' is reserved for operator selections
    """
    raise NotImplementedError
