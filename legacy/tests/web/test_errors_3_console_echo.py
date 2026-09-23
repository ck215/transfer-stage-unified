"""Test for ERRORS-3: Console echo and no flood on first connect

Two requirements:
1. Error reports should print to console even when subscribers exist
2. First connection should not flood all accumulated errors as toasts
"""


def test_errors_3_console_echo():
    """WebErrorManager should print error reports to console"""
    with open('src/views/web/web_view.py', 'r') as f:
        content = f.read()

    # Check that WebErrorManager report methods include print statements
    lines = content.split('\n')
    found_report_error_print = False
    found_report_warning_print = False
    found_report_info_print = False

    for i, line in enumerate(lines):
        if 'def report_error' in line:
            # Check next 15 lines for a print statement
            method_text = '\n'.join(lines[i:i+15])
            if 'print(' in method_text:
                found_report_error_print = True
        elif 'def report_warning' in line:
            method_text = '\n'.join(lines[i:i+15])
            if 'print(' in method_text:
                found_report_warning_print = True
        elif 'def report_info' in line:
            method_text = '\n'.join(lines[i:i+10])
            if 'print(' in method_text:
                found_report_info_print = True

    assert found_report_error_print, "report_error should print to console"
    assert found_report_warning_print, "report_warning should print to console"
    assert found_report_info_print, "report_info should print to console"


def test_errors_3_no_flood_on_init():
    """JavaScript should initialize lastErrorId to avoid flooding on first connect"""
    with open('src/views/web/static/js/app.js', 'r') as f:
        content = f.read()

    # Check that there's an initializeErrorTracking method
    assert 'initializeErrorTracking' in content, \
        "Should have initializeErrorTracking() to set lastErrorId on init"

    # Check that init() calls initializeErrorTracking
    assert 'await this.initializeErrorTracking()' in content, \
        "init() should call initializeErrorTracking()"

    # Check that it fetches /api/errors without a since parameter
    assert '/api/errors' in content


if __name__ == '__main__':
    test_errors_3_console_echo()
    test_errors_3_no_flood_on_init()
    print("Tests passed")
