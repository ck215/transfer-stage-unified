"""Test for WEB-11: Frontend should not call non-existent commands

The web interface should not have UI for send_raw_command and execute_script
since these commands don't exist on any model.
"""

import re


def test_web_11_no_file_picker_modal():
    """Verify file picker modal is removed (execute_script command doesn't exist)"""
    
    with open('src/views/web/static/index.html', 'r') as f:
        html_content = f.read()
    
    # The file-picker-modal should not exist since execute_script doesn't exist
    assert 'file-picker-modal' not in html_content, \
        "file-picker-modal should be removed - execute_script command doesn't exist"
    
    # btn-confirm-file should not exist
    assert 'btn-confirm-file' not in html_content, \
        "btn-confirm-file should be removed"


def test_web_11_no_raw_command_input():
    """Verify log console raw command input is removed"""
    
    with open('src/views/web/static/index.html', 'r') as f:
        html_content = f.read()
    
    # The input-log-cmd should not exist since send_raw_command doesn't exist
    assert 'input-log-cmd' not in html_content, \
        "input-log-cmd should be removed - send_raw_command command doesn't exist"


if __name__ == '__main__':
    test_web_11_no_file_picker_modal()
    test_web_11_no_raw_command_input()
    print("All tests passed!")
