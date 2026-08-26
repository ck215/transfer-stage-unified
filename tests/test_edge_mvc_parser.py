import pytest
import sys
import os

# Add src to path so we can import app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import app

# Since the parser is deeply nested and untestable, we'll extract the exact logic
# found in app.py -> run_pyside_app -> SetupWindow -> launch_unified to test it directly.
def parse_pygame_string(controllerID):
    if "Joy" in controllerID:
        prefix = controllerID.split(":")[0]
        controllerID = int(prefix.replace("Joy ", ""))
    elif controllerID == "None":
        controllerID = None
    return controllerID

def test_parser_untestable_architecture():
    """
    Test that the parser is exposed and testable.
    Currently, the parser logic is trapped inside an inner class 
    (SetupWindow.launch_unified) within run_pyside_app(), making it untestable.
    """
    assert hasattr(app, "launch_dashboard") or hasattr(app, "launch_unified"), \
        "Implementation Error: Parser is not exposed at the module level. It is buried inside an inner class."

def test_parser_none():
    """
    Test that the parser handles None properly without crashing.
    """
    assert parse_pygame_string(None) is None, "Parser should return None when given None"

def test_parser_malformed_string():
    """
    Test that the parser handles malformed strings gracefully.
    """
    # A malformed string like "Joy" without a colon/number should be handled gracefully, e.g. returning None
    assert parse_pygame_string("Joy") is None, "Parser should return None or handle malformed strings gracefully"

def test_parser_unicode():
    """
    Test that the parser handles Unicode strings properly.
    """
    assert parse_pygame_string("Joy 🕹️: Controller") is None, "Parser should handle unicode gracefully without ValueError"
    
