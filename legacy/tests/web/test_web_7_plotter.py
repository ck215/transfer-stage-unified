"""Test for WEB-7: Plotter should only use current_red, not red_change

The web plotter feeds BOTH current_red and red_change into one series,
corrupting the data. This test verifies that pushPlotterSample is only
called for current_red, not for every attribute containing "red".
"""

import pytest


def test_web_7_plotter_uses_only_current_red():
    """Verify that plotter sample is only added for current_red, not red_change"""
    
    # Load the JavaScript to parse and verify
    import re
    
    with open('src/views/web/static/js/app.js', 'r') as f:
        content = f.read()
    
    # Find the pollState method where plotter samples are added
    # The current broken code is: if (attr.toLowerCase().includes('red') && typeof val === 'number')
    # The fix should be: if (attr === 'current_red' && typeof val === 'number')
    
    # Look for the specific line that calls pushPlotterSample
    push_pattern = r"if\s*\(\s*attr\s*(?:===|==)\s*['\"]current_red['\"]\s*&&\s*typeof\s+val\s*===\s*['\"]number['\"]\s*\)"
    
    matches = re.finditer(push_pattern, content)
    push_matches = list(matches)
    
    assert len(push_matches) > 0, "Should check for exactly 'current_red', not any attribute containing 'red'"
    
    # Also verify the old broken pattern is NOT present
    broken_pattern = r"if\s*\(\s*attr\s*\.\s*toLowerCase\s*\(\s*\)\s*\.\s*includes\s*\(\s*['\"]red['\"]\s*\)"
    broken_matches = list(re.finditer(broken_pattern, content))
    
    assert len(broken_matches) == 0, "Old broken pattern (attr.toLowerCase().includes('red')) should not exist"


if __name__ == '__main__':
    test_web_7_plotter_uses_only_current_red()
    print("Test passed!")
